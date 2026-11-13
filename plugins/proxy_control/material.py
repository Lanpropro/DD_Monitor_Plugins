"""One bounded, transient background sample; no live video blur loop."""
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene


def transparency_enabled():
    if sys.platform != 'win32' or QApplication.platformName() != 'windows':
        return False
    import ctypes
    import winreg
    class HighContrast(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint), ('flags', ctypes.c_uint), ('scheme', ctypes.c_wchar_p)]
    state = HighContrast()
    state.size = ctypes.sizeof(state)
    if ctypes.windll.user32.SystemParametersInfoW(0x0042, state.size, ctypes.byref(state), 0) and state.flags & 1:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return bool(winreg.QueryValueEx(key, 'EnableTransparency')[0])
    except OSError:
        return True


def background_sample(window, rect):
    if not transparency_enabled():
        return QPixmap()
    # Sample only the covered area before showing the native child. Like the
    # host's settings material, blur a small static image, never the controls.
    source = window.screen().grabWindow(int(window.winId()), rect.x(), rect.y(), rect.width(), rect.height())
    if source.isNull():
        return source
    source = source.scaled(330, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    source.setDevicePixelRatio(1)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(source)
    blur = QGraphicsBlurEffect()
    blur.setBlurRadius(18)
    item.setGraphicsEffect(blur)
    scene.addItem(item)
    scene.setSceneRect(QRectF(source.rect()))
    result = QPixmap(source.size())
    result.fill(Qt.transparent)
    from PySide6.QtGui import QPainter
    painter = QPainter(result)
    scene.render(painter, QRectF(result.rect()), QRectF(source.rect()))
    painter.end()
    return result
