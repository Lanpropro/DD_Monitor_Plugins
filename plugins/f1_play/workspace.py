"""F1 Play's event view and main-feed controls."""
from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from .onboard import OnboardPane, hls_address
from .standings import StandingsPane


PLAYBACK_STATUS = {
    'connecting': '正在连接主画面…',
    'buffering': '正在缓冲…',
    'playing': '正在播放',
    'frozen': '画面暂时停滞…',
    'error': '播放中断，请检查 HLS 地址后重试。',
}


class F1Workspace(QWidget):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.create_player = context.create_workspace_player
        self.player = None
        self.match = None
        self.address = ''

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel('F1 Play')
        title.setObjectName('SettingsTitle')
        self.back_button = QPushButton('返回画面墙')
        self.back_button.setObjectName('IconButton')
        self.back_button.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch()
        self.rank_toggle = QPushButton('排行')
        self.rank_toggle.setObjectName('IconButton')
        self.rank_toggle.setCheckable(True)
        self.rank_toggle.setChecked(True)
        header.addWidget(self.rank_toggle)
        self.onboard_toggle = QPushButton('车载视角')
        self.onboard_toggle.setObjectName('IconButton')
        self.onboard_toggle.setCheckable(True)
        header.addWidget(self.onboard_toggle)
        header.addWidget(self.back_button)
        layout.addLayout(header)

        source = QHBoxLayout()
        source.addWidget(QLabel('比赛画面'))
        self.source = QLineEdit()
        self.source.setAccessibleName('比赛画面 HLS 地址')
        self.source.setPlaceholderText('粘贴 HLS 播放地址（m3u8）')
        self.source.returnPressed.connect(self.play)
        source.addWidget(self.source, 1)
        self.play_button = QPushButton('播放')
        self.play_button.setObjectName('PrimaryButton')
        self.play_button.clicked.connect(self.play)
        source.addWidget(self.play_button)
        layout.addLayout(source)

        body = QHBoxLayout()
        self.screen = QStackedWidget()
        self.screen.setMinimumSize(240, 140)
        self.screen.setStyleSheet('QStackedWidget { background: #0b0d10; }')
        idle = QLabel('等待播放')
        idle.setAlignment(Qt.AlignCenter)
        idle.setStyleSheet('color: #adb7c3; background: #0b0d10;')
        self.video = QWidget()
        self.video.setStyleSheet('background: #0b0d10;')
        self.screen.addWidget(idle)
        self.screen.addWidget(self.video)
        body.addWidget(self.screen, 3)

        self.side = QWidget()
        self.side.setMinimumWidth(260)
        self.side.setMaximumWidth(318)
        side_layout = QVBoxLayout(self.side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.standings = StandingsPane(self.picture_position)
        side_layout.addWidget(self.standings, 1)
        self.onboard = OnboardPane(self.create_player)
        self.onboard.hide()
        self.onboard.close_requested.connect(lambda: self.onboard_toggle.setChecked(False))
        self.onboard.promote_requested.connect(self.swap_feeds)
        side_layout.addWidget(self.onboard, 1)
        self.rank_toggle.toggled.connect(self.update_side)
        self.onboard_toggle.toggled.connect(self.update_side)
        body.addWidget(self.side, 1)
        layout.addLayout(body, 1)

        self.seek_bar = QSlider(Qt.Horizontal)
        self.seek_bar.setAccessibleName('主画面回放进度')
        self.seek_bar.setTracking(False)
        self.seek_bar.valueChanged.connect(self.seek_picture)
        self.seek_bar.hide()
        layout.addWidget(self.seek_bar)
        controls = QHBoxLayout()
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setAccessibleName('播放状态')
        controls.addWidget(self.status, 1)
        self.mute_button = QPushButton('开启声音')
        self.mute_button.setObjectName('IconButton')
        self.mute_button.setCheckable(True)
        self.mute_button.toggled.connect(self.set_sound)
        self.mute_button.setEnabled(False)
        controls.addWidget(self.mute_button)
        self.stop_button = QPushButton('停止')
        self.stop_button.setObjectName('IconButton')
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)

        tools = QHBoxLayout()
        self.console_button = QPushButton('解说控制台')
        self.console_button.setObjectName('IconButton')
        self.console_button.clicked.connect(self.toggle_console)
        tools.addWidget(self.console_button)
        tools.addStretch()
        self.overlay_button = QPushButton('显示弹幕')
        self.overlay_button.setObjectName('IconButton')
        self.overlay_button.setCheckable(True)
        self.overlay_button.toggled.connect(self.toggle_overlay)
        tools.addWidget(self.overlay_button)
        self.send_button = QPushButton('发弹幕')
        self.send_button.setObjectName('IconButton')
        self.send_button.clicked.connect(self.toggle_sender)
        tools.addWidget(self.send_button)
        layout.addLayout(tools)
        self.clock = QTimer(self)
        self.clock.setInterval(250)
        self.clock.timeout.connect(self.update_timeline)

    def play(self):
        address = hls_address(self.source.text())
        if not address:
            self.status.setText('请输入有效的 HTTP 或 HTTPS HLS 播放地址。')
            self.source.setFocus()
            return
        self.play_address(address)

    def play_address(self, address):
        if self.address != address or self.match is None:
            self.standings.reset_anchor()
        self.address = address
        if self.match is not None:
            self.screen.setCurrentWidget(self.match)
            self.match.change_picture(address)
            self.status.setText('比赛二路已接管播放')
            self.clock.start()
            self.mute_button.hide()
            self.stop_button.setEnabled(True)
            return True
        self.screen.setCurrentWidget(self.video)
        try:
            if self.player is None:
                self.player = self.create_player(self.video)
                self.player.stateChanged.connect(self.show_status)
                self.player.set_muted(not self.mute_button.isChecked())
            self.status.setText(PLAYBACK_STATUS['connecting'])
            self.player.play(address, profile='m3u_live')
        except (OSError, RuntimeError, ValueError):
            self.stop()
            self.status.setText('播放器启动失败，请重试。')
            return False
        self.mute_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.clock.start()
        return True

    def show_status(self, state):
        self.status.setText(PLAYBACK_STATUS.get(state, ''))

    def set_sound(self, enabled):
        self.mute_button.setText('关闭声音' if enabled else '开启声音')
        if self.player is not None:
            self.player.set_muted(not enabled)
        if self.match is not None:
            self.match.set_picture_muted(not enabled)

    def update_side(self):
        rank_open = self.rank_toggle.isChecked()
        onboard_open = self.onboard_toggle.isChecked()
        self.side.setVisible(rank_open or onboard_open)
        self.standings.setVisible(rank_open)
        self.onboard.setVisible(onboard_open)
        if not onboard_open:
            self.onboard.stop()
        self.standings.update_picture()

    def swap_feeds(self):
        if not self.address:
            self.onboard.status.setText('请先加入主画面')
            return
        first, second = self.address, self.onboard.address
        if second and self.play_address(second):
            self.source.setText(second)
            self.onboard.source.setText(first)
            self.onboard.play_address(first)

    def enable_match(self):
        address = self.address or hls_address(self.source.text())
        if not address:
            self.status.setText('先填写比赛主画面的 HLS 地址。')
            return False
        if self.match is None:
            factory = getattr(self.context, 'create_plugin_widget', None)
            match = factory('match_sync', 'race_workspace_v1', self.screen,
                            address=address, muted=not self.mute_button.isChecked()) if factory else None
            if match is None:
                self.status.setText('请安装并启用支持 F1 Play 的比赛二路同步候选包。')
                return False
            self.match = match
            match.picture_clock_changed.connect(self.standings.reset_anchor)
            match.overlay_changed.connect(self.reflect_overlay)
            self.screen.addWidget(match)
            self.release_player()
            self.standings.reset_anchor()
        if self.screen.currentWidget() is not self.match:
            self.play_address(address)
        return True

    def toggle_console(self):
        if self.enable_match():
            self.match.toggle_console()

    def toggle_sender(self):
        if self.enable_match():
            self.match.toggle_sender()

    def toggle_overlay(self, visible):
        if visible and not self.enable_match():
            with QSignalBlocker(self.overlay_button):
                self.overlay_button.setChecked(False)
            return
        self.reflect_overlay(visible)
        if self.match is not None:
            self.match.set_overlay_visible(visible)

    def reflect_overlay(self, visible):
        with QSignalBlocker(self.overlay_button):
            self.overlay_button.setChecked(visible)
        self.overlay_button.setText('隐藏弹幕' if visible else '显示弹幕')

    def picture_position(self):
        if self.match is not None:
            return self.match.picture_position()
        return self.player.playback_timing()[0] if self.player is not None else None

    def update_timeline(self):
        if self.player is not None:
            position, total = self.player.playback_timing()
            self.seek_bar.setVisible(total is not None and total >= 1)
            if self.seek_bar.isVisible() and not self.seek_bar.isSliderDown():
                with QSignalBlocker(self.seek_bar):
                    self.seek_bar.setMaximum(round(total * 10))
                    self.seek_bar.setValue(round((position or 0) * 10))
        else:
            self.seek_bar.hide()
        self.standings.update_picture()

    def seek_picture(self):
        if self.player is not None:
            self.player.seek_to(self.seek_bar.value() / 10)

    def release_player(self):
        if self.player is not None:
            self.player.release()
            self.player = None

    def stop(self):
        self.release_player()
        if self.match is not None and self.match.running:
            self.match.stop()
        self.clock.stop()
        self.address = ''
        self.seek_bar.hide()
        self.standings.reset_anchor()
        self.screen.setCurrentIndex(0)
        self.status.clear()
        self.mute_button.show()
        self.mute_button.setEnabled(False)
        self.stop_button.setEnabled(False)

    def closeEvent(self, event):
        self.stop()
        self.onboard_toggle.setChecked(False)
        if self.match is not None:
            self.match.close()
            self.screen.removeWidget(self.match)
            self.match.deleteLater()
            self.match = None
        self.overlay_button.setChecked(False)
        super().closeEvent(event)
