"""F1 Play's race layout, using the shared Match Sync player and controls."""
from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ddm import theme

from .standings import StandingsPane


class F1Workspace(QWidget):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.match = None
        self.onboard = None
        self.setObjectName('F1Workspace')
        self.setAttribute(Qt.WA_StyledBackground)
        self.apply_backdrop()
        theme.signals().changed.connect(self.apply_backdrop)
        if context.workspace_background_changed is not None:
            context.workspace_background_changed.connect(self.apply_backdrop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel('F1 Play')
        title.setObjectName('SettingsTitle')
        header.addWidget(title)
        header.addStretch()
        self.rank_toggle = self.button(header, '排行', self.update_side, checkable=True)
        self.onboard_toggle = self.button(header, '车载视角', self.update_side, checkable=True)
        self.back_button = self.button(header, '返回画面墙', self.close)
        layout.addLayout(header)

        body = QHBoxLayout()
        self.screen = QVBoxLayout()
        self.unavailable = QLabel('请启用配套版本的比赛二路同步')
        self.unavailable.setAlignment(Qt.AlignCenter)
        self.screen.addWidget(self.unavailable)
        body.addLayout(self.screen, 3)

        self.side = QWidget()
        self.side.setMinimumWidth(260)
        self.side.setMaximumWidth(318)
        side_layout = QVBoxLayout(self.side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.standings = StandingsPane(self.picture_position)
        side_layout.addWidget(self.standings, 1)
        self.onboard_panel = QWidget()
        self.onboard_layout = QVBoxLayout(self.onboard_panel)
        self.onboard_layout.setContentsMargins(0, 0, 0, 0)
        onboard_header = QHBoxLayout()
        onboard_header.addWidget(QLabel('车载视角'), 1)
        self.promote = self.button(onboard_header, '切为主画面', self.swap_feeds)
        self.button(onboard_header, '关闭', lambda: self.onboard_toggle.setChecked(False))
        self.onboard_layout.addLayout(onboard_header)
        side_layout.addWidget(self.onboard_panel, 1)
        self.onboard_panel.hide()
        body.addWidget(self.side, 1)
        layout.addLayout(body, 1)

        tools = QHBoxLayout()
        self.console_button = self.button(tools, '解说控制台', lambda: self.match.toggle_console())
        tools.addStretch()
        self.overlay_button = self.button(tools, '显示弹幕', self.toggle_overlay, checkable=True)
        self.send_button = self.button(tools, '发弹幕', lambda: self.match.toggle_sender())
        layout.addLayout(tools)

        self.rank_toggle.setChecked(True)
        self.clock = QTimer(self)
        self.clock.setInterval(250)
        self.clock.timeout.connect(self.standings.update_picture)

    @staticmethod
    def button(layout, title, action, *, checkable=False):
        button = QPushButton(title)
        button.setObjectName('IconButton')
        button.setCheckable(checkable)
        if checkable:
            button.toggled.connect(action)
        else:
            button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def apply_backdrop(self):
        tint = theme.qcolor(theme.BG)
        opacity = self.context.workspace_background_mask() / 100
        self.setStyleSheet(f'QWidget#F1Workspace {{background: rgba({tint.red()}, {tint.green()}, {tint.blue()}, {opacity});}}')

    def make_viewer(self, *, onboard=False):
        return self.context.create_plugin_widget('match_sync', 'race_workspace_v2', self, onboard=onboard)

    def showEvent(self, event):
        super().showEvent(event)
        if self.match is None:
            self.match = self.make_viewer()
            if self.match is not None:
                self.match.picture_clock_changed.connect(self.standings.reset_anchor)
                self.match.overlay_changed.connect(self.reflect_overlay)
                self.screen.addWidget(self.match)
        available = self.match is not None
        self.unavailable.setVisible(not available)
        for button in (self.console_button, self.overlay_button, self.send_button, self.onboard_toggle):
            button.setEnabled(available)
        self.clock.start()

    def update_side(self, _checked):
        rank_open = self.rank_toggle.isChecked()
        onboard_open = self.onboard_toggle.isChecked()
        self.side.setVisible(rank_open or onboard_open)
        self.standings.setVisible(rank_open)
        self.onboard_panel.setVisible(onboard_open)
        if onboard_open and self.onboard is None:
            self.onboard = self.make_viewer(onboard=True)
            if self.onboard is not None:
                self.onboard_layout.addWidget(self.onboard, 1)
                self.onboard.picture_clock_changed.connect(self.update_promote)
        elif not onboard_open and self.onboard is not None:
            self.onboard.close()
            self.onboard_layout.removeWidget(self.onboard)
            self.onboard.deleteLater()
            self.onboard = None
        self.update_promote()
        self.standings.update_picture()

    def update_promote(self):
        self.promote.setEnabled(self.onboard is not None and self.onboard.main.currentData() is not None)

    def swap_feeds(self):
        first = self.match.main.currentData()
        second = self.onboard.main.currentData()
        if second is None or first == second or not self.match.show_room(second):
            return
        if first is None:
            self.onboard_toggle.setChecked(False)
        elif self.onboard.show_room(first):
            self.match.remove_room(first)

    def toggle_overlay(self, visible):
        self.match.set_overlay_visible(visible)

    def reflect_overlay(self, visible):
        with QSignalBlocker(self.overlay_button):
            self.overlay_button.setChecked(visible)
        self.overlay_button.setText('隐藏弹幕' if visible else '显示弹幕')

    def picture_position(self):
        return self.match.picture_position() if self.match is not None else None

    def closeEvent(self, event):
        self.clock.stop()
        self.onboard_toggle.setChecked(False)
        if self.match is not None:
            self.match.close()
            self.screen.removeWidget(self.match)
            self.match.deleteLater()
            self.match = None
        self.reflect_overlay(False)
        self.standings.reset_anchor()
        super().closeEvent(event)
