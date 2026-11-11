"""Own one loopback-only CPU translation process for the active session."""
import socket
import secrets
import time

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest

try:
    from .host_process import HostProcess
except ImportError:
    from host_process import HostProcess


def server_arguments(options, port, api_key=''):
    return ['-m', options.local_model, '--host', '127.0.0.1', '--port', str(port),
            '--alias', 'live-translate', '-ngl', '0', '-c', '1024', '-np', '1',
            '-t', str(options.threads), '-tb', str(options.threads), '-b', '64', '-ub', '32',
            *(['--api-key', api_key] if api_key else [])]


class LocalTranslation(QObject):
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.process.readAllStandardOutput)
        self.process.readyReadStandardError.connect(self.process.readAllStandardError)
        self.process.errorOccurred.connect(lambda _error: self._fail('本地翻译运行程序无法启动'))
        self.process.finished.connect(lambda _code, _status: self._fail('本地翻译进程已退出'))
        self.network = QNetworkAccessManager(self)
        self.network.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self.reply = None
        self.group = None
        self.active = False
        self.api_key = ''
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)

    def start(self, options):
        group = HostProcess(options.local_server, include_dlls=True)
        try:
            program = group.prepare()
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
        except Exception:
            group.cleanup()
            raise
        self.group = group
        self.url = f'http://127.0.0.1:{port}'
        self.deadline = time.monotonic() + 60
        self.api_key = secrets.token_urlsafe(24)
        self.active = True
        self.timer.start()
        self.process.setProgram(str(program))
        if group.directory is not None:
            self.process.setWorkingDirectory(str(group.directory))
        self.process.setArguments(server_arguments(options, port, self.api_key))
        self.process.start()

    def _poll(self):
        if time.monotonic() >= self.deadline:
            self._fail('本地翻译模型加载超时，请检查模型与运行环境')
        elif self.reply is None:
            request = QNetworkRequest(QUrl(self.url + '/health'))
            request.setRawHeader(b'Authorization', ('Bearer ' + self.api_key).encode('ascii'))
            request.setTransferTimeout(1000)
            reply = self.reply = self.network.get(request)
            reply.finished.connect(lambda: self._checked(reply))

    def _checked(self, reply):
        if self.reply is reply:
            self.reply = None
            if self.active and reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) == 200:
                self.timer.stop()
                self.ready.emit(self.url + '/v1')
        reply.deleteLater()

    def _fail(self, message):
        if self.active:
            self.stop()
            self.failed.emit(message)

    def stop(self):
        self.active = False
        self.timer.stop()
        if self.reply is not None:
            reply, self.reply = self.reply, None
            reply.abort()
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        group, self.group = self.group, None
        if group is not None:
            group.cleanup()
