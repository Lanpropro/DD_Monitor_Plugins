"""Shared appearance of the playback and sending consoles."""
import ctypes
import sys
import time
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListView, QPushButton, QScrollArea, QSizePolicy, QStyledItemDelegate, QStyle, QToolTip, QVBoxLayout, QWidget

from ddm import theme
from ddm.widgets import TileButton, _icon_color
from .replay_cache import CACHE_MARGIN_SECONDS, CACHE_MINUTES, estimated_disk_gb

try:
    from ddm.settings_layout import SettingsColumns, SettingsSection
    from ddm.settings_window import SettingsWindow
except ImportError:
    SettingsColumns = SettingsSection = None
    SettingsWindow = QDialog


class ChatConnectionIcon(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.connections = 0
        self.setFixedSize(20, 20)

    def set_connections(self, count):
        if self.connections != count:
            self.connections = count
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(10, 10)
        painter.rotate(90)
        painter.translate(-10, -10)
        for level, radius in enumerate((5, 9), 1):
            color = theme.qcolor(theme.PINK if self.connections >= level else theme.TEXT3)
            painter.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(QRectF(10 - radius, 15 - radius, radius * 2, radius * 2), 45 * 16, 90 * 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(theme.PINK if self.connections else theme.TEXT3))
        painter.drawEllipse(QPointF(10, 15), 1.4, 1.4)


class LinkCursor(QObject):
    def eventFilter(self, widget, event):
        if event.type() == QEvent.EnabledChange:
            widget.setCursor(Qt.PointingHandCursor if widget.isEnabled() else Qt.ArrowCursor)
        if (event.type() in (QEvent.Enter, QEvent.HoverMove, QEvent.MouseMove)
                and widget.isEnabled() and sys.platform == 'win32'
                and QApplication.platformName() == 'windows'
                and QApplication.widgetAt(QCursor.pos()) is widget):
            # Use the live system link cursor, including animated Windows themes.
            user32 = ctypes.windll.user32
            user32.LoadCursorW.restype = ctypes.c_void_p
            user32.SetCursor.argtypes = (ctypes.c_void_p,)
            user32.SetCursor(user32.LoadCursorW(None, 32649))  # IDC_HAND
        return False


def use_link_cursor(widget):
    widget.setCursor(Qt.PointingHandCursor)
    widget.setAttribute(Qt.WA_Hover)
    widget.setMouseTracking(True)
    if not hasattr(widget, '_match_link_cursor'):
        widget._match_link_cursor = LinkCursor(widget)
        widget.installEventFilter(widget._match_link_cursor)


def style_checkbox(checkbox):
    use_link_cursor(checkbox)
    checkbox.setAttribute(Qt.WA_Hover)
    checkbox.setMouseTracking(True)
    checkbox.setStyleSheet(f'''QCheckBox {{color: {theme.TEXT1}; border: none; border-radius: 8px; padding: 4px 6px;}}
        QCheckBox:hover {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}
        QCheckBox:pressed {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}''')


def console_hover_color():
    return theme.mix(theme.CONTENT, theme.ACCENT, .25)


class ConsoleModeToggle(QCheckBox):
    """Settings-style checkbox used by the console's mode controls."""
    def __init__(self, text, kind):
        super().__init__(text)
        self.kind = kind
        self.setFocusPolicy(Qt.StrongFocus)
        style_checkbox(self)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 42, 32)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        super().paintEvent(event)


def console_role_icon():
    icon = QIcon()
    for state, color in ((QIcon.Off, theme.TEXT2), (QIcon.On, theme.ACCENT)):
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(theme.qcolor(color), 1.2))
        painter.drawRoundedRect(QRectF(1, 2, 14, 10), 1, 1)
        painter.drawLine(8, 12, 8, 15)
        painter.drawLine(5, 15, 11, 15)
        painter.end()
        icon.addPixmap(pixmap, QIcon.Normal, state)
    return icon


class ConsoleNotice(QLabel):
    """Allow console messages to wrap when the toolbar is narrow."""
    def __init__(self, text=''):
        super().__init__(text)
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(0)
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWordWrap(True)


class ConsoleToolbar(QWidget):
    def __init__(self, leading, notices, trailing):
        super().__init__()
        self.setObjectName('MatchSyncToolbar')
        self.setStyleSheet(f'QWidget#MatchSyncToolbar {{border-bottom: 1px solid {theme.BORDER_STRONG};}}')
        heading = QGridLayout(self)
        heading.setContentsMargins(16, 10, 16, 10)
        heading.setHorizontalSpacing(16)
        heading.setColumnStretch(1, 1)
        heading.addWidget(leading, 0, 0, Qt.AlignLeft)
        heading.addWidget(trailing, 0, 2, Qt.AlignRight)
        heading.addWidget(notices, 0, 1)
        self.notice_group = (leading, notices, trailing)

    def event(self, event):
        if event.type() in (QEvent.Resize, QEvent.LayoutRequest) and hasattr(self, 'notice_group'):
            leading, notices, trailing = self.notice_group
            heading = self.layout()
            messages = [label for label in notices.findChildren(ConsoleNotice) if not label.isHidden()]
            line_width = max((label.fontMetrics().horizontalAdvance(label.text()) for label in messages), default=0)
            needed = len(messages) * line_width + max(0, len(messages) - 1) * notices.layout().spacing()
            margins = heading.contentsMargins()
            content_width = self.width() - margins.left() - margins.right()
            available = content_width - 2 * heading.horizontalSpacing() - leading.sizeHint().width() - trailing.sizeHint().width()
            position = (1, 0, 1, 3) if needed > max(0, available) else (0, 1, 1, 1)
            if heading.getItemPosition(heading.indexOf(notices)) != position:
                heading.removeWidget(notices)
                heading.addWidget(notices, *position)
            width = content_width if position[0] else available
            height = max(0, notices.layout().heightForWidth(max(0, width)))
            if notices.minimumHeight() != height:
                notices.setMinimumHeight(height)
            controls_height = max(leading.sizeHint().height(), trailing.sizeHint().height())
            rows_height = controls_height + heading.verticalSpacing() + height if position[0] else max(controls_height, height)
            minimum_height = margins.top() + rows_height + margins.bottom()
            if self.minimumHeight() != minimum_height:
                self.setMinimumHeight(minimum_height)
        return super().event(event)


class ConsoleIconButton(QPushButton):
    def __init__(self, kind, tooltip, parent=None, *, size=28):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(size, 26 if size == 28 else size)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFocusPolicy(Qt.NoFocus)
        use_link_cursor(self)
        self.setAttribute(Qt.WA_Hover)
        self.setMouseTracking(True)
        self.setStyleSheet(f'''QPushButton {{background: transparent; border: none; outline: none; padding: 0; border-radius: 6px;
            min-width: {self.width()}px; max-width: {self.width()}px;
            min-height: {self.height()}px; max-height: {self.height()}px;}}''')

    def event(self, event):
        if (event.type() == QEvent.FocusIn and event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)):
            QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), self.toolTip(), self)
        return super().event(event)

    def changeEvent(self, event):
        if event.type() == QEvent.EnabledChange:
            self.setCursor(Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor)
        super().changeEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.isEnabled() and QApplication.widgetAt(QCursor.pos()) is self
        pressed = self.isDown()
        if hovered or self.isChecked() or pressed:
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(theme.mix(console_hover_color(), theme.ACCENT, .25)
                                         if pressed else console_hover_color() if hovered else theme.ACCENT_SOFT))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        painter.translate(self.width() / 2, self.height() / 2)
        color = (theme.TEXT4 if not self.isEnabled() else theme.ACCENT
                 if hovered or self.isChecked() or pressed else theme.TEXT1)
        pen = QPen(theme.qcolor(color), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        if self.kind == 'floating':
            painter.drawRoundedRect(QRectF(-8, -6, 16, 12), 2, 2)
            painter.drawRect(QRectF(1, 0, 5, 4))
        elif self.kind == 'region':
            for x, y in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                painter.drawLine(x * 8, y * 3, x * 8, y * 8)
                painter.drawLine(x * 3, y * 8, x * 8, y * 8)
            painter.drawRect(QRectF(-3, -3, 6, 6))
        elif self.kind == 'assist':
            for x, direction in ((-4, -1), (4, 1)):
                painter.drawLine(x, -6, x, 6)
                painter.drawLine(x, direction * 6, x - 3, direction * 3)
                painter.drawLine(x, direction * 6, x + 3, direction * 3)
        elif self.kind == 'clock':
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(-7, -6, 14, 14))
            painter.drawLine(QPointF(0, -6), QPointF(0, -9))
            painter.drawLine(QPointF(-3, -9), QPointF(3, -9))
            painter.drawLine(QPointF(0, 1), QPointF(0, -3))
            painter.drawLine(QPointF(0, 1), QPointF(3, 3))
        elif self.kind == 'sound_calibration':
            for x, height in ((-7, 1.5), (-3.5, 4), (0, 7), (3.5, 4), (7, 1.5)):
                painter.drawLine(QPointF(x, -height), QPointF(x, height))
        elif self.kind == 'undo_sound_calibration':
            painter.setBrush(Qt.NoBrush)
            path = QPainterPath(QPointF(-7, -2))
            path.lineTo(3, -2)
            path.cubicTo(9, -2, 9, 6, 3, 6)
            path.lineTo(-3, 6)
            painter.drawPath(path)
            painter.drawLine(QPointF(-7, -2), QPointF(-3, -6))
            painter.drawLine(QPointF(-7, -2), QPointF(-3, 2))
        elif self.kind == 'independent':
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(-8, -7, 16, 14), 2, 2)
            painter.drawLine(QPointF(-2, -4), QPointF(4, 0))
            painter.drawLine(QPointF(4, 0), QPointF(-2, 4))
            painter.drawLine(QPointF(-2, 4), QPointF(-2, -4))
        elif self.kind == 'resync':
            painter.drawArc(QRectF(-7, -7, 14, 14), 35 * 16, 285 * 16)
            painter.drawLine(QPointF(6, -6), QPointF(7, -1))
            painter.drawLine(QPointF(7, -1), QPointF(2, -2))
        elif self.kind == 'minimize':
            painter.drawLine(QPointF(-6, 3), QPointF(6, 3))
        elif self.kind == 'copy':
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(-3, -3, 9, 10), 1.5, 1.5)
            painter.drawLine(QPointF(-6, 3), QPointF(-6, -6))
            painter.drawLine(QPointF(-6, -6), QPointF(3, -6))
        elif self.kind == 'emote':
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(-7, -7, 14, 14))
            painter.drawPoint(QPointF(-2.5, -2))
            painter.drawPoint(QPointF(2.5, -2))
            painter.drawArc(QRectF(-4, -2, 8, 6), 200 * 16, 140 * 16)
        elif self.kind == 'gift':
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(-6, -1, 12, 8), 1, 1)
            painter.drawRect(QRectF(-7, -4, 14, 3))
            painter.drawLine(QPointF(0, -4), QPointF(0, 7))
            painter.drawEllipse(QRectF(-5, -8, 5, 4))
            painter.drawEllipse(QRectF(0, -8, 5, 4))
        elif self.kind == 'save':
            painter.setBrush(Qt.NoBrush)
            bookmark = QPainterPath()
            bookmark.moveTo(-5, -7)
            bookmark.lineTo(5, -7)
            bookmark.lineTo(5, 7)
            bookmark.lineTo(0, 3)
            bookmark.lineTo(-5, 7)
            bookmark.closeSubpath()
            painter.drawPath(bookmark)
        else:
            painter.drawLine(QPointF(-7, 0), QPointF(4, 0))
            painter.drawLine(QPointF(0, -4), QPointF(4, 0))
            painter.drawLine(QPointF(0, 4), QPointF(4, 0))
            painter.drawLine(QPointF(7, -6), QPointF(7, 6))


class RestorePanelButton(TileButton):
    """Draw a compact icon on the host playback button surface."""

    def __init__(self, kind, label, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setObjectName('TileCtrl')
        self.setProperty('tileButton', True)
        self.setFixedSize(28, 26)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAutoDefault(False)
        self.setToolTip(label)
        self.setAccessibleName(label)
        use_link_cursor(self)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        scale_press = getattr(self, '_scale_press', None)
        if scale_press is not None:
            scale_press(painter)
        painter.translate(self.width() / 2, self.height() / 2)
        hovered = self.underMouse() or self.property('hovered')
        color = _icon_color(self) if hovered else QColor('#ffffff')
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if self.kind == 'playback':
            painter.drawRoundedRect(QRectF(-8, -6, 16, 12), 2, 2)
            painter.drawLine(QPointF(-8, 2), QPointF(8, 2))
            return
        bubble = QPainterPath(QPointF(-5, -6))
        bubble.lineTo(5, -6)
        bubble.quadTo(8, -6, 8, -3)
        bubble.lineTo(8, 1)
        bubble.quadTo(8, 4, 5, 4)
        bubble.lineTo(-2, 4)
        bubble.lineTo(-6, 7)
        bubble.lineTo(-6, 4)
        bubble.quadTo(-8, 4, -8, 1)
        bubble.lineTo(-8, -3)
        bubble.quadTo(-8, -6, -5, -6)
        painter.drawPath(bubble)
        if self.kind == 'chat':
            painter.drawLine(QPointF(-4, -2.5), QPointF(4, -2.5))
            painter.drawLine(QPointF(-4, .5), QPointF(1, .5))
        else:
            painter.drawLine(QPointF(-2, -3.5), QPointF(2, -3.5))
            painter.drawLine(QPointF(0, -3.5), QPointF(0, 1.5))
            painter.drawLine(QPointF(-2, 1.5), QPointF(2, 1.5))


def native_console_button(button, tooltip, size=32, *, unchecked_hover=None):
    """Keep the host's icon drawing and behavior with the console's spacing."""
    button.setFixedSize(size, size)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setFocusPolicy(Qt.StrongFocus)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    use_link_cursor(button)
    hover_rule = (f'QPushButton:!checked:hover {{background: {unchecked_hover};}}'
                  if unchecked_hover is not None else '')
    button.setStyleSheet(f'''QPushButton {{background: transparent; border: none; outline: none;
        padding: 0; border-radius: 6px;}}
        QPushButton:hover {{background: {console_hover_color()};}}
        QPushButton:pressed {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}
        QPushButton:checked {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}
        QPushButton:checked:hover {{background: {console_hover_color()};}}
        QPushButton:disabled {{color: {theme.TEXT4};}}
        {hover_rule}''')
    return button


class ColorSwatchDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(32, 32)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            painter.setBrush(theme.qcolor(console_hover_color()))
            painter.drawRoundedRect(QRectF(option.rect).adjusted(1, 1, -1, -1), 5, 5)
        painter.setBrush(QColor(index.data(Qt.UserRole)))
        painter.drawEllipse(QPointF(option.rect.center()), 7, 7)
        if option.state & QStyle.State_Selected:
            painter.setBrush(theme.qcolor(theme.BG))
            painter.drawEllipse(QPointF(option.rect.center()), 2, 2)
        painter.restore()


class ConsoleChannels(QComboBox):
    """Direct L/R buttons backed by the existing saved channel values and mute."""
    audibleChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.audible = True
        self.setFixedSize(62, 32)
        self.setFocusPolicy(Qt.NoFocus)
        for label, value in (('原始声道', 0), ('仅左输出', 3), ('仅右输出', 4)):
            self.addItem(label, value)
        self.left = native_console_button(QPushButton('L'), '左声道', unchecked_hover=theme.CONTENT_HOVER)
        self.right = native_console_button(QPushButton('R'), '右声道', unchecked_hover=theme.CONTENT_HOVER)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for button in (self.left, self.right):
            button.setFixedSize(30, 32)
            button.setCheckable(True)
            button.clicked.connect(self._clicked)
            layout.addWidget(button)
        use_link_cursor(self)
        self.currentIndexChanged.connect(self._sync_buttons)
        self._sync_buttons()

    def _sync_buttons(self, *_args):
        self.left.setChecked(self.audible and self.currentData() in (0, 3))
        self.right.setChecked(self.audible and self.currentData() in (0, 4))

    def set_audible(self, enabled):
        self.audible = enabled
        self._sync_buttons()

    def _clicked(self, _checked=False):
        enabled = self.left.isChecked() or self.right.isChecked()
        if enabled:
            value = 0 if self.left.isChecked() and self.right.isChecked() else 3 if self.left.isChecked() else 4
            self.audible = True
            self.setCurrentIndex(self.findData(value))
        self.set_audible(enabled)
        self.audibleChanged.emit(enabled)

    def showPopup(self):
        pass

    def paintEvent(self, event):
        pass

    def wheelEvent(self, event):
        event.ignore()


class ConsoleChoice(QComboBox):
    """Keep native popup options while using a compact color face."""
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(48, 32)
        use_link_cursor(self)
        if kind == 'color':
            view = QListView()
            view.setFlow(QListView.LeftToRight)
            view.setWrapping(False)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            view.setItemDelegate(ColorSwatchDelegate(view))
            self.setView(view)
            self.setStyleSheet('QComboBox {combobox-popup: 0;}')

    def showPopup(self):
        if self.kind == 'color':
            self.view().setFixedSize(self.count() * 32 + 8, 36)
            self.view().window().setFixedSize(self.count() * 32 + 12, 40)
        super().showPopup()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if self.underMouse():
            painter.setBrush(theme.qcolor(console_hover_color()))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        painter.translate(0, 2)
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QPen(theme.qcolor(theme.TEXT1), 1.5, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(QColor(self.currentData() or theme.ACCENT))
        painter.drawRoundedRect(QRectF(8, 8, 12, 12), 2, 2)
        x = self.width() - 12
        painter.drawLine(x - 3, 12, x, 15)
        painter.drawLine(x, 15, x + 3, 12)


class MatchSettingsDialog(SettingsWindow):
    _MOVE_TIMER = 0x4D534452

    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self._moving_clock = None
        self._move_hwnd = None
        self._move_aux_due = 0.
        self._painting_move = False
        self.setAttribute(Qt.WA_DontShowOnScreen, viewer.testAttribute(Qt.WA_DontShowOnScreen))
        self.setWindowTitle('比赛二路设置')
        root = self.body_layout if SettingsColumns is not None else QVBoxLayout(self)
        if SettingsColumns is not None:
            self._drag_offset = None
            self._drag_target = None
            self._drag_timer = QTimer(self)
            self._drag_timer.setSingleShot(True)
            self._drag_timer.setInterval(16)
            self._drag_timer.timeout.connect(self._apply_header_move)
            self.window_header.installEventFilter(self)
            self.window_header.breadcrumb.setText(self.windowTitle())
            self.window_header.close_button.setAccessibleName('关闭比赛二路设置')
            self.window_header.close_button.setToolTip('关闭比赛二路设置（修改自动保存）')
            self.set_material(getattr(viewer.context.window, 'settings', {}))
        self.page = QScrollArea(self)
        self.page.setObjectName('SettingsPage')
        self.page.setFrameShape(QFrame.NoFrame)
        self.page.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.page.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName('SettingsPage')
        self.page.setWidget(content)
        self.page.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 4, 12, 12)
        layout.setSpacing(12)
        root.addWidget(self.page)
        self.preview_section, preview_grid = self._section('回看与预览')
        from .replay_preview import PreviewSizePicker
        self.preview_sizes = PreviewSizePicker(viewer, self.preview_section)
        self.preview_sizes.changed.connect(self._preview_size_changed)
        self.preview_sizes.setMinimumHeight(252)
        preview_grid.addWidget(self.preview_sizes, 0, 0, 1, 2)
        duration_label = QLabel('回看时长')
        self.replay_duration = QComboBox()
        duration_label.setBuddy(self.replay_duration)
        self.replay_duration.setAccessibleName(duration_label.text())
        for minutes in CACHE_MINUTES:
            self.replay_duration.addItem(f'{minutes} 分钟', minutes)
        self.replay_duration.setCurrentIndex(self.replay_duration.findData(viewer.replay_minutes))
        self.replay_duration.currentIndexChanged.connect(self._replay_duration_changed)
        preview_grid.addWidget(duration_label, 1, 0)
        preview_grid.addWidget(self.replay_duration, 1, 1)
        self.replay_storage_hint = QLabel()
        self.replay_storage_hint.setObjectName('SettingsHint')
        self.replay_storage_hint.setWordWrap(True)
        preview_grid.addWidget(self.replay_storage_hint, 2, 0, 1, 2)
        self.refresh_replay_storage()
        self.playback_section, playback_grid = self._section('播放与画质')
        self.playback_section.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(12)
        form.addRow('缓存余量', viewer.buffer_reserve)
        form.addRow('画中画画质', viewer.crop_quality)
        viewer.crop_quality.show()
        self._add_playback_options(form)
        self.chat_source_display = QComboBox()
        self.chat_source_display.setAccessibleName('弹幕列表来源')
        self.chat_source_display.addItem('主播名称', 'name')
        self.chat_source_display.addItem('主播头像', 'avatar')
        self.chat_source_display.setCurrentIndex(self.chat_source_display.findData(viewer.panel.source_display))
        self.chat_source_display.currentIndexChanged.connect(
            lambda _index: viewer.panel.set_source_display(self.chat_source_display.currentData()))
        form.addRow('弹幕列表来源', self.chat_source_display)
        for index in range(form.rowCount()):
            control = form.itemAt(index, QFormLayout.FieldRole).widget()
            control.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            control.setMaximumWidth(280)
        playback_grid.addLayout(form, 0, 0, 1, 2)
        self.quality_status = QLabel()
        self.quality_status.setObjectName('SettingsHint')
        self.quality_status.setWordWrap(True)
        playback_grid.addWidget(self.quality_status, 1, 0, 1, 2)
        self.recording_section, recording_grid = self._section('录制')
        format_label = QLabel('录制格式')
        format_label.setBuddy(viewer.record_format)
        recording_grid.addWidget(format_label, 0, 0)
        recording_grid.addWidget(viewer.record_format, 0, 1, Qt.AlignLeft)
        viewer.record_format.show()
        self.recording_routes = QWidget()
        self.track_grid = QGridLayout(self.recording_routes)
        self.track_grid.setContentsMargins(0, 0, 0, 0)
        self.track_grid.setHorizontalSpacing(8)
        self.track_grid.setVerticalSpacing(7)
        self.track_grid.setColumnStretch(0, 2)
        for number in range(1, 7):
            self.track_grid.setColumnStretch(number, 1)
        recording_grid.addWidget(self.recording_routes, 1, 0, 1, 2)
        if SettingsColumns is not None:
            sections = SettingsColumns()
            sections.add_section('preview', self.preview_section)
            sections.add_section('playback', self.playback_section)
            sections.add_section('recording', self.recording_section, wide=True)
            layout.addWidget(sections)
        else:
            for section in (self.preview_section, self.playback_section, self.recording_section):
                layout.addWidget(section)
            close = QPushButton('关闭')
            close.setObjectName('IconButton')
            close.clicked.connect(self.close)
            root.addWidget(close, 0, Qt.AlignRight)
        layout.addStretch(1)
        self.track_checks = {}
        self._recording_rooms = None
        self.refresh_recording_routes()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(500)
        self.status_timer.timeout.connect(self.refresh_quality_status)
        self.status_timer.timeout.connect(self.refresh_recording_routes)
        self.status_timer.timeout.connect(self.refresh_replay_storage)
        area = self.screen().availableGeometry().size()
        self.setMinimumSize(QSize(440, 420).boundedTo(area))
        self.resize(QSize(1140, 620).boundedTo(area))

    @staticmethod
    def _section(title):
        if SettingsSection is not None:
            section = SettingsSection(title)
            return section, section.grid
        section = QGroupBox(title)
        return section, QGridLayout(section)

    def _add_playback_options(self, form):
        if getattr(self.viewer, 'd3d11_picture', None) is None:
            return
        self.picture_renderer = QComboBox()
        self.picture_renderer.addItem('光栅', False)
        self.picture_renderer.addItem('D3D11（试验）', True)
        form.addRow('主画面渲染', self.picture_renderer)
        self._sync_picture_renderer()
        self.picture_renderer.currentIndexChanged.connect(self._picture_renderer_changed)
        self.viewer.d3d11_picture.stateChanged.connect(self._sync_picture_renderer)

    def _picture_renderer_changed(self, _index):
        self.viewer.d3d11_picture.select(self.picture_renderer.currentData())

    def _sync_picture_renderer(self):
        gpu = self.viewer.d3d11_picture
        with QSignalBlocker(self.picture_renderer):
            self.picture_renderer.setCurrentIndex(self.picture_renderer.findData(gpu.requested))
        self.picture_renderer.setEnabled(gpu.available)

    def _preview_size_changed(self, ratio):
        self.viewer.replay_preview_ratio = ratio
        self.viewer.replay_bar.preview.reposition()
        self.viewer.changed()

    def _replay_duration_changed(self, _index):
        minutes = self.replay_duration.currentData()
        self.viewer.replay_minutes = minutes
        self.viewer.context.set_setting('replay_minutes', minutes)
        self.preview_sizes.update()
        self.refresh_replay_storage()

    def refresh_replay_storage(self):
        sources = [row.decoder for row in self.viewer.rows.values() if row.decoder is not None]
        now = time.monotonic()
        rates = [rate for source in sources if (rate := source.cache_video_rate(now)) is not None]
        if not sources:
            storage = '开始播放后按各路实测码率计算磁盘占用。'
        elif not rates:
            storage = '正在测量当前直播码率…'
        else:
            amount = estimated_disk_gb(self.replay_duration.currentData(), rates)
            scope = f'{len(sources)} 路合计' if len(rates) == len(sources) else f'已测速 {len(rates)}/{len(sources)} 路'
            storage = f'{scope}约 {amount:.2f} GB，含 {CACHE_MARGIN_SECONDS // 60} 分钟缓冲。'
        text = storage + '修改时长在下次开始播放时生效。'
        if self.replay_storage_hint.text() != text:
            self.replay_storage_hint.setText(text)

    def refresh_quality_status(self):
        crops = list(self.viewer.all_floating_crops())
        if self.viewer.crop_quality.currentData() != 'light':
            self.quality_status.setText('')
        elif not crops:
            self.quality_status.setText('轻量增强：等待添加画中画')
        else:
            enhanced = sum(not crop.enhanced.isNull() for crop in crops)
            fallback = sum(crop.enhance_state == 'fallback' for crop in crops)
            reasons = {'cpu_budget': '持续处理较慢', 'backend_timeout': '增强结果持续延迟',
                       'large_crop': '选区过大', 'unavailable': '增强不可用'}
            detail = '、'.join(dict.fromkeys(reasons.get(crop.enhance_reason, '处理较慢')
                              for crop in crops if crop.enhance_state == 'fallback'))
            suffix = f'（{detail}）' if detail else ''
            self.quality_status.setText(f'轻量增强：{enhanced} 路增强，{fallback} 路已回退{suffix}，{len(crops) - enhanced - fallback} 路准备中')
        self.quality_status.setVisible(bool(self.quality_status.text()))

    def refresh_recording_routes(self):
        rooms = tuple((key, row.label()) for key, row in self.viewer.rows.items())
        if rooms != self._recording_rooms:
            self._recording_rooms = rooms
            grid = self.track_grid
            while grid.count():
                widget = grid.takeAt(0).widget()
                widget.hide()
                widget.deleteLater()
            self.track_checks = {}
            if not rooms:
                hint = QLabel('添加直播间后可为各路分配音轨')
                hint.setObjectName('SettingsHint')
                grid.addWidget(hint, 0, 0, 1, 7)
            else:
                source = QLabel('直播来源')
                source.setObjectName('SettingsHint')
                grid.addWidget(source, 0, 0)
                for number in range(1, 7):
                    label = QLabel(f'音轨 {number}')
                    label.setObjectName('SettingsHint')
                    grid.addWidget(label, 0, number, Qt.AlignHCenter)
            for index, (key, label) in enumerate(rooms, 1):
                name = QLabel(label)
                name.setTextFormat(Qt.PlainText)
                name.setWordWrap(True)
                grid.addWidget(name, index, 0)
                checks = []
                for number in range(1, 7):
                    check = QCheckBox()
                    check.setAccessibleName(f'{label} · 音轨 {number}')
                    check.setToolTip(check.accessibleName())
                    check.setChecked(number in self.viewer.rows[key].record_tracks)
                    check.setCursor(Qt.PointingHandCursor)
                    check.toggled.connect(lambda _checked, k=key: self._record_tracks_changed(k))
                    grid.addWidget(check, index, number, Qt.AlignHCenter)
                    checks.append(check)
                self.track_checks[key] = checks
        self.recording_routes.setEnabled(self.viewer.multi_recorder is None)
        self.viewer.record_format.setEnabled(self.viewer.multi_recorder is None)

    def _apply_header_move(self):
        if self._drag_target is not None:
            self.move(self._drag_target)
            self._drag_target = None

    def _native_move_clock(self):
        presenter = getattr(self.viewer, 'd3d11_picture', None)
        worker = getattr(presenter, 'worker', None)
        target = getattr(worker, 'target', None)
        if getattr(target, 'observation', None) is not None and self.viewer.canvas.native_presenting:
            return getattr(presenter, 'settings_move', None)
        return None

    def nativeEvent(self, event_type, message):
        if sys.platform == 'win32' and QApplication.platformName() == 'windows':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            kind = msg.message
            if kind == 0x0231:  # WM_ENTERSIZEMOVE
                self._moving_clock = self._native_move_clock()
                if self._moving_clock is not None:
                    self._moving_clock.set()
                    self._move_hwnd = msg.hWnd
                    self._move_aux_due = 0.
                    ctypes.windll.user32.SetTimer(ctypes.c_void_p(self._move_hwnd), self._MOVE_TIMER, 16, None)
            elif kind == 0x0232 and self._moving_clock is not None:  # WM_EXITSIZEMOVE
                self._end_native_move()
            elif self._moving_clock is not None:
                if kind == 0x0216:  # WM_MOVING
                    self._refresh_during_move()
                elif kind == 0x0113 and msg.wParam == self._MOVE_TIMER:  # WM_TIMER
                    self._refresh_during_move()
                    return True, 0
        return super().nativeEvent(event_type, message)

    def _refresh_during_move(self):
        viewer = self.viewer
        now = time.perf_counter()
        if (self._painting_move or not viewer.running or not viewer.render_timer.isActive()
                or viewer._picture_due is None or now < viewer._picture_due):
            return
        self._painting_move = True
        try:
            # The Windows move loop does not dispatch the usual Qt playback timers.
            viewer._picture_tick()
            if now >= self._move_aux_due and viewer.auxiliary_timer.isActive():
                self._move_aux_due = now + 1 / 30
                viewer._auxiliary_tick()
                # Enhanced frames arrive as coalesced queued signals on each crop.
                for crop in viewer.all_floating_crops():
                    QApplication.sendPostedEvents(crop, QEvent.MetaCall)
                QApplication.sendPostedEvents(viewer.window(), QEvent.UpdateRequest)
        finally:
            self._painting_move = False

    def _end_native_move(self):
        if self._move_hwnd is not None:
            ctypes.windll.user32.KillTimer(ctypes.c_void_p(self._move_hwnd), self._MOVE_TIMER)
            self._move_hwnd = None
        if self._moving_clock is not None:
            self._moving_clock.clear()
            self._moving_clock = None

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'window_header', None):
            kind = event.type()
            if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and not self.isMaximized():
                if self._native_move_clock() is not None:
                    return False  # The header starts the native move; the picture follows device audio.
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if self._drag_offset is not None:
                if kind == QEvent.MouseMove and event.buttons() & Qt.LeftButton:
                    self._drag_target = event.globalPosition().toPoint() - self._drag_offset
                    if not self._drag_timer.isActive():
                        self._drag_timer.start()
                    return True
                if kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                    self._drag_timer.stop()
                    self._drag_target = event.globalPosition().toPoint() - self._drag_offset
                    self._drag_offset = None
                    self._apply_header_move()
                    return True
                if kind in (QEvent.Hide, QEvent.UngrabMouse, QEvent.WindowDeactivate):
                    self._drag_timer.stop()
                    self._drag_offset = self._drag_target = None
        return super().eventFilter(watched, event)

    def _record_tracks_changed(self, key):
        if key in self.viewer.rows and self.viewer.multi_recorder is None:
            self.viewer.rows[key].record_tracks = tuple(number for number, check in enumerate(self.track_checks[key], 1) if check.isChecked())
            self.viewer.changed()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_quality_status()
        self.refresh_recording_routes()
        self.refresh_replay_storage()
        self.status_timer.start()

    def hideEvent(self, event):
        self._end_native_move()
        self.status_timer.stop()
        super().hideEvent(event)


def console_button_style(selector):
    return f"""
        {selector} {{
            background: palette(button); color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: {theme.RADIUS_MD}px; padding: 4px 6px;
        }}
        {selector}:hover {{ background: palette(light); border-color: palette(highlight); }}
        {selector}:pressed {{ background: palette(highlight); color: palette(highlighted-text); }}
        {selector}:checked {{ background: rgba(251, 114, 153, 45); border-color: palette(highlight); }}
    """


class SettingsPanel(QWidget):
    def __init__(self, viewer, object_name="MatchSyncSettings"):
        super().__init__(viewer)
        self.viewer = viewer
        self.setObjectName(object_name)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumHeight(130)
        if object_name != 'MatchSyncSettings':
            self.setMaximumHeight(240)
        self.setStyleSheet(f"""
            #MatchSyncSettings, #MatchSyncRows {{
                background: transparent; border: none;
            }}
            #MatchSyncSettings QLabel, #MatchSyncSettings QCheckBox {{ color: palette(window-text); }}
            #MatchSyncSettings QScrollArea, #MatchSyncSettings QScrollArea > QWidget {{
                background: transparent; border: none;
            }}
            #MatchSyncSettings QLineEdit,
            #MatchSyncSettings QComboBox, #MatchSyncSettings QDoubleSpinBox {{
                background: palette(button); color: palette(window-text);
                border: 1px solid palette(mid);
                border-radius: {theme.RADIUS_MD}px; padding: 4px 6px;
            }}
        """.replace("MatchSyncSettings", object_name)
            + console_button_style(f'#{object_name} QPushButton'))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        # Match the host's Tile and DanmakuPanel outer corners.
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        if self.objectName() == 'MatchSyncSendConsole':
            path.setFillRule(Qt.WindingFill)
            path.addRect(QRectF(0, 0, self.width(), 10))
        tint = QLinearGradient(0, 0, 0, self.height())
        top_color, bottom_color = theme.qcolor(theme.CONTENT), theme.qcolor(theme.BG)
        top_color.setAlpha(round(255 * self.viewer.panel_alpha) if self.viewer.skin_visible else 238)
        bottom_color.setAlpha(round(255 * self.viewer.panel_alpha) if self.viewer.skin_visible else 245)
        tint.setColorAt(0, top_color)
        tint.setColorAt(1, bottom_color)
        painter.fillPath(path, tint)
