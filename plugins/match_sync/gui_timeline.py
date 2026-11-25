"""Timing-only GUI evidence for stalls too short for the stack sampler."""
from collections import deque
from contextlib import contextmanager
import time


class GuiTimeline:
    def __init__(self):
        self.events = deque(maxlen=2048)
        self.gaps = deque(maxlen=240)
        self.previous_paint = None
        self.wall_offset = time.time() - time.perf_counter()

    def point(self, label, at, value=None):
        self.events.append((label, at, at, None, value))

    @staticmethod
    def begin():
        return time.perf_counter(), time.thread_time()

    def end(self, label, start, value=None):
        started, cpu = start
        self.events.append((label, started, time.perf_counter(), time.thread_time() - cpu, value))

    @contextmanager
    def span(self, label):
        start = self.begin()
        try:
            yield
        finally:
            self.end(label, start)

    def paint(self, started, painted, frame_key, cpu_started):
        if frame_key is None:
            self.previous_paint = None
            return
        previous = self.previous_paint
        if previous is not None and frame_key == previous[1]:
            return
        self.previous_paint = (started, frame_key, cpu_started)
        self.events.append(('picture.paint', started, painted, time.thread_time() - cpu_started, None))
        if previous is not None and started - previous[0] >= .030:
            self.gaps.append((previous[0], started, cpu_started - previous[2]))

    def sample(self, since, until):
        gaps = [gap for gap in tuple(self.gaps) if since < gap[1] <= until]
        if not gaps:
            return None
        # A small number of complete intervals per second preserves ordering and overlap.
        selected = sorted(sorted(gaps, key=lambda gap: gap[1] - gap[0], reverse=True)[:6])
        events = tuple(self.events)
        windows = []
        for start, end, cpu in selected:
            matches = [item for item in events if item[1] <= end and item[2] >= start]
            matches.sort(key=lambda item: item[1])
            windows.append(dict(
                start_unix_s=round(start + self.wall_offset, 6),
                gap_ms=round((end - start) * 1000, 3),
                gap_thread_cpu_ms=round(cpu * 1000, 3),
                truncated=bool(events and start < events[0][1]) or len(matches) > 96,
                events=[(label, round((at - start) * 1000, 3), round((done - at) * 1000, 3),
                         round(cpu * 1000, 3) if cpu is not None else None, value)
                        for label, at, done, cpu, value in matches[:96]]))
        return dict(gaps=len(gaps), samples=windows)
