"""Bounded real-runtime timings, with no account or media identifiers."""
import time
from PySide6.QtCore import QEvent, QObject, QTimer


class SettingsTrace(QObject):
    def __init__(self, page, prefix='settings timing'):
        super().__init__(page)
        self.page = page
        self.prefix = prefix
        self.started = time.perf_counter()
        self.last = self.started
        self.target = None
        self.done = False

    def mark(self, phase):
        now = time.perf_counter()
        self.page._log(f'{self.prefix} phase={phase} elapsed_ms={(now-self.started)*1000:.1f} stage_ms={(now-self.last)*1000:.1f}')
        self.last = now

    def watch(self, panel):
        self.target = panel
        panel.installEventFilter(self)
        QTimer.singleShot(1500, self.finish)

    def eventFilter(self, watched, event):
        if watched is self.target and event.type() == QEvent.Paint:
            self.finish('first_paint')
        return False

    def finish(self, phase='paint_timeout'):
        if self.done:
            return
        self.done = True
        self.mark(phase)
        self.deleteLater()
