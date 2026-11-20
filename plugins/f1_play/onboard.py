"""Optional independently closable native video surface for an existing HLS feed."""
from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


def hls_address(text):
    address = text.strip()
    url = QUrl(address, QUrl.StrictMode)
    if url.isValid() and url.scheme() in ('http', 'https') and url.host():
        return address
    return ''


class OnboardPane(QFrame):
    close_requested = Signal()
    promote_requested = Signal()

    def __init__(self, create_player):
        super().__init__()
        self.create_player = create_player
        self.player = None
        self.address = ''
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel('车载视角'), 1)
        close = QPushButton('关闭')
        close.setObjectName('IconButton')
        close.clicked.connect(self.close_requested.emit)
        header.addWidget(close)
        layout.addLayout(header)
        self.video = QWidget()
        self.video.setMinimumHeight(128)
        self.video.setStyleSheet('background: #0b0d10;')
        layout.addWidget(self.video, 1)
        source = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setAccessibleName('车载 HLS 播放地址')
        self.source.setPlaceholderText('粘贴可播放的车载 HLS')
        self.source.returnPressed.connect(self.play)
        source.addWidget(self.source, 1)
        play = QPushButton('播放')
        play.setObjectName('IconButton')
        play.clicked.connect(self.play)
        source.addWidget(play)
        layout.addLayout(source)
        controls = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        controls.addWidget(self.status, 1)
        self.sound = QPushButton('开启声音')
        self.sound.setObjectName('IconButton')
        self.sound.setCheckable(True)
        self.sound.toggled.connect(self.set_sound)
        self.sound.setEnabled(False)
        controls.addWidget(self.sound)
        self.promote = QPushButton('切为主画面')
        self.promote.setObjectName('IconButton')
        self.promote.setEnabled(False)
        self.promote.clicked.connect(self.promote_requested.emit)
        controls.addWidget(self.promote)
        layout.addLayout(controls)

    def play(self):
        address = hls_address(self.source.text())
        if not address:
            self.status.setText('请输入有效的 HLS 地址')
            return
        self.play_address(address)

    def play_address(self, address):
        try:
            if self.player is None:
                self.player = self.create_player(self.video)
                self.player.stateChanged.connect(self.show_status)
                self.player.set_muted(not self.sound.isChecked())
            self.player.play(address, profile='m3u_live')
        except (OSError, RuntimeError, ValueError):
            self.stop()
            self.status.setText('车载画面启动失败')
            return False
        self.address = address
        self.status.setText('正在连接…')
        self.sound.setEnabled(True)
        self.promote.setEnabled(True)
        return True

    def show_status(self, state):
        self.status.setText({'connecting': '正在连接…', 'buffering': '正在缓冲…',
                             'error': '播放中断'}.get(state, ''))

    def set_sound(self, enabled):
        self.sound.setText('关闭声音' if enabled else '开启声音')
        if self.player is not None:
            self.player.set_muted(not enabled)

    def stop(self):
        if self.player is not None:
            self.player.release()
            self.player = None
        self.address = ''
        self.status.clear()
        self.promote.setEnabled(False)
        self.sound.setChecked(False)
        self.sound.setEnabled(False)
