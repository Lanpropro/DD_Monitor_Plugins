"""Bounded diagnostic of common sound in three tracks submitted to the same output device."""
import itertools
import threading
import time

from .engine import RATE, np


WINDOW = 3
WINDOWS = 4


def pair_windows(first, second):
    # Average 4 stereo samples into each mono sample at 12 kHz.
    rate = RATE // 4
    width = WINDOW * rate
    size = 1 << (2 * width - 1).bit_length()
    band = np.fft.rfftfreq(size, 1 / rate)
    mask = (band >= 180) & (band <= 4400)
    limit = rate
    rows = []
    for start in range(0, len(first) - width + 1, width):
        spectra = []
        for track in (first, second):
            samples = track[start:start + width]
            spectrum = np.fft.rfft(samples * np.hanning(width), size)
            amplitude = np.abs(spectrum)
            # Partial whitening reduces false matches on a single loud musical note.
            spectrum *= mask / np.maximum(amplitude, max(amplitude.max() * .005, 1.)) ** .7
            spectra.append(spectrum)
        a, b = spectra
        norm = np.sqrt(np.vdot(a, a).real * np.vdot(b, b).real)
        if norm <= 0:
            rows.append(dict(usable=False))
            continue
        corr = np.fft.irfft(a.conj() * b, size) * (size / (2 * norm))
        scores = np.abs(np.r_[corr[-limit:], corr[:limit + 1]])
        index = int(scores.argmax())
        peak = float(scores[index])
        scores[max(0, index - rate // 100):index + rate // 100 + 1] = 0
        ratio = peak / max(float(scores.max()), 1e-9)
        rows.append(dict(lag_ms=round((index - limit) * 1000 / rate, 2),
                         score=round(peak, 4), separation=round(ratio, 2),
                         usable=bool(peak >= .07 and ratio >= 1.35 and 0 < index < 2 * limit)))
    return rows


class MixLagProbe:
    def __init__(self, histories):
        if np is None or len(histories) not in (2, 3):
            raise ValueError('Two or three live tracks and NumPy required')
        self.histories = tuple(map(id, histories))
        self.buffers = [bytearray() for _ in histories]
        self.remaining = RATE * WINDOW * WINDOWS
        self.deadline = time.monotonic() + 20
        self.result = dict(state='collecting')

    def cancel(self):
        if self.buffers:
            self.buffers = []
            self.result = dict(state='interrupted')

    def append(self, sources, inputs, written):
        # Called under the existing device lock, only after a successful write.
        audible = [(s, pcm) for s, (pcm, gain) in zip(sources, inputs) if gain > 0]
        if (time.monotonic() > self.deadline or self.histories != tuple(id(s[0]) for s, _ in audible)
                or any(isinstance(s[1], tuple) for s, _ in audible)):
            self.cancel()
            return
        count = min(written, self.remaining)
        for out, (_, pcm) in zip(self.buffers, audible):
            out.extend(memoryview(pcm)[:count * 4])
        self.remaining -= count
        if self.remaining == 0:
            buffers, self.buffers = self.buffers, []
            self.result = dict(state='analyzing')
            threading.Thread(target=self._analyze, args=(buffers,), daemon=True).start()

    def _analyze(self, buffers):
        try:
            tracks = [np.frombuffer(buf, dtype='<i2').reshape(-1, 8).mean(axis=1) for buf in buffers]
            pairs = [dict(first=i, second=j, windows=pair_windows(tracks[i], tracks[j]))
                     for i, j in itertools.combinations(range(len(tracks)), 2)]
            self.result = dict(state='done', seconds=WINDOW * WINDOWS, pairs=pairs)
        except Exception:
            self.result = dict(state='analysis_failed')
