"""Use the existing match picture, mixer and chat controls inside a race page."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QInputDialog

from ddm.plugin_types import Platform, RoomInfo

from . import source_control
from .viewer import Viewer


class RaceFeed(Platform):
    kind = 'f1_play'
    label = 'F1 Play'
    room_id = 'f1_play:picture'

    def __init__(self, address):
        self.address = address

    def room_info(self, room_id):
        return RoomInfo(room_id, '比赛主画面', live=True, platform=self.kind)

    def play_url(self, room_id, quality=250):
        return self.address, quality, 'm3u_live', {}


class RaceViewer(Viewer):
    picture_clock_changed = Signal()
    overlay_changed = Signal(bool)

    def __init__(self, context, parent, address, muted):
        super().__init__(context, {})
        self.setParent(parent, Qt.Widget)
        self.feed = RaceFeed(address)
        self.send_console.excluded_rooms.add(self.feed.room_id)
        source_control.add_room(self, self.feed.room_id,
                                {'alias': '比赛主画面', 'quality_auto': False, 'show_chat': False},
                                platform=self.feed)
        row = self.rows[self.feed.room_id]
        row.audible.setChecked(not muted)
        row.remove_button.hide()
        self.input.hide()
        self.add_controls[1].setText('添加解说')
        self.add_controls[2].hide()
        self.chat_pane.hide()
        self.left_pane.layout().addWidget(self.send_console)
        self.send_console.inline_height = 152
        self.send_console.set_collapsed(True)
        self.set_settings_visible(False)
        self.settings_height = 220
        self.restore_settings.hide()
        self.picture.danmaku_button.setChecked(False)
        self.picture.danmaku_button.toggled.connect(self.overlay_changed.emit)
        if self.context.setting('automatic') is None:
            self.automatic.setChecked(False)
        self.main.currentIndexChanged.connect(lambda _index: self.picture_clock_changed.emit())

    def set_settings_visible(self, visible):
        super().set_settings_visible(visible)
        self.restore_settings.hide()

    def toggle_console(self):
        self.set_settings_visible(self.settings_panel.isHidden())

    def _add_input(self):
        source, accepted = QInputDialog.getText(self, '添加解说', '直播间号或官方链接')
        if accepted and source.strip():
            self.add_room(source)

    def set_sender_open(self, opened):
        self.send_console.set_collapsed(not opened)

    def toggle_sender(self):
        self.set_sender_open(self.send_console.isHidden())

    def set_overlay_visible(self, visible):
        self.picture.danmaku_button.setChecked(visible)

    def set_picture_muted(self, muted):
        self.rows[self.feed.room_id].audible.setChecked(not muted)

    def picture_position(self):
        row = self.rows.get(self.main.currentData())
        return row.actual_position if row is not None else None

    def start(self):
        if not self.running:
            self.toggle_running()

    def change_picture(self, address):
        if address != self.feed.address:
            self.stop()
            self.feed.address = address
            self.select_main(self.feed.room_id)
        self.start()

    def remove_room_with_undo(self, room_id):
        if room_id != self.feed.room_id:
            super().remove_room_with_undo(room_id)

    def remove_room(self, room_id):
        if room_id != self.feed.room_id:
            super().remove_room(room_id)
