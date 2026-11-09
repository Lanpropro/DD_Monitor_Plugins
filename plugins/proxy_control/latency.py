"""User-triggered latency checks through the core API, at most two in flight."""
from collections import deque
import json
import logging
from urllib.parse import quote, urlencode

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest, QNetworkReply
from .configuration import TEST_TARGETS


class Latency(QObject):
    result = Signal(str, object, str)
    running = Signal(bool)
    detail = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self.active = {}
        self.pending = deque()
        self.testing = False

    def start(self, port, secret, names, target=TEST_TARGETS['google'], timeout_ms=8000):
        self.stop()
        self.port, self.secret, self.target = port, secret, target
        self.timeout_ms = timeout_ms
        self.pending = deque(dict.fromkeys(names))
        self.testing = bool(self.pending)
        self.running.emit(self.testing)
        self._next()

    def _next(self):
        while self.pending and len(self.active) < 2:
            name = self.pending.popleft()
            query = urlencode({'url': self.target, 'timeout': self.timeout_ms})
            request = QNetworkRequest(QUrl(f'http://127.0.0.1:{self.port}/providers/proxies/subscription/'
                                          f'{quote(name, safe="")}/healthcheck?{query}'))
            request.setRawHeader(b'Authorization', ('Bearer ' + self.secret).encode())
            request.setTransferTimeout(self.timeout_ms + 2000)
            reply = self.manager.get(request)
            reply.setReadBufferSize(2048)
            self.active[reply] = name
            self.result.emit(name, None, 'testing')
            reply.finished.connect(lambda reply=reply: self._finished(reply))
        if self.testing and not self.pending and not self.active:
            self.testing = False
            self.running.emit(False)

    def _finished(self, reply):
        name = self.active.pop(reply, None)
        if name is None:
            return
        delay = None
        code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
        state = 'timeout' if reply.error() == QNetworkReply.TimeoutError or code == 504 else 'failed'
        if reply.error() == QNetworkReply.NoError:
            try:
                value = json.loads(bytes(reply.readAll())).get('delay')
                if type(value) is int and 0 <= value <= 60000:
                    delay, state = value, 'ready'
            except (ValueError, AttributeError):
                pass
        if state != 'ready':
            detail = ('测试超时' if state == 'timeout' else
                      {401: '内核鉴权失败', 404: '节点接口未找到', 503: '检测失败（503）'}.get(code, '测试失败'))
            self.detail.emit(name, detail)
            logging.getLogger('ddm.proxy_control').info('proxy latency error http=%s transport=%s',
                code, reply.error().value)
        reply.deleteLater()
        self.result.emit(name, delay, state)
        self._next()

    def stop(self):
        was_testing = self.testing
        active, self.active = self.active, {}
        self.pending.clear()
        for reply, name in active.items():
            reply.abort()
            reply.deleteLater()
            self.result.emit(name, None, 'idle')
        self.testing = False
        if was_testing:
            self.running.emit(False)
