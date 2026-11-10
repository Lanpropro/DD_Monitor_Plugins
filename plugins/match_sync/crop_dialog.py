"""Crop picker shell; caption movement stays in the Qt event loop on Windows."""
import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QDialog


class CropDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self._caption_drag = None

    def nativeEvent(self, event_type, message):
        if sys.platform == 'win32' and QApplication.platformName() == 'windows':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x00a1 and msg.wParam == 2:  # WM_NCLBUTTONDOWN, HTCAPTION
                # DefWindowProc would run a native move loop, delaying Qt's
                # posted playback timers. Keep the native frame, move via Qt.
                self._caption_drag = (QCursor.pos(), self.pos())
                self.grabMouse()
                return True, 0
        return super().nativeEvent(event_type, message)

    def _finish_caption_drag(self, restore=False):
        if self._caption_drag is None:
            return
        _, initial = self._caption_drag
        self._caption_drag = None
        if restore:
            self.move(initial)
        self.releaseMouse()

    def mouseMoveEvent(self, event):
        if self._caption_drag is not None:
            if event.buttons() & Qt.LeftButton:
                cursor, initial = self._caption_drag
                self.move(initial + event.globalPosition().toPoint() - cursor)
            else:
                self._finish_caption_drag()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._caption_drag is not None and event.button() == Qt.LeftButton:
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
            self._caption_drag = None
        return super().event(event)

    def hideEvent(self, event):
        self._finish_caption_drag()
        super().hideEvent(event)
