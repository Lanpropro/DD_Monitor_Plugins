"""F1 Play's event view and main-feed controls."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)


PLAYBACK_STATUS = {
    'connecting': '正在连接主画面…',
    'buffering': '正在缓冲…',
    'playing': '正在播放',
    'frozen': '画面暂时停滞…',
    'error': '播放中断，请检查 HLS 地址后重试。',
}


class F1Workspace(QWidget):
    def __init__(self, create_player):
        super().__init__()
        self.create_player = create_player
        self.player = None

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
        body.addWidget(self.screen, 1)

        standings = QFrame()
        standings.setFrameShape(QFrame.StyledPanel)
        standings.setFixedWidth(156)
        standings_layout = QVBoxLayout(standings)
        standings_layout.addWidget(QLabel('即时排行'))
        standings_layout.addWidget(QLabel('尚未接入'))
        standings_layout.addStretch()
        body.addWidget(standings)
        layout.addLayout(body, 1)

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

        commentary = QFrame()
        commentary.setFrameShape(QFrame.StyledPanel)
        commentary_layout = QHBoxLayout(commentary)
        commentary_layout.addWidget(QLabel('二路解说'))
        commentary_layout.addWidget(QLabel('尚未接入'))
        commentary_layout.addStretch()
        layout.addWidget(commentary)

    def play(self):
        address = self.source.text().strip()
        url = QUrl(address, QUrl.StrictMode)
        if not url.isValid() or url.scheme() not in ('http', 'https') or not url.host():
            self.status.setText('请输入有效的 HTTP 或 HTTPS HLS 播放地址。')
            self.source.setFocus()
            return

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
            return
        self.mute_button.setEnabled(True)
        self.stop_button.setEnabled(True)

    def show_status(self, state):
        self.status.setText(PLAYBACK_STATUS.get(state, ''))

    def set_sound(self, enabled):
        self.mute_button.setText('关闭声音' if enabled else '开启声音')
        if self.player is not None:
            self.player.set_muted(not enabled)

    def stop(self):
        if self.player is not None:
            self.player.release()
            self.player = None
        self.screen.setCurrentIndex(0)
        self.status.clear()
        self.mute_button.setEnabled(False)
        self.stop_button.setEnabled(False)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
