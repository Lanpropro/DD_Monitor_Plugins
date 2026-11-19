"""Keep detached page requests alive until completion, then release their data."""
from PySide6.QtCore import QCoreApplication, QObject, Qt, Slot


def dispose_finished_request(worker):
    try:
        worker.wait()
        worker.cookies.clear()
        worker.deleteLater()
    except RuntimeError:
        pass  # A queued completion may follow Qt object destruction.


class RetiredPageRequests(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.pending = set()
        app.aboutToQuit.connect(self.drain)

    def retire(self, worker):
        worker.cancel()
        if worker.wait(100):
            dispose_finished_request(worker)
            return
        self.pending.add(worker)
        worker.finished.connect(self._finished, Qt.QueuedConnection)
        # Covers completion between wait() timing out and connecting finished.
        if worker.isFinished():
            self._complete(worker)

    @Slot()
    def _finished(self):
        self._complete(self.sender())

    def _complete(self, worker):
        if worker not in self.pending:
            return
        self.pending.remove(worker)
        dispose_finished_request(worker)

    @Slot()
    def drain(self):
        # The UI is already closing. Do not destroy a running QThread when the
        # application tears down; existing HTTP timeouts bound its I/O wait.
        for worker in tuple(self.pending):
            worker.cancel()
        for worker in tuple(self.pending):
            self._complete(worker)


_retirement = None


def retire_page_request(worker):
    global _retirement
    if _retirement is None:
        _retirement = RetiredPageRequests(QCoreApplication.instance())
    _retirement.retire(worker)
