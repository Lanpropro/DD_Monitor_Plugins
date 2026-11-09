"""A bounded in-window panel anchored to the plugin rail button."""
from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QLinearGradient, QPainter, QPen, QPixmap, QRegion, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

from ddm import theme
from ddm.corner_geometry import continuous_path
from .material import background_sample


class Popover(QFrame):
    hidden = Signal()

    def __init__(self, panel, window, anchor):
        super().__init__(window, Qt.Widget)
        self.anchor = anchor
        self.backdrop = QPixmap()
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setObjectName('ProxyPopover')
        self.setProperty('customControlSurface', True)
        self.setMinimumSize(0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(panel)
        window.installEventFilter(self)
        shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.hide)
        theme.signals().changed.connect(self.refresh_theme)
        self.refresh_theme()
        self.hide()

    def refresh_theme(self, *_args):
        self.setStyleSheet('#ProxyPopover {background: transparent; border: none;}')
        self.update()

    def resizeEvent(self, event):
        self.backdrop = QPixmap()
        # Clip the native child and its opaque descendants, not just its QSS fill.
        path = continuous_path(self.rect(), theme.RADIUS_LG)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = continuous_path(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), theme.RADIUS_LG)
        painter.setClipPath(path)
        if not self.backdrop.isNull():
            painter.drawPixmap(self.rect(), self.backdrop)
        tint = theme.qcolor(theme.SIDEBAR)
        tint.setAlpha(226 if not self.backdrop.isNull() else 255)
        painter.fillPath(path, tint)
        sheen = QLinearGradient(0, 0, self.width(), self.height())
        sheen.setColorAt(0, QColor(255, 255, 255, 26 if theme.is_light() else 16))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        painter.fillPath(path, sheen)
        painter.setClipping(False)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(theme.qcolor(theme.BORDER_STRONG), 1))
        painter.drawPath(path)
        painter.end()

    def _place(self):
        window = self.parentWidget()
        width, height = min(660, max(1, window.width() - 16)), min(480, max(1, window.height() - 16))
        origin = self.anchor.mapTo(window, QPoint(self.anchor.width() + 8, -8))
        x = max(8, min(origin.x(), window.width() - width - 8))
        y = max(8, min(origin.y(), window.height() - height - 8))
        self.setGeometry(x, y, width, height)
        self.raise_()

    def showEvent(self, event):
        self._place()
        QApplication.instance().installEventFilter(self)
        super().showEvent(event)

    def show(self):
        if self.isHidden():
            self._place()
            sample = background_sample(self.parentWidget(), self.geometry())
            super().show()
            self.backdrop = sample
            self.update()
        else:
            super().show()

    def hideEvent(self, event):
        self.backdrop = QPixmap()
        QApplication.instance().removeEventFilter(self)
        self.hidden.emit()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if not self.isHidden():
            if watched is self.parentWidget() and event.type() == QEvent.Resize:
                self._place()
            elif event.type() == QEvent.MouseButtonPress:
                if watched is self or (isinstance(watched, QWidget) and self.isAncestorOf(watched)):
                    return False
                position = event.globalPosition().toPoint()
                if self.rect().contains(self.mapFromGlobal(position)):
                    return False
                popup = QApplication.activePopupWidget() or QApplication.activeModalWidget()
                if popup is not None and self.isAncestorOf(popup):
                    return False
                if not self.anchor.rect().contains(self.anchor.mapFromGlobal(position)):
                    self.hide()
        return super().eventFilter(watched, event)
