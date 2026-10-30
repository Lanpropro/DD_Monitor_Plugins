"""Independent platform composers below the merged chat, one per live room."""
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QListView, QListWidget,
    QListWidgetItem, QLabel, QLineEdit, QPushButton, QStackedWidget, QTabBar, QVBoxLayout, QWidget)

from ddm.widgets import Tile
from .panels import SettingsPanel, console_button_style


class SendRow(QFrame):
    def __init__(self, viewer, row):
        super().__init__()
        self.manager = viewer.context.manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignTop)
        self.composer_bar = QWidget(self)
        self.composer_bar.setFixedHeight(40)
        composer_layout = QHBoxLayout(self.composer_bar)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.composer_bar)
        self._tile_controls = None
        self.tile = Tile({'room_id': row.room_id, 'uname': row.label()}, self)
        self.tile.hide()
        self.manager.attach_tile(self.tile)
        self.tile._plugin_controls.set_panel(self)
        self.unavailable = QLabel('请启用此平台的弹幕互动插件')
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
        self.collapsed = False
        self.expanded_without_settings = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        heading = QHBoxLayout()
        heading.addWidget(QLabel('弹幕发送'), 1)
        self.mode = QTabBar()
        self.mode.addTab('单独发送')
        self.mode.addTab('同文多发')
        self.mode.setStyleSheet('''QTabBar::tab {background: palette(button); color: palette(window-text);
            border: 1px solid palette(mid); border-radius: 8px; padding: 4px 8px;}
            QTabBar::tab:selected {border-color: palette(highlight);}
        ''')
        self.mode.currentChanged.connect(self._mode_changed)
        heading.addWidget(self.mode)
        self.minimize = QPushButton('最小化')
        self.minimize.setFixedWidth(68)
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
        self.target = QComboBox()
        self.target.hide()
        self.target.setAccessibleName('发送弹幕的直播间')
        self.target.currentIndexChanged.connect(self._select_target)
        self.rooms = QListWidget()
        self.rooms.setFlow(QListView.LeftToRight)
        self.rooms.setWrapping(False)
        self.rooms.setFixedHeight(62)
        self.rooms.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.rooms.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.rooms.setStyleSheet('''QListWidget {background: transparent; border: none;}
            QListWidget::item {background: palette(button); color: palette(window-text);
                border: 1px solid palette(mid); border-radius: 8px; margin: 2px; padding: 3px;}
            QListWidget::item:selected {border-color: palette(highlight);}
        ''')
        self.rooms.currentItemChanged.connect(self._room_selected)
        self.rooms.itemChanged.connect(self._room_checked)
        layout.addWidget(self.rooms)
        self.empty = QLabel('加入直播间后，可在这里分别发送弹幕')
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.stack = QStackedWidget()
        self.pages = QStackedWidget()
        self.pages.addWidget(self.stack)
        self.batch_panel = BatchSend(self)
        self.pages.addWidget(self.batch_panel)
        layout.addWidget(self.pages, 1)
        self.feedback = QLabel('选择主播后发送；各房间草稿独立保留。')
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

    def sync_rooms(self):
        selected = self.target.currentData()
        checked = set(self.checked_rooms())
        self.target.blockSignals(True)
        self.target.clear()
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
                            button.clicked.connect(lambda _checked=False, room_id=key:
                                                   self.set_status(room_id, '已请求发送'))
            self.rows[key].update_name(room)
            self.target.addItem(f'{room.label()} · {key}', key)
        self.target.setCurrentIndex(max(0, self.target.findData(selected)))
        self.target.blockSignals(False)
        self.rooms.blockSignals(True)
        self.rooms.clear()
        for key in self.rows:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, key)
            item.setSizeHint(QSize(150, 42))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable if self.mode.currentIndex()
                          else item.flags() & ~Qt.ItemIsUserCheckable)
            if self.mode.currentIndex():
                item.setCheckState(Qt.Checked if key in checked else Qt.Unchecked)
            self.rooms.addItem(item)
            self.refresh_room(key)
        self.rooms.blockSignals(False)
        self.batch_targets.intersection_update(self.rows)
        self._select_target()
        self.empty.setVisible(not self.rows)

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
                item.setToolTip(f'{row.label()} · {key}')

    def set_status(self, key, text):
        self.statuses[key] = text
        self.refresh_room(key)
        self.feedback.setText(f'{self.viewer.rows[key].label()}：{text}；结果请查看平台提示。'
                              if key in self.viewer.rows else f'{key}：{text}')

    def _mode_changed(self, mode):
        self.pages.setCurrentIndex(mode)
        self.rooms.blockSignals(True)
        for index in range(self.rooms.count()):
            item = self.rooms.item(index)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable if mode
                          else item.flags() & ~Qt.ItemIsUserCheckable)
            if mode:
                item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in self.batch_targets else Qt.Unchecked)
            else:
                item.setData(Qt.CheckStateRole, None)
        self.rooms.blockSignals(False)
        self.rooms.setEnabled(not self.batch_panel.timer.isActive())
        if mode and not self.checked_rooms():
            item = self.rooms.currentItem()
            if item is not None:
                item.setCheckState(Qt.Checked)
        self.batch_panel.update_targets()

    def _room_selected(self, item, _previous):
        if item is not None and not self.mode.currentIndex():
            self.target.setCurrentIndex(self.target.findData(item.data(Qt.UserRole)))

    def _room_checked(self, item):
        if self.mode.currentIndex() and item.data(Qt.CheckStateRole) is not None:
            key = item.data(Qt.UserRole)
            if item.checkState() == Qt.Checked:
                self.batch_targets.add(key)
            else:
                self.batch_targets.discard(key)
        self.batch_panel.update_targets()

    def _select_target(self):
        row = self.rows.get(self.target.currentData())
        if row is not None:
            self.stack.setCurrentWidget(row)
            for index in range(self.rooms.count()):
                if self.rooms.item(index).data(Qt.UserRole) == self.target.currentData():
                    self.rooms.setCurrentRow(index)

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
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setAlignment(Qt.AlignTop)
        compose = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText('发送给所选主播的相同内容')
        compose.addWidget(self.edit, 1)
        self.send = QPushButton('发送到所选主播')
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
        self.console.mode.setEnabled(False)
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
        self.console.mode.setEnabled(True)
        self.update_targets()
