"""Worker-thread VAD jobs and finalized-only translation; Qt stays in Session."""
import time
from collections import deque

try:
    from .speech_segments import SpeechSegments
    from .quality_policy import recognition_issues
except ImportError:
    from speech_segments import SpeechSegments
    from quality_policy import recognition_issues


class SpeechPipeline:
    def __init__(self, options, np, vad, recognizer, decode_lock, translator):
        self.options, self.np, self.vad = options, np, vad
        self.recognizer, self.decode_lock, self.translator = recognizer, decode_lock, translator
        self.segments = SpeechSegments(np)
        self.cached = None
        self.previous = deque(maxlen=2)

    def reset(self, options=None):
        if options is not None:
            self.options = self.translator.options = options
        self.vad.reset()
        self.translator.reset()
        self.segments.reset()
        self.cached = None
        self.previous.clear()

    def feed(self, waveform):
        now = time.monotonic()
        self.vad.accept_waveform(waveform)
        jobs = []
        while not self.vad.empty():
            segment = self.vad.front
            jobs += self.segments.accept(segment.start, self.np.array(segment.samples, dtype=self.np.float32),
                                         finished=True, now=now)
            self.vad.pop()
        if self.vad.is_speech_detected():
            segment = self.vad.current_segment
            jobs += self.segments.accept(segment.start, self.np.array(segment.samples, dtype=self.np.float32),
                                         finished=False, now=now)
        return jobs + self.poll()

    def poll(self):
        return self.segments.poll(time.monotonic())

    def process(self, job, current, can_review=lambda: True):
        try:
            from .recognition import recognize
        except ImportError:
            from recognition import recognize
        if not current():
            return None
        if not job.final and not can_review():
            return None
        process_started = time.monotonic()
        key = (job.segment_id, job.start, len(job.samples))
        cached = self.cached is not None and self.cached[0] == key
        if cached:
            _, text, _asr_ms = self.cached
            asr_ms = 0
        else:
            began = time.monotonic()
            with self.decode_lock:
                if not current():
                    return None
                text = recognize(self.recognizer, job.samples, self.options.source_language)
            asr_ms = (time.monotonic() - began) * 1000
            self.cached = (key, text, asr_ms)
        if not current():
            return None
        reasons, attempts, skipped = [], 0, ''
        if job.final:
            reasons = recognition_issues(text, len(job.samples) / 16000, tuple(self.previous))
            if reasons:
                if not can_review():
                    skipped = 'backlog'
                elif time.monotonic() - process_started >= 1.0:
                    skipped = 'deadline'
                elif not self.decode_lock.acquire(blocking=False):
                    skipped = 'shared_model_busy'
                else:
                    began = time.monotonic()
                    try:
                        if not current():
                            return None
                        attempts = 1
                        # Preserve the full utterance, adding 100 ms at each
                        # boundary instead of dropping suspected words.
                        padded = self.np.pad(job.samples, (1600, 1600))
                        candidate = recognize(self.recognizer, padded, self.options.source_language)
                    finally:
                        self.decode_lock.release()
                    asr_ms += (time.monotonic() - began) * 1000
                    if time.monotonic() - process_started >= 1.0:
                        skipped = 'deadline'
                    elif candidate and len(recognition_issues(candidate, len(job.samples) / 16000,
                                                              tuple(self.previous))) < len(reasons):
                        text = candidate
            if not current():
                return None
            if text:
                self.previous.append(text)
        if not text and not job.final:
            return None
        began = time.monotonic()
        self.translator.current = current
        translated, warning = self.translator.run(text) if job.final and text else ('', '')
        if not current():
            return None
        review = getattr(self.translator, 'review', {}) if job.final and text else {}
        return dict(event=('caption' if text else 'quality_review') if job.final else 'draft',
                    phase='final' if job.final else 'draft',
                    segment_id=job.segment_id, revision=job.revision, boundary=job.reason,
                    text=text, translated=translated, warning=warning if text else '识别未返回正文，已保留上一条字幕',
                    has_translation=bool(translated), asr_cached=cached,
                    quality_reasons=reasons + list(review.get('reasons', ())),
                    asr_review_attempts=attempts, asr_review_skipped=skipped,
                    translation_attempts=review.get('attempts', 0),
                    audio_start=job.start / 16000, audio_seconds=len(job.samples) / 16000,
                    asr_ms=round(asr_ms, 1), translation_ms=round((time.monotonic() - began) * 1000, 1))
