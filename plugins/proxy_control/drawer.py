"""An embedded left drawer that reserves space beside the native video wall."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QVBoxLayout

from ddm import theme


class Drawer(QFrame):
    hidden = Signal()

    def __init__(self, panel, container):
        super().__init__(container, Qt.Widget)
        self.setObjectName('ProxyDrawer')
        self.container = container
        self._margins = None
        self._laying_out = False
        self.setMinimumSize(0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)
        container.installEventFilter(self)
        shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.hide)
        theme.signals().changed.connect(self.refresh_theme)
        self.refresh_theme()
        self.hide()

    def refresh_theme(self, *_args):
        self.setStyleSheet(f'#ProxyDrawer {{background: {theme.SIDEBAR}; border: none; '
                          f'border-right: 1px solid {theme.BORDER};}}')

    def _place(self):
        if self._laying_out or self._margins is None:
            return
        self._laying_out = True
        try:
            width = max(1, min(380, max(240, round(self.container.width() * .34)),
                               self.container.width() - 160))
            self.setGeometry(0, 0, width, self.container.height())
            layout = self.container.layout()
            if layout is not None:
                left, top, right, bottom = self._margins
                layout.setContentsMargins(left + width, top, right, bottom)
                layout.activate()
            self.raise_()
        finally:
            self._laying_out = False

    def showEvent(self, event):
        if self._margins is None:
            layout = self.container.layout()
            self._margins = layout.getContentsMargins() if layout is not None else (0, 0, 0, 0)
        self._place()
        super().showEvent(event)

    def hideEvent(self, event):
        margins, self._margins = self._margins, None
        layout = self.container.layout()
        if margins is not None and layout is not None:
            layout.setContentsMargins(*margins)
            layout.activate()
        self.hidden.emit()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.container and event.type() == QEvent.Resize and not self.isHidden():
            self._place()
        return super().eventFilter(watched, event)
