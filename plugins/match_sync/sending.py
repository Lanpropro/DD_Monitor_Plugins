"""Independent platform composers below the merged chat, one per live room."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ddm.widgets import Tile
from .panels import SettingsPanel


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
        self.collapsed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        heading = QHBoxLayout()
        heading.addWidget(QLabel('弹幕发送'), 1)
        self.minimize = QPushButton('最小化')
        self.minimize.setFixedWidth(68)
        self.minimize.setToolTip('收起发送控制台，保留草稿并扩展弹幕栏。')
        self.minimize.clicked.connect(lambda: self.set_collapsed(True))
        heading.addWidget(self.minimize)
        layout.addLayout(heading)
        self.restore = QPushButton('展开发送', viewer.panel.bar)
        self.restore.setFixedWidth(90)
        self.restore.clicked.connect(lambda: self.set_collapsed(False))
        viewer.panel.bar.layout().insertWidget(0, self.restore)
        self.restore.hide()
        target_bar = QHBoxLayout()
        self.target = QComboBox()
        self.target.setAccessibleName('发送弹幕的直播间')
        self.target.currentIndexChanged.connect(self._select_target)
        target_bar.addWidget(self.target, 1)
        self.batch = QPushButton('同文多发…')
        self.batch.clicked.connect(self.open_batch)
        target_bar.addWidget(self.batch)
        layout.addLayout(target_bar)
        self.empty = QLabel('加入直播间后，可在这里分别发送弹幕')
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.batch_dialog = None

    def sync_rooms(self):
        selected = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        for key in tuple(self.rows):
            if key not in self.viewer.rows:
                row = self.rows.pop(key)
                self.stack.removeWidget(row)
                row.shutdown()
        for key, room in self.viewer.rows.items():
            if key not in self.rows:
                self.rows[key] = SendRow(self.viewer, room)
                self.stack.addWidget(self.rows[key])
                room.alias.textChanged.connect(lambda _text: self.sync_rooms())
            self.rows[key].update_name(room)
            self.target.addItem(f'{room.label()} · {key}', key)
        self.target.setCurrentIndex(max(0, self.target.findData(selected)))
        self.target.blockSignals(False)
        self._select_target()
        self.empty.setVisible(not self.rows)
        self.batch.setEnabled(bool(self.rows))

    def open_batch(self):
        if self.batch_dialog is None:
            self.batch_dialog = BatchSend(self)
            self.batch_dialog.finished.connect(lambda _result: setattr(self, 'batch_dialog', None))
        self.batch_dialog.show()
        self.batch_dialog.raise_()

    def _select_target(self):
        row = self.rows.get(self.target.currentData())
        if row is not None:
            self.stack.setCurrentWidget(row)

    def sync_height(self):
        settings = self.viewer.settings_panel
        self.setVisible(not settings.isHidden() and not self.collapsed)
        self.restore.setVisible(not settings.isHidden() and self.collapsed)
        if not settings.isHidden():
            self.setFixedHeight(settings.height())

    def set_collapsed(self, collapsed):
        self.collapsed = collapsed
        self.sync_height()

    def hideEvent(self, event):
        if self.batch_dialog is not None:
            self.batch_dialog.reject()
        super().hideEvent(event)


class BatchSend(QDialog):
    """One explicit send per checked room; separate composers keep drafts intact."""
    def __init__(self, console):
        super().__init__(console)
        self.setWindowTitle('同文多发')
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.console = console
        self.active = None
        self.pending = []
        self.choices = {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('选择接收相同弹幕的主播；发送结果请查看平台提示。'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        rows = QVBoxLayout(body)
        for key, room in console.viewer.rows.items():
            choice = QCheckBox(f'{room.label()} · {key}')
            choice.setChecked(key == console.target.currentData())
            status = QLabel()
            rows.addWidget(choice)
            rows.addWidget(status)
            self.choices[key] = (choice, status)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText('发送给所选主播的相同内容')
        layout.addWidget(self.edit)
        self.send = QPushButton('发送到所选主播')
        self.send.clicked.connect(self.start)
        layout.addWidget(self.send)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)
        self.finished.connect(self.stop)
        self.resize(420, 320)

    def start(self):
        if self.timer.isActive() or not self.edit.text().strip():
            return
        self.pending = [key for key, (choice, _status) in self.choices.items() if choice.isChecked()]
        if not self.pending:
            return
        self.text = self.edit.text()
        self.send.setEnabled(False)
        self.edit.setReadOnly(True)
        for choice, status in self.choices.values():
            choice.setEnabled(False)
            status.clear()
        self.timer.start()
        self.advance()

    def advance(self):
        if self.active is not None:
            key, row, editor = self.active
            if key in self.console.viewer.rows and (editor.isReadOnly() or not editor.isEnabled()):
                return
            self.choices[key][1].setText('已处理，请查看平台提示' if key in self.console.viewer.rows
                                          else '直播间已移除，已取消')
            row.shutdown()
            self.active = None
        while self.pending:
            key = self.pending.pop(0)
            room = self.console.viewer.rows.get(key)
            if room is None:
                self.choices[key][1].setText('直播间已移除，未发送')
                continue
            row = SendRow(self.console.viewer, room)
            row.setParent(self)
            row.hide()
            controls = row.tile._plugin_controls
            editor = controls._repeat_editor()
            if editor is None or len(self.text) > editor.maxLength():
                self.choices[key][1].setText('发送入口不可用或内容超过长度限制')
                row.shutdown()
                continue
            self.active = (key, row, editor)
            controls.repeat_text(self.text, key)
            self.choices[key][1].setText('正在处理…')
            return
        self.stop()
        self.edit.setReadOnly(False)
        self.send.setEnabled(True)
        for choice, _status in self.choices.values():
            choice.setEnabled(True)

    def stop(self, *_args):
        self.timer.stop()
        self.pending.clear()
        if self.active is not None:
            self.active[1].shutdown()
            self.active = None
