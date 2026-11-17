"""Native video overlays, layered alpha and legacy position badge."""
from __future__ import annotations
import ctypes
import sys
from ctypes import wintypes
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

POSITION_BADGE_HEIGHT = 24


POSITION_BADGE_MIN_WIDTH = 46


POSITION_BADGE_LEFT = 10


POSITION_BADGE_TOP = 8


POSITION_BADGE_TEXT_LEFT = 20   # 左侧圆点区


POSITION_BADGE_TEXT_RIGHT = 12  # 右侧内边距


_SCREEN_CHANGE = getattr(QEvent.Type, 'ScreenChangeInternal', QEvent.Type.DevicePixelRatioChange)


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
        # Private function objects keep our Structure signatures independent
        # of the host's BadgeSurface, which uses the same Windows DLLs.
        if not hasattr(_push_layered_alpha, '_user32'):
            _push_layered_alpha._user32 = ctypes.WinDLL('user32')
            _push_layered_alpha._gdi32 = ctypes.WinDLL('gdi32')
        user32 = _push_layered_alpha._user32
        gdi32 = _push_layered_alpha._gdi32
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
            user32.GetDC.argtypes = (wintypes.HWND,)
            user32.GetDC.restype = wintypes.HDC
            user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int,
                                              ctypes.c_long)
            gdi32.CreateCompatibleDC.restype = wintypes.HDC
            gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
            gdi32.CreateDIBSection.restype = wintypes.HANDLE
            gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(_BITMAPINFOHEADER),
                wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
            gdi32.SelectObject.restype = wintypes.HANDLE
            gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
            gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)
            gdi32.DeleteDC.argtypes = (wintypes.HDC,)
            user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
            user32.UpdateLayeredWindow.argtypes = (wintypes.HWND, wintypes.HDC,
                ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(_BLEND), wintypes.DWORD)
            user32.UpdateLayeredWindow.restype = wintypes.BOOL
            _push_layered_alpha._ready = True
        bmi = _push_layered_alpha._bmi
        blend_t = _push_layered_alpha._blend
        # 宽度按物理像素提交，alpha 通道才不会因 DPR 被拉伸。
        width = int(image.width())
        height = int(image.height())
        if width <= 0 or height <= 0:
            return False
        # 64 位安全：ctypes 对未声明 argtypes 的 int 参数按 32 位 c_int 传，
        # HWND / HDC 句柄会被截断 → 换屏重绘时 UpdateLayeredWindow 拿到无效
        # 句柄，Windows 抛 access violation。这里显式包成 c_void_p，且**不改**
        # 全局函数的 argtypes：宿主 BadgeSurface 也在用同一批 Win32 函数，
        # 两边各自私有的 Structure 类型会让 argtypes 互相冲突。
        ptr = ctypes.c_void_p
        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        if not user32.IsWindow(hwnd):   # 跨屏时原生 HWND 可能已被 Qt 重建
            return False
        screen = user32.GetDC(None)
        if not screen:
            return False
        memory = gdi32.CreateCompatibleDC(ptr(screen))
        info = bmi(40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        data = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            ptr(screen), ctypes.byref(info), 0, ctypes.byref(data), None, 0)
        try:
            if not memory or not bitmap:
                return False
            previous = gdi32.SelectObject(ptr(memory), ptr(bitmap))
            try:
                raw = bytes(image.constBits())
                ctypes.memmove(data, raw, min(len(raw), int(image.sizeInBytes())))
                style = user32.GetWindowLongW(hwnd, -20)
                if not style & 0x00080000:
                    user32.SetWindowLongW(hwnd, -20, style | 0x00080000)
                size = wintypes.SIZE(width, height)
                origin = wintypes.POINT(0, 0)
                blend = blend_t(0, 0, 255, 1)
                ok = user32.UpdateLayeredWindow(
                    ptr(hwnd), ptr(screen), None, ctypes.byref(size), ptr(memory),
                    ctypes.byref(origin), 0, ctypes.byref(blend), 2)
                return bool(ok)
            finally:
                gdi32.SelectObject(ptr(memory), ptr(previous))
        finally:
            if bitmap:
                gdi32.DeleteObject(ptr(bitmap))
            if memory:
                gdi32.DeleteDC(ptr(memory))
            user32.ReleaseDC(None, ptr(screen))
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
        # 用 getattr 取枚举：个别 PySide6 版本没有 ScreenChangeInternal，
        # 直接写死会在换屏事件派发时抛 AttributeError（Qt 层不可恢复）。
        if event.type() in (QEvent.DevicePixelRatioChange, _SCREEN_CHANGE):
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
