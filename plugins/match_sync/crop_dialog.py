"""Native worker movement and Qt-coalesced movement for in-process pickers."""
import sys

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QDialog


class CropDialog(QDialog):
    caption_started = Signal()
    caption_finished = Signal()

    def __init__(self, parent, *, native_caption=False):
        super().__init__(parent)
        self._native_caption = native_caption
        self._caption_drag = None
        self._caption_pending = None
        self._caption_move_timer = QTimer(self)
        self._caption_move_timer.setSingleShot(True)
        self._caption_move_timer.setInterval(16)
        self._caption_move_timer.timeout.connect(self._flush_caption_move)

    def nativeEvent(self, event_type, message):
        if sys.platform == 'win32' and QApplication.platformName() == 'windows':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if self._native_caption:
                # Only the isolated worker uses the system move/size loop.
                # Observe its boundaries without consuming native messages.
                if msg.message == 0x0231:  # WM_ENTERSIZEMOVE
                    self.caption_started.emit()
                elif msg.message == 0x0232:  # WM_EXITSIZEMOVE
                    self.caption_finished.emit()
            elif msg.message == 0x00a1 and msg.wParam == 2:  # WM_NCLBUTTONDOWN, HTCAPTION
                # DefWindowProc would run a native move loop, delaying Qt's
                # posted playback timers. Keep the native frame, move via Qt.
                self._caption_drag = (QCursor.pos(), self.pos())
                self._caption_pending = None
                self._caption_move_timer.stop()
                self.grabMouse()
                self.caption_started.emit()
                return True, 0
        return super().nativeEvent(event_type, message)

    def _finish_caption_drag(self, restore=False):
        self._caption_move_timer.stop()
        if self._caption_drag is None:
            return
        _, initial = self._caption_drag
        if restore:
            self.move(initial)
        else:
            self._flush_caption_move()
        self._caption_drag = None
        self._caption_pending = None
        self.releaseMouse()
        self.caption_finished.emit()

    def _flush_caption_move(self):
        self._caption_move_timer.stop()
        target, self._caption_pending = self._caption_pending, None
        if self._caption_drag is not None and target is not None and target != self.pos():
            self.move(target)

    def mouseMoveEvent(self, event):
        if self._caption_drag is not None:
            if event.buttons() & Qt.LeftButton:
                cursor, initial = self._caption_drag
                self._caption_pending = initial + event.globalPosition().toPoint() - cursor
                if not self._caption_move_timer.isActive():
                    self._caption_move_timer.start()
            else:
                self._finish_caption_drag()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._caption_drag is not None and event.button() == Qt.LeftButton:
            cursor, initial = self._caption_drag
            self._caption_pending = initial + event.globalPosition().toPoint() - cursor
            self._finish_caption_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._caption_drag is not None and event.key() == Qt.Key_Escape:
            self._finish_caption_drag(restore=True)
            event.accept()
            return
        super().keyPressEvent(event)

    def event(self, event):
        if event.type() == QEvent.UngrabMouse:
            active = self._caption_drag is not None
            self._caption_drag = None
            self._caption_pending = None
            self._caption_move_timer.stop()
            if active:
                self.caption_finished.emit()
        return super().event(event)

    def hideEvent(self, event):
        self._caption_pending = None
        self._finish_caption_drag()
        super().hideEvent(event)
