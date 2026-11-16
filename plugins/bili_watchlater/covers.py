"""Cookie-free cover loading with a small in-memory LRU cache.

Network access happens on the GUI thread through QNetworkAccessManager
(fully async, never blocking).  v1.0: JPEG/PNG decode + downscale run on a
dedicated QThreadPool.  v1.0 (CPU fix): the row cover is only 224x126 logical
pixels, so downloading the full 2560x1440 original and decoding it at full
size was ~50x more CPU than needed.  ``models.cover_thumbnail_url`` asks the
CDN for a 336x188 JPEG (still 1.5x of the widget, and the decoder only ever
downscales), and ``_DecodeTask`` uses QImageReader scaled decode (JPEG DCT
scaling) so even a non-thumbnail source never becomes a full-resolution
QImage.  The cache stores display-size bitmaps only.

Covers are requested with a fixed Referer and User-Agent and never with the
account cookies.
"""
from __future__ import annotations

from collections import OrderedDict
import time

from PySide6.QtCore import (
    QByteArray,
    QBuffer,
    QIODevice,
    QObject,
    QRunnable,
    QSize,
    QThreadPool,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import QGuiApplication, QImage, QImageReader, QPixmap
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from .bili_api import UA
from .models import normalize_cover_url

MAX_ACTIVE = 4
MAX_CACHE = 96
MAX_PENDING = 60
MAX_FAILURES = 2
TRANSFER_TIMEOUT_MS = 8000

# Covers are stored pre-scaled so a large source image never stays resident.
# v1.0: the row widget draws 224x126 logical px (exactly 16:9); at 2x DPR
# 448x252 is pixel-perfect and capped by COVER_MAX_*.  The old 320x180/640x360
# target made us decode and smooth-scale ~3.6x more pixels than needed, and the
# 168x94 base was neither 16:9 nor large enough for the 980px popup.
COVER_BASE_W = 224
COVER_BASE_H = 126
COVER_MAX_W = 448
COVER_MAX_H = 252

# v1.0 需求 7：网格视图的封面改为占满列宽（CenteredColumn 上限 1100px →
# 列宽约 256 → 封面约 248×140），比列表封面的 224×126 大一点。解码目标按
# 这个更大的基准算，仍然被 COVER_MAX_* 封顶，缓存张数（MAX_CACHE）不变。
#
# v1.0 需求 6 复核：网格封面现在恒占满卡片内容宽、高度不足时由
# CoverWidget 以 KeepAspectRatioByExpanding 裁切，显示区域只会更宽不会更窄。
# 980×660 默认窗口 4 列 → 列宽约 232，2x DPR 下目标 ≈464，与 COVER_MAX_W
# 的 448 只差 ~3%；解码基准 256 / 上限 448 仍是最省 CPU 且够用的取值，
# 不改动数值以免破坏既有解码上限契约（缓存张数 MAX_CACHE 也不变）。
GRID_COVER_BASE_W = 256
GRID_COVER_BASE_H = 144


def _cover_target_size() -> QSize:
    """Bound the stored cover size by the screen device pixel ratio."""
    dpr = 1.0
    app = QGuiApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        # Prefer the screen's DPR: the app-wide getter can report 1.0 and
        # mask a 2x display.  Take the largest valid value, capped at 2x.
        getters = (
            getattr(screen, "devicePixelRatioF", None) if screen else None,
            getattr(screen, "devicePixelRatio", None) if screen else None,
            getattr(QGuiApplication, "devicePixelRatioF", None),
        )
        for getter in getters:
            if getter is None:
                continue
            try:
                value = float(getter())
            except (TypeError, ValueError, RuntimeError):
                continue
            if value >= 1.0:
                dpr = max(dpr, min(2.0, value))
    base_w = max(COVER_BASE_W, GRID_COVER_BASE_W)
    base_h = max(COVER_BASE_H, GRID_COVER_BASE_H)
    return QSize(
        min(COVER_MAX_W, round(base_w * dpr)),
        min(COVER_MAX_H, round(base_h * dpr)),
    )


class _DecodeSignals(QObject):
    """QRunnable 不能有信号，用这个常驻载体把结果排队回 GUI 线程。"""

    done = Signal(str, QImage)


class _DecodeTask(QRunnable):
    """在线程池里解码 + 缩放；绝不触碰 GUI 对象。

    QImageReader.setScaledSize() 让 JPEG 解码器直接产出小位图（1/2、1/4、
    1/8 DCT 缩放），2560x1440 的原图根本不会被完整解码出来；CDN 缩略图
    则连重采样都不需要。
    """

    def __init__(
        self, url: str, payload: bytes, target: QSize, signals: _DecodeSignals
    ) -> None:
        super().__init__()
        self._url = url
        self._payload = payload
        self._target = target
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        image = QImage()
        try:
            # QBuffer(QByteArray*) 不持有数据所有权：QByteArray 必须活到
            # reader.read() 结束。写成 QBuffer(QByteArray(payload)) 会让临时
            # 对象立刻被回收，实测先出空图、紧接着段错误。
            payload = QByteArray(self._payload)
            buffer = QBuffer(payload)
            if buffer.open(QIODevice.OpenModeFlag.ReadOnly):
                reader = QImageReader(buffer)
                source_size = reader.size()
                if source_size.isValid() and (
                    source_size.width() > self._target.width()
                    or source_size.height() > self._target.height()
                ):
                    reader.setScaledSize(
                        source_size.scaled(
                            self._target,
                            Qt.AspectRatioMode.KeepAspectRatio,
                        )
                    )
                decoded = reader.read()
                if decoded.isNull():
                    # 少数格式/Qt 构建忽略 scaledSize 或读不出来；退回常规
                    # 解码，宁可这一张慢一点也不要留空白封面。
                    decoded = QImage.fromData(self._payload)
                if not decoded.isNull() and (
                    decoded.width() > self._target.width()
                    or decoded.height() > self._target.height()
                ):
                    # 兜底缩放用快速变换即可：源图此时已很小，
                    # SmoothTransformation 只是白白烧 CPU。
                    decoded = decoded.scaled(
                        self._target,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                image = decoded
        except Exception:
            image = QImage()
        try:
            self._signals.done.emit(self._url, image)
        except RuntimeError:
            pass  # 插件已销毁，结果直接丢弃


class CoverLoader(QObject):
    image_ready = Signal(str)
    diagnostic = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._has_transfer_timeout = hasattr(self._manager, "setTransferTimeout")
        if self._has_transfer_timeout:
            try:
                self._manager.setTransferTimeout(TRANSFER_TIMEOUT_MS)
            except (AttributeError, TypeError):
                self._has_transfer_timeout = False
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._failed: set[str] = set()
        self._failures: dict[str, int] = {}
        self._pending: list[str] = []
        self._queued: set[str] = set()
        self._active: dict[QNetworkReply, tuple[str, QTimer | None]] = {}
        self._by_url: dict[str, QNetworkReply] = {}
        # v1.0：解码线程池（独立于全局池，最多 2 路，不和宿主抢线程）。
        self._decode_pool = QThreadPool(self)
        self._decode_pool.setMaxThreadCount(2)
        self._decode_signals = _DecodeSignals(self)
        self._decode_signals.done.connect(self._decoded)
        self._decoding: set[str] = set()
        self._paused = True
        self._shutdown = False
        self._timings: dict[str, list[float]] = {}
        self._diagnostics_left = 12
        self._resumed_at = time.monotonic()

    # ------------------------------------------------------------------ API
    def get(self, url: str) -> QPixmap | None:
        normalized = normalize_cover_url(url)
        if not normalized:
            return None
        cached = self._cache.get(normalized)
        if cached is not None:
            self._cache.move_to_end(normalized)
            return cached
        if self._shutdown or self._exhausted(normalized):
            return None
        if (
            normalized not in self._queued
            and normalized not in self._by_url
            and normalized not in self._decoding
        ):
            # Keep only a short queue of the earliest (visible) rows.  A
            # dropped URL is re-requested by the next get() once the queue
            # drains, so scrolling back into view can still pick it up.
            if len(self._pending) >= MAX_PENDING:
                return None
            self._queued.add(normalized)
            self._pending.append(normalized)
            self._timings[normalized] = [time.monotonic()]
            self._pump()
        return None

    def cached(self, url: str) -> QPixmap | None:
        normalized = normalize_cover_url(url)
        if not normalized:
            return None
        pixmap = self._cache.get(normalized)
        if pixmap is not None:
            self._cache.move_to_end(normalized)
        return pixmap

    def resume(self) -> None:
        if self._shutdown:
            return
        self._paused = False
        self._diagnostics_left = 12
        self._resumed_at = time.monotonic()
        # Never re-inject a long historical queue in one go; keep at most the
        # earliest MAX_PENDING entries (the rows most likely to be visible).
        if len(self._pending) > MAX_PENDING:
            dropped = self._pending[MAX_PENDING:]
            del self._pending[MAX_PENDING:]
            self._queued.difference_update(dropped)
        self._pump()

    def pause(self) -> None:
        self._paused = True
        self._pending.clear()
        self._queued.clear()
        self._timings = {url: times for url, times in self._timings.items()
                         if url in self._by_url or url in self._decoding}

    def prioritize(self, urls: list[str]) -> None:
        """Drop stale waiting rows, then queue current viewport before prefetch."""
        wanted = list(dict.fromkeys(filter(None, map(normalize_cover_url, urls))))
        previous = self._timings
        self._pending.clear()
        self._queued.clear()
        self._timings = {url: times for url, times in previous.items()
                         if url in self._by_url or url in self._decoding}
        # Queue as one batch so _pump cannot start an old prefetch first.
        paused = self._paused
        self._paused = True
        for url in wanted[:MAX_PENDING]:
            self.get(url)
            if url in self._queued and url in previous:
                self._timings[url] = previous[url]
        self._paused = paused
        self._pump()

    def clear(self) -> None:
        self._cache.clear()
        self._failed.clear()
        self._failures.clear()
        self._pending.clear()
        self._queued.clear()
        self._abort_all()
        self._timings.clear()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._paused = True
        self._cache.clear()
        self._failed.clear()
        self._failures.clear()
        self._pending.clear()
        self._queued.clear()
        self._abort_all()
        self._timings.clear()
        # 丢弃未开始的解码任务，短暂等待在跑的 1-2 个收尾（每个几十 ms），
        # 避免任务向已销毁的载体投结果。与 page.shutdown 的 worker join 同级。
        try:
            self._decode_pool.clear()
            self._decode_pool.waitForDone(3000)
        except RuntimeError:
            pass
        self._decoding.clear()
        try:
            self._manager.deleteLater()
        except RuntimeError:
            pass

    # ------------------------------------------------------------- internals
    def _exhausted(self, url: str) -> bool:
        """True once a URL has used up its retries and must not be retried."""
        return url in self._failed or self._failures.get(url, 0) >= MAX_FAILURES

    def _pump(self) -> None:
        if self._paused or self._shutdown:
            return
        while self._pending and len(self._active) < MAX_ACTIVE:
            url = self._pending.pop(0)
            self._queued.discard(url)
            if (
                not url
                or url in self._cache
                or self._exhausted(url)
                or url in self._by_url
                or url in self._decoding
            ):
                continue
            request = QNetworkRequest(QUrl(url))
            self._timings.setdefault(url, [time.monotonic()]).append(time.monotonic())
            request.setRawHeader(b"User-Agent", UA.encode("utf-8"))
            request.setRawHeader(
                b"Referer", b"https://www.bilibili.com/"
            )
            reply = self._manager.get(request)
            timer: QTimer | None = None
            if not self._has_transfer_timeout:
                timer = QTimer(reply)
                timer.setSingleShot(True)
                timer.setInterval(TRANSFER_TIMEOUT_MS)
                timer.timeout.connect(reply.abort)
                timer.start()
            self._active[reply] = (url, timer)
            self._by_url[url] = reply
            reply.finished.connect(
                lambda current=reply: self._finished(current)
            )

    def _finished(self, reply: QNetworkReply) -> None:
        entry = self._active.pop(reply, None)
        if entry is None:
            reply.deleteLater()
            return
        url, timer = entry
        self._by_url.pop(url, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

        if self._shutdown:
            reply.deleteLater()
            return

        status = reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )
        failed = reply.error() != QNetworkReply.NetworkError.NoError
        self._timings.setdefault(url, [time.monotonic(), time.monotonic()]).append(time.monotonic())
        try:
            payload = bytes(reply.readAll())
        except RuntimeError:
            payload = b""
        finally:
            reply.deleteLater()

        if not failed and (status is None or 200 <= int(status) < 300):
            # v1.0（bug 4）：解码+缩放挪进线程池；v1.0 起由 QImageReader
            # 在解码时直接缩到目标尺寸。GUI 线程只读字节、投递任务，
            # 封面再多再大也不会卡住画面墙。
            self._decoding.add(url)
            task = _DecodeTask(
                url, payload, _cover_target_size(), self._decode_signals
            )
            try:
                self._decode_pool.start(task)
            except RuntimeError:
                self._decoding.discard(url)
            # 网络槽位已空出，继续下载；解码完成后再发 image_ready。
            self._pump()
            return
        # v1 kept a failed URL in _failed for the whole session, so one network
        # hiccup left that cover as a permanent placeholder.  Count the failure
        # and only give up for good once MAX_FAILURES attempts were used up, so
        # a later get() can still re-enqueue the URL.
        self._failures[url] = self._failures.get(url, 0) + 1
        self._report_timing(url, 'network_failed')
        if self._failures[url] >= MAX_FAILURES:
            self._failed.add(url)
        self._pump()

    def _decoded(self, url: str, image: QImage) -> None:
        """解码结果回到 GUI 线程：只做 QPixmap 上传（非解码）和缓存。"""
        if url not in self._decoding:
            return  # 重复/迟到投递保护
        self._decoding.discard(url)
        if self._shutdown:
            return
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            if not pixmap.isNull():
                self._cache[url] = pixmap
                self._cache.move_to_end(url)
                while len(self._cache) > MAX_CACHE:
                    self._cache.popitem(last=False)
                self.image_ready.emit(url)
                self._report_timing(url, 'ready')
                self._pump()
                return
        self._failures[url] = self._failures.get(url, 0) + 1
        self._report_timing(url, 'decode_failed')
        if self._failures[url] >= MAX_FAILURES:
            self._failed.add(url)
        self._pump()

    def _report_timing(self, url: str, result: str) -> None:
        times = self._timings.pop(url, None)
        if times is None or len(times) < 3 or self._diagnostics_left <= 0:
            return
        self._diagnostics_left -= 1
        queued, started, received = times[:3]
        self.diagnostic.emit(
            f'cover timing result={result} queue_ms={(started-queued)*1000:.0f} '
            f'network_ms={(received-started)*1000:.0f} '
            f'decode_ms={(time.monotonic()-received)*1000:.0f} '
            f'since_open_ms={(time.monotonic()-self._resumed_at)*1000:.0f} '
            f'active={len(self._active)} pending={len(self._pending)}')

    def _abort_all(self) -> None:
        for reply in list(self._active):
            timer = self._active[reply][1]
            if timer is not None:
                timer.stop()
            try:
                reply.abort()
            except RuntimeError:
                pass
            reply.deleteLater()
        self._active.clear()
        self._by_url.clear()
