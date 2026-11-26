"""Bounded VAD audio assembly; snapshots are drafts, only commits reach MT."""
from dataclasses import dataclass

SAMPLE_RATE = 16000
MAX_SPEECH_SECONDS = 6.0


@dataclass(frozen=True)
class SpeechJob:
    segment_id: int
    revision: int
    final: bool
    start: int
    samples: object
    reason: str


class SpeechSegments:
    """Worker-thread state; receives absolute VAD sample positions, owns no model."""
    def __init__(self, np):
        self.np = np
        self.segment_id = 1
        self.reset()

    def reset(self):
        self.samples = self.np.empty(0, dtype=self.np.float32)
        self.start = self.committed_end = 0
        self.revision = 0
        self.last_draft_end = 0
        self.updated_at = None
        self.segment_id += 1

    def _job(self, size, final, reason):
        self.revision += 1
        job = SpeechJob(self.segment_id, self.revision, final, self.start,
                        self.samples[:size].copy(), reason)
        if final:
            self.start += size
            self.committed_end = self.start
            self.samples = self.samples[size:].copy()
            self.segment_id += 1
            self.revision = 0
            self.last_draft_end = self.start
            if not len(self.samples):
                self.updated_at = None
        else:
            self.last_draft_end = self.start + size
        return job

    def _cut(self):
        limit = int(MAX_SPEECH_SECONDS * SAMPLE_RATE)
        # Prefer a >=80 ms quiet interval in the latter half. A cut never
        # removes its tail; even a poor pause estimate cannot discard speech.
        window = 320
        section = self.samples[:limit].reshape(-1, window)
        energy = self.np.sqrt(self.np.mean(section * section, axis=1))
        threshold = min(0.01, float(self.np.median(energy)) * 0.15)
        best_start = best_size = run_start = run_size = 0
        for index in range(len(energy) // 2, len(energy)):
            if energy[index] <= threshold:
                if not run_size:
                    run_start = index
                run_size += 1
                if run_size > best_size:
                    best_start, best_size = run_start, run_size
            else:
                run_size = 0
        return (best_start + best_size // 2) * window if best_size >= 4 else limit

    def accept(self, start, samples, *, finished, now):
        jobs = []
        end = start + len(samples)
        accepted_end = self.start + len(self.samples) if len(self.samples) else self.committed_end
        forced = len(samples) >= int((MAX_SPEECH_SECONDS - 0.05) * SAMPLE_RATE)
        if end <= accepted_end:
            if finished and not forced and end == accepted_end and len(self.samples) >= 19200:
                return [self._job(len(self.samples), True, 'pause')]
            return jobs
        if len(self.samples) and start - accepted_end > SAMPLE_RATE:
            jobs.append(self._job(len(self.samples), True, 'gap'))
        if not len(self.samples):
            self.start = max(start, self.committed_end)
        accepted_end = self.start + len(self.samples)
        gap = max(0, start - accepted_end)
        offset = max(0, accepted_end - start)
        self.samples = self.np.concatenate((self.samples, self.np.zeros(gap, dtype=self.np.float32),
                                            samples[offset:]))
        self.updated_at = now
        while len(self.samples) >= int(MAX_SPEECH_SECONDS * SAMPLE_RATE):
            jobs.append(self._job(self._cut(), True, 'max_wait'))
        if finished and not forced and len(self.samples) >= int(1.2 * SAMPLE_RATE):
            jobs.append(self._job(len(self.samples), True, 'pause'))
        elif not jobs and len(self.samples) and (finished or self.start + len(self.samples) - self.last_draft_end >= 19200):
            jobs.append(self._job(len(self.samples), False, 'short_hold' if finished else 'speech'))
        return jobs

    def poll(self, now):
        if len(self.samples) and self.updated_at is not None and now - self.updated_at >= 1.0:
            return [self._job(len(self.samples), True, 'hold_limit')]
        return []
