"""Outline proxy icon; the host paints its running badge after rail tinting."""
from pathlib import Path
import weakref

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap

from ddm import theme


class EntryIcon(QIconEngine):
    def __init__(self, entry):
        super().__init__()
        self.entry = weakref.ref(entry) if entry is not None else lambda: None
        self.source = QIcon(str(Path(__file__).with_name('proxy.svg')))

    def clone(self):
        return EntryIcon(self.entry())

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        result = QPixmap(QSize(round(size.width() * scale), round(size.height() * scale)))
        result.setDevicePixelRatio(scale)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return result

    def paint(self, painter, rect, mode, state):
        entry = self.entry()
        if entry is None:
            return
        color = theme.ACCENT if entry.isChecked() else theme.TEXT2
        pixmap = self.source.pixmap(rect.size(), painter.device().devicePixelRatioF())
        tint = QPainter(pixmap)
        tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
        tint.fillRect(pixmap.rect(), theme.qcolor(color))
        tint.end()
        painter.drawPixmap(rect, pixmap)
