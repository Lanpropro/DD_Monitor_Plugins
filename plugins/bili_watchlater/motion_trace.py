"""Bounded measurements of real scrolling/preview work; no media identifiers."""
import time
from functools import wraps
from PySide6.QtCore import QEvent, QObject, QTimer


def measure(phase):
    def decorate(method):
        @wraps(method)
        def measured(owner, *args, **kwargs):
            page = owner if hasattr(owner, 'motion_trace') else getattr(owner, 'page', None)
            trace = getattr(page, 'motion_trace', None)
            active = trace is not None and trace.timer.isActive()
            started = time.perf_counter()
            try:
                return method(owner, *args, **kwargs)
            finally:
                if active:
                    trace.sample(phase, (time.perf_counter()-started)*1000)
        return measured
    return decorate


class MotionTrace(QObject):
    def __init__(self, page, *, areas=None, visible=None, parent=None, prefix='motion timing'):
        super().__init__(parent or page)
        self.page = page
        self.visible = visible or (lambda: page._visible)
        self.prefix = prefix
        self.windows = 0
        self.samples = {}
        self.last = 0
        self.gap = 0
        self.started = 0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        for scroll in areas if areas is not None else (page.list_scroll, page.grid_scroll):
            scroll.viewport().installEventFilter(self)
            scroll.verticalScrollBar().valueChanged.connect(lambda _: self.sample('scroll', 0))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel:
            self.sample('wheel', 0)
        if event.type() == QEvent.Paint and self.timer.isActive():
            self.samples.setdefault('paint', []).append(0)
        return False

    def sample(self, kind, elapsed):
        if self.windows >= 3 or not self.visible():
            return
        if not self.timer.isActive():
            self.started = self.last = time.perf_counter()
            self.gap = 0
            self.samples = {}
            self.timer.start()
        self.samples.setdefault(kind, []).append(elapsed)

    def tick(self):
        now = time.perf_counter()
        self.gap = max(self.gap, (now-self.last)*1000)
        self.last = now
        if now-self.started >= 5:
            self.timer.stop()
            self.windows += 1
            detail = ' '.join(f'{key}_n={len(values)} {key}_max_ms={max(values):.1f}'
                              for key, values in sorted(self.samples.items()))
            self.page._log(f'{self.prefix} window={self.windows} loop_gap_max_ms={self.gap:.1f} {detail}')
