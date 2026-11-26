"""Window-owned translation surface anchored beside the public rail control."""
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QKeySequence, QPainter, QPen, QRegion, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

from ddm import theme
from ddm.ui import continuous_path


class HeightGrip(QWidget):
    def __init__(self, panel, edge):
        super().__init__(panel)
        self.edge = edge
        self.start = None
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip('上下拖动调整面板高度')
        self.setAccessibleName('调整面板高度')
        self.setFixedSize(48, 10)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start = (event.globalPosition().toPoint(), QRect(self.parentWidget().geometry()))
            event.accept()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            point, geometry = self.start
            self.parentWidget().resize_vertical(self.edge, geometry, event.globalPosition().toPoint().y() - point.y())
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.start is not None:
            self.start = None
            panel = self.parentWidget()
            panel.heightChanged.emit(panel.preferred_size.height())
            event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(theme.qcolor(theme.TEXT3), 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(15, 5, 33, 5)


class AnchoredPanel(QFrame):
    hidden = Signal()
    heightChanged = Signal(int)

    def __init__(self, panel, window, anchor, height=None):
        super().__init__(window, Qt.Widget)
        self.anchor = anchor
        self.preferred_size = panel.size()
        self.preferred_size.setHeight(max(360, int(height or panel.height())))
        self._top_offset = 0
        self.setObjectName('TranslatePopover')
        self.setProperty('customControlSurface', True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setStyleSheet('#TranslatePopover {background: transparent; border: none;}')
        box = QVBoxLayout(self)
        box.setContentsMargins(1, 1, 1, 1)
        box.addWidget(panel)
        self.top_grip = HeightGrip(self, 'top')
        self.bottom_grip = HeightGrip(self, 'bottom')
        window.installEventFilter(self)
        anchor.installEventFilter(self)
        shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.hide)
        theme.signals().changed.connect(self.update)
        self.hide()

    def _place(self):
        window = self.parentWidget()
        origin = self.anchor.mapTo(window, QPoint(self.anchor.width() + 10, -8))
        available = window.width() - origin.x() - 8
        width = min(self.preferred_size.width(), max(1, window.width() - 16),
                    available if available >= 300 else max(1, window.width() - 16))
        height = min(self.preferred_size.height(), max(1, window.height() - 16))
        x = max(8, min(origin.x(), window.width() - width - 8))
        y = max(8, min(origin.y() + self._top_offset, window.height() - height - 8))
        self.setGeometry(x, y, width, height)
        self.raise_()

    def resize_vertical(self, edge, start, delta):
        window = self.parentWidget()
        minimum = min(360, max(1, window.height() - 16))
        if edge == 'top':
            top = max(8, min(start.y() + delta, start.bottom() + 1 - minimum))
            height = start.bottom() + 1 - top
        else:
            top = start.y()
            height = max(minimum, min(start.height() + delta, window.height() - 8 - top))
        self.preferred_size.setHeight(height)
        origin = self.anchor.mapTo(window, QPoint(self.anchor.width() + 10, -8))
        self._top_offset = top - origin.y()
        self.setGeometry(start.x(), top, start.width(), height)

    def resizeEvent(self, event):
        path = continuous_path(self.rect(), 24)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        self.top_grip.move((self.width() - self.top_grip.width()) // 2, 0)
        self.bottom_grip.move((self.width() - self.bottom_grip.width()) // 2, self.height() - self.bottom_grip.height())
        self.top_grip.raise_()
        self.bottom_grip.raise_()
        super().resizeEvent(event)

    def showEvent(self, event):
        self._place()
        QApplication.instance().installEventFilter(self)
        self.setFocus(Qt.OtherFocusReason)
        super().showEvent(event)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        self.hidden.emit()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if self.isHidden():
            return False
        if watched is self.parentWidget() and event.type() in (QEvent.Resize, QEvent.Move):
            self._place()
        elif watched is self.anchor and event.type() in (QEvent.Move, QEvent.Resize):
            self._place()
        elif event.type() == QEvent.MouseButtonPress:
            if watched is self or isinstance(watched, QWidget) and self.isAncestorOf(watched):
                return False
            point = event.globalPosition().toPoint()
            if self.rect().contains(self.mapFromGlobal(point)):
                return False
            popup = QApplication.activePopupWidget() or QApplication.activeModalWidget()
            if popup is not None and (self.isAncestorOf(popup) or popup.parentWidget() is not None
                                      and self.isAncestorOf(popup.parentWidget())):
                return False
            if not self.anchor.rect().contains(self.anchor.mapFromGlobal(point)):
                self.hide()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        path = continuous_path(rect, 24)
        painter.fillPath(path, theme.qcolor(theme.ELEVATED))
        painter.setPen(QPen(theme.qcolor(theme.BORDER), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
