"""Bound audio waiting in Qt's pipe; reset recognition across discarded audio."""
import struct


HEADER = struct.Struct('!I?')
MAX_FRAME = 32000  # One second of 16 kHz mono signed 16-bit PCM.
MAX_PENDING = 64000  # At most two seconds awaiting delivery to the worker.


class AudioGate:
    def __init__(self):
        self.reset = False
        self.dropped_bytes = 0

    def frame(self, audio, pending):
        if len(audio) > MAX_FRAME or pending + len(audio) + HEADER.size > MAX_PENDING:
            self.reset = True
            self.dropped_bytes += len(audio)
            return None
        result = HEADER.pack(len(audio), self.reset) + audio
        self.reset = False
        return result


def read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        chunk = stream.read(size - len(result))
        if not chunk:
            if not result:
                return None
            raise EOFError('Truncated audio frame')
        result.extend(chunk)
    return bytes(result)


def read_frame(stream):
    header = read_exact(stream, HEADER.size)
    if header is None:
        return None
    size, reset = HEADER.unpack(header)
    if size > MAX_FRAME or size % 2:
        raise ValueError('Invalid PCM frame size')
    audio = read_exact(stream, size)
    if audio is None and size:
        raise EOFError('Missing PCM frame')
    return audio or b'', reset
