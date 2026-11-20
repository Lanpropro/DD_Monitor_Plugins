"""Optional F1 tools embedded in the shared Match Sync workspace."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .standings import StandingsPane


class RaceTools(QFrame):
    def __init__(self, context, match):
        super().__init__(match.chat_pane)
        self.context = context
        self.match = match
        self.onboard = None
        self.clock_room = None
        self.setObjectName('F1RaceTools')
        self.setStyleSheet('QFrame#F1RaceTools { background: palette(base); border-radius: 8px; }')

        gear = match.match_settings_button
        toolbar = gear.parentWidget().layout()
        self.toggle = QPushButton('赛事工具')
        self.toggle.setObjectName('IconButton')
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.setVisible)
        toolbar.insertWidget(toolbar.indexOf(gear), self.toggle)

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.rank_toggle = QPushButton('排行')
        self.rank_toggle.setObjectName('IconButton')
        self.rank_toggle.setCheckable(True)
        self.rank_toggle.setChecked(True)
        header.addWidget(self.rank_toggle)
        self.onboard_toggle = QPushButton('车载视角')
        self.onboard_toggle.setObjectName('IconButton')
        self.onboard_toggle.setCheckable(True)
        self.onboard_toggle.toggled.connect(self.set_onboard_open)
        header.addWidget(self.onboard_toggle)
        header.addStretch()
        collapse = QPushButton('收起')
        collapse.setObjectName('IconButton')
        collapse.clicked.connect(lambda: self.toggle.setChecked(False))
        header.addWidget(collapse)
        layout.addLayout(header)

        self.standings = StandingsPane(self.picture_position)
        self.rank_toggle.toggled.connect(self.standings.setVisible)
        layout.addWidget(self.standings, 1)
        self.onboard_panel = QWidget()
        self.onboard_layout = QVBoxLayout(self.onboard_panel)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('车载视角'), 1)
        self.promote = QPushButton('切为主画面')
        self.promote.setObjectName('IconButton')
        self.promote.setEnabled(False)
        self.promote.clicked.connect(self.swap_feeds)
        bar.addWidget(self.promote)
        self.onboard_layout.addLayout(bar)
        self.onboard_panel.hide()
        layout.addWidget(self.onboard_panel, 1)

        self.clock = QTimer(self)
        self.clock.setInterval(250)
        self.clock.timeout.connect(self.update_timeline)
        match.finished.connect(self.close_for_workspace)
        self.hide()
        match.chat_pane.layout().insertWidget(0, self, 1)

    def showEvent(self, event):
        super().showEvent(event)
        self.update_timeline()
        self.clock.start()

    def set_onboard_open(self, opened):
        if opened:
            self.onboard = self.context.create_plugin_widget(
                'match_sync', 'race_workspace_v2', self.onboard_panel, onboard=True)
            if self.onboard is not None:
                self.onboard_layout.addWidget(self.onboard, 1)
                self.onboard.picture_clock_changed.connect(self.update_promote)
        elif self.onboard is not None:
            self.onboard.close()
            self.onboard_layout.removeWidget(self.onboard)
            self.onboard.deleteLater()
            self.onboard = None
        self.onboard_panel.setVisible(self.onboard is not None)
        self.update_promote()

    def update_promote(self):
        self.promote.setEnabled(self.onboard is not None and self.onboard.main.currentData() is not None)

    def swap_feeds(self):
        first = self.match.main.currentData()
        second = self.onboard.main.currentData()
        if second is None or first == second:
            return
        if second not in self.match.rows and not self.match.add_room(second):
            return
        self.match.select_main(second)
        if not self.match.running:
            self.match.toggle_running()
        if first is None:
            self.onboard_toggle.setChecked(False)
        elif self.onboard.show_room(first):
            self.match.remove_room(first)
        self.update_timeline()

    def picture_position(self):
        row = self.match.rows.get(self.match.main.currentData())
        return row.actual_position if row is not None else None

    def update_timeline(self):
        room_id = self.match.main.currentData()
        if room_id != self.clock_room:
            self.clock_room = room_id
            self.standings.reset_anchor()
        self.standings.update_picture()

    def close_for_workspace(self, _result):
        self.standings.reset_anchor()
        self.toggle.setChecked(False)

    def hideEvent(self, event):
        self.clock.stop()
        self.onboard_toggle.setChecked(False)
        super().hideEvent(event)
