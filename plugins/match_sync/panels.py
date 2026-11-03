"""Shared appearance of the playback and sending consoles."""
import ctypes
import sys
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QListView, QPushButton, QScrollArea, QStyledItemDelegate, QStyle, QToolTip, QVBoxLayout, QWidget

from ddm import theme


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
    checkbox.setStyleSheet(f'''QCheckBox {{color: {theme.TEXT1}; border: none; border-radius: 5px; padding: 3px;}}
        QCheckBox:hover {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}''')


def console_hover_color():
    return theme.mix(theme.CONTENT, theme.ACCENT, .25)


class ConsoleModeToggle(QCheckBox):
    """Native checkbox behavior with the approved switch/compare presentation."""
    def __init__(self, text, kind):
        super().__init__(text)
        self.kind = kind
        self.setFocusPolicy(Qt.StrongFocus)
        use_link_cursor(self)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 42, 32)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.isEnabled() and self.underMouse()
        if hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(console_hover_color()))
            painter.drawRoundedRect(QRectF(self.rect()), 5, 5)
        y = self.height() / 2
        font = painter.font()
        font.setPixelSize(13)
        painter.setFont(font)
        if self.kind == 'automatic':
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(theme.ACCENT if self.isChecked() else theme.TEXT3))
            painter.drawRoundedRect(QRectF(1, y - 8, 27, 16), 8, 8)
            painter.setBrush(QColor('#e9edf2'))
            painter.drawEllipse(QPointF(20 if self.isChecked() else 9, y), 5, 5)
        else:
            painter.setPen(QPen(theme.qcolor(theme.ACCENT if self.isChecked() else theme.TEXT2), 1.5))
            painter.setBrush(Qt.NoBrush)
            for x in (3, 17):
                painter.drawRoundedRect(QRectF(x, y - 8, 8, 16), 1, 1)
        painter.setPen(theme.qcolor(theme.ACCENT if hovered else theme.TEXT1 if self.isChecked() else theme.TEXT2))
        painter.drawText(QRect(38, 0, self.width() - 38, self.height()), Qt.AlignVCenter, self.text())


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
        if hovered or self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(console_hover_color() if hovered else theme.ACCENT_SOFT))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        painter.translate(self.width() / 2, self.height() / 2)
        color = theme.TEXT4 if not self.isEnabled() else theme.ACCENT if hovered or self.isChecked() else theme.TEXT1
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


def native_console_button(button, tooltip, size=32):
    """Keep the host's icon drawing and behavior with the console's spacing."""
    button.setFixedSize(size, size)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setFocusPolicy(Qt.StrongFocus)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    use_link_cursor(button)
    button.setStyleSheet(f'''QPushButton {{background: transparent; border: none; outline: none;
        padding: 0; border-radius: 6px;}}
        QPushButton:hover {{background: {console_hover_color()};}}
        QPushButton:checked {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}
        QPushButton:checked:hover {{background: {console_hover_color()};}}
        QPushButton:disabled {{color: {theme.TEXT4};}}''')
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
    """Direct L/R buttons backed by the existing saved channel values and signals."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(62, 32)
        self.setFocusPolicy(Qt.NoFocus)
        for label, value in (('原始声道', 0), ('仅左输出', 3), ('仅右输出', 4)):
            self.addItem(label, value)
        self.left = native_console_button(QPushButton('L'), '左声道')
        self.right = native_console_button(QPushButton('R'), '右声道')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for button in (self.left, self.right):
            button.setFixedSize(30, 32)
            button.setCheckable(True)
            button.setStyleSheet(button.styleSheet() + f'''
                QPushButton:!checked:hover {{background: {theme.CONTENT_HOVER};}}''')
            button.clicked.connect(lambda _checked=False, b=button: self._clicked(b))
            layout.addWidget(button)
        use_link_cursor(self)
        self.currentIndexChanged.connect(self._sync_buttons)
        self._sync_buttons()

    def _sync_buttons(self, *_args):
        self.left.setChecked(self.currentData() in (0, 3))
        self.right.setChecked(self.currentData() in (0, 4))

    def _clicked(self, button):
        if not self.left.isChecked() and not self.right.isChecked():
            button.setChecked(True)
        value = 0 if self.left.isChecked() and self.right.isChecked() else 3 if self.left.isChecked() else 4
        self.setCurrentIndex(self.findData(value))

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


class MatchSettingsDialog(QDialog):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.setAttribute(Qt.WA_DontShowOnScreen, viewer.testAttribute(Qt.WA_DontShowOnScreen))
        self.setWindowTitle('比赛二路设置')
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow('缓存余量', viewer.buffer_reserve)
        form.addRow('画中画画质', viewer.crop_quality)
        form.addRow('录制格式', viewer.record_format)
        viewer.record_format.show()
        viewer.crop_quality.show()
        from .replay_preview import PreviewSizePicker
        self.preview_sizes = PreviewSizePicker(viewer, self)
        self.preview_sizes.changed.connect(self._preview_size_changed)
        layout.addLayout(form)
        layout.addWidget(QLabel('回看预览大小'))
        layout.addWidget(self.preview_sizes)
        note = QLabel('自动对齐和对照微调保留在外部控制栏。\n重放最多回看十分钟，实际范围取主画面可用缓存。')
        note.setWordWrap(True)
        layout.addWidget(note)
        quality_note = QLabel('两种模式共用来源，副路优先720P，否则使用中间档；主画面不降档。\n'
                              '轻量增强只处理摄像头选区，切换模式不重连来源。\n'
                              '持续处理跟不上时保持普通缩放；切换到流畅优先后再选轻量增强可重试。')
        quality_note.setWordWrap(True)
        layout.addWidget(quality_note)
        self.quality_status = QLabel()
        self.quality_status.setWordWrap(True)
        layout.addWidget(self.quality_status)
        format_note = QLabel('MKV：适合长时间录制。MP4：便于播放和剪辑，需正常停止录制以完成文件。')
        format_note.setWordWrap(True)
        layout.addWidget(format_note)
        layout.addWidget(QLabel('录制音轨'))
        routing_note = QLabel('每路可选 1～6 中的一条或多条；同一音轨混合所选来源。\n'
                              '不勾选则不录该路声音。收听音量、静音和 L/R 不影响录制。\n'
                              '录制期间音轨固定；添加、移除或切换主画面、跳转回看会结束当前文件。')
        routing_note.setWordWrap(True)
        layout.addWidget(routing_note)
        self.recording_routes = QScrollArea()
        self.recording_routes.setWidgetResizable(True)
        self.recording_routes.setMaximumHeight(240)
        self.recording_routes.setMinimumHeight(80)
        layout.addWidget(self.recording_routes)
        self.track_checks = {}
        self._recording_rooms = None
        self.refresh_recording_routes()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(500)
        self.status_timer.timeout.connect(self.refresh_quality_status)
        self.status_timer.timeout.connect(self.refresh_recording_routes)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.close)
        layout.addWidget(close)

    def _preview_size_changed(self, ratio):
        self.viewer.replay_preview_ratio = ratio
        self.viewer.replay_bar.preview.reposition()
        self.viewer.changed()

    def refresh_quality_status(self):
        crops = list(self.viewer.floating_crops.values())
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

    def refresh_recording_routes(self):
        rooms = tuple((key, row.label()) for key, row in self.viewer.rows.items())
        if rooms != self._recording_rooms:
            self._recording_rooms = rooms
            old = self.recording_routes.takeWidget()
            if old is not None:
                old.deleteLater()
            body = QWidget()
            grid = QGridLayout(body)
            self.track_checks = {}
            if not rooms:
                grid.addWidget(QLabel('添加直播间后可为各路分配音轨'), 0, 0)
            for index, (key, label) in enumerate(rooms):
                name = QLabel(label)
                name.setMaximumWidth(200)
                name.setWordWrap(True)
                grid.addWidget(name, index, 0)
                checks = []
                for number in range(1, 7):
                    check = QCheckBox(str(number))
                    check.setAccessibleName(f'{label}录制音轨{number}')
                    check.setChecked(number in self.viewer.rows[key].record_tracks)
                    check.setCursor(Qt.PointingHandCursor)
                    check.toggled.connect(lambda _checked, k=key: self._record_tracks_changed(k))
                    grid.addWidget(check, index, number)
                    checks.append(check)
                self.track_checks[key] = checks
            grid.setRowStretch(len(rooms), 1)
            self.recording_routes.setWidget(body)
        self.recording_routes.setEnabled(self.viewer.multi_recorder is None)
        self.viewer.record_format.setEnabled(self.viewer.multi_recorder is None)

    def _record_tracks_changed(self, key):
        if key in self.viewer.rows and self.viewer.multi_recorder is None:
            self.viewer.rows[key].record_tracks = tuple(number for number, check in enumerate(self.track_checks[key], 1) if check.isChecked())
            self.viewer.changed()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_quality_status()
        self.refresh_recording_routes()
        self.status_timer.start()

    def hideEvent(self, event):
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
