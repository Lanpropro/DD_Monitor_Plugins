"""Own one on-demand Mihomo process and its authenticated loopback controller."""
import json
from pathlib import Path
import secrets
import socket
import tempfile
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, QUrl, Signal, QByteArray
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest, QNetworkReply

from .configuration import core_config


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Core(QObject):
    ready = Signal(str, object, str)
    failed = Signal(str)
    selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.directory = None
        self.reply = None
        self.manager = QNetworkAccessManager(self)
        self.manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)
        self.nodes = []
        self.current = ''

    def start(self, options, root, environment, profile=b''):
        self.stop()
        self.directory = tempfile.TemporaryDirectory(prefix='session-', dir=root)
        directory = Path(self.directory.name)
        mixed, self.port = free_port(), free_port()
        while self.port == mixed:
            self.port = free_port()
        self.endpoint = f'http://127.0.0.1:{mixed}'
        self.secret = secrets.token_urlsafe(32)
        config = core_config(options, mixed, self.port, self.secret)
        (directory / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        if options.mode == 'managed':
            (directory / 'nodes.txt').write_bytes(profile)
        process = self.process = QProcess(self)
        env = QProcessEnvironment()
        for key, value in environment.items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setStandardErrorFile(QProcess.nullDevice())
        process.errorOccurred.connect(lambda error: self._failure(process, '代理内核无法启动，请检查程序路径')
                                     if error == QProcess.FailedToStart else None)
        process.finished.connect(lambda _code, _status: self._failure(process, '代理内核已退出，已恢复原有代理'))
        self.deadline = time.monotonic() + 30
        process.start(options.core, ['-d', str(directory), '-f', str(directory / 'config.json')])
        self.timer.start()

    def _request(self, method, payload=None):
        request = QNetworkRequest(QUrl(f'http://127.0.0.1:{self.port}/proxies/DDM'))
        request.setRawHeader(b'Authorization', ('Bearer ' + self.secret).encode())
        request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        request.setTransferTimeout(2000)
        reply = self.manager.get(request) if method == 'GET' else self.manager.put(
            request, QByteArray(json.dumps(payload).encode()))
        reply.setReadBufferSize(1024 * 1024)
        return reply

    def _poll(self):
        if time.monotonic() >= self.deadline:
            self._failure(self.process, '内核未能载入有效节点，请检查订阅或节点格式')
            return
        if self.reply is not None:
            return
        reply = self.reply = self._request('GET')
        reply.finished.connect(lambda: self._polled(reply))

    def _polled(self, reply):
        if self.reply is not reply:
            return
        self.reply = None
        data = bytes(reply.readAll())
        okay = reply.error() == QNetworkReply.NoError
        reply.deleteLater()
        if not okay:
            return
        try:
            result = json.loads(data)
            nodes = result['all']
            # Mihomo supplies this direct placeholder when no provider nodes loaded.
            if nodes == ['COMPATIBLE']:
                return
            if not isinstance(nodes, list) or not nodes or len(nodes) > 4096 or any(
                    not isinstance(node, str) or len(node) > 512 for node in nodes):
                return
            self.nodes = nodes
            self.current = result.get('now', '')
            self.timer.stop()
            self.ready.emit(self.endpoint, nodes, self.current)
        except (ValueError, KeyError, TypeError):
            return

    def select(self, name):
        if self.process is None or name not in self.nodes or self.reply is not None:
            return
        reply = self.reply = self._request('PUT', {'name': name})
        reply.finished.connect(lambda: self._selected(reply, name))

    def _selected(self, reply, name):
        if self.reply is not reply:
            return
        self.reply = None
        okay = reply.error() == QNetworkReply.NoError
        reply.deleteLater()
        if okay:
            self.current = name
        self.selected.emit(name if okay else '')

    def _failure(self, process, message):
        if process is None or self.process is not process:
            return
        self.stop()
        self.failed.emit(message)

    def stop(self):
        self.timer.stop()
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        process, self.process = self.process, None
        if process is not None:
            if process.state() != QProcess.NotRunning:
                process.terminate()
                if not process.waitForFinished(500):
                    process.kill()
                    process.waitForFinished(1000)
            process.deleteLater()
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
        self.nodes = []
        self.current = ''
