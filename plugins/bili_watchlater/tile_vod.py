"""格子内点播轻量控件（v1.0 架构：宿主播放器直注）。

v1.0 起不再自建播放层：解析出的点播直链交给宿主自己的
``window._play_on(tile, url, profile="web", headers=...)``，播放器进
``window.players[tile]``，占位符/暂停帧/底栏按钮/快捷键/拖拽秒切全部回归
宿主原生逻辑（宿主源码证据：app.py ``_play_on``、2208
``_on_player_state``、widgets.py ``set_video_active``、6301
``set_paused``、room_placement.py ``hot_swap``）。

本模块只剩两个**不抢键盘、不挡宿主快捷键**的格子内控件：

* ``VodProgressStrip`` —— 进度条，贴在视频区底部、宿主底栏操作按钮之上
  （用户需求 4）。宿主弹幕层 ``WA_TransparentForMouseEvents``
  （video_danmaku.py），所以进度条在它下面也点得到。
* ``VodStatusCard`` —— 仅错误/结束两种状态的状态卡（重试/重播/浏览器/
  还原）。播放与暂停期间完全隐藏，画面交还宿主。

红线不变：不 patch 宿主类、不改 ``tile.bottom`` 原有控件、不把网页 URL
喂给播放器、不在 GUI 线程做网络/解码/mpv 阻塞调用。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import (QEvent, QPoint, QPointF, QRect, QRectF,
                            QSize, Qt, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter,
                          QPainterPath, QRegion)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

STRIP_HEIGHT = 26
BAR_HEIGHT = 28   # 底栏进度条固定高（宿主 TileControls 约束 <= 30）
LINE_HEIGHT = 3   # 视频区底边蓝色进度线
#: v1.0 需求：：进度条时间标签按最长时钟串预留宽度，滑杆不再被文本推移。
_CLOCK_SAMPLE = "00:00:00"
#: v1.0 位置徽章：宿主 stream_badge 高 24、原点 (10, 8)（widgets.py）。
POSITION_BADGE_HEIGHT = 24
POSITION_BADGE_MIN_WIDTH = 46
POSITION_BADGE_LEFT = 10
POSITION_BADGE_TOP = 8
POSITION_BADGE_TEXT_LEFT = 20   # 左侧圆点区
POSITION_BADGE_TEXT_RIGHT = 12  # 右侧内边距

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Referer": "https://www.bilibili.com/",
}

#: 网页 URL 标记：直链绝不能是这些（宿主 ytdl=False，喂网页必错）。
_WEBPAGE_MARKERS = (
    "bilibili.com/video/",
    "bilibili.com/bangumi/",
    "bilibili.com/list/",
    "bilibili.com/watchlater",
)

_ACCENT = "#00a1d6"  # B 站蓝，与宿主悬停强调色一致
_PILL_BG = "rgba(12, 13, 16, 200)"
_PILL_TEXT = "rgba(214, 218, 224, 235)"

_PILL_QSS = """
QPushButton {
    background: %s;
    color: %s;
    border: none;
    border-radius: 12px;
    padding: 4px 14px;
    font-weight: 600;
}
QPushButton:hover { background: %s; color: #04161f; }
QPushButton:pressed { background: %s; color: #04161f; }
QPushButton:focus { outline: none; }
""" % (_PILL_BG, _PILL_TEXT, _ACCENT, _ACCENT)


def is_direct_url(url: Any) -> bool:
    """True 仅当 http(s) 直链且不是 B 站网页。注入宿主前必须过这道闸。"""
    if not isinstance(url, str):
        return False
    text = url.strip()
    if not text or not text.lower().startswith(("http://", "https://")):
        return False
    lowered = text.lower()
    return not any(marker in lowered for marker in _WEBPAGE_MARKERS)


def merge_headers(headers: Any) -> dict[str, str]:
    """始终带 UA + Referer 的非空头字典（大小写不敏感去重）。

    宿主 ``_play_on`` 对空 headers 会回退到直播 Referer
    （``live.bilibili.com``），点播 CDN 会 403。调用方若自带任意大小写
    的 referer/user-agent，尊重它，不再叠加默认值造成重复头。
    """
    merged: dict[str, str] = {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            if isinstance(key, str) and isinstance(value, str) and key.strip():
                merged[key] = value
    lowered = {key.lower() for key in merged}
    if "referer" not in lowered:
        merged["Referer"] = DEFAULT_HEADERS["Referer"]
    if "user-agent" not in lowered:
        merged["User-Agent"] = DEFAULT_HEADERS["User-Agent"]
    return merged


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%02d:%02d" % (minutes, secs)


def video_geometry(tile) -> QRect:
    """格子视频区（宿主 Tile.video 是 VideoSurface，geometry 即画面区）。"""
    video = getattr(tile, "video", None)
    if video is not None:
        try:
            return video.geometry()
        except Exception:
            pass
    try:
        return tile.rect()
    except Exception:
        return QRect(0, 0, 0, 0)


def _push_layered_alpha(widget: QWidget, image: QImage) -> bool:
    """把 ARGB 图像按「逐像素 alpha」贴到原生子窗口上（仅 Windows）。

    Qt 的 ``WA_TranslucentBackground`` 只对顶层窗口有效；原生子窗口用
    ``setMask`` 抠圆角时，遮罩外露出的是父控件底色 —— 用户报的
    「左上角挖孔露底」。宿主 ``ddm/badge_surface.py`` 用的是同一套
    ``UpdateLayeredWindow``，这里复刻最小实现；任何一步失败就返回 False，
    由调用方回退到遮罩路径（不会比旧行为更差）。
    """
    if sys.platform != "win32":
        return False
    try:
        if QApplication.platformName() != "windows":
            return False
    except Exception:
        return False
    try:
        hwnd = int(widget.winId())
    except Exception:
        return False
    if not hwnd or image.isNull():
        return False
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        if not getattr(_push_layered_alpha, "_ready", False):
            class _BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD),
                            ("biWidth", wintypes.LONG),
                            ("biHeight", wintypes.LONG),
                            ("biPlanes", wintypes.WORD),
                            ("biBitCount", wintypes.WORD),
                            ("biCompression", wintypes.DWORD),
                            ("biSizeImage", wintypes.DWORD),
                            ("biXPelsPerMeter", wintypes.LONG),
                            ("biYPelsPerMeter", wintypes.LONG),
                            ("biClrUsed", wintypes.DWORD),
                            ("biClrImportant", wintypes.DWORD)]

            class _BLEND(ctypes.Structure):
                _fields_ = [("BlendOp", ctypes.c_ubyte),
                            ("BlendFlags", ctypes.c_ubyte),
                            ("SourceConstantAlpha", ctypes.c_ubyte),
                            ("AlphaFormat", ctypes.c_ubyte)]

            _push_layered_alpha._bmi = _BITMAPINFOHEADER
            _push_layered_alpha._blend = _BLEND
            user32.GetDC.restype = wintypes.HDC
            user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int,
                                              ctypes.c_long)
            gdi32.CreateCompatibleDC.restype = wintypes.HDC
            gdi32.CreateDIBSection.restype = wintypes.HANDLE
            gdi32.SelectObject.restype = wintypes.HANDLE
            _push_layered_alpha._ready = True
        bmi = _push_layered_alpha._bmi
        blend_t = _push_layered_alpha._blend
        # 宽度按物理像素提交，alpha 通道才不会因 DPR 被拉伸。
        width = int(image.width())
        height = int(image.height())
        screen = user32.GetDC(None)
        memory = gdi32.CreateCompatibleDC(screen)
        info = bmi(40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        data = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            screen, ctypes.byref(info), 0, ctypes.byref(data), None, 0)
        try:
            if not memory or not bitmap:
                return False
            previous = gdi32.SelectObject(memory, bitmap)
            try:
                ctypes.memmove(data, bytes(image.constBits()), image.sizeInBytes())
                style = user32.GetWindowLongW(hwnd, -20)
                if not style & 0x00080000:
                    user32.SetWindowLongW(hwnd, -20, style | 0x00080000)
                size = wintypes.SIZE(width, height)
                origin = wintypes.POINT(0, 0)
                blend = blend_t(0, 0, 255, 1)
                ok = user32.UpdateLayeredWindow(
                    hwnd, screen, None, ctypes.byref(size), memory,
                    ctypes.byref(origin), 0, ctypes.byref(blend), 2)
                return bool(ok)
            finally:
                gdi32.SelectObject(memory, previous)
        finally:
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory:
                gdi32.DeleteDC(memory)
            user32.ReleaseDC(None, screen)
    except Exception:
        return False


class VodPositionBadge(QWidget):
    """格子左上角的「序号/总数」位置徽章（v1.0 任务 3 / 任务 8）。

    宿主在点播期间把左上角「未开播」药丸藏掉（``room["live"]=False`` 由
    ``playback_badges.py`` 驱动，插件按实例拦截 Show 后 ``hide()``），于是
    那里留下一个空洞。本控件把那个位置填回来：文案是当前视频在稍后再看
    列表里的 ``N/总数``，样式对齐宿主药丸（深底胶囊 + 白字 + 左侧圆点）。

    放在同一个原点（宿主 ``widgets.py`` 的 ``stream_badge.move(10, 8)``），
    所以全屏与非全屏都自然跟随；宽度比宿主「未开播」药丸窄，不会压到紧跟在
    后面的 ``title_badge``。

    不抢焦点（NoFocus）、不吃鼠标（``WA_TransparentForMouseEvents``），
    单击暂停/双击全屏与宿主拖拽都不受影响。
    """

    def __init__(self, tile: QWidget) -> None:
        super().__init__(tile)
        self.setObjectName("watchlaterPosition")
        # 视频是原生 HWND，非原生控件会被画面盖住；遮罩保证圆角外透明。
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("当前视频在列表里的位置")
        self._text = ""
        self._alpha_uploaded = False
        self._render_key = None
        self.setFixedHeight(POSITION_BADGE_HEIGHT)
        self.setFixedWidth(self._width_for(""))
        self.hide()

    # ------------------------------------------------------------------ text
    @staticmethod
    def _font() -> QFont:
        font = QFont()
        font.setPixelSize(13)
        font.setBold(True)
        return font

    def _width_for(self, text: str) -> int:
        """v1.0 需求：：宽度必须覆盖「左圆点区 20 + 文字 + 右内边距 12」。

        v1.0 只加了 22 而绘制区左右共扣了 28，最后 1~2 个字符被裁掉，
        「5/642」显示成「5/64」——这里让预算与绘制矩形严格一致。
        """
        metrics = QFontMetrics(self._font())
        # 空文案也留一个「000/000」的宽度，避免首次显示时窗口尺寸跳一下。
        sample = text or "000/000"
        return max(POSITION_BADGE_MIN_WIDTH,
                   POSITION_BADGE_TEXT_LEFT + metrics.horizontalAdvance(sample)
                   + POSITION_BADGE_TEXT_RIGHT)

    def set_text(self, text: str) -> None:
        text = str(text or "")
        if text == self._text:
            return
        self._text = text
        self.setFixedWidth(self._width_for(text))
        self._rebuild_mask()
        self.update()

    def text(self) -> str:
        return self._text

    # ----------------------------------------------------------------- paint
    def _pill_path(self) -> QPainterPath:
        height = max(2, self.height())
        radius = (height - 1) / 2
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0.5, 0.5, max(1, self.width() - 1), height - 1),
            radius, radius,
        )
        return path

    def _paint(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(21, 23, 28, 235))
        painter.drawPath(self._pill_path())
        if not self._text:
            return
        # 左侧圆点呼应宿主 LIVE 药丸的圆环，位置/尺寸刻意接近。
        painter.setBrush(QColor(_ACCENT))
        painter.drawEllipse(QPointF(12.0, self.height() / 2.0), 3.0, 3.0)
        painter.setFont(self._font())
        painter.setPen(QColor(232, 236, 242, 240))
        painter.drawText(
            self.rect().adjusted(
                POSITION_BADGE_TEXT_LEFT, 0, -POSITION_BADGE_TEXT_RIGHT, 0),
            int(Qt.AlignVCenter | Qt.AlignLeft),
            self._text,
        )

    def _rebuild_mask(self) -> None:
        """不再使用遮罩。

        v1.0 需求：：原生子窗口 `setMask` 抠圆角后，遮罩外露出的是**父控件
        底色**（也就是格子的其它界面），用户看到的就是「左上角挖了个孔、露出
        底部界面」。现在要么走逐像素 alpha（真透明），要么回退成整块铺满的
        深色小条 —— 宁可是直角，也绝不露底。
        """
        return

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._render_key = None
        self._rebuild_mask()

    def event(self, event) -> bool:  # type: ignore[override]
        # 跨屏/DPI 变化后必须重传，否则分层窗口还是旧尺寸的位图。
        if event.type() in (QEvent.DevicePixelRatioChange,
                            QEvent.ScreenChangeInternal):
            self._render_key = None
            self.update()
        return super().event(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        key = (self._text, self.width(), self.height(),
               round(self.devicePixelRatioF(), 2))
        if key != self._render_key:
            # 先渲染到 ARGB 图像，再按逐像素 alpha 贴到原生窗口：
            # 这样胶囊外是真正的透明，不会露出父控件底色（「挖孔露底」）。
            if self._paint_layered(key):
                return
            self._render_key = key
        painter = QPainter(self)
        # 先按控件矩形铺满同色底，再画胶囊：没有逐像素 alpha 时也不会露底。
        painter.fillRect(self.rect(), QColor(21, 23, 28, 235))
        self._paint(painter)
        painter.end()

    # ------------------------------------------------------- layered alpha
    def _paint_layered(self, key) -> bool:
        image = QImage(self.size() * self.devicePixelRatioF(),
                       QImage.Format_ARGB32_Premultiplied)
        if image.isNull():
            return False
        image.setDevicePixelRatio(self.devicePixelRatioF())
        image.fill(Qt.transparent)
        painter = QPainter(image)
        try:
            self._paint(painter)
        finally:
            painter.end()
        if not _push_layered_alpha(self, image):
            return False
        if self.mask().isEmpty() is False:
            self.clearMask()
        self._alpha_uploaded = True
        self._render_key = key
        return True

    # -------------------------------------------------------------- geometry
    def sync_geometry(self, tile) -> None:
        """贴住宿主 stream_badge 的原点，并保持在画面浮层最上层。"""
        try:
            self.move(POSITION_BADGE_LEFT, POSITION_BADGE_TOP)
        except RuntimeError:
            return
        try:
            self.raise_()
        except RuntimeError:
            pass


class VodProgressStrip(QWidget):
    """视频区底部的 seek 条：当前时间 + 滑杆 + 总时长。

    原生子窗口（视频是 HWND，非原生控件会被盖住）。样式固定深色，不随
    调色板事件写样式表 —— v1.0 的 changeEvent 递归坑从结构上消失。
    """

    seek_requested = Signal(float)  # 绝对秒数

    def __init__(self, tile: QWidget) -> None:
        super().__init__(tile)
        self.setObjectName("watchlaterProgress")
        # 先初始化再装 eventFilter：installEventFilter 之后滑杆事件随时可能
        # 到达（polish/hover），eventFilter 会读 _preview_provider。
        self._preview_provider = None
        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(STRIP_HEIGHT)
        self.setStyleSheet("""
            QWidget#watchlaterProgress {
                background: rgba(8, 9, 11, 170);
                border-top: 1px solid rgba(255, 255, 255, 28);
            }
            QLabel { color: rgba(214, 218, 224, 220); background: transparent; }
            QSlider { background: transparent; }
            QSlider::groove:horizontal {
                height: 4px; background: rgba(214, 218, 224, 70);
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #00a1d6; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 10px; margin: -4px 0; border-radius: 5px;
                border: none; background: #00a1d6;
            }
            QSlider::handle:horizontal:hover { background: #ffffff; }
        """)
        self.setAccessibleName("播放进度")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(8)

        self.setMouseTracking(True)

        self.current_label = QLabel("00:00", self)
        row.addWidget(self.current_label, 0)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setObjectName("watchlaterSeek")
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setTracking(False)
        self.slider.setPageStep(50)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.setMouseTracking(True)
        self.slider.installEventFilter(self)
        self.slider.setToolTip("播放进度（拖动跳转）")
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.sliderPressed.connect(self._pressed)
        self.slider.sliderMoved.connect(self._moved)
        self.slider.sliderReleased.connect(self._released)
        row.addWidget(self.slider, 1)

        self.duration_label = QLabel("00:00", self)
        row.addWidget(self.duration_label, 0)

        # v1.0 需求：：与底栏进度条一致，时间标签固定宽度，滑杆不被文本推移。
        self._lock_clock_width()

        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self._preview_provider = None
        self.hide()

    def _lock_clock_width(self) -> None:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(_CLOCK_SAMPLE) + 2
        for label in (self.current_label, self.duration_label):
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignCenter)

    # ------------------------------------------------------------------ state
    def set_duration(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        self._duration = max(0.0, value)
        self.duration_label.setText(format_clock(self._duration))

    def set_position(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        self._position = max(0.0, value)
        if not self._seeking:
            self.current_label.setText(format_clock(self._position))
            if self._duration > 0:
                ratio = max(0.0, min(1.0, self._position / self._duration))
                self.slider.setValue(int(ratio * 1000))

    def reset(self) -> None:
        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self.slider.setValue(0)
        self.current_label.setText("00:00")
        self.duration_label.setText("00:00")

    # ------------------------------------------------------------------ seek
    def _pressed(self) -> None:
        self._seeking = True
        self._cancel_preview()

    def _moved(self, value: int) -> None:
        if self._duration > 0:
            self.current_label.setText(
                format_clock(value / 1000.0 * self._duration)
            )

    def _released(self) -> None:
        # tracking=False 时 sliderReleased 早于 value 提交，读取最终拖动位置。
        value = self.slider.sliderPosition()
        self._seeking = False
        if self._duration > 0:
            target = value / 1000.0 * self._duration
            self._position = target
            self.current_label.setText(format_clock(target))
            self.seek_requested.emit(target)

    # --------------------------------------------------------------- preview
    def set_preview_provider(self, provider: Any) -> None:
        """挂/摘画面预览源（vod_preview.VodPreviewProvider，鸭子类型）。

        ``None`` 时行为与 v1.0 完全一致：不建弹窗、不发任何请求。
        """
        if provider is self._preview_provider:
            return
        self._cancel_preview()
        self._preview_provider = provider

    def _cancel_preview(self) -> None:
        provider = self._preview_provider
        if provider is not None:
            try:
                provider.cancel_preview()
            except Exception:
                pass

    def _preview_hover(self, strip_x: float) -> None:
        provider = self._preview_provider
        if provider is None or self._duration <= 0 or not self.isVisible():
            return
        geo = self.slider.geometry()
        if geo.width() <= 0:
            return
        ratio = (float(strip_x) - geo.x()) / geo.width()
        ratio = max(0.0, min(1.0, ratio))
        seconds = ratio * self._duration
        anchor = QPoint(geo.x() + int(ratio * geo.width()), 0)
        try:
            provider.request_preview(self.mapToGlobal(anchor), seconds)
        except Exception:
            pass

    def mouseMoveEvent(self, event) -> None:
        if (self._preview_provider is not None
                and not (event.buttons() & Qt.LeftButton)):
            self._preview_hover(event.position().x())
        super().mouseMoveEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.slider:
            kind = event.type()
            if kind == QEvent.Type.MouseMove:
                if (self._preview_provider is not None
                        and not (event.buttons() & Qt.LeftButton)):
                    self._preview_hover(self.slider.x() + event.position().x())
                    return True  # 仅消费预览悬停；拖动必须交还 QSlider。
            if kind == QEvent.Type.Leave:
                self._cancel_preview()
        return super().eventFilter(watched, event)

    def leaveEvent(self, event) -> None:
        self._cancel_preview()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._cancel_preview()
        super().hideEvent(event)

    # -------------------------------------------------------------- geometry
    def sync_geometry(self, tile) -> None:
        rect = video_geometry(tile)
        if not rect.isValid() or rect.width() <= 0:
            return
        target = QRect(rect.x(), rect.bottom() - STRIP_HEIGHT + 1,
                       rect.width(), STRIP_HEIGHT)
        if self.geometry() != target:
            self.setGeometry(target)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 原生视频子窗之上；只在显示/恢复时调整层级，不在进度轮询中反复 raise。
        self.raise_()


class VodBarProgress(QWidget):
    """全屏底栏里的进度条（v1.0 需求：控件侧）。

    构造 **parent=None**：宿主 ``TileControls.add`` 会 ``setParent(wrapper)``
    再插进 ``tile.bottom.layout()``（全屏时随底栏变独立 Tool 窗，同隐同显）。
    不要 ``WA_NativeWindow`` —— 底栏有自己的合成 backing store。
    seek 契约与 ``VodProgressStrip`` 完全一致。
    """

    seek_requested = Signal(float)  # 绝对秒数

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("watchlaterBarProgress")
        # 同 VodProgressStrip：先初始化，再让滑杆事件有可能到达。
        self._preview_provider = None
        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(BAR_HEIGHT)
        self.setStyleSheet("""
            QWidget#watchlaterBarProgress { background: transparent; }
            QLabel { color: rgba(214, 218, 224, 220); background: transparent; }
            QSlider { background: transparent; }
            QSlider::groove:horizontal {
                height: 4px; background: rgba(214, 218, 224, 70);
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #00a1d6; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 10px; margin: -4px 0; border-radius: 5px;
                border: none; background: #00a1d6;
            }
            QSlider::handle:horizontal:hover { background: #ffffff; }
        """)
        self.setAccessibleName("全屏播放进度")

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(6)

        self.setMouseTracking(True)

        self.current_label = QLabel("00:00", self)
        row.addWidget(self.current_label, 0)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setObjectName("watchlaterBarSeek")
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setTracking(False)
        self.slider.setPageStep(50)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.setMouseTracking(True)
        self.slider.installEventFilter(self)
        self.slider.setToolTip("播放进度（拖动跳转）")
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.sliderPressed.connect(self._pressed)
        self.slider.sliderMoved.connect(self._moved)
        self.slider.sliderReleased.connect(self._released)
        row.addWidget(self.slider, 1)

        self.duration_label = QLabel("00:00", self)
        row.addWidget(self.duration_label, 0)
        # v1.0 需求：：时间文本宽度一变（00:05 → 1:02:33），QLabel 重排会把
        # 中间的滑杆整体推移，全屏下看起来就是「进度条诡异平移」。按最长
        # 时钟串预留固定宽度，滑杆左右边缘从此不动。
        self._lock_clock_width()

        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self._preview_provider = None

    def _lock_clock_width(self) -> None:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(_CLOCK_SAMPLE) + 2
        for label in (self.current_label, self.duration_label):
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignCenter)

    # ------------------------------------------------------------------ state
    def set_duration(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        self._duration = max(0.0, value)
        self.duration_label.setText(format_clock(self._duration))

    def set_position(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        self._position = max(0.0, value)
        if not self._seeking:
            self.current_label.setText(format_clock(self._position))
            if self._duration > 0:
                ratio = max(0.0, min(1.0, self._position / self._duration))
                self.slider.setValue(int(ratio * 1000))

    def reset(self) -> None:
        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self.slider.setValue(0)
        self.current_label.setText("00:00")
        self.duration_label.setText("00:00")

    # ------------------------------------------------------------------ seek
    def _pressed(self) -> None:
        self._seeking = True
        self._cancel_preview()

    def _moved(self, value: int) -> None:
        if self._duration > 0:
            self.current_label.setText(
                format_clock(value / 1000.0 * self._duration)
            )

    def _released(self) -> None:
        # tracking=False 时 sliderReleased 早于 value 提交，读取最终拖动位置。
        value = self.slider.sliderPosition()
        self._seeking = False
        if self._duration > 0:
            target = value / 1000.0 * self._duration
            self._position = target
            self.current_label.setText(format_clock(target))
            self.seek_requested.emit(target)

    # --------------------------------------------------------------- preview
    def set_preview_provider(self, provider: Any) -> None:
        if provider is self._preview_provider:
            return
        self._cancel_preview()
        self._preview_provider = provider

    def _cancel_preview(self) -> None:
        provider = self._preview_provider
        if provider is not None:
            try:
                provider.cancel_preview()
            except Exception:
                pass

    def _preview_hover(self, bar_x: float) -> None:
        provider = self._preview_provider
        if provider is None or self._duration <= 0 or not self.isVisible():
            return
        geo = self.slider.geometry()
        if geo.width() <= 0:
            return
        ratio = (float(bar_x) - geo.x()) / geo.width()
        ratio = max(0.0, min(1.0, ratio))
        seconds = ratio * self._duration
        anchor = QPoint(geo.x() + int(ratio * geo.width()), 0)
        try:
            provider.request_preview(self.mapToGlobal(anchor), seconds)
        except Exception:
            pass

    def mouseMoveEvent(self, event) -> None:
        if (self._preview_provider is not None
                and not (event.buttons() & Qt.LeftButton)):
            self._preview_hover(event.position().x())
        super().mouseMoveEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.slider:
            kind = event.type()
            if kind == QEvent.Type.MouseMove:
                if (self._preview_provider is not None
                        and not (event.buttons() & Qt.LeftButton)):
                    self._preview_hover(self.slider.x() + event.position().x())
                    return True  # 仅消费预览悬停；拖动必须交还 QSlider。
            if kind == QEvent.Type.Leave:
                self._cancel_preview()
        return super().eventFilter(watched, event)

    def leaveEvent(self, event) -> None:
        self._cancel_preview()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._cancel_preview()
        super().hideEvent(event)


class VodProgressLine(QWidget):
    """视频区底边的蓝色进度线（v1.0 需求：控件侧）。

    原生子窗口 + 鼠标穿透，贴在 ``video_geometry`` 底边全宽；纯
    ``paintEvent`` 绘制（轨道 ``rgba(255,255,255,40)``，左起按 ratio 填
    B 站蓝），不碰样式表。
    """

    def __init__(self, tile: QWidget) -> None:
        super().__init__(tile)
        self.setObjectName("watchlaterProgressLine")
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(LINE_HEIGHT)
        self._ratio = 0.0
        self.hide()

    def set_ratio(self, ratio: Any) -> None:
        try:
            value = float(ratio)
        except (TypeError, ValueError):
            value = 0.0
        value = max(0.0, min(1.0, value))
        if value != self._ratio:
            self._ratio = value
            self.update()

    def sync_geometry(self, tile) -> None:
        rect = video_geometry(tile)
        if not rect.isValid() or rect.width() <= 0:
            return
        target = QRect(rect.x(), rect.bottom() - LINE_HEIGHT + 1,
                       rect.width(), LINE_HEIGHT)
        if self.geometry() != target:
            self.setGeometry(target)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(255, 255, 255, 40))
        width = int(self.rect().width() * self._ratio)
        if width > 0:
            painter.fillRect(QRect(0, 0, width, self.rect().height()),
                             QColor("#00a1d6"))
        painter.end()


def _status_action_icon(kind: str):
    """v1.0 需求：：结束卡按钮的 16×16 线条图标（不引图片资源）。"""
    from PySide6.QtGui import QIcon, QPainterPath, QPen, QPixmap

    def draw(painter: QPainter) -> None:
        pen = QPen(QColor(232, 236, 242, 235))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if kind == "replay":
            rect = QRectF(2.6, 2.6, 10.8, 10.8)
            painter.drawArc(rect, 40 * 16, 300 * 16)
            painter.setBrush(QColor(232, 236, 242, 235))
            painter.setPen(Qt.NoPen)
            path = QPainterPath()
            path.moveTo(11.6, 1.6)
            path.lineTo(13.9, 5.0)
            path.lineTo(9.9, 5.3)
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "next":
            painter.setBrush(QColor(232, 236, 242, 235))
            painter.setPen(Qt.NoPen)
            path = QPainterPath()
            path.moveTo(3.0, 2.6)
            path.lineTo(9.8, 8.0)
            path.lineTo(3.0, 13.4)
            path.closeSubpath()
            painter.drawPath(path)
            painter.setPen(pen)
            painter.drawLine(QPointF(12.4, 2.8), QPointF(12.4, 13.2))
        else:  # browser
            painter.drawRect(QRectF(2.6, 6.4, 7.4, 7.0))
            painter.drawLine(QPointF(7.4, 8.6), QPointF(13.2, 2.8))
            painter.drawLine(QPointF(9.6, 2.8), QPointF(13.2, 2.8))
            painter.drawLine(QPointF(13.2, 2.8), QPointF(13.2, 6.4))

    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    try:
        draw(painter)
    finally:
        painter.end()
    if pixmap.isNull():
        return None
    return QIcon(pixmap)


class VodStatusCard(QWidget):
    """错误 / 结束状态卡：播放与暂停期间隐藏，画面完全交还宿主。

    不抢焦点（卡片与按钮都 NoFocus），宿主全局快捷键不受影响。
    """

    retry_requested = Signal()
    replay_requested = Signal()
    browser_requested = Signal()
    restore_requested = Signal()
    #: v1.0 任务 7：播放结束后「下一个视频」。
    next_requested = Signal()

    def __init__(self, tile: QWidget) -> None:
        super().__init__(tile)
        self.setObjectName("watchlaterStatus")
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("""
            QWidget#watchlaterStatus { background: rgba(8, 9, 11, 215); }
            QLabel { color: rgba(230, 234, 240, 245); background: transparent; }
            QLabel#watchlaterStatusTitle { font-size: 15px; font-weight: 700; }
            QLabel#watchlaterStatusMessage { color: rgba(180, 186, 196, 220); }
        """)
        self.setStyleSheet(self.styleSheet() + _PILL_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        self._outer = outer
        outer.addStretch(1)

        self.title_label = QLabel("", self)
        self.title_label.setObjectName("watchlaterStatusTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        outer.addWidget(self.title_label)

        self.message_label = QLabel("", self)
        self.message_label.setObjectName("watchlaterStatusMessage")
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)
        outer.addWidget(self.message_label)

        outer.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._row = row
        self._compact = False
        self._labels = {}
        row.addStretch(1)
        self.primary_button = self._pill("", row)
        # v1.0 任务 7：结束态才有「下一个视频」，排在「重播」右边。
        self.next_button = self._pill("下一个视频", row)
        self.next_button.setToolTip("播放稍后再看列表里的下一个视频")
        self.next_button.setVisible(False)
        self.browser_button = self._pill("用浏览器打开", row)
        self._set_button_text(self.browser_button, "用浏览器打开")
        self._set_button_text(self.next_button, "下一个视频")
        # v1.0 需求：：结束态只留「重播 / 下一个视频 / 用浏览器打开」三个按钮。
        # 「还原为直播态」不再出现在卡片上（仍可从格子右键菜单还原），但保留
        # 控件对象与信号，避免外部接线报 AttributeError。
        self.restore_button = self._pill("还原为直播态", row)
        self.restore_button.hide()
        row.removeWidget(self.restore_button)
        self.restore_button.setParent(self)
        for button, kind in ((self.primary_button, "replay"),
                             (self.next_button, "next"),
                             (self.browser_button, "browser")):
            icon = _status_action_icon(kind)
            if icon is not None:
                button.setIcon(icon)
                button.setIconSize(QSize(16, 16))
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self.primary_button.clicked.connect(self._primary)
        self.next_button.clicked.connect(self.next_requested)
        self.browser_button.clicked.connect(self.browser_requested)
        self.restore_button.clicked.connect(self.restore_requested)
        self._mode = ""
        self.hide()

    # ------------------------------------------------ narrow / portrait PiP
    def set_compact(self, compact: bool) -> None:
        """v1.0 需求：：竖屏/窄画中画里三个按钮改为等宽图标，不再挤成一堆。"""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._outer.setContentsMargins(8, 8, 8, 8) if compact else \
            self._outer.setContentsMargins(16, 12, 16, 12)
        self._row.setSpacing(5 if compact else 8)
        for button in (self.primary_button, self.next_button,
                       self.browser_button):
            label = self._labels.get(button, button.text())
            if compact:
                button.setText("")
                button.setToolTip(label)
                button.setFixedWidth(34)
                button.setIconSize(QSize(16, 16))
            else:
                button.setText(label)
                button.setMinimumWidth(0)
                button.setMaximumWidth(16777215)

    def _set_button_text(self, button, text: str) -> None:
        """状态切换时设置文案；compact 态只记在 tooltip 里。"""
        text = str(text or "")
        self._labels[button] = text
        button.setToolTip(text)
        button.setText("" if self._compact else text)

    def _pill(self, text: str, row: QHBoxLayout) -> QPushButton:
        button = QPushButton(text, self)
        button.setAutoDefault(False)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        row.addWidget(button, 0)
        return button

    def _primary(self) -> None:
        if self._mode == "error":
            self.retry_requested.emit()
        elif self._mode == "ended":
            self.replay_requested.emit()

    # ------------------------------------------------------------------ modes
    def show_error(self, message: str) -> None:
        self._mode = "error"
        self.title_label.setText("播放失败")
        self.message_label.setText(str(message or "取流失败，请用浏览器打开"))
        self._set_button_text(self.primary_button, "重试")
        self.next_button.setVisible(False)
        self._show()

    def show_ended(self, has_next: bool = True) -> None:
        """结束态：只有这里会露出「下一个视频」。

        ``has_next`` 为 False（已是列表最后一条 / 列表里找不到它）时按钮
        置灰并给出说明，避免点了没反应。
        """
        self._mode = "ended"
        self.title_label.setText("播放结束")
        self.message_label.setText("看完了，要再看一遍吗？")
        self._set_button_text(self.primary_button, "重播")
        self.set_next_available(has_next)
        self._show()

    def set_next_available(self, available: bool) -> None:
        """v1.0 任务 7：没有下一条时禁用并解释原因（不隐藏，位置不跳）。"""
        available = bool(available)
        self.next_button.setVisible(self._mode == "ended")
        self.next_button.setEnabled(available)
        self.next_button.setToolTip(
            "播放稍后再看列表里的下一个视频" if available
            else "已经是稍后再看列表里的最后一个视频"
        )
        if self._mode == "ended":
            self.message_label.setText(
                "看完了，要再看一遍吗？" if available
                else "看完了，这已经是列表里的最后一个。"
            )

    def _show(self) -> None:
        tile = self.parentWidget()
        if tile is not None:
            self.sync_geometry(tile)
        self.show()
        self.raise_()

    # -------------------------------------------------------------- geometry
    def sync_geometry(self, tile) -> None:
        rect = video_geometry(tile)
        if rect.isValid() and rect.width() > 0 and self.geometry() != rect:
            self.setGeometry(rect)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()


class VodSubtitleButton(QToolButton):
    """格子底栏的字幕开关按钮（B 站字幕）。

    v1.0 需求：：经官方 ``register_tile_control`` 以 priority 59 进
    ``TileControls`` 容器，容器本身紧挨宿主「弹」按钮的**左侧** —— 这就是
    用户要的「字幕与弹幕互换位置」。高度约束在底栏 36px 内（26px），文案是
    完整的「字幕」两字，宽度 52px 不截断。checkable 表示字幕开/关；无字幕
    时由插件隐藏。不抢焦点（NoFocus），不影响宿主快捷键。
    """

    #: 两字「字幕」+ 左右内边距的保守宽度；字体度量更宽时自动加宽。
    MIN_WIDTH = 52
    HEIGHT = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("watchlaterSubtitle")
        self.setText("字幕")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("字幕开关（B 站字幕）")
        self.setAccessibleName("字幕开关")
        self.setFixedSize(self._fit_width("字幕"), self.HEIGHT)
        self.setStyleSheet("""
            QToolButton#watchlaterSubtitle {
                background: rgba(12, 13, 16, 200);
                color: rgba(214, 218, 224, 235);
                border: none; border-radius: 6px; font-weight: 600;
            }
            QToolButton#watchlaterSubtitle:hover { background: #00a1d6; color: #04161f; }
            QToolButton#watchlaterSubtitle:checked { background: #00a1d6; color: #04161f; }
        """)

    def _fit_width(self, text: str) -> int:
        """按字体度量留宽，保证「字幕」两字在任何 DPI/字号下都不被截断。"""
        metrics = QFontMetrics(self.font())
        return max(self.MIN_WIDTH, metrics.horizontalAdvance(text) + 22)

    def set_checked(self, on: bool) -> None:
        self.setChecked(bool(on))