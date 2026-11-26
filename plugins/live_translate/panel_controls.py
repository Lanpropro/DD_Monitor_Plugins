"""Compact selectors and rounded actions with stable input-state rendering."""
from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPen
from ddm import theme
from ddm.ui import ActionButton, SelectControl


class RoundedAction(ActionButton):
    """Keep the host button's signals/accessibility without native square focus."""
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setProperty('customControlSurface', True)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.45)
        primary = self.objectName() == 'PrimaryButton'
        background = theme.ACCENT if primary else theme.CONTENT
        if self.isDown():
            background = theme.mix(background, '#000000', .12)
        elif self.underMouse():
            background = theme.mix(background, '#ffffff', .1) if primary else theme.CONTENT_HOVER
        painter.setBrush(theme.qcolor(background))
        painter.setPen(QPen(theme.qcolor(theme.ACCENT if self.hasFocus() else theme.BORDER), 1))
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = rect.height() / 2
        painter.drawRoundedRect(rect, radius, radius)
        if not self.icon().isNull():
            side = min(20, self.height() - 8)
            self.icon().paint(painter, QRect((self.width()-side)//2, (self.height()-side)//2, side, side),
                              Qt.AlignCenter, QIcon.Normal if self.isEnabled() else QIcon.Disabled)
        if self.text():
            painter.setPen(theme.qcolor('#04161f' if primary else theme.TEXT1))
            painter.drawText(rect, Qt.AlignCenter, self.text())


class StableSelect(SelectControl):
    def wheelEvent(self, event):
        event.ignore()

    def sizeHint(self):
        return QSize(160, theme.CONTROL_HEIGHT)

    def minimumSizeHint(self):
        return QSize(0, theme.CONTROL_HEIGHT)
