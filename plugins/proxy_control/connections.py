"""Bounded, on-demand snapshots of Mihomo connections; never retain raw replies."""
import json
import re

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest


ERRORS = {'dns': '节点域名解析失败', 'tls': 'TLS 握手或证书失败',
          'authentication': '节点鉴权失败', 'dependency': '节点引用的代理不存在',
          'refused': '连接被拒绝', 'unreachable': '网络不可达', 'timeout': '连接超时',
          'connection': '节点连接失败', 'unknown': '内核未提供具体原因'}
MAX_SNAPSHOT = 512 * 1024


def connection_rows(data):
    records = data.get('connections')
    if not isinstance(records, list):
        raise ValueError('Invalid connections')
    rows = []
    for record in records[:100]:
        metadata = record.get('metadata', {})
        host = str(metadata.get('host') or metadata.get('destinationIP') or '')
        if not re.fullmatch(r'[A-Za-z0-9_.:\[\]-]{1,253}', host):
            host = '未知目标'
        port = str(metadata.get('destinationPort', ''))
        target = f'{host}:{port}' if port.isdigit() and len(port) <= 5 else host
        chain = ' → '.join(str(part)[:128] for part in record.get('chains', [])[:8])
        rule = str(record.get('rule', ''))[:128]
        payload = str(record.get('rulePayload', ''))[:128]
        upload, download = record.get('upload', 0), record.get('download', 0)
        if type(upload) is not int or type(download) is not int:
            upload = download = 0
        rows.append((target, str(metadata.get('network', ''))[:16].upper(),
                     chain or '—', f'↑ {max(0, upload) / 1024:.1f} / ↓ {max(0, download) / 1024:.1f} KiB',
                     f'{rule} · {payload}' if payload else rule))
    return rows, len(records)


class ConnectionMonitor(QObject):
    snapshot = Signal(object, int)
    status = Signal(str)

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.active = self.running = False
        self.reply = None
        self.buffer = bytearray()
        self.manager = QNetworkAccessManager(self)
        self.manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)

    def set_active(self, active):
        self.active = active
        self._sync()

    def set_running(self, running):
        self.running = running
        self.snapshot.emit([], 0)
        self._sync()

    def _sync(self):
        if self.active and self.running:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()
            reply, self.reply = self.reply, None
            if reply is not None:
                reply.abort()
                reply.deleteLater()
            self.buffer.clear()
            self.status.emit('代理未启动；下方保留本次会话事件' if not self.running else '连接监测已暂停')

    def refresh(self):
        if not self.active or not self.running or self.reply is not None:
            return
        request = QNetworkRequest(QUrl(f'http://127.0.0.1:{self.core.port}/connections'))
        request.setRawHeader(b'Authorization', ('Bearer ' + self.core.secret).encode())
        request.setTransferTimeout(2000)
        reply = self.reply = self.manager.get(request)
        reply.setReadBufferSize(65536)
        self.buffer.clear()
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply))

    def _read(self, reply):
        if self.reply is not reply:
            return
        self.buffer.extend(bytes(reply.readAll()))
        if len(self.buffer) > MAX_SNAPSHOT:
            self.set_active(False)
            self.snapshot.emit([], 0)
            self.status.emit('连接快照超过 512 KiB，已暂停自动刷新；点击刷新重试')

    def _finished(self, reply):
        self._read(reply)
        if self.reply is not reply:
            return
        self.reply = None
        try:
            if reply.error() != QNetworkReply.NoError:
                raise ValueError('Request failed')
            rows, total = connection_rows(json.loads(self.buffer))
            self.snapshot.emit(rows, total)
            self.status.emit(f'{total} 条活动连接' + ('，显示前 100 条' if total > 100 else '') + ' · 每 2 秒刷新')
        except (ValueError, TypeError, AttributeError):
            self.snapshot.emit([], 0)
            self.status.emit('读取连接失败，请检查代理是否仍在运行，或点击刷新重试')
        finally:
            self.buffer.clear()
            reply.deleteLater()
