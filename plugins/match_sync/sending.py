"""Independent platform composers below the merged chat, one per live room."""
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import (QBoxLayout, QFrame, QHBoxLayout, QListView, QListWidget,
    QListWidgetItem, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from ddm.widgets import Tile
from .panels import SettingsPanel, console_button_style
from .send_design import RecipientDelegate, colors, stylesheet


class SendRow(QFrame):
    def __init__(self, viewer, row):
        super().__init__()
        self.manager = viewer.context.manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignTop)
        self.composer_bar = QWidget(self)
        self.composer_bar.setFixedHeight(40)
        composer_layout = QHBoxLayout(self.composer_bar)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.composer_bar)
        self._tile_controls = None
        room = next((dict(item) for item in getattr(viewer.context.window, 'rooms', [])
                     if str(item.get('room_id') or '') == row.room_id), {})
        room.update(room_id=row.room_id, uname=row.label(),
                    platform=row.platform.kind if row.platform is not None else 'bilibili')
        self.tile = Tile(room, self)
        self.tile.hide()
        self.manager.attach_tile(self.tile)
        self.tile._plugin_controls.set_panel(self)
        self.unavailable = QLabel('当前平台的发送控件不可用，请检查平台插件是否已启用。')
        self.unavailable.setWordWrap(True)
        layout.addWidget(self.unavailable)
        self.update_name(row)

    def update_name(self, row):
        self.unavailable.setVisible(not self.tile._plugin_controls.applicable())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._tile_controls is not None:
            self._tile_controls.layout_panel()

    def shutdown(self):
        self.manager.detach_tile(self.tile)
        self.hide()
        self.deleteLater()


class SendConsole(SettingsPanel):
    def __init__(self, viewer):
        super().__init__(viewer, 'MatchSyncSendConsole')
        self.rows = {}
        self.statuses = {}
        self.batch_targets = set()
        self.selected_room = None
        self.collapsed = False
        self.expanded_without_settings = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        self.title = QLabel('弹幕发送')
        self.title.setObjectName('SendTitle')
        heading.addWidget(self.title, 1)
        self.selection_hint = QLabel('左键选择，可多选')
        self.selection_hint.setObjectName('SendFeedback')
        heading.addWidget(self.selection_hint)
        self.minimize = QPushButton('−')
        self.minimize.setObjectName('SendMinimize')
        self.minimize.setAccessibleName('最小化发送台')
        self.minimize.setFixedSize(28, 28)
        self.minimize.setToolTip('收起发送控制台，保留草稿并扩展弹幕栏。')
        self.minimize.clicked.connect(lambda: self.set_collapsed(True))
        heading.addWidget(self.minimize)
        layout.addLayout(heading)
        self.restore_bar = QWidget(viewer.panel)
        self.restore_bar.setObjectName('DanmakuBar')
        restore_layout = QHBoxLayout(self.restore_bar)
        restore_layout.setContentsMargins(10, 2, 10, 2)
        self.restore = QPushButton('展开发送', self.restore_bar)
        self.restore.setFixedWidth(90)
        self.restore.setStyleSheet(console_button_style('QPushButton'))
        self.restore.clicked.connect(lambda: self.set_collapsed(False))
        restore_layout.addWidget(self.restore)
        restore_layout.addStretch(1)
        viewer.panel.layout().addWidget(self.restore_bar)
        self.restore_bar.hide()
        self.restore.hide()
        self.rooms = QListWidget()
        self.rooms.setAccessibleName('左键勾选主播；选一位单发，选多位同文发送')
        self.rooms.setWrapping(False)
        self.rooms.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        self.rooms.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.rooms.setMouseTracking(True)
        self.rooms.setItemDelegate(RecipientDelegate(self.rooms))
        self.rooms.itemChanged.connect(self._room_checked)
        body = QWidget()
        self.body_layout = QBoxLayout(QBoxLayout.LeftToRight, body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        self.body_layout.addWidget(self.rooms)
        self.work = QWidget()
        self.work.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        work_layout = QVBoxLayout(self.work)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(6)
        self.context = QLabel('选择一位主播')
        self.context.setObjectName('SendContext')
        self.context.setTextFormat(Qt.PlainText)
        self.context.setWordWrap(True)
        work_layout.addWidget(self.context)
        self.body_layout.addWidget(self.work, 1)
        self.body_scroll = QScrollArea()
        self.body_scroll.setFrameShape(QFrame.NoFrame)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setWidget(body)
        layout.addWidget(self.body_scroll, 1)
        self.empty = QLabel('加入直播间后，可在这里分别发送弹幕')
        self.empty.setWordWrap(True)
        work_layout.addWidget(self.empty)
        self.stack = QStackedWidget()
        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.pages.addWidget(self.stack)
        self.batch_panel = BatchSend(self)
        self.pages.addWidget(self.batch_panel)
        work_layout.addWidget(self.pages, 1)
        self.feedback = QLabel('左键选择主播；选一位单发，选多位同文发送。')
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setObjectName('SendFeedback')
        self.feedback.setWordWrap(True)
        self.feedback.setMaximumHeight(self.feedback.fontMetrics().height() * 2 + 4)
        work_layout.addWidget(self.feedback)
        self.refresh_design()

    def refresh_design(self):
        self.setStyleSheet(stylesheet())
        c = colors()
        for editor in self.findChildren(QLineEdit):
            palette = editor.palette()
            palette.setColor(QPalette.Text, QColor(c['text']))
            palette.setColor(QPalette.PlaceholderText, QColor(c['secondary']))
            editor.setPalette(palette)
        self.rooms.viewport().update()
        self.restore.setStyleSheet(console_button_style('QPushButton'))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'body_layout'):
            return
        regular = self.width() >= 520
        self.layout().setContentsMargins(16 if regular else 12, 10 if regular else 8,
                                        16 if regular else 12, 10 if regular else 8)
        self.layout().setSpacing(10 if regular else 6)
        self.body_layout.setSpacing(12 if regular else 6)
        self.body_layout.setDirection(QBoxLayout.LeftToRight if regular else QBoxLayout.TopToBottom)
        self.selection_hint.setVisible(self.width() >= 400)
        row_height = max(52, self.rooms.fontMetrics().height() * 2 + 12)
        if regular:
            self.rooms.setFixedWidth(176)
            self.rooms.setMinimumHeight(0)
            self.rooms.setMaximumHeight(16777215)
            self.rooms.setFlow(QListView.TopToBottom)
            self.rooms.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.rooms.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.rooms.setMinimumWidth(0)
            self.rooms.setMaximumWidth(16777215)
            self.rooms.setFixedHeight(row_height + 8)
            self.rooms.setFlow(QListView.LeftToRight)
            self.rooms.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.rooms.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        painter.fillPath(path, QColor(colors()['surface']))

    def sync_rooms(self):
        had_rows = bool(self.rows)
        checked = set(self.checked_rooms())
        for key in tuple(self.rows):
            if key not in self.viewer.rows:
                row = self.rows.pop(key)
                self.stack.removeWidget(row)
                row.shutdown()
                self.statuses.pop(key, None)
        for key, room in self.viewer.rows.items():
            if key not in self.rows:
                self.rows[key] = SendRow(self.viewer, room)
                self.stack.addWidget(self.rows[key])
                room.alias.textChanged.connect(lambda _text: self.sync_rooms())
                editor = self.rows[key].tile._plugin_controls._repeat_editor()
                if editor is not None:
                    editor.textChanged.connect(lambda _text, room_id=key: self.refresh_room(room_id))
                    editor.returnPressed.connect(lambda room_id=key: self.set_status(room_id, '已请求发送'))
                    for button in editor.parentWidget().findChildren(QPushButton):
                        if button.text() == '发送' and button.window() is editor.window():
                            button.setProperty('sendPrimary', True)
                            button.clicked.connect(lambda _checked=False, room_id=key:
                                                   self.set_status(room_id, '已请求发送'))
            self.rows[key].update_name(room)
        if not had_rows and self.rows:
            checked = {next(iter(self.rows))}
        self.batch_targets = checked.intersection(self.rows)
        self.rooms.blockSignals(True)
        self.rooms.clear()
        for key in self.rows:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if key in self.batch_targets else Qt.Unchecked)
            self.rooms.addItem(item)
            self.refresh_room(key)
        self.rooms.blockSignals(False)
        self.batch_targets.intersection_update(self.rows)
        self._update_selection()
        self.refresh_design()

    def checked_rooms(self):
        return [key for key in self.rows if key in self.batch_targets]

    def refresh_room(self, key):
        row = self.viewer.rows.get(key)
        if row is None:
            return
        controls = self.rows[key].tile._plugin_controls
        editor = controls._repeat_editor()
        draft = editor is not None and bool(editor.text())
        for index in range(self.rooms.count()):
            item = self.rooms.item(index)
            if item.data(Qt.UserRole) == key:
                text = self.statuses.get(key, '')
                item.setText(f"{row.label()}\n{'有草稿 · ' if draft else ''}{text or '待发送'}")
                item.setToolTip(f'{row.label()} · {key}\n{item.text().partition(chr(10))[2]}')
                self.refresh_avatar(key)

    def set_status(self, key, text):
        self.statuses[key] = text
        self.refresh_room(key)
        self.feedback.setText(f'{self.viewer.rows[key].label()}：{text}；结果请查看平台提示。'
                              if key in self.viewer.rows else f'{key}：{text}')
        self.feedback.setToolTip(self.feedback.text())

    def refresh_avatar(self, key):
        row = self.viewer.rows.get(key)
        if row is None:
            return
        pixmap = row.avatar.pixmap()
        if pixmap is None or pixmap.isNull():
            return
        for index in range(self.rooms.count()):
            item = self.rooms.item(index)
            if item.data(Qt.UserRole) == key and item.data(Qt.UserRole + 3) != pixmap.cacheKey():
                item.setData(Qt.DecorationRole, pixmap)
                item.setData(Qt.UserRole + 3, pixmap.cacheKey())

    def _room_checked(self, item):
        key = item.data(Qt.UserRole)
        checked = item.checkState() == Qt.Checked
        if checked == (key in self.batch_targets):
            return
        if checked:
            self.batch_targets.add(key)
        else:
            self.batch_targets.discard(key)
        self._update_selection()

    def _update_selection(self):
        keys = self.checked_rooms()
        self.pages.setCurrentIndex(1 if len(keys) > 1 else 0)
        self.pages.setVisible(bool(keys))
        self.empty.setVisible(not keys)
        if not keys:
            self.selected_room = None
            self.empty.setText('左键选择主播；选一位单发，选多位同文发送。')
            self.context.setText('选择接收弹幕的主播')
        elif len(keys) == 1:
            self.selected_room = keys[0]
            self.stack.setCurrentWidget(self.rows[keys[0]])
            self.context.setText(f'发送给 {self.viewer.rows[keys[0]].label()}')
            for index in range(self.rooms.count()):
                if self.rooms.item(index).data(Qt.UserRole) == keys[0]:
                    self.rooms.setCurrentRow(index)
        else:
            self.selected_room = None
            self.context.setText(f'同文发送至 {len(keys)} 个直播间')
        self.batch_panel.update_targets()

    def sync_height(self):
        settings = self.viewer.settings_panel
        if not settings.isHidden():
            self.expanded_without_settings = False
            self.setFixedHeight(settings.height())
        visible = not self.collapsed and (not settings.isHidden() or self.expanded_without_settings)
        self.setVisible(visible)
        self.restore.setVisible(not visible)
        self.restore_bar.setVisible(not visible)

    def set_collapsed(self, collapsed):
        self.collapsed = collapsed
        self.expanded_without_settings = not collapsed and self.viewer.settings_panel.isHidden()
        self.sync_height()

    def hideEvent(self, event):
        self.batch_panel.stop()
        super().hideEvent(event)


class BatchSend(QWidget):
    """One explicit send per checked room; separate composers keep drafts intact."""
    def __init__(self, console):
        super().__init__(console)
        self.console = console
        self.active = None
        self.pending = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setAlignment(Qt.AlignTop)
        compose = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText('发送给所选主播的相同内容')
        compose.addWidget(self.edit, 1)
        self.send = QPushButton('发送到所选主播')
        self.send.setProperty('sendPrimary', True)
        self.send.clicked.connect(self.start)
        compose.addWidget(self.send)
        self.cancel = QPushButton('取消')
        self.cancel.clicked.connect(self.stop)
        self.cancel.hide()
        compose.addWidget(self.cancel)
        layout.addLayout(compose)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)

    def update_targets(self):
        if not self.timer.isActive():
            count = len(self.console.checked_rooms())
            self.send.setText(f'发送至 {count} 个房间')
            self.send.setEnabled(bool(count))

    def start(self):
        if self.timer.isActive() or not self.edit.text().strip():
            return
        self.pending = self.console.checked_rooms()
        if not self.pending:
            return
        self.text = self.edit.text()
        self.send.setEnabled(False)
        self.cancel.show()
        self.edit.setReadOnly(True)
        self.console.rooms.setEnabled(False)
        for key in self.pending:
            self.console.set_status(key, '待处理')
        self.timer.start()
        self.advance()

    def advance(self):
        if self.active is not None:
            key, row, editor = self.active
            if key in self.console.viewer.rows and (editor.isReadOnly() or not editor.isEnabled()):
                return
            self.console.set_status(key, '已处理' if key in self.console.viewer.rows else '已移除，已取消')
            row.shutdown()
            self.active = None
        while self.pending:
            key = self.pending.pop(0)
            room = self.console.viewer.rows.get(key)
            if room is None:
                self.console.set_status(key, '已移除，未发送')
                continue
            row = SendRow(self.console.viewer, room)
            row.setParent(self)
            row.hide()
            controls = row.tile._plugin_controls
            editor = controls._repeat_editor()
            if editor is None or len(self.text) > editor.maxLength():
                self.console.set_status(key, '入口不可用或超出长度限制')
                row.shutdown()
                continue
            self.active = (key, row, editor)
            controls.repeat_text(self.text, key)
            self.console.set_status(key, '正在处理…')
            return
        self.stop()

    def stop(self, *_args):
        self.timer.stop()
        for key in self.pending:
            self.console.set_status(key, '已取消，未发送')
        self.pending.clear()
        if self.active is not None:
            self.console.set_status(self.active[0], '已取消后续处理，请核对平台结果')
            self.active[1].shutdown()
            self.active = None
        self.edit.setReadOnly(False)
        self.cancel.hide()
        self.console.rooms.setEnabled(True)
        self.update_targets()
