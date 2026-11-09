"""Reflect panel selection and service state without the rail's static tint."""
from pathlib import Path
import weakref

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap

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
        if running:
            painter.save()
            painter.translate(rect.x(), rect.y())
            painter.scale(rect.width() / 24, rect.height() / 24)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(color))
            shield = QPainterPath()
            shield.moveTo(12, 2)
            shield.lineTo(21, 6)
            shield.lineTo(20, 14)
            shield.cubicTo(19, 18, 16, 21, 12, 23)
            shield.cubicTo(8, 21, 5, 18, 4, 14)
            shield.lineTo(3, 6)
            shield.closeSubpath()
            painter.drawPath(shield)
            painter.setPen(QPen(theme.qcolor(theme.SIDEBAR), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            check = QPainterPath()
            check.moveTo(7.5, 12)
            check.lineTo(10.5, 15)
            check.lineTo(16.5, 9)
            painter.drawPath(check)
            painter.restore()
        else:
            self._outline(painter, rect, color)
        if running and opened:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            diameter = max(4, rect.width() * .3)
            painter.setPen(QPen(theme.qcolor(theme.SIDEBAR), 1))
            painter.setBrush(theme.qcolor(theme.SUCCESS))
            painter.drawEllipse(QRectF(rect.right() - diameter, rect.bottom() - diameter, diameter, diameter))
            painter.restore()

    def _outline(self, painter, rect, color):
        pixmap = self.source.pixmap(rect.size())
        tint = QPainter(pixmap)
        tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
        tint.fillRect(pixmap.rect(), theme.qcolor(color))
        tint.end()
        painter.drawPixmap(rect, pixmap)
