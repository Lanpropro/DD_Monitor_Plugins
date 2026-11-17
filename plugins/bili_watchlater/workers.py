"""QThread wrappers for the watch-later HTTP calls.

A worker never touches widgets or the host context.  It owns its API object,
its requests.Session and a cancellation event; results leave only via signals.
"""
from __future__ import annotations

import threading
from typing import Any, Mapping

from PySide6.QtCore import QThread, Signal

from .bili_api import BiliCancelled, BiliWatchLaterAPI


class _WatchLaterRequest(QThread):
    completed = Signal(int, int, object)
    failed = Signal(int, int, object)

    def __init__(
        self,
        cookies: Mapping[str, Any],
        generation: int,
        request_id: int,
    ) -> None:
        super().__init__()
        self.cookies = dict(cookies)
        self.generation = int(generation)
        self.request_id = int(request_id)
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def _run_api(self, api: BiliWatchLaterAPI) -> Any:
        raise NotImplementedError

    def run(self) -> None:
        api = BiliWatchLaterAPI()
        try:
            if self.cancelled.is_set():
                return
            result = self._run_api(api)
        except BiliCancelled:
            return
        except Exception as error:  # exceptions must never escape a QThread
            if not self.cancelled.is_set():
                self.failed.emit(self.request_id, self.generation, error)
        else:
            if not self.cancelled.is_set():
                self.completed.emit(self.request_id, self.generation, result)
        finally:
            self.cookies.clear()
            api.close()


class ListRequest(_WatchLaterRequest):
    def _run_api(self, api: BiliWatchLaterAPI) -> Any:
        return api.list_all(self.cookies, cancelled=self.cancelled.is_set)


class RemoveRequest(_WatchLaterRequest):
    def __init__(
        self,
        cookies: Mapping[str, Any],
        generation: int,
        request_id: int,
        aid: int,
    ) -> None:
        super().__init__(cookies, generation, request_id)
        self.aid = int(aid)

    def _run_api(self, api: BiliWatchLaterAPI) -> Any:
        return api.remove(
            self.cookies, self.aid, cancelled=self.cancelled.is_set
        )


class AddRequest(RemoveRequest):
    def _run_api(self, api: BiliWatchLaterAPI) -> Any:
        return api.add(self.cookies, self.aid, cancelled=self.cancelled.is_set)


class ClearRequest(_WatchLaterRequest):
    def _run_api(self, api: BiliWatchLaterAPI) -> Any:
        return api.clear(self.cookies, cancelled=self.cancelled.is_set)


class VodResolveRequest(QThread):
    """Resolve one VOD direct stream without ever blocking the GUI thread.

    Follows the same cancellation / generation / ``completed`` + ``failed``
    contract as :class:`_WatchLaterRequest`, but it does not reuse the
    watch-later ``BiliWatchLaterAPI``: :func:`~.vod_stream.resolve_vod` owns its
    own ``requests.Session`` (created and closed inside that function) so two
    concurrent resolutions never share a network object.

    Cookies are passed in as a plain dict snapshot taken on the GUI thread.
    This worker never logs them and never touches a widget.
    """

    completed = Signal(int, int, object)
    failed = Signal(int, int, object)
    metadata_ready = Signal(int, int, object)

    def __init__(
        self,
        cookies: Mapping[str, Any],
        generation: int,
        request_id: int,
        bvid: str,
        cid: int = 0,
        qn: int = 0,
        hq: bool = False,
        page: int = 0,
    ) -> None:
        super().__init__()
        self.cookies = dict(cookies)
        self.generation = int(generation)
        self.request_id = int(request_id)
        self.bvid = str(bvid or "")
        self.cid = int(cid or 0)
        self.qn = int(qn or 0)
        self.hq = bool(hq)
        self.page = max(0, int(page or 0))   # v1.0 需求：：多 P 视频的 0 基页码
        self.load_metadata = False
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        if self.cancelled.is_set():
            return
        try:
            # Late import inside try: keeps this module importable when the VOD
            # module is not needed, lets tests install stand-ins, and ensures a
            # missing frozen-host stdlib module surfaces via failed, never
            # escaping the QThread.
            from .vod_stream import resolve_vod

            stream = resolve_vod(
                self.bvid,
                cid=self.cid,
                cookies=self.cookies,
                cancelled=self.cancelled.is_set,
                qn=self.qn,
                hq=self.hq,
                page=self.page,
            )
        except Exception as error:  # never let an exception escape a QThread
            if not self.cancelled.is_set():
                self.failed.emit(self.request_id, self.generation, error)
        else:
            if not self.cancelled.is_set():
                self.completed.emit(self.request_id, self.generation, stream)
            # Old recovery snapshots lack owner fields. Repair only those,
            # after handing off the stream, without delaying playback for metadata.
            if self.load_metadata and not self.cancelled.is_set():
                try:
                    from .bili_api import video_stat
                    result = video_stat(self.bvid, with_view=True)
                    item = result.item
                except Exception:
                    return  # Optional metadata must not turn playback into failure.
                if item is not None and item.bvid == self.bvid and not self.cancelled.is_set():
                    self.metadata_ready.emit(self.request_id, self.generation, item)


class SubtitleRequest(QThread):
    """One explicitly requested subtitle, never an eager multi-file download.

    Construction does no I/O; the caller starts this only on a button click.
    completed(request_id, []) means unavailable/network/format failure, or
    [(label, srt_path)] transfers one file to the caller (including stale-result
    cleanup). Unexpected/import/write failures emit failed with a safe error.
    Before that transfer, every cancellation/failure removes the local file;
    cancellation emits neither signal. Generation/binding belong to the caller.
    """

    completed = Signal(int, object)
    failed = Signal(int, object)

    def __init__(
        self,
        cookies: Mapping[str, Any],
        request_id: int,
        bvid: str,
        cid: int = 0,
    ) -> None:
        super().__init__()
        self.cookies = dict(cookies)
        self.request_id = int(request_id)
        self.bvid = str(bvid or "")
        self.cid = int(cid or 0)
        self.cancelled = threading.Event()
        # Serialize cancel with signal handoff, not with network/disk I/O.
        self._delivery_lock = threading.RLock()
        # 已创建的临时 SRT 路径（交接后仍记录）：退出 drain 时兜底删除，
        # 防止 completed 信号在宿主退出前未被处理导致临时文件泄漏。
        self.srt_path: str | None = None

    def cancel(self) -> None:
        with self._delivery_lock:
            self.cancelled.set()

    def run(self) -> None:
        if self.cancelled.is_set():
            return
        path = fd = None
        handed_off = False
        failure = None
        try:
            # Frozen-host missing imports are contained within this worker.
            import os
            import tempfile

            from .vod_subtitle import (
                download_subtitle_srt, fetch_subtitle_list, select_subtitle,
            )

            entries = fetch_subtitle_list(
                self.bvid, self.cid,
                cookies=self.cookies, cancelled=self.cancelled.is_set,
            )
            if self.cancelled.is_set():
                return
            entry = select_subtitle(entries)
            results = []
            if entry is not None:
                srt = download_subtitle_srt(
                    entry["url"], cancelled=self.cancelled.is_set
                )
                if self.cancelled.is_set():
                    return
                if srt:
                    fd, path = tempfile.mkstemp(suffix=".srt", prefix="biliwl_sub_")
                    if self.cancelled.is_set():
                        return
                    handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
                    fd = None  # handle now owns the descriptor, even on write error
                    with handle:
                        handle.write(srt)
                    self.srt_path = path  # 记录以便退出 drain 兜底清理
                    results = [(entry["label"], path)]
            with self._delivery_lock:
                if self.cancelled.is_set():
                    return
                self.completed.emit(self.request_id, results)
                handed_off = True
        except BiliCancelled:
            return
        except Exception:
            # Do not forward transport/import exceptions containing credentials
            # or signed URLs, nor keep their traceback in the emitted object.
            failure = RuntimeError("字幕处理失败，请稍后重试")
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if path is not None and not handed_off:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        if failure is not None:
            with self._delivery_lock:
                if not self.cancelled.is_set():
                    self.failed.emit(self.request_id, failure)


class DanmakuRequest(QThread):
    """Fetch one VOD's danmaku list off the GUI thread (v1.0 需求 5).

    Same cancellation contract as ``VodResolveRequest``.  ``completed``
    carries ``[(time, mode, color, text), ...]`` sorted by time; the GUI-side
    ticker feeds them into the host's native ``tile.video_danmaku`` engine.
    A danmaku failure never blocks playback: the caller just logs it.
    """

    completed = Signal(int, object)
    failed = Signal(int, object)

    def __init__(
        self,
        cookies: Mapping[str, Any],
        request_id: int,
        bvid: str,
        cid: int = 0,
    ) -> None:
        super().__init__()
        self.cookies = dict(cookies)
        self.request_id = int(request_id)
        self.bvid = str(bvid or "")
        self.cid = int(cid or 0)
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        if self.cancelled.is_set():
            return
        try:
            # 导入放进 try：冻结宿主可能缺 xml.etree 等 stdlib 模块，
            # 导入异常也必须走 failed 信号，绝不能逃出 QThread。
            from .vod_danmaku import fetch_danmaku

            comments = fetch_danmaku(
                self.bvid,
                self.cid,
                cookies=self.cookies,
                cancelled=self.cancelled.is_set,
            )
        except Exception as error:  # never let an exception escape a QThread
            if not self.cancelled.is_set():
                self.failed.emit(self.request_id, error)
        else:
            if not self.cancelled.is_set():
                self.completed.emit(self.request_id, comments)
