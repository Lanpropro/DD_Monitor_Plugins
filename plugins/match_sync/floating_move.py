"""One transient native move surface; the crop retains its image and placement."""
import ctypes
from ctypes import wintypes
import json
import os
import sys
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


def _trace(phase):
    if os.environ.get('DDM_MATCH_DIAGNOSTICS', '1') != '0':
        print('[悬浮拖动] '+json.dumps(dict(time=time.time(), pid=os.getpid(), phase=phase)),
              file=sys.stderr, flush=True)


class MoveSurface(QWidget):
    def __init__(self, move):
        super().__init__(move.owner, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.move_request = move
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(move.crop.size())
        self.move(move.crop.mapToGlobal(QPoint()))

    def paintEvent(self, event):
        self.move_request.crop.paint_surface(QPainter(self))

    def nativeEvent(self, kind, address):
        if sys.platform == 'win32' and self.move_request.active:
            message = wintypes.MSG.from_address(int(address))
            if message.message == 0x0231:  # WM_ENTERSIZEMOVE
                _trace('entered')
            if message.message == 0x0216:  # WM_MOVING: physical screen coordinates
                rect = wintypes.RECT.from_address(message.lParam)
                self.move_request.constrain(rect)
                return True, 1
            if message.message == 0x0232:  # WM_EXITSIZEMOVE (including Escape)
                self.move_request.finish(cancel=bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000))
        return super().nativeEvent(kind, address)


class NativeCropMove(QObject):
    def __init__(self, crop):
        super().__init__(crop)
        self.crop = crop
        self.owner = crop.parentWidget()
        self.original = QRect(crop.drag[1])
        self.active = False
        self.preview = MoveSurface(self)
        crop.destroyed.connect(self.abandon)

    def abandon(self):
        # QObject deletion may bypass closeEvent; never read the deleted crop.
        if self.active:
            self.active = False
            if isValid(self.preview):
                self.preview.hide()
                self.preview.deleteLater()

    def begin(self):
        self.preview.show()
        self.preview.raise_()
        if not self.preview.windowHandle().startSystemMove():
            _trace('unsupported')
            self.preview.hide()
            self.preview.deleteLater()
            self.deleteLater()
            return False
        self.active = True
        _trace('requested')
        self.owner.installEventFilter(self)
        self.crop.hide()
        # Clear the exposed source before the system's nested move loop starts.
        self.crop.gesture_finished.emit()
        return True

    def constrain(self, rect):
        bounds = self.crop.movement_bounds()
        window = self.owner.window()
        offset = self.owner.mapTo(window, bounds.topLeft())
        scale = window.devicePixelRatioF()
        origin = wintypes.POINT(round(offset.x() * scale), round(offset.y() * scale))
        user = ctypes.windll.user32
        user.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        user.ClientToScreen(int(window.winId()), ctypes.byref(origin))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        left = max(origin.x, min(origin.x + round(bounds.width() * scale) - width, rect.left))
        top = max(origin.y, min(origin.y + round(bounds.height() * scale) - height, rect.top))
        rect.left, rect.top = left, top
        rect.right, rect.bottom = left + width, top + height

    def finish(self, *, cancel=False, show=True):
        if not self.active:
            return
        self.active = False
        _trace('cancelled' if cancel else 'finished')
        self.owner.removeEventFilter(self)
        self.crop.native_move = None
        self.crop.drag = None
        point = self.original.topLeft() if cancel else self.owner.mapFromGlobal(self.preview.pos())
        self.crop.fit_geometry(point.x(), point.y(), self.original.width())
        if show:
            self.crop.show()
            self.crop.raise_()
        self.preview.hide()
        self.preview.deleteLater()
        self.crop.gesture_finished.emit()
        self.deleteLater()

    def eventFilter(self, watched, event):
        if watched is self.owner and event.type() in (QEvent.Hide, QEvent.Close, QEvent.Move, QEvent.Resize):
            self.finish(cancel=True)
        return False


def start_native_move(crop, event):
    if (sys.platform != 'win32' or QApplication.platformName() != 'windows'
            or not event.spontaneous()):
        return False
    origin, geometry, _ = crop.drag
    point = geometry.topLeft() + event.globalPosition().toPoint() - origin
    crop.fit_geometry(point.x(), point.y(), geometry.width())
    move = NativeCropMove(crop)
    crop.native_move = move
    if move.begin():
        return True
    crop.native_move = None
    return False
