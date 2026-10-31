"""Temporary disk-backed packet segments and a sample-addressed PCM ring."""
import mmap
import tempfile
import threading

from .engine import AudioRing, RATE

REPLAY_SECONDS = 600
CACHE_SECONDS = 720  # Also retain the live reserve and cross-stream alignment margin.
CACHE_BYTES = 2 * 1024 ** 3


class PacketSegment:
    def __init__(self):
        self.file = tempfile.TemporaryFile(prefix='ddm-replay-video-', buffering=0)
        self.size = 0
        self.lock = threading.Lock()


class PacketSlice:
    def __init__(self, segment, offset, length):
        self.segment, self.offset, self.length = segment, offset, length

    def __len__(self):
        return self.length

    def __bytes__(self):
        with self.segment.lock:
            self.segment.file.seek(self.offset)
            return self.segment.file.read(self.length)


class PacketStore:
    def __init__(self):
        self.segment = None

    def append(self, data):
        if self.segment is None or self.segment.size + len(data) > 8 * 1024 ** 2:
            self.segment = PacketSegment()
        segment = self.segment
        with segment.lock:
            offset = segment.size
            segment.file.seek(offset)
            segment.file.write(data)
            segment.size += len(data)
        # A retired segment closes automatically after its last packet/reader releases it.
        return PacketSlice(segment, offset, len(data))


class DiskAudioRing(AudioRing):
    def __init__(self, seconds):
        self.capacity = max(1, int(seconds * RATE))
        self.file = tempfile.TemporaryFile(prefix='ddm-replay-audio-')
        self.file.truncate(self.capacity * 4)
        self.data = mmap.mmap(self.file.fileno(), self.capacity * 4)
        self.end = 0
        self.lock = threading.RLock()
