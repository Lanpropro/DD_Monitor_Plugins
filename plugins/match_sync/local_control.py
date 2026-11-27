"""Newline-delimited JSON controls on a same-user Qt local socket."""
import json

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer


class LocalControlServer(QLocalServer):
    def __init__(self, parent, dispatch):
        super().__init__(parent)
        self.dispatch = dispatch
        self.setSocketOptions(QLocalServer.UserAccessOption)
        self.newConnection.connect(self.accept)

    def accept(self):
        while self.hasPendingConnections():
            peer = self.nextPendingConnection()
            peer.setReadBufferSize(1025)
            peer.disconnected.connect(peer.deleteLater)
            QTimer.singleShot(3000, peer, peer.abort)
            peer.readyRead.connect(lambda p=peer: self.respond(p))
            if peer.bytesAvailable():
                self.respond(peer)

    def respond(self, peer):
        if not peer.canReadLine():
            if peer.bytesAvailable() >= 1024:
                peer.abort()
            return
        try:
            request = json.loads(bytes(peer.readLine(1024)))
            reply = {'state': self.dispatch(request)}
        except (ValueError, TypeError, KeyError, AttributeError):
            reply = {'error': 'Invalid control request'}
        peer.write(json.dumps(reply).encode('ascii') + b'\n')
        peer.disconnectFromServer()
