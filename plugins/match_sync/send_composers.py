"""Send composers. Existing component ownership and lifecycle are preserved."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame

from .send_design import ACCESSORY_WIDTH, INPUT_HEIGHT
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
from PySide6.QtCore import QTimer, Qt
from ddm.widgets import Tile


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
        composer_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
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
        self.compose = QWidget(self)
        self.compose.setFixedHeight(40)
        compose = QHBoxLayout(self.compose)
        compose.setContentsMargins(0, 0, 16, 0)
        compose.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText('输入弹幕，Enter 发送')
        self.edit.textChanged.connect(lambda _text: self.update_targets())
        self.edit.returnPressed.connect(self.start)
        compose.addWidget(self.edit, 1)
        self.send = QPushButton('发送')
        self.send.setProperty('sendPrimary', True)
        self.send.clicked.connect(self.start)
        accessories = QWidget(self.compose)
        accessories.setFixedWidth(ACCESSORY_WIDTH)
        extras = QHBoxLayout(accessories)
        extras.setContentsMargins(0, 0, 0, 0)
        self.interactions = console.make_interactions(accessories)
        extras.addWidget(self.interactions)
        self.cancel = QPushButton('取消')
        self.cancel.clicked.connect(self.stop)
        self.cancel.hide()
        self.cancel.setFixedHeight(INPUT_HEIGHT)
        extras.addWidget(self.cancel)
        actions = QWidget(self.compose)
        actions.setFixedHeight(INPUT_HEIGHT)
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)
        action_layout.addWidget(accessories)
        action_layout.addWidget(self.send)
        self.compose._match_actions = actions
        compose.addWidget(actions, 0, Qt.AlignRight)
        layout.addWidget(self.compose)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)

    def update_targets(self):
        if not self.timer.isActive():
            count = len(self.console.checked_rooms())
            self.send.setText('发送')
            self.send.setEnabled(bool(count and self.edit.text().strip()))
        self.console.refresh_store_state()

    def start(self, *_args):
        self.begin(self.edit.text())

    def begin(self, text, event=None):
        if self.timer.isActive() or not text.strip():
            return
        self.pending = self.console.checked_rooms()
        if not self.pending:
            return
        self.text = text
        self.event = dict(event) if event is not None else {'text': text}
        self.send.setEnabled(False)
        self.cancel.show()
        self.interactions.hide()
        self.edit.setReadOnly(True)
        self.console.rooms.setEnabled(False)
        self.console.saved_texts.setEnabled(False)
        for key in self.pending:
            self.console.set_status(key, '待处理')
        self.timer.start()
        self.console.refresh_store_state()
        self.advance()

    def advance(self):
        if self.active is not None:
            key, row, editor = self.active
            if key in self.console.viewer.rows and (editor.isReadOnly() or not editor.isEnabled()):
                return
            self.console.set_status(key, ('✓' if not editor.text() and self.event.get('dm_type') != 1 else '')
                                    if key in self.console.viewer.rows else '已移除，已取消')
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
            if editor is None or not controls.can_repeat(self.event) or (self.event.get('dm_type') != 1 and len(self.text) > editor.maxLength()):
                self.console.set_status(key, '入口不可用或超出长度限制')
                row.shutdown()
                continue
            self.active = (key, row, editor)
            if not controls.repeat_message(self.event, key):
                self.console.set_status(key, '入口不可用或未开始发送')
                row.shutdown()
                self.active = None
                continue
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
        self.interactions.show()
        self.console.rooms.setEnabled(True)
        self.console.saved_texts.setEnabled(True)
        self.update_targets()
