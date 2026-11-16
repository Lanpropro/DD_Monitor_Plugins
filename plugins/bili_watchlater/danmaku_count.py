"""v1.0 需求：：弹幕数懒加载提供器。

照 covers.py 的模式：GUI 线程只维护 LRU 缓存 / in-flight 去重 / 有界队列，
真正的 HTTP（bili_api.video_stat）跑在专属 QThreadPool(2) 的 QRunnable 里，
结果通过常驻 Signal 载体排队回 GUI 线程。无 Cookie、失败静默不重试、
412/429 直接丢弃；队列满时丢弃新请求（靠滚动可见区补取重新触发）。

v1.0：同一次请求顺带取播放量（``stat.view``），所以这里同时缓存
弹幕数与播放量，但仍然只有一次 HTTP、一套去重与限流。
"""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import bili_api
from .models import valid_bvid

MAX_CACHE = 512
MAX_PENDING = 40
POOL_THREADS = 2


class _StatSignals(QObject):
    """QRunnable 不能有信号，用这个常驻载体把结果排队回 GUI 线程。"""

    #: (bvid, 弹幕数(-1=失败), 播放量(None=未知))
    done = Signal(str, int, object)


class _StatTask(QRunnable):
    """在线程池里跑一次 video_stat；绝不触碰任何 GUI 对象。"""

    def __init__(self, bvid: str, signals: _StatSignals) -> None:
        super().__init__()
        self._bvid = bvid
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        count = -1
        view = None
        try:
            # v1.0：with_view=True 让播放量与弹幕数共用这一次请求。
            result = bili_api.video_stat(self._bvid, with_view=True)
            count = int(getattr(result, "danmaku", result))
            view = getattr(result, "view", None)
        except Exception:
            count = -1  # 412/429/超时/格式异常一律静默丢弃，不重试
        try:
            self._signals.done.emit(self._bvid, count, view)
        except RuntimeError:
            pass  # 插件已销毁，结果直接丢弃


class DanmakuCountProvider(QObject):
    """按 bvid 懒取弹幕数（+ 播放量）；结果缓存后通过信号通知页面刷新。"""

    count_ready = Signal(str, int)
    #: v1.0：播放量就绪（与 count_ready 同时到达，同一次请求）。
    view_ready = Signal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache: OrderedDict[str, int] = OrderedDict()
        self._views: OrderedDict[str, int] = OrderedDict()
        self._failed: set[str] = set()
        self._pending: list[str] = []
        self._queued: set[str] = set()
        self._inflight: set[str] = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(POOL_THREADS)
        self._signals = _StatSignals(self)
        self._signals.done.connect(self._delivered)
        self._paused = True
        self._shutdown = False

    # ------------------------------------------------------------------ API
    def get(self, bvid: str) -> int | None:
        """返回已缓存的弹幕数；未命中则按需入队补取，当次返回 None。"""
        identifier = valid_bvid(bvid)
        if not identifier or self._shutdown:
            return None
        cached = self._cache.get(identifier)
        if cached is not None:
            self._cache.move_to_end(identifier)
            return cached
        if identifier in self._failed:
            return None
        if (
            identifier not in self._queued
            and identifier not in self._inflight
        ):
            # 队列有界：满了就丢弃，滚动可见区补取时会再次 get() 补回来。
            if len(self._pending) >= MAX_PENDING:
                return None
            self._queued.add(identifier)
            self._pending.append(identifier)
            self._pump()
        return None

    def cached(self, bvid: str) -> int | None:
        """只读缓存，绝不入队（用于重建行时直接显示已知数量）。"""
        identifier = valid_bvid(bvid)
        if not identifier:
            return None
        value = self._cache.get(identifier)
        if value is not None:
            self._cache.move_to_end(identifier)
        return value

    def cached_view(self, bvid: str) -> int | None:
        """v1.0：只读播放量缓存（重建行时直接显示，绝不入队）。"""
        identifier = valid_bvid(bvid)
        if not identifier:
            return None
        value = self._views.get(identifier)
        if value is not None:
            self._views.move_to_end(identifier)
        return value

    def resume(self) -> None:
        if self._shutdown:
            return
        self._paused = False
        self._pump()

    def pause(self) -> None:
        self._paused = True
        self._pending.clear()
        self._queued.clear()

    def clear(self) -> None:
        """账号切换：丢弃全部缓存与待办（in-flight 结果回来时按代际丢弃）。"""
        self._cache.clear()
        self._views.clear()
        self._failed.clear()
        self._pending.clear()
        self._queued.clear()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._paused = True
        self._cache.clear()
        self._views.clear()
        self._failed.clear()
        self._pending.clear()
        self._queued.clear()
        # 丢弃未开始的任务，短暂等待在跑的 1-2 个收尾，避免向已销毁载体投递。
        try:
            self._pool.clear()
            self._pool.waitForDone(3000)
        except RuntimeError:
            pass
        self._inflight.clear()

    # ------------------------------------------------------------- internals
    def _pump(self) -> None:
        if self._paused or self._shutdown:
            return
        while self._pending:
            bvid = self._pending.pop(0)
            self._queued.discard(bvid)
            if (
                not bvid
                or bvid in self._cache
                or bvid in self._failed
                or bvid in self._inflight
            ):
                continue
            self._inflight.add(bvid)
            task = _StatTask(bvid, self._signals)
            try:
                self._pool.start(task)
            except RuntimeError:
                self._inflight.discard(bvid)

    def _delivered(self, bvid: str, count: int, view: object = None) -> None:
        """线程池结果回到 GUI 线程：只写缓存并发信号。"""
        self._inflight.discard(bvid)
        if self._shutdown:
            return
        try:
            value = int(count)
        except (TypeError, ValueError):
            value = -1
        if value < 0:
            self._failed.add(bvid)
        else:
            self._cache[bvid] = value
            self._cache.move_to_end(bvid)
            while len(self._cache) > MAX_CACHE:
                self._cache.popitem(last=False)
            self.count_ready.emit(bvid, value)
            # v1.0：播放量未知（None/解析失败）时保持"未获取"，界面不显示。
            try:
                plays = None if view is None else max(0, int(view))
            except (TypeError, ValueError):
                plays = None
            if plays is not None:
                self._views[bvid] = plays
                self._views.move_to_end(bvid)
                while len(self._views) > MAX_CACHE:
                    self._views.popitem(last=False)
                self.view_ready.emit(bvid, plays)
        self._pump()
