"""Own one on-demand Mihomo process and its authenticated loopback controller."""
import json
from pathlib import Path
import secrets
import socket
import tempfile
import time
import logging

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, QUrl, Signal, QByteArray
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest, QNetworkReply, QTcpSocket

from .configuration import core_config
from .profile_settings import merge_resolution


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def error_category(line):
    text = line.casefold()
    if not any(marker in text for marker in ('error', 'failed', 'failure')):
        return ''
    for kind, markers in (
        ('dns', ('dns resolve', 'dns query', 'all dns', 'lookup', "couldn't find ip", 'no such host',
                 'ipv6 disabled', 'ip version error')),
        ('tls', ('tls', 'x509', 'certificate', 'handshake')),
        ('authentication', ('authentication', 'password', 'unauthorized')),
        ('dependency', ('dialer-proxy', 'proxy not found', 'proxy not exist')),
        ('refused', ('connection refused', 'actively refused')),
        ('unreachable', ('network is unreachable', 'no route to host')),
        ('timeout', ('timeout', 'timed out', 'deadline exceeded')),
    ):
        if any(marker in text for marker in markers):
            return kind
    return 'connection'


def dns_reason(line):
    text = line.casefold()
    for reason, markers in (
        ('ipv6-disabled', ('ipv6 disabled',)),
        ('ip-version', ('ip version error',)),
        ('nxdomain', ('nxdomain', 'no such host', 'name does not exist')),
        ('timeout', ('timeout', 'timed out', 'deadline exceeded')),
        ('refused', ('refused',)),
        ('server-failed', ('servfail', 'server misbehaving', 'server failure')),
        ('no-address', ("couldn't find ip", 'no ip address', 'empty answer')),
    ):
        if any(marker in text for marker in markers):
            return reason
    return 'unknown'


class Core(QObject):
    ready = Signal(str, object, str)
    failed = Signal(str)
    selected = Signal(str)
    modeChanged = Signal(str)
    diagnostic = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.directory = None
        self.reply = None
        self.mode_reply = None
        self.listener_probe = None
        self._listener_ready = False
        self.manager = QNetworkAccessManager(self)
        self.manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)
        self.nodes = []
        self.types = {}
        self._types_loaded = False
        self._global_selected = False
        self.current = ''

    def start(self, options, root, environment, profile=b'', routing=None):
        self.stop()
        self.preferred_node = options.node
        self.directory = tempfile.TemporaryDirectory(prefix='session-', dir=root)
        directory = Path(self.directory.name)
        mixed, self.port = free_port(), free_port()
        while self.port == mixed:
            self.port = free_port()
        self.endpoint = f'http://127.0.0.1:{mixed}'
        self.mixed_port = mixed
        self.secret = secrets.token_urlsafe(32)
        config = core_config(mixed, self.port, self.secret, routing or options.routing)
        dns_fields, hosts = merge_resolution(config, profile)
        logging.getLogger('ddm.proxy_control').info('proxy resolution profile dns_fields=%d hosts=%d', dns_fields, hosts)
        (directory / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        (directory / 'nodes.txt').write_bytes(profile)
        process = self.process = QProcess(self)
        env = QProcessEnvironment()
        for key, value in environment.items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        self._output = b''
        self.last_diagnostic = ''
        self.last_dns_reason = ''
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._read_output(process))
        process.errorOccurred.connect(lambda error: self._failure(process, '代理内核无法启动，请检查程序路径')
                                     if error == QProcess.FailedToStart else None)
        process.finished.connect(lambda _code, _status: self._failure(process, '代理内核已退出，已恢复原有代理'))
        self.deadline = time.monotonic() + 30
        process.start(options.core, ['-d', str(directory), '-f', str(directory / 'config.json')])
        self.timer.start()

    def _read_output(self, process):
        if self.process is not process:
            return
        data = self._output + bytes(process.readAllStandardOutput())
        lines = data.split(b'\n')
        self._output = lines.pop()[-8192:]
        for line in lines:
            decoded = line.decode('utf-8', errors='replace')
            kind = error_category(decoded)
            if kind == 'dns':
                reason = dns_reason(decoded)
                if reason != self.last_dns_reason:
                    self.last_dns_reason = reason
                    logging.getLogger('ddm.proxy_control').info('proxy dns diagnostic reason=%s', reason)
            if kind and kind != self.last_diagnostic:
                self.last_diagnostic = kind
                self.diagnostic.emit(kind)
                logging.getLogger('ddm.proxy_control').info('proxy core diagnostic kind=%s', kind)

    def _request(self, method, payload=None, path='/proxies/DDM'):
        request = QNetworkRequest(QUrl(f'http://127.0.0.1:{self.port}{path}'))
        request.setRawHeader(b'Authorization', ('Bearer ' + self.secret).encode())
        request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        request.setTransferTimeout(2000)
        if method == 'GET':
            reply = self.manager.get(request)
        elif method == 'PATCH':
            reply = self.manager.sendCustomRequest(request, b'PATCH', QByteArray(json.dumps(payload).encode()))
        else:
            reply = self.manager.put(request, QByteArray(json.dumps(payload).encode()))
        reply.setReadBufferSize(4 * 1024 * 1024)
        return reply

    def _poll(self):
        if time.monotonic() >= self.deadline:
            self._failure(self.process, '内核未能载入有效节点，请检查订阅或节点格式')
            return
        if self.reply is not None:
            return
        if not self._listener_ready:
            if self.listener_probe is None:
                probe = self.listener_probe = QTcpSocket(self)
                probe.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
                probe.connected.connect(lambda: self._listener_checked(probe, True))
                probe.errorOccurred.connect(lambda _error: self._listener_checked(probe, False))
                probe.connectToHost('127.0.0.1', self.mixed_port)
            return
        if self._types_loaded and not self._global_selected:
            reply = self.reply = self._request('PUT', {'name': 'DDM'}, path='/proxies/GLOBAL')
            reply.finished.connect(lambda: self._global_ready(reply))
            return
        path = '/proxies/DDM' if self._types_loaded else '/providers/proxies/subscription'
        reply = self.reply = self._request('GET', path=path)
        reply.finished.connect(lambda: self._polled(reply))

    def _listener_checked(self, probe, ready):
        if self.listener_probe is not probe:
            return
        self.listener_probe = None
        self._listener_ready = ready
        probe.abort()
        probe.deleteLater()

    def _global_ready(self, reply):
        if self.reply is not reply:
            return
        self.reply = None
        self._global_selected = reply.error() == QNetworkReply.NoError
        reply.deleteLater()

    def set_mode(self, mode):
        if self.process is None or self.mode_reply is not None:
            return False
        reply = self.mode_reply = self._request('PATCH', {'mode': mode}, path='/configs')
        reply.finished.connect(lambda: self._mode_changed(reply, mode))
        return True

    def _mode_changed(self, reply, mode):
        if self.mode_reply is not reply:
            return
        self.mode_reply = None
        okay = reply.error() == QNetworkReply.NoError
        reply.deleteLater()
        self.modeChanged.emit(mode if okay else '')

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
            if not self._types_loaded:
                records = result['proxies']
                if not isinstance(records, list) or not records or len(records) > 4096:
                    return
                self.types = {record['name']: record.get('type', '代理节点') for record in records}
                self._types_loaded = True
                return
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
            if self.preferred_node in nodes and self.preferred_node != self.current:
                pending = self.reply = self._request('PUT', {'name': self.preferred_node})
                pending.finished.connect(lambda: self._initial_selected(pending))
            else:
                self.ready.emit(self.endpoint, nodes, self.current)
        except (ValueError, KeyError, TypeError):
            return

    def _initial_selected(self, reply):
        if self.reply is not reply:
            return
        self.reply = None
        okay = reply.error() == QNetworkReply.NoError
        reply.deleteLater()
        if not okay:
            self._failure(self.process, '无法选择预设节点，未启用插件代理')
            return
        self.current = self.preferred_node
        self.ready.emit(self.endpoint, self.nodes, self.current)

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
        probe, self.listener_probe = self.listener_probe, None
        if probe is not None:
            probe.abort()
            probe.deleteLater()
        self._listener_ready = False
        reply, self.mode_reply = self.mode_reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
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
        self.types = {}
        self._types_loaded = False
        self._global_selected = False
        self.current = ''
