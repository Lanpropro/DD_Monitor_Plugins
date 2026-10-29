"""Room-bound host composers and comment actions for the merged chat panel."""
from PySide6.QtCore import QPointF
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from ddm.danmaku_actions import show_comment_menu
from ddm.widgets import DanmakuPanel, Tile


class ChatPanel(DanmakuPanel):
    def __init__(self, viewer):
        self.viewer = viewer
        super().__init__(viewer)
        self.targets = {}
        self.target = QComboBox()
        self.target.setAccessibleName('发送弹幕的直播间')
        self.target.currentIndexChanged.connect(self._select_target)
        header = QWidget(self)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(QLabel('发送至'))
        layout.addWidget(self.target, 1)
        close = QPushButton('×')
        close.setToolTip('关闭右侧弹幕栏')
        close.clicked.connect(lambda: viewer.chat_visible.setChecked(False))
        layout.addWidget(close)
        self.layout().insertWidget(1, header)

    def sync_rooms(self):
        manager = self.viewer.context.manager
        for key in tuple(self.targets):
            if key not in self.viewer.rows:
                tile = self.targets.pop(key)
                controls = getattr(tile, '_plugin_controls', None)
                if controls is not None:
                    controls.set_panel(None)
                manager.detach_tile(tile)
                tile.deleteLater()
        selected = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        for key, row in self.viewer.rows.items():
            if key not in self.targets:
                tile = Tile({'room_id': key, 'uname': row.label()}, self)
                tile.hide()
                self.targets[key] = tile
                manager.attach_tile(tile)
            self.target.addItem(row.label(), key)
        self.target.setCurrentIndex(max(0, self.target.findData(selected)))
        self.target.blockSignals(False)
        self._select_target()

    def _select_target(self):
        tile = self.targets.get(self.target.currentData())
        controls = getattr(tile, '_plugin_controls', None)
        previous = self._tile_controls
        if previous is not None and previous is not controls:
            previous.set_panel(None)
        if controls is not None:
            controls.set_panel(self)

    def add_event(self, event):
        super().add_event(event)
        # Keep metadata beside the host's bounded blocks, including after trimming.
        self._blocks[-1]['source_room'] = event.get('source_room', '')

    def open_actions(self, text, room_id, position):
        tile = self.targets.get(room_id)
        controls = getattr(tile, '_plugin_controls', None)
        show_comment_menu(self, text, controls, position)

    def _message_clicked(self, index, position):
        if not 0 <= index < len(self._blocks):
            return
        entry = self._blocks[index]
        self.body._action_block = self.body.document().findBlockByNumber(index)
        self.body.viewport().update()
        try:
            self.open_actions(entry['text'], entry.get('source_room', ''), position)
        finally:
            self.body._action_block = None
            self.body._hover_at(QPointF(self.body.viewport().mapFromGlobal(QCursor.pos())))

    def overlay_actions(self, _text, position):
        overlay = self.viewer.video_danmaku
        comment = overlay.comment_at(overlay.mapFromGlobal(position))
        if comment is not None:
            self.open_actions(getattr(comment, 'source_text', comment.message),
                              getattr(comment, 'source_room', ''), position)
