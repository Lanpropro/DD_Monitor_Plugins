"""Copy cached main video and encode one aligned AAC track per room."""
from fractions import Fraction
from pathlib import Path
import shutil
import threading
import time

from PySide6.QtCore import QObject, Signal

from .engine import RATE
from .video import av


class CacheMuxer:
    def __init__(self, path, history, target, tracks):
        self.output = None
        self.history = history
        self.origin = history.origin
        self.codec = history.codec
        self.sequence = None
        self.written = 0
        self.tracks = tracks
        self.audio = []
        result = history.packet_range(None, target - self.origin)
        if result is None:
            raise ValueError('No cached video keyframe available')
        first = result[1][0]
        self.start = self.origin + float((first.dts if first.dts is not None else first.pts) * first.time_base)
        try:
            self.output = av.open(str(path), 'w', format='matroska')
            with history.lock:
                if history.stream_template is None:
                    raise ValueError('Main stream is reconnecting; retry after video resumes')
                self.video = self.output.add_stream_from_template(history.stream_template)
            self.video.time_base = Fraction(1, 1000000)
            for key, label in tracks:
                stream = self.output.add_stream('aac', rate=RATE)
                stream.layout = 'stereo'
                stream.bit_rate = 192000
                stream.metadata['title'] = f'{label} ({key})'
                self.audio.append(stream)
        except Exception:
            self.close()
            raise

    def write_until(self, target, sources):
        if self.history.origin != self.origin or self.history.codec != self.codec:
            raise ValueError('Main stream changed; recording stopped')
        result = self.history.packet_range(self.sequence, target - self.origin)
        if result is None or (self.sequence is not None and result[2]):
            raise ValueError('Recording fell behind retained video cache')
        for record in result[1]:
            if record.time + self.origin > target:
                break
            packet = record.decode_packet()
            packet.pts = round((float(record.pts * record.time_base) + self.origin - self.start) * 1000000) if record.pts is not None else None
            packet.dts = round((float(record.dts * record.time_base) + self.origin - self.start) * 1000000) if record.dts is not None else None
            packet.duration = round(float(record.duration * record.time_base) * 1000000)
            packet.time_base = Fraction(1, 1000000)
            packet.stream = self.video
            self.output.mux(packet)
            self.sequence = record.sequence + 1
        limit = max(0, round((target - self.start) * RATE))
        while self.written < limit:
            frames = min(1024, limit - self.written)
            stamp = self.start + self.written / RATE
            for (key, _label), stream in zip(self.tracks, self.audio):
                history, shift = sources.get(key, (None, 0))
                pcm = history.pcm_at(stamp + shift, frames) if history is not None else bytes(frames * 4)
                frame = av.AudioFrame(format='s16', layout='stereo', samples=frames)
                frame.planes[0].update(pcm)
                frame.sample_rate = RATE
                frame.pts = self.written
                frame.time_base = Fraction(1, RATE)
                for packet in stream.encode(frame):
                    self.output.mux(packet)
            self.written += frames

    def close(self):
        output, self.output = self.output, None
        if output is not None:
            try:
                for stream in self.audio:
                    for packet in stream.encode(None):
                        output.mux(packet)
            finally:
                output.close()


class MultiTrackRecorder(QObject):
    completed = Signal(str, str)

    def __init__(self, path, history, tracks, snapshot, parent=None, minimum_free=0):
        super().__init__(parent)
        self.path = Path(path)
        self.history = history
        self.tracks = tracks
        self.snapshot = snapshot
        self.minimum_free = minimum_free
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self._run, name='match-sync-recording', daemon=False)

    def start(self):
        self.thread.start()

    def stop(self, wait=False):
        self.cancelled.set()
        if wait and self.thread.ident is not None:
            self.thread.join(5)

    def _run(self):
        muxer = None
        error = ''
        try:
            target, sources = self.snapshot
            muxer = CacheMuxer(self.path, self.history, target, self.tracks)
            checked = 0
            while True:
                if time.monotonic() - checked >= 2:
                    checked = time.monotonic()
                    if shutil.disk_usage(self.path.parent).free < self.minimum_free:
                        raise OSError('Insufficient disk space; recording stopped')
                target, sources = self.snapshot
                muxer.write_until(target, sources)
                if self.cancelled.wait(.05):
                    target, sources = self.snapshot
                    muxer.write_until(target, sources)
                    break
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        finally:
            if muxer is not None:
                try:
                    muxer.close()
                except Exception as exc:
                    error = error or f'{type(exc).__name__}: {exc}'
            self.completed.emit(str(self.path), error)
