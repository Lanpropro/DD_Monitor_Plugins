"""Use the existing match picture, mixer and chat controls inside a race page."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QInputDialog, QLabel, QVBoxLayout

from ddm.plugin_types import Platform, RoomInfo

from . import source_control
from .room_input import parse_room
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
    split_presentation = False

    picture_clock_changed = Signal()
    overlay_changed = Signal(bool)

    def __init__(self, context, parent, address=None, muted=True, *, onboard=False):
        self.onboard = onboard
        self.empty_hint = None
        self.clock_room = None
        super().__init__(context, {})
        self.setParent(parent, Qt.Widget)
        self.feed = RaceFeed(address) if address is not None else None
        if self.feed is not None:
            self.send_console.excluded_rooms.add(self.feed.room_id)
            source_control.add_room(self, self.feed.room_id,
                                    {'alias': '比赛主画面', 'quality_auto': False, 'show_chat': False},
                                    platform=self.feed)
            row = self.rows[self.feed.room_id]
            row.audible.setChecked(not muted)
            row.remove_button.hide()
        else:
            self.picture.setProperty('roomDropOnly', True)
            self.picture.add_room_button.hide()
            self.empty_hint = QLabel('从左侧拖入车载卡片' if onboard else '从左侧拖入直播卡片开播')
            self.empty_hint.setAlignment(Qt.AlignCenter)
            self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.empty_hint.setStyleSheet('background: transparent;')
            QVBoxLayout(self.canvas).addWidget(self.empty_hint)
        if onboard:
            self.canvas.setMinimumSize(180, 128)
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

    def _main_changed(self, *_args):
        super()._main_changed(*_args)
        room_id = self.main.currentData()
        if self.empty_hint is not None:
            self.empty_hint.setVisible(room_id is None)
        if room_id != self.clock_room:
            self.clock_room = room_id
            self.picture_clock_changed.emit()

    def _add_dragged(self, room_id):
        try:
            room_id = parse_room(room_id, self.context.manager)
        except ValueError as error:
            if self.empty_hint is not None and not self.rows:
                self.empty_hint.setText(str(error))
            else:
                self.notice.setText(str(error))
            return False
        if not self.running and room_id in self.rows:
            self.select_main(room_id)
            self.start()
            return True
        previous = self.main.currentData()
        if not super()._add_dragged(room_id):
            return False
        if self.onboard:
            self.select_main(room_id)
            if previous is not None:
                self.remove_room(previous)
        self.start()
        return True

    def add_room(self, text, preferences=None):
        if self.onboard:
            preferences = {**(preferences or {}), 'audible': False, 'show_chat': False}
        return super().add_room(text, preferences)

    def save(self):
        if not self.onboard:
            super().save()

    def show_room(self, room_id):
        if room_id not in self.rows or not self.running:
            if not self._add_dragged(room_id):
                return False
        self.select_main(room_id)
        return True

    def set_settings_visible(self, visible):
        super().set_settings_visible(visible)
        self.restore_settings.hide()

    def toggle_console(self):
        self.set_settings_visible(self.settings_panel.isHidden())

    def _add_input(self):
        source, accepted = QInputDialog.getText(self, '添加解说', '直播间号或官方链接')
        if accepted and source.strip():
            self._add_dragged(source)

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
        if self.feed is None or room_id != self.feed.room_id:
            super().remove_room_with_undo(room_id)

    def remove_room(self, room_id):
        if self.feed is None or room_id != self.feed.room_id:
            super().remove_room(room_id)
