"""Small prepared frames for continuous scrubbing; never decode video packets."""
from bisect import bisect_right
from collections import deque
import threading
import time

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage

from .diagnostics import StageTimings
from .replay_cache import CACHE_SECONDS
from .video import image_from_frame


class PreviewFrames:
    def __init__(self, byte_limit=32 * 1024 * 1024):
        self.byte_limit = byte_limit
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.cancelled = threading.Event()
        self.thread = None
        self.source = self.history = self.pending = None
        self.generation = 0
        self.next_pts = None
        self.frames = deque()
        self.byte_size = self.produced = self.dropped = 0
        self.thread_cpu_s = 0.
        self.timings = StageTimings()
        self.decoded_key = self.decoded_image = None

    def select(self, source):
        with self.lock:
            if source is self.source:
                return
            self.source = source
            self._reset(source.history if source is not None else None)

    def _reset(self, history):
        self.history = history
        self.generation += 1
        self.pending = self.next_pts = None
        self.frames.clear()
        self.byte_size = 0

    def submit(self, source, history, frame, pts):
        # Capture only lends a frame reference. It never waits for conversion.
        if not self.lock.acquire(blocking=False):
            self.dropped += 1
            return False
        try:
            if self.cancelled.is_set() or source is not self.source:
                return False
            if history is not self.history:
                self._reset(history)
            if self.next_pts is not None and pts + .0001 < self.next_pts:
                return False
            self.next_pts = pts if self.next_pts is None else self.next_pts
            self.next_pts += (int((pts + .0001 - self.next_pts) * 8) + 1) / 8
            self.dropped += int(self.pending is not None)
            self.pending = (self.generation, pts, frame)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='match-sync-preview', daemon=True)
                self.thread.start()
            self.wake.set()
            return True
        finally:
            self.lock.release()

    def _run(self):
        cpu = time.thread_time()
        while not self.cancelled.is_set():
            self.wake.wait(1)
            self.wake.clear()
            if self.cancelled.is_set():
                break
            with self.lock:
                work, self.pending = self.pending, None
            if work is None:
                continue
            generation, pts, frame = work
            work_started = time.perf_counter()
            image = image_from_frame(frame, self.timings, width=240)
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.WriteOnly)
            started = time.perf_counter()
            saved = image.save(buffer, 'JPEG', 60)
            buffer.close()
            self.timings.record('preview_jpeg_encode', time.perf_counter() - started)
            encoded = bytes(data)
            with self.lock:
                if saved and generation == self.generation and not self.cancelled.is_set():
                    self.frames.append((pts, encoded))
                    self.byte_size += len(encoded)
                    self.produced += 1
                    while self.frames and (self.byte_size > self.byte_limit
                                           or pts - self.frames[0][0] > CACHE_SECONDS):
                        self.byte_size -= len(self.frames.popleft()[1])
            self.thread_cpu_s = time.thread_time() - cpu
            work = frame = image = data = encoded = buffer = None
            self.cancelled.wait(max(0, .125 - (time.perf_counter() - work_started)))

    def get(self, history, frame):
        if not self.lock.acquire(blocking=False):
            return None
        try:
            if history is not self.history:
                return None
            index = bisect_right(self.frames, frame[1], key=lambda item: item[0]) - 1
            if index < 0:
                return None
            pts, encoded = self.frames[index]
            if frame[1] - pts > .25:
                return None
            key = self.generation, pts
        finally:
            self.lock.release()
        if key != self.decoded_key:
            started = time.perf_counter()
            self.decoded_image = QImage.fromData(encoded, 'JPEG')
            self.decoded_key = key
            self.timings.record('preview_jpeg_read', time.perf_counter() - started)
        return frame[0] - frame[1] + pts, self.decoded_image

    def snapshot(self, now):
        result = dict(worker_pipeline=self.timings.stats(now),
                      thread_cpu_total_ms=round(self.thread_cpu_s * 1000, 3))
        if not self.lock.acquire(blocking=False):
            return dict(result, worker_busy=True)
        try:
            return dict(result, prepared_count=len(self.frames), prepared_bytes=self.byte_size,
                        prepared_bounds=(self.frames[0][0], self.frames[-1][0]) if self.frames else None,
                        produced=self.produced, dropped=self.dropped, queued=int(self.pending is not None),
                        byte_limit=self.byte_limit, sample_fps=8, width=240)
        finally:
            self.lock.release()

    def stop(self):
        self.cancelled.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(1)
        with self.lock:
            self.source = None
            self._reset(None)
        self.decoded_key = self.decoded_image = None
