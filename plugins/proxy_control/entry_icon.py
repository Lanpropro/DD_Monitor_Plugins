"""Reflect panel selection and service state without the rail's static tint."""
from pathlib import Path
import weakref

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPen, QPixmap

from ddm import theme


class EntryIcon(QIconEngine):
    def __init__(self, entry):
        super().__init__()
        self.entry = weakref.ref(entry) if entry is not None else lambda: None
        self.source = QIcon(str(Path(__file__).with_name('proxy.svg')))

    def clone(self):
        return EntryIcon(self.entry())

    def pixmap(self, size, mode, state):
        result = QPixmap(size)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        self.paint(painter, result.rect(), mode, state)
        painter.end()
        return result

    def paint(self, painter, rect, mode, state):
        entry = self.entry()
        if entry is None:
            return
        opened, running = entry.isChecked(), bool(entry.property('proxyRunning'))
        color = theme.ACCENT if opened else (theme.SUCCESS if running else theme.TEXT2)
        pixmap = self.source.pixmap(rect.size())
        tint = QPainter(pixmap)
        tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
        tint.fillRect(pixmap.rect(), theme.qcolor(color))
        tint.end()
        painter.drawPixmap(rect, pixmap)
        if running and opened:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            diameter = max(4, rect.width() * .3)
            painter.setPen(QPen(theme.qcolor(theme.SIDEBAR), 1))
            painter.setBrush(theme.qcolor(theme.SUCCESS))
            painter.drawEllipse(QRectF(rect.right() - diameter, rect.bottom() - diameter, diameter, diameter))
            painter.restore()
