"""Chat protocol ownership, signal forwarding and cancellation; no live connections."""
import argparse
import asyncio
from importlib import import_module
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QEvent, QObject, QTimer, Signal
    from shiboken6 import isValid
    clients = import_module(module.__package__ + '.chat_clients')
    media = import_module(module.__package__ + '.media')
    assert media.Chat is clients.Chat and media.PlatformChat is clients.PlatformChat
    ready = threading.Event()
    async def receive(chat):
        chat.loop, chat.task = asyncio.get_running_loop(), asyncio.current_task()
        ready.set()
        await asyncio.Future()
    chat = clients.Chat('fixture')
    with patch.object(clients.Chat, '_main', receive), patch.object(clients.danmaku, 'blivedm', object()):
        try:
            chat.start()
            assert ready.wait(2), 'Chat worker did not enter its receive loop'
            assert chat.thread.name == 'match-sync-chat-fixture' and chat.thread.daemon
        finally:
            chat.stop()
            chat.thread.join(2)
        assert chat.cancelled.is_set() and not chat.thread.is_alive()
    chat.events.deleteLater()

    class Client(QObject):
        status, message, finished = Signal(str), Signal(dict), Signal()
        def __init__(self, parent):
            super().__init__(parent)
            self.running, self.stopped, self.wait_ms = False, False, None
        def start(self):
            self.running = True
            self.status.emit('connected')
            self.message.emit({'text': 'fixture'})
        def stop(self):
            self.stopped = True
        def isRunning(self):
            return self.running
        def wait(self, ms):
            self.wait_ms, self.running = ms, False
            QTimer.singleShot(0, self.finished.emit)
    platform = SimpleNamespace(danmaku_client=lambda _key, parent: Client(parent))
    worker = clients.PlatformChat('fixture', platform, host)
    client = worker.client
    assert worker.parent() is host and client.parent() is worker
    statuses, messages = [], []
    worker.events.state.connect(statuses.append)
    worker.events.message.connect(messages.append)
    worker.start()
    assert statuses == ['connected'] and messages == [{'text': 'fixture'}]
    events = worker.events
    worker.stop()
    assert client.stopped and client.wait_ms == 2000 and worker.stopped
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(client) and not isValid(worker)
    events.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    return dict(passed=True, protocol_exports=True, plain_thread_cancelled=True,
                platform_signals_forwarded=True, original_parent_and_wait=True,
                finished_cleanup=True, real_connections=0, real_messages=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
