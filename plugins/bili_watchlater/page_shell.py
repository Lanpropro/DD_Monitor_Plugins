"""One frameless browser window with embedded, disposable detail pages."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from ddm import theme
from ddm.ui import ActionButton, HintLabel, SettingsSurface, TitleLabel, continuous_path
from .entry_icon import GalleryIcon
from .page_panels import GalleryToolButton


def background_color():
    # Match the current host settings' flat light/dark foundation.
    return '#eeedf4' if theme.is_light() else '#33373b'


def card_color():
    return '#ffffff' if theme.is_light() else '#3c4044'


class InlinePanel(QWidget):
    finished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._finished = False

    def accept(self):
        self._finish(1)

    def reject(self):
        self._finish(0)

    def _finish(self, result):
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.finished.emit(result)


class InlineConfirmation(InlinePanel):
    def __init__(self, title, message, action, parent):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 12, 28, 18)
        outer.addStretch()
        card = SettingsSurface(self)
        body = QVBoxLayout(card)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(18)
        body.addWidget(TitleLabel(title, card))
        detail = HintLabel(message, card)
        detail.setTextFormat(Qt.PlainText)
        body.addWidget(detail)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = ActionButton('取消', card)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.confirm_button = ActionButton(action, card)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_button)
        body.addLayout(buttons)
        outer.addWidget(card)
        outer.addStretch()
        self.setFocusProxy(self.cancel_button)


class BrowserTitleBar(QWidget):
    back_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setMinimumHeight(48)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 4, 12, 4)
        row.setSpacing(10)
        self.back = GalleryToolButton(self)
        self.back.setIcon(QIcon(GalleryIcon('back')))
        self.back.setToolTip('返回视频列表（Esc）')
        self.back.setAccessibleName('返回视频列表')
        self.back.clicked.connect(self.back_requested)
        row.addWidget(self.back)
        self.back.hide()
        self.breadcrumb = QLabel('稍后再看', self)
        self.breadcrumb.setObjectName('SettingsBreadcrumb')
        self.breadcrumb.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.breadcrumb)
        row.addStretch()
        self.maximize = GalleryToolButton(self)
        self.maximize.setIcon(QIcon(GalleryIcon('maximize')))
        self.maximize.setAccessibleName('最大化或还原')
        self.maximize.setToolTip('最大化 / 还原')
        self.maximize.clicked.connect(self.toggle_maximize)
        row.addWidget(self.maximize)
        self.close_button = GalleryToolButton(self)
        self.close_button.setIcon(QIcon(GalleryIcon('close')))
        self.close_button.setAccessibleName('关闭稍后再看')
        self.close_button.setToolTip('关闭稍后再看')
        self.close_button.clicked.connect(parent.close)
        row.addWidget(self.close_button)

    def set_page(self, title=''):
        self.back.setVisible(bool(title))
        self.breadcrumb.setText('稍后再看' + ('  ›  ' + title if title else ''))

    def toggle_maximize(self):
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window().windowHandle() is not None:
            if self.window().windowHandle().startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class BrowserWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        if parent is None:
            self.setWindowFlag(Qt.FramelessWindowHint)
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setProperty('settingsGlassContent', True)
        self.setProperty('appearanceLight', theme.is_light())
        self.setProperty('customControlSurface', True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        painter.setBrush(theme.qcolor(background_color()))
        painter.setPen(QPen(theme.qcolor(theme.BORDER), 1))
        painter.drawPath(continuous_path(rect, 0 if self.isMaximized() else 18))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isWindow() and self.windowHandle() is not None and not self.isMaximized():
            point = event.position()
            edges = Qt.Edges()
            if point.x() < 8:
                edges |= Qt.LeftEdge
            if point.x() >= self.width() - 8:
                edges |= Qt.RightEdge
            if point.y() < 8:
                edges |= Qt.TopEdge
            if point.y() >= self.height() - 8:
                edges |= Qt.BottomEdge
            if edges and self.windowHandle().startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)
