"""点播进度条画面预览（v1.0 需求：：雪碧图取帧）。

数据来源（已实测、无需 Cookie）::

    GET https://api.bilibili.com/x/player/videoshot?bvid=..&cid=..&index=1

``data`` 字段：

* ``image``       —— 雪碧图 URL 数组，**协议相对**（``//bimp.hdslb.com/..``，
  必须补 ``https:``）；
* ``index``       —— 帧时间戳升序数组（跨全部雪碧图连续编号）；
* ``img_x_len`` / ``img_y_len``   —— 每张雪碧图的列数 / 行数（实测 10/10）；
* ``img_x_size`` / ``img_y_size`` —— **单帧尺寸动态**（实测 480x270，常见
  160x90），必须用返回值，不得写死。

红线：

* 网络与 QImage 解码全部在专属 ``QThreadPool(1)`` 工作线程；GUI 线程只做
  ``QPixmap.fromImage`` 与绘制；
* 412/429/``code != 0``/任何异常 —— 静默禁用预览且**不重试**；
* ``release()`` 递增 generation，迟到的工作结果一律丢弃；
* 弹窗 ``WA_TransparentForMouseEvents`` + ``WA_ShowWithoutActivating``，
  永不抢焦点、永不挡鼠标。
"""
from __future__ import annotations

import bisect
import math
import weakref
from collections import OrderedDict
from typing import Any, Optional
from urllib.parse import urlsplit

import requests
from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .vod_headers import DEFAULT_HEADERS
from .vod_progress import format_clock

SHOT_API = "https://api.bilibili.com/x/player/videoshot"

#: 预览帧统一缩到该宽度（保持纵横比）。
PREVIEW_WIDTH = 240
#: 雪碧图在工作线程解码后允许的最大宽度（超出等比缩到该值）。
MAX_SPRITE_WIDTH = 2400
#: 最多缓存的雪碧图张数（LRU）。刻意限 2 张控制解码内存（每张可达数 MB）；
#: 跨多张雪碧图来回拖会重新下载，属内存/网络权衡下的有意取舍。
MAX_SPRITES_CACHED = 2

_POPUP_PAD = 8
_POPUP_GAP = 4
_POPUP_TEXT_H = 18
_POPUP_OFFSET = 14  # 弹窗底边与指针的间距


# --------------------------------------------------------------------------
# 网络层（模块级函数 = 测试 monkeypatch 目标；工作线程内调用）
# --------------------------------------------------------------------------
def _fetch_videoshot(bvid: str, cid: str) -> Optional[dict]:
    """取雪碧图元数据 JSON。风控/异常/``code != 0`` 一律返回 None（不重试）。"""
    try:
        response = requests.get(
            SHOT_API,
            params={"bvid": bvid, "cid": cid, "index": 1},
            headers=dict(DEFAULT_HEADERS),
            timeout=8,
        )
    except Exception:
        return None
    if response.status_code != 200:  # 含 412/429 风控
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _fetch_sprite_bytes(url: str) -> Optional[bytes]:
    """下载一张雪碧图的原始字节。任何失败返回 None（不重试）。"""
    try:
        response = requests.get(url, headers=dict(DEFAULT_HEADERS), timeout=8)
    except Exception:
        return None
    if response.status_code != 200:
        return None
    return response.content or None


def _normalize_sprite_url(url: Any) -> Optional[str]:
    """协议相对 URL 补 https:；非法项返回 None。"""
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    try:
        parsed = urlsplit(text)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
    except ValueError:
        return None
    return text


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and value != number:
        return None
    # QRect/QImage 使用有符号 int 尺寸，避免畸形 meta 在 GUI 裁图时溢出。
    return number if 1 <= number <= 2 ** 31 - 1 else None


def _preview_seconds(value: Any) -> float:
    try:
        moment = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return max(0.0, moment) if math.isfinite(moment) else 0.0


def _parse_meta(data: Any) -> Optional[dict]:
    """校验并规范化 videoshot 的 data 段；任何缺项/非法项返回 None。"""
    if not isinstance(data, dict):
        return None
    images, timestamps = data.get("image"), data.get("index")
    if (not isinstance(images, (list, tuple)) or not images
            or not isinstance(timestamps, (list, tuple)) or not timestamps):
        return None
    urls = []
    for item in images:
        normalized = _normalize_sprite_url(item)
        if normalized is None:
            return None
        urls.append(normalized)
    index = []
    for item in timestamps:
        try:
            stamp = float(item)
        except (TypeError, ValueError, OverflowError):
            return None
        if (not math.isfinite(stamp) or stamp < 0
                or (index and stamp < index[-1])):
            return None
        index.append(stamp)
    x_len = _positive_int(data.get("img_x_len"))
    y_len = _positive_int(data.get("img_y_len"))
    x_size = _positive_int(data.get("img_x_size"))
    y_size = _positive_int(data.get("img_y_size"))
    if not (x_len and y_len and x_size and y_size):
        return None
    if (len(index) > len(urls) * x_len * y_len
            or x_len * x_size > 2 ** 31 - 1
            or y_len * y_size > 2 ** 31 - 1):
        return None
    return {
        "urls": urls,
        "index": index,
        "x_len": x_len,
        "y_len": y_len,
        "x_size": x_size,
        "y_size": y_size,
    }


def sprite_cell(frame_index: int, x_len: int, y_len: int) -> tuple[int, int, int]:
    """全局帧号 -> (雪碧图序号, 行, 列)。纯函数，供离线测试直接锁定。"""
    fps = x_len * y_len
    sprite = frame_index // fps
    row = (frame_index % fps) // x_len
    col = frame_index % x_len
    return sprite, row, col


# --------------------------------------------------------------------------
# 工作项
# --------------------------------------------------------------------------
class _PreviewTask(QRunnable):
    """专属线程池里的取数任务：JSON + 雪碧图下载 + QImage 解码/缩放。"""

    def __init__(self, provider_ref, generation: int, kind: str,
                 bvid: str = "", cid: str = "", url: str = "") -> None:
        super().__init__()
        self._ref = provider_ref
        self._generation = generation
        self._kind = kind
        self._bvid = bvid
        self._cid = cid
        self._url = url
        self.setAutoDelete(True)

    def _is_current(self) -> bool:
        provider = self._ref()
        return (provider is not None and provider._generation == self._generation
                and provider._state not in ("disabled", "released"))

    def _emit(self, signal_name: str, *args) -> None:
        provider = self._ref()
        if provider is None or not self._is_current():
            return
        try:
            getattr(provider, signal_name).emit(self._generation, *args)
        except RuntimeError:  # provider 的 C++ 对象已销毁
            pass

    def run(self) -> None:  # noqa: C901 - 顺序流程
        if not self._is_current():
            return
        if self._kind == "meta":
            try:
                meta = _parse_meta(_fetch_videoshot(self._bvid, self._cid))
            except Exception:
                meta = None
            self._emit("_meta_ready", meta)
            if meta is not None:
                self._fetch_sprite(meta["urls"][0])
        elif self._kind == "sprite":
            self._fetch_sprite(self._url)

    def _fetch_sprite(self, url: str) -> None:
        # release 不阻塞等待正在进行的 HTTP，但不再开始下一次下载/解码。
        if not self._is_current():
            return
        payload = None
        try:
            raw = _fetch_sprite_bytes(url)
            if not self._is_current():
                return
            if raw:
                image = QImage.fromData(raw)
                if not image.isNull():
                    scale = 1.0
                    if image.width() > MAX_SPRITE_WIDTH:
                        scale = MAX_SPRITE_WIDTH / float(image.width())
                        image = image.scaledToWidth(
                            MAX_SPRITE_WIDTH, Qt.SmoothTransformation
                        )
                    if not image.isNull():
                        payload = {"image": image, "scale": scale}
        except Exception:
            payload = None
        self._emit("_sprite_decoded", url, payload)


# --------------------------------------------------------------------------
# 预览弹窗
# --------------------------------------------------------------------------
class PreviewPopup(QWidget):
    """指针上方的画面预览浮窗：上画帧、下画时间。

    ``paintEvent`` 手绘深色圆角底，不用样式表（杜绝自触发回调）。
    """

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._pixmap: Optional[QPixmap] = None
        self._text = ""
        self._frame_height = int(PREVIEW_WIDTH * 9 / 16)
        self.hide()

    def show_at(self, global_pos: QPoint, pixmap: Optional[QPixmap],
                seconds: float) -> None:
        self._pixmap = pixmap if (pixmap is not None and not pixmap.isNull()) else None
        self._text = format_clock(_preview_seconds(seconds))
        frame_width = self._pixmap.width() if self._pixmap else PREVIEW_WIDTH
        self._frame_height = (
            self._pixmap.height() if self._pixmap
            else int(PREVIEW_WIDTH * 9 / 16)
        )
        self.resize(
            frame_width + _POPUP_PAD * 2,
            _POPUP_PAD + self._frame_height + _POPUP_GAP + _POPUP_TEXT_H + _POPUP_PAD,
        )
        # v1.0 需求：：跨屏后预览窗与指针脱离，根因是窗口还挂在旧屏幕上，
        # 它的 devicePixelRatio 与鼠标所在屏不一致，move() 用的是旧屏的逻辑
        # 坐标 → 按比例偏移。先把它挂到指针所在屏，再按那块屏的可用区夹取。
        screen = self._screen_at(global_pos)
        self._bind_screen(screen)
        x = int(global_pos.x()) - self.width() // 2
        y = int(global_pos.y()) - self.height() - _POPUP_OFFSET
        available = self._available_geometry(screen)
        if available is not None:
            if self.width() <= available.width():
                x = max(available.left(),
                        min(x, available.right() - self.width() + 1))
            else:
                x = available.left()
            if y < available.top():
                # 上方放不下则翻到指针下方，最终仍夹取在屏幕内
                y = int(global_pos.y()) + _POPUP_OFFSET
            if self.height() <= available.height():
                y = max(available.top(),
                        min(y, available.bottom() - self.height() + 1))
            else:
                y = available.top()
        self.move(x, y)
        self.show()
        self.raise_()
        # v1.0 需求：：半透明顶层窗 move()/show() 不会重绘内容，换帧后必须
        # 显式 update()，否则鼠标移动时预览图停留在旧帧（不跟随刷新）。
        self.update()

    def hide_popup(self) -> None:
        self._pixmap = None
        self.hide()

    @staticmethod
    def _screen_at(global_pos: QPoint):
        """指针所在的屏幕（跨屏预览的落点判断都基于它）。"""
        try:
            screen = QApplication.screenAt(global_pos)
        except Exception:
            screen = None
        if screen is None:
            try:
                screen = QApplication.primaryScreen()
            except Exception:
                screen = None
        return screen

    def _bind_screen(self, screen) -> None:
        """把弹出窗挂到目标屏：混屏 DPI 下不切屏就会按旧 DPR 换算坐标。"""
        if screen is None:
            return
        try:
            handle = self.windowHandle()
            if handle is not None and handle.screen() is not screen:
                handle.setScreen(screen)
        except Exception:
            pass

    def _available_geometry(self, screen=None) -> Optional[QRect]:
        try:
            if screen is None:
                screen = self.screen()
            if screen is None:
                screen = QApplication.primaryScreen()
            return screen.availableGeometry() if screen is not None else None
        except Exception:
            return None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(
            QRect(0, 0, self.width(), self.height()).adjusted(0, 0, -1, -1), 8, 8
        )
        painter.fillPath(path, QColor(16, 17, 20, 235))
        if self._pixmap is not None:
            painter.drawPixmap(_POPUP_PAD, _POPUP_PAD, self._pixmap)
        painter.setPen(QColor(222, 226, 232, 235))
        painter.drawText(
            QRect(_POPUP_PAD, _POPUP_PAD + self._frame_height + _POPUP_GAP,
                  self.width() - _POPUP_PAD * 2, _POPUP_TEXT_H),
            int(Qt.AlignCenter),
            self._text,
        )
        painter.end()


# --------------------------------------------------------------------------
# 预览提供者
# --------------------------------------------------------------------------
class VodPreviewProvider(QObject):
    """某一支视频（bvid+cid）的进度条画面预览源。

    懒加载：首次 ``frame_at()`` 才发起抓取；专属 ``QThreadPool(1)``；
    412/429/``code != 0`` 静默禁用且不重试；LRU 最多 ``MAX_SPRITES_CACHED`` 张雪碧图；
    ``release()`` 后迟到结果按 generation 丢弃。
    """

    #: 任一雪碧图（重新）就绪时发出；弹窗若正显示应刷新。
    preview_ready = Signal()

    #: 工作线程 -> GUI 线程（队列投递）。
    _meta_ready = Signal(int, object)
    _sprite_decoded = Signal(int, str, object)

    def __init__(self, bvid: Any, cid: Any, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._bvid = str(bvid or "").strip()
        self._cid = str(cid or "").strip()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._generation = 0
        self._state = "idle"  # idle / loading / ready / disabled / released
        self._meta: Optional[dict] = None
        self._index: list[float] = []
        self._urls: list[str] = []
        self._sprites: "OrderedDict[str, tuple[QPixmap, float]]" = OrderedDict()
        self._pending: set[str] = set()
        self._failed_urls: set[str] = set()
        self._popup: Optional[PreviewPopup] = None
        self._hover: Optional[tuple[QPoint, float]] = None
        self._meta_ready.connect(self._on_meta)
        self._sprite_decoded.connect(self._on_sprite)
        self.preview_ready.connect(self._refresh_hover)

    # ------------------------------------------------------------------ state
    def state(self) -> str:
        return self._state

    # ------------------------------------------------------------------- query
    def frame_at(self, seconds: Any) -> tuple[Optional[QPixmap], float]:
        """返回 ``(帧图或 None, 实际时间戳)``。未就绪时触发后台加载。"""
        moment = _preview_seconds(seconds)
        if self._state == "idle":
            self._start()
        if self._state != "ready" or not self._meta or not self._index:
            return (None, moment)
        position = bisect.bisect_right(self._index, moment) - 1
        position = max(0, min(position, len(self._index) - 1))
        stamp = float(self._index[position])
        meta = self._meta
        sprite, row, col = sprite_cell(position, meta["x_len"], meta["y_len"])
        if sprite >= len(self._urls):
            return (None, stamp)
        url = self._urls[sprite]
        entry = self._sprites.get(url)
        if entry is None:
            self._load_sprite(url)
            return (None, stamp)
        self._sprites.move_to_end(url)
        pixmap, scale = entry
        frame_width = max(1, int(round(meta["x_size"] * scale)))
        frame_height = max(1, int(round(meta["y_size"] * scale)))
        rect = QRect(col * frame_width, row * frame_height,
                     frame_width, frame_height)
        if not pixmap.rect().contains(rect):
            return (None, stamp)  # meta 与实际图片不符，不分配越界的帧图。
        frame = pixmap.copy(rect)
        if frame.isNull():
            return (None, stamp)
        if frame.width() != PREVIEW_WIDTH:
            frame = frame.scaledToWidth(PREVIEW_WIDTH, Qt.SmoothTransformation)
        return (frame, stamp)

    # ------------------------------------------------------------------ popup
    def request_preview(self, global_pos: QPoint, seconds: Any) -> None:
        """在指针上方显示预览；风控禁用/已释放时静默跳过。"""
        if self._state in ("disabled", "released"):
            return
        moment = _preview_seconds(seconds)
        self._hover = (QPoint(global_pos), moment)
        frame, stamp = self.frame_at(moment)
        if self._state in ("disabled", "released"):
            self.cancel_preview()
            return
        if self._popup is None:
            self._popup = PreviewPopup()
        self._popup.show_at(self._hover[0], frame, stamp)

    def cancel_preview(self) -> None:
        self._hover = None
        if self._popup is not None:
            self._popup.hide_popup()

    def _refresh_hover(self) -> None:
        if (self._hover is not None and self._popup is not None
                and self._popup.isVisible()):
            position, moment = self._hover
            frame, stamp = self.frame_at(moment)
            self._popup.show_at(position, frame, stamp)

    # ---------------------------------------------------------------- loading
    def _start(self) -> None:
        if not self._bvid or not self._cid:
            self._state = "disabled"
            return
        self._state = "loading"
        self._pool.start(_PreviewTask(weakref.ref(self), self._generation,
                                      "meta", bvid=self._bvid, cid=self._cid))

    def _load_sprite(self, url: str) -> None:
        if (url in self._sprites or url in self._pending or url in self._failed_urls
                or self._state in ("disabled", "released")):
            return
        self._pending.add(url)
        self._pool.start(_PreviewTask(weakref.ref(self), self._generation,
                                      "sprite", url=url))

    # ------------------------------------------------------------ worker slots
    def _on_meta(self, generation: int, meta: Optional[dict]) -> None:
        if generation != self._generation or self._state in ("disabled", "released"):
            return
        if meta is None:
            self._state = "disabled"
            self.cancel_preview()
            return
        self._meta = meta
        self._index = meta["index"]
        self._urls = meta["urls"]

    def _on_sprite(self, generation: int, url: str, payload: Optional[dict]) -> None:
        if generation != self._generation or self._state in ("disabled", "released"):
            return
        self._pending.discard(url)
        # GUI 线程只转换 pixmap；失败 URL 独立记忆，不随 LRU 淘汰而重试。
        pixmap = QPixmap.fromImage(payload["image"]) if payload is not None else QPixmap()
        if pixmap.isNull():
            self._failed_urls.add(url)
            if self._state in ("idle", "loading") and not self._sprites:
                self._state = "disabled"  # 首图都拿不到：静默禁用
                self.cancel_preview()
            return
        self._sprites[url] = (pixmap, float(payload["scale"]))
        self._sprites.move_to_end(url)
        while len(self._sprites) > MAX_SPRITES_CACHED:
            self._sprites.popitem(last=False)
        if self._state == "loading":
            self._state = "ready"
        if self._state == "ready":
            self.preview_ready.emit()

    # ---------------------------------------------------------------- teardown
    def release(self) -> None:
        """停工作项、清缓存、藏弹窗；迟到结果按 generation 丢弃。"""
        self._generation += 1
        self._state = "released"
        self._pool.clear()
        self.cancel_preview()
        self._sprites.clear()
        self._pending.clear()
        self._failed_urls.clear()
        self._meta = None
        self._index = []
        self._urls = []
        if self._popup is not None:
            self._popup.hide()
            self._popup.deleteLater()
            self._popup = None
