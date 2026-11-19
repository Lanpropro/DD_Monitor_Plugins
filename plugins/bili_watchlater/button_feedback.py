"""Stable capsule actions and keyboard-only focus outlines for this plugin."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton
from ddm import theme
from ddm.ui import ActionButton, continuous_path


class FocusVisible:
    def __init__(self, *args, **kwargs):
        self._keyboard_focus = False
        super().__init__(*args, **kwargs)

    def focus_outline(self):
        return self.hasFocus() and self._keyboard_focus

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)
        self.update()

    def mousePressEvent(self, event):
        self._keyboard_focus = False
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        self._keyboard_focus = True
        super().keyPressEvent(event)
        self.update()


class StableActionButton(FocusVisible, ActionButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setProperty('customControlSurface', True)
        theme.signals().changed.connect(self.update)

    def enterEvent(self, event):
        QPushButton.enterEvent(self, event)
        self.update()

    def leaveEvent(self, event):
        QPushButton.leaveEvent(self, event)
        self.update()

    def paintEvent(self, event):
        primary = self.objectName() == 'PrimaryButton'
        flat = bool(self.window().property('settingsGlassContent'))
        control = ('#edecef' if theme.is_light() else '#43444b') if flat else theme.CONTENT
        color = theme.ACCENT if primary else control
        if self.isDown():
            color = theme.mix(color, '#000000', .12)
        elif self.underMouse():
            color = theme.mix(color, theme.TEXT1, .07)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.45)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        painter.setBrush(theme.qcolor(color))
        painter.setPen(QPen(theme.qcolor(theme.ACCENT if self.focus_outline() else theme.BORDER), 1))
        painter.drawPath(continuous_path(rect, rect.height() / 2))
        option = QStyleOptionButton()
        self.initStyleOption(option)
        text = ('#ffffff' if theme.is_light() else '#04161f') if primary else theme.TEXT1
        option.palette.setColor(QPalette.ButtonText, theme.qcolor(text))
        self.style().drawControl(QStyle.CE_PushButtonLabel, option, painter, self)
