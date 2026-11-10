"""Comparison cards and secondary picture presentation."""
from __future__ import annotations

from .canvas import Canvas
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from PySide6.QtCore import QRectF
from ddm import theme


class ComparisonPanel(QFrame):
    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.cards = {}
        self.shown_rooms = ()
        self.setObjectName("MatchSyncCompare")
        self.setMinimumHeight(185)
        self.setMaximumHeight(300)
        self.setStyleSheet(f"""
            #MatchSyncCompare {{ background: {theme.CONTENT}; border: none;
                border-radius: {theme.RADIUS_MD}px; }}
            #MatchSyncCompare QLabel, #MatchSyncCompare QCheckBox {{ color: {theme.TEXT1}; }}
            #MatchSyncCompare QScrollArea {{ border: none; background: transparent; }}
        """)
        heading = QHBoxLayout()
        self.heading_label = QLabel()
        heading.addWidget(self.heading_label)
        heading.addStretch()
        self.target = QComboBox()
        self.target.setMinimumWidth(150)
        self.target.currentIndexChanged.connect(lambda: viewer.render())
        heading.addWidget(self.target)
        self.zoom = QCheckBox("放大框选区域")
        self.zoom.setChecked(True)
        heading.addWidget(self.zoom)
        self.clock_button = QPushButton('计时器对齐')
        self.clock_button.setToolTip('框选同一游戏计时器，核对翻秒画面后计算偏移')
        self.clock_button.clicked.connect(lambda: self.open_clock())
        heading.addWidget(self.clock_button)
        self.close_button = QPushButton("收起对照")
        self.close_button.setObjectName("ChipButton")
        self.close_button.setToolTip("隐藏对照画面，保留已调整的偏移并继续播放。")
        self.close_button.clicked.connect(lambda: viewer.compare.setChecked(False))
        heading.addWidget(self.close_button)
        body = QWidget()
        self.cards_layout = QHBoxLayout(body)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addLayout(heading)
        layout.addWidget(scroll, 1)
        self.hide()

    def open_clock(self):
        from .clock_dialog import open_clock_dialog
        open_clock_dialog(self.viewer)

    def rooms_to_show(self):
        reference, rows = self.viewer.alignment.reference, self.viewer.rows
        others = tuple(key for key in rows if key != reference)
        selected = self.target.currentData()
        secondary = others if len(rows) <= 4 else ((selected,) if selected in others else others[:1])
        return tuple(key for key in (reference, *secondary) if key in rows)

    def render(self, clock, shifts):
        rows = self.viewer.rows
        if tuple(self.cards) != tuple(rows):
            for card, *_ in self.cards.values():
                self.cards_layout.removeWidget(card)
                card.deleteLater()
            self.cards.clear()
            self.shown_rooms = ()
            for room_id in rows:
                card = QWidget()
                name = QLabel()
                canvas = Canvas()
                canvas.transparent_background = self.viewer.skin_visible
                canvas.setMinimumSize(200, 110)
                state = QLabel()
                state.setWordWrap(True)
                heading = QHBoxLayout()
                heading.addWidget(name, 1)
                layout = QVBoxLayout(card)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.addLayout(heading)
                layout.addWidget(canvas, 1)
                layout.addWidget(state)
                card.hide()
                self.cards[room_id] = (card, name, canvas, state)
        reference = self.viewer.alignment.reference
        selected = self.target.currentData()
        choices = [(key, row.label()) for key, row in rows.items() if key != reference]
        if choices != [(self.target.itemData(i), self.target.itemText(i))
                       for i in range(self.target.count())]:
            self.target.blockSignals(True)
            self.target.clear()
            for key, label in choices:
                self.target.addItem(label, key)
            self.target.setCurrentIndex(max(0, self.target.findData(selected)))
            self.target.blockSignals(False)
        self.target.setVisible(len(rows) > 4)
        self.heading_label.setText("偏移对照 · 全部直播间" if len(rows) <= 4 else "偏移对照 · 主画面与选中直播间")
        shown = self.rooms_to_show()
        if shown != self.shown_rooms:
            for card, *_ in self.cards.values():
                self.cards_layout.removeWidget(card)
                card.hide()
            for key in shown:
                card = self.cards[key][0]
                self.cards_layout.addWidget(card, 1)
                card.show()
            self.shown_rooms = shown
        for room_id, row in rows.items():
            if room_id not in shown:
                continue
            _card, name, canvas, state = self.cards[room_id]
            name.setText(f"{'主画面' if room_id == reference else '对照'} · {row.label()}")
            style = f"color: {row.color};"
            if name.styleSheet() != style:
                name.setStyleSheet(style)
            crop, zoom = QRectF(*row.crop), self.zoom.isChecked()
            repaint = canvas.crop != crop or canvas.zoom_crop != zoom
            canvas.crop, canvas.zoom_crop = crop, zoom
            if not row.paused:
                if room_id == reference:
                    canvas.preview_only = False
                    canvas.preview_shift = None
                    if self.viewer.canvas.frame is not None:
                        canvas.set_frame(self.viewer.canvas.frame)
                    waiting = self.viewer.canvas.waiting
                else:
                    target = clock + shifts[room_id]
                    bounds = row.decoder.history.bounds_at(target) if row.decoder else None
                    canvas.preview_only = False
                    if (self.viewer.alignment.automatic and row.match_confirmed and row.awaiting_confirmation and bounds is not None
                            and not bounds[0] <= target <= bounds[1] - .05):
                        latest = row.decoder.history.latest()
                        preview_bounds = row.decoder.history.bounds_at(latest[0]) if latest else None
                        if preview_bounds and preview_bounds[1] - preview_bounds[0] > .1:
                            preview = clock + canvas.preview_shift if canvas.preview_shift is not None else None
                            if preview is None or not preview_bounds[0] <= preview <= preview_bounds[1] - .05:
                                reserve = min(self.viewer.safety_buffer.target, preview_bounds[1] - preview_bounds[0] - .05)
                                canvas.preview_shift = preview_bounds[1] - reserve - clock
                            target = clock + canvas.preview_shift
                            canvas.preview_only = True
                    if not canvas.preview_only:
                        canvas.preview_shift = None
                    frame = canvas.show_at(row.decoder, target) if row.decoder else None
                    waiting = canvas.playback_waiting(row.decoder, target, frame, self.viewer.sync_waiting)
                repaint |= canvas.waiting != waiting
                canvas.waiting = waiting
            row.actual_position = canvas.frame_key
            if repaint:
                canvas._sync_opaque_paint()
                canvas.update()
            relative = shifts[room_id] - shifts.get(reference, 0)
            position = ("主画面基准" if room_id == reference else "与主画面偏移相同" if abs(relative) < .05 else
                        f"相对主画面{'提前' if relative >= 0 else '延后'} {abs(relative):.1f} 秒")
            if room_id != reference:
                if room_id in self.viewer.independent_shifts or (self.viewer.alignment.automatic and not row.match_confirmed):
                    position = "画面时间差尚未确认"
                elif not self.viewer.alignment.automatic:
                    position = f"设置偏移 {row.delay.value() - self.viewer.rows[reference].delay.value():+.2f} 秒"
            if canvas.preview_only:
                position = "同步目标超出当前缓存；正在独立预览，尚未与主画面对齐"
                playback = "已暂停" if row.paused else "正在准备预览画面" if canvas.waiting else "缓存预览播放中（本路静音）"
            else:
                playback = "已暂停" if row.paused else "等待播放缓存" if canvas.waiting else row.playback_status()
            text = f"{position} · {playback}"
            if state.text() != text:
                state.setText(text)
