"""Qt asynchronous downloads with bounded buffers and no raw network errors."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import getproxies, proxy_bypass

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest, QNetworkReply
from .configuration import DEFAULT_USER_AGENT


def current_proxy(url):
    parts = urlsplit(url)
    if proxy_bypass(parts.hostname):
        return ''
    proxies = getproxies()
    return proxies.get(parts.scheme) or proxies.get('all') or ''


def qt_proxy(address):
    if not address:
        return QNetworkProxy(QNetworkProxy.NoProxy)
    parts = urlsplit(address)
    kind = QNetworkProxy.Socks5Proxy if parts.scheme.startswith('socks') else QNetworkProxy.HttpProxy
    return QNetworkProxy(kind, parts.hostname or '', parts.port or 80,
                         unquote(parts.username or ''), unquote(parts.password or ''))


class ConnectionProbe(QObject):
    """One bounded HEAD request through the temporary core, without global routing."""
    finished = Signal(bool, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None

    def start(self, endpoint, target):
        self.cancel()
        self.manager.setProxy(qt_proxy(endpoint))
        request = QNetworkRequest(QUrl(target))
        request.setTransferTimeout(10000)
        reply = self.reply = self.manager.head(request)
        reply.setReadBufferSize(2048)
        reply.finished.connect(lambda: self._finished(reply))

    def _finished(self, reply):
        if self.reply is not reply:
            return
        code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
        reachable = reply.error() == QNetworkReply.NoError and 200 <= code < 400
        # Let the core's pipe deliver its sanitized connection error first.
        QTimer.singleShot(40, lambda: self._complete(reply, reachable, code))

    def _complete(self, reply, reachable, code):
        if self.reply is not reply:
            return
        self.reply = None
        reply.deleteLater()
        self.finished.emit(reachable, code)

    def cancel(self):
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()


class Download(QObject):
    finished = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None
        self.profile_title = self.content_disposition = ''
        self.stream = None
        self.partial = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)

    def start(self, url, destination, limit, user_agent=DEFAULT_USER_AGENT):
        self.cancel()
        self.profile_title = self.content_disposition = ''
        self.destination = Path(destination)
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.partial = self.destination.with_suffix('.download')
        self.stream = self.partial.open('wb')
        self.limit, self.received = limit, 0
        self.manager.setProxy(qt_proxy(current_proxy(url)))
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'User-Agent', user_agent.encode('ascii'))
        request.setTransferTimeout(15000)
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self.reply = self.manager.get(request)
        reply.setReadBufferSize(65536)
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply))
        self.timer.start(60000)

    def _read(self, reply):
        if self.reply is not reply:
            return
        data = bytes(reply.readAll())
        self.received += len(data)
        if self.received > self.limit:
            self._fail('下载内容超过大小上限')
            return
        try:
            self.stream.write(data)
        except OSError:
            self._fail('无法保存下载内容，请检查数据目录')

    def _finished(self, reply):
        if self.reply is not reply:
            return
        self._read(reply)
        if self.reply is not reply:
            return
        code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
        if reply.error() != QNetworkReply.NoError or not 200 <= code < 300:
            self._fail(f'下载失败（HTTP {code}），请检查代理或稍后重试')
            return
        self.timer.stop()
        self.profile_title = bytes(reply.rawHeader('profile-title')).decode('utf-8', errors='replace')
        self.content_disposition = bytes(reply.rawHeader('Content-Disposition')).decode('utf-8', errors='replace')
        self.reply = None
        reply.deleteLater()
        self.stream.close()
        self.stream = None
        try:
            self.partial.replace(self.destination)
        except OSError:
            self._fail('无法保存下载文件')
            return
        self.partial = None
        self.finished.emit(self.destination, '')

    def _timeout(self):
        self._fail('下载等待超过 60 秒，请检查网络或代理')

    def _fail(self, message):
        self.cancel()
        self.finished.emit(None, message)

    def cancel(self):
        self.timer.stop()
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.partial is not None:
            self.partial.unlink(missing_ok=True)
            self.partial = None
