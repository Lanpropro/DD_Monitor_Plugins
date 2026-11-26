"""Bounded audio-event window; speech with BGM keeps normal captions."""
from heapq import nlargest
from operator import itemgetter
from pathlib import Path

MUSIC_MARKER = '🎵MUSIC🎵'
_VOCAL_FLOOR = .18
_SINGING = {'Singing', 'Male singing', 'Female singing', 'Child singing', 'A capella', 'Choir'}
_MELODIC = _SINGING | {'Mantra', 'Chant', 'Lullaby', 'Yodeling'}
_SPEECH = {'Speech', 'Child speech, kid speaking', 'Female speech, woman speaking',
           'Male speech, man speaking', 'Conversation', 'Whispering'}


_CHANT = ('Mantra', 'Chant', 'Lullaby')


def _chant_strength(scores):
    strength = max(scores.get(k, 0.) for k in _CHANT)
    speech = max(scores.get(k, 0.) for k in _SPEECH)
    if speech > .20 or strength != max(scores.values(), default=0.):
        return 0.
    return strength


class ChantState:
    def __init__(self):
        self.active = False
        self._recent = ()

    def update_chant(self, scores, last_second):
        """last_second() lazily classifies the newest 1 s of this same PCM window."""
        strength = _chant_strength(scores)
        if self.active and strength >= .35:
            return True
        self.active = False
        if strength < .45:
            self._recent = ()
            return False
        self._recent = (*self._recent[-2:], strength)
        if len(self._recent) == 3 and max(self._recent) >= .70:
            self.active = _chant_strength(last_second()) >= .45
        return self.active


class MusicState:
    def __init__(self):
        self.active = False
        self.baseline_active = False
        self._positive = 0
        self._vocal_history = ()
        self._seed = False
        self._weak_hops_left = None

    def _continuous_vocal(self, scores, baseline):
        # Only the four leading events can corroborate a vocal phrase.
        top = nlargest(4, scores.items(), key=itemgetter(1))
        peak = top[0][1] if top else 0
        melodic = [value for name, value in top if name in _MELODIC]
        vocal = melodic[0] if melodic else 0
        singing = max((value for name, value in top if name in _SINGING), default=0)
        # When speech is outside the top four, the fourth score bounds it above.
        speech = max((value for name, value in top if name in _SPEECH),
                     default=top[-1][1] if len(top) == 4 else 0)
        leading = vocal > 0 and vocal == peak
        seed = leading and singing >= .15 and vocal >= 1.25 * speech
        mass = sum(melodic)
        corroborated = len(melodic) >= 2 and mass >= max(.15, .375 * peak)
        confirmed = self._seed and corroborated and speech <= .15
        self._seed = seed

        # Admit a strong lead immediately, or confirm a modest lead on the next hop.
        strong = seed and mass >= .35 and speech < .40
        renewed = self._weak_hops_left is not None and leading and vocal >= .15 and speech < .50
        if (baseline and vocal >= .15) or strong or confirmed or renewed:
            self._weak_hops_left = 4
            return True

        # No unconditional release timer. Each extra hop needs a current melodic label.
        cost = 2 if speech >= .70 else 1
        supported = mass >= max(.08, .375 * peak)
        if self._weak_hops_left is not None and self._weak_hops_left >= cost and supported:
            self._weak_hops_left -= cost
            return True
        self._weak_hops_left = None
        return False

    def _vocal_marker(self, speech, singing, related, peak):
        vocal = max(singing, related)
        if speech >= .25 or speech >= vocal:
            self._vocal_history = ()
            return False

        # An anchor needs direct singing evidence, including when a chant
        # or lullaby is the strongest event. A weak window cannot start alone.
        anchor = (vocal == peak and vocal >= _VOCAL_FLOOR and
                  vocal >= 1.5 * speech and singing >= .05)
        support = ((singing >= .08 and singing >= .5 * peak) or
                   (related == peak and related >= _VOCAL_FLOOR))
        # Track strong, weak and absent evidence over this and two prior hops.
        evidence = 2 if anchor else int(support)
        history = (*self._vocal_history[-2:], evidence)
        self._vocal_history = history
        if anchor and singing * related >= _VOCAL_FLOOR ** 2:
            return True
        return evidence > 0 and 2 in history and sum(level > 0 for level in history) >= 2

    def update(self, scores):
        speech = max((scores.get(name, 0) for name in (
            'Speech', 'Child speech, kid speaking', 'Female speech, woman speaking',
            'Male speech, man speaking', 'Conversation', 'Whispering')), default=0)
        singing = max((scores.get(name, 0) for name in (
            'Singing', 'Male singing', 'Female singing', 'Child singing', 'A capella', 'Choir')), default=0)
        music = scores.get('Music', 0)
        related = max(scores.get(name, 0) for name in ('Mantra', 'Chant', 'Lullaby'))
        peak = max(scores.values(), default=0)
        # Quiet speech over game BGM must not be suppressed. Require very
        # little speech evidence for instrumental music, or clear singing.
        if self._positive >= 3:
            vocal_candidate = singing >= .15 and speech < .25 and singing > speech
        else:
            vocal_candidate = singing >= _VOCAL_FLOOR and speech < .20 and singing >= 1.5 * speech
        leading_singing = vocal_candidate and singing == peak
        candidate = (speech < .08 and music >= .85) or (speech < .2 and singing >= .65) or leading_singing
        previous = self.active
        self._positive = min(3, self._positive + 1) if candidate else 0
        vocal_marker = self._vocal_marker(speech, singing, related, peak)
        baseline = self._positive >= 3 or vocal_marker
        self.baseline_active = baseline
        continuous = self._continuous_vocal(scores, baseline)
        self.active = baseline or continuous
        return self.active if previous != self.active else None


class FastExit:
    """Feed every block in order; True is a release suggestion for this call."""

    HOP = 1600

    def __init__(self, np, short_check=None):
        self.np = np
        self._window = np.hanning(400).astype(np.float32)
        self._pending = np.empty(self.HOP, np.float32)
        self._history = np.empty((66, 4), np.float32)
        self._short_check = short_check
        self._pcm = np.empty(8 * self.HOP, np.float32) if short_check is not None else None
        self.reset()

    def reset(self):
        self._used = 0
        self._count = 0
        self._probe_age = -1

    def _resumed_speech(self):
        # Only probe on a voiced restart after a NEW low, steady noise floor.
        # A quiet pause itself never releases. The callback reads <=0.8 s.
        np = self.np
        levels = self._history[-self._count:, 0]
        if self._probe_age < 0:
            if levels[-1] < -50:
                return False
            for trim in (1, 2):
                quiet = levels[-trim-3:-trim]
                ceiling = quiet.max()
                if levels[-1] - ceiling < 22 or np.ptp(quiet) > 8:
                    continue
                old = levels[:-trim-3]
                if ceiling < np.percentile(old, 5) - 6 and ceiling < np.median(old) - 30:
                    self._probe_age = 0
                    break
            return False
        self._probe_age += 1
        if self._probe_age == 2:
            return False
        if self._probe_age == 3:
            self._probe_age = -1
        return bool(self._short_check(self._pcm, speech_only=True))

    def _observe(self, samples, music_active):
        np = self.np
        # Four Hann spectra per hop. Bands carry total level, bass and treble
        # share (dB) and the fraction in the voiced midrange.
        spec = np.fft.rfft(samples.reshape(4, 400) * self._window, axis=1)
        power = (spec.real ** 2 + spec.imag ** 2).sum(axis=0)
        total = max(float(power.sum()), 1e-12)
        level, bass, treble = 10 * np.log10(np.maximum([
            total / 240000, power[:5].sum() / total, power[75:].sum() / total,
        ], 1e-14))
        self._history[:-1] = self._history[1:]
        self._history[-1] = (level, bass, treble, power[5:35].sum() / total)
        self._count = min(len(self._history), self._count + 1)
        if self._pcm is not None:
            self._pcm[:-self.HOP] = self._pcm[self.HOP:]
            self._pcm[-self.HOP:] = samples
        if not music_active or self._count < 33:
            self._probe_age = -1
            return False
        if self._pcm is not None and self._resumed_speech():
            return True
        recent = self._history[-3:]
        if recent[:, 0].min() < -58 or recent[:, 3].min() < .5:
            return False
        # Compare 300 ms of new sound with the preceding 3 s. A breath,
        # a single transient or a quiet syllable should not clear the marker.
        lo, center, hi = np.percentile(self._history[-33:-3, :3], (10, 50, 90), axis=0)
        if center[0] < -52:
            return False
        current = np.median(recent[:, :3], axis=0)
        change = current - center
        margin = np.where(change > 0, recent[:, :3].min(axis=0) - hi,
                          lo - recent[:, :3].max(axis=0))
        bandwidth_break = abs(change[2]) >= 24 and margin[2] >= 10 and abs(change[0]) <= 12
        color_break = np.any((abs(change[1:]) >= 12) & (margin[1:] >= 4))
        level_break = change[0] <= -18 and margin[0] >= 0 and color_break
        if not (bandwidth_break or level_break):
            return False
        if self._short_check is None:
            return True
        return bool(self._short_check(self._pcm[-4 * self.HOP:], speech_only=False))

    def feed(self, samples, music_active):
        """Emit at the end of a completed hop. A large call reports at its return."""
        np = self.np
        samples = np.asarray(samples)
        if samples.ndim != 1 or not np.issubdtype(samples.dtype, np.floating):
            raise ValueError("Expected normalized mono floating-point PCM")
        released = False
        while samples.size:
            size = min(self.HOP - self._used, len(samples))
            self._pending[self._used:self._used + size] = samples[:size]
            self._used += size
            samples = samples[size:]
            if self._used == self.HOP:
                released |= self._observe(self._pending, music_active and not released)
                self._used = 0
        return released


class ExtensionVeto:
    """Filter only a continuous extension after the same marked run had a base vote."""

    PERIOD = 3200
    WINDOW = 12800

    def __init__(self):
        self.frames = 0
        self.had_base = False
        self.blocked = False

    def apply(self, size, active, base, waveform, classify):
        old_slot = self.frames // self.PERIOD
        self.frames += size
        if not active:
            self.had_base = self.blocked = False
            return False
        if base:
            self.had_base = True
            self.blocked = False
            return True
        if self.had_base and self.frames // self.PERIOD != old_slot:
            scores = classify(waveform[-self.WINDOW:])
            speech = max(scores.get(name, 0) for name in _SPEECH)
            melodic = max(scores.get(name, 0) for name in _MELODIC)
            self.blocked = speech >= .75 and speech >= melodic
        return not self.blocked


def pitch_trace(waveform, np):
    # 50 ms analysis, 25 ms hop, 80–600 Hz. Remove the DC component
    # and average adjacent samples before decimating to 8 kHz.
    frames = np.lib.stride_tricks.sliding_window_view(waveform, 800)[::400]
    frames = frames.reshape(-1, 400, 2).mean(axis=2)
    frames -= frames.mean(axis=1, keepdims=True)
    fft = np.fft.rfft(frames, n=1024, axis=1)
    ac = np.fft.irfft(abs(fft) ** 2, axis=1)
    energy = np.pad(np.cumsum(frames ** 2, axis=1), ((0, 0), (1, 0)))
    lag = np.arange(12, 102)
    denom = np.sqrt(energy[:, 400 - lag] * (energy[:, -1:] - energy[:, lag]))
    corr = ac[:, lag] / np.maximum(denom, 1e-8)
    peaks = (corr[:, 1:-1] >= corr[:, :-2]) & (corr[:, 1:-1] > corr[:, 2:])
    peaks &= corr[:, 1:-1] >= np.maximum(.82, .92 * corr[:, 1:-1].max(axis=1, keepdims=True))
    index = 1 + peaks.argmax(axis=1)
    row = np.arange(len(frames))
    left, center, right = (corr[row, index + delta] for delta in (-1, 0, 1))
    shift = .5 * (left - right) / np.minimum(left - 2 * center + right, -1e-8)
    semitones = 12 * np.log2(8000 / (lag[index] + np.clip(shift, -.5, .5)) / 440)
    valid = peaks.any(axis=1) & (energy[:, -1] >= .0004)
    return np.where(valid, semitones, np.nan)


def plateaus(pitch, np, frames, spread):
    groups = np.lib.stride_tricks.sliding_window_view(pitch, frames)
    starts = np.flatnonzero(np.ptp(groups, axis=1) <= spread)
    # Merge overlapping starts into note spans. Adjacent notes can differ in pitch.
    notes = []
    for i in starts:
        i = int(i)
        if notes and i < notes[-1][1]:
            notes[-1][1] = i + frames
        else:
            notes.append([i, i + frames])
    return notes


def _weak_singing(scores):
    singing = max(scores.get(k, 0.) for k in _SINGING)
    speech = max(scores.get(k, 0.) for k in _SPEECH)
    return singing >= .055 and speech <= .65 and scores.get('Music', 0.) < .35


def _pitch_support(waveform, np):
    # Keep this fallback separate from the existing continuous vocal history.
    # Nine 25 ms hops within 0.65 semitones count as a stable note.
    notes = plateaus(pitch_trace(waveform[-48000:], np), np, 9, .65)
    return len(notes) >= 2 and sum(b - a for a, b in notes) >= 24


class MusicDetector:
    def __init__(self, directory, sherpa, np):
        root = Path(directory)
        model = sherpa.AudioTaggingModelConfig(
            zipformer=sherpa.OfflineZipformerAudioTaggingModelConfig(
                model=str(root / 'model.int8.onnx')), num_threads=1, provider='cpu')
        self.tagger = sherpa.AudioTagging(sherpa.AudioTaggingConfig(
            model=model, labels=str(root / 'class_labels_indices.csv'), top_k=527))
        self.np = np
        self.fast = FastExit(np, self._confirm_change)
        self.reset()

    def reset(self):
        self.veto = ExtensionVeto()
        self._visible = False
        self._pitch_active = False
        self._pitch_confirmed = False
        self.fast.reset()
        self.state = MusicState()
        self.chant = ChantState()
        self._window = self.np.empty(0, dtype=self.np.float32)
        self._since = 0

    @property
    def active(self):
        return self._visible

    def _scores(self, waveform):
        stream = self.tagger.create_stream()
        stream.accept_waveform(16000, waveform)
        return {event.name: event.prob for event in self.tagger.compute(stream)}

    def _confirm_change(self, waveform, *, speech_only):
        scores = self._scores(waveform)
        speech = scores.get('Speech', 0)
        vocals = max(scores.get(k, 0) for k in _MELODIC)
        music = scores.get('Music', 0)
        if speech_only:
            return speech >= .85 and speech >= 2 * max(music, vocals)
        return max(speech, music) >= .65 and vocals < .15

    def _recent_chant(self):
        return self._scores(self._window[-16000:])

    def feed(self, samples):
        previous = self.active
        raw_before = self.state.active
        self._update_main(samples)
        if self.fast.feed(samples, raw_before and self.state.active):
            self.reset()
        # Independently confirmed chant is genuine support for the current hop.
        self._visible = self.veto.apply(len(samples), self.state.active,
                                        self.state.baseline_active or self.chant.active,
                                        self._window, self._scores)
        self._visible = self._visible or self._pitch_active
        return self.active if self.active != previous else None

    def _update_main(self, samples):
        self._window = self.np.concatenate((self._window, samples))[-64000:]
        self._since += len(samples)
        if len(self._window) < 64000 or self._since < 16000:
            return None
        self._since = 0
        scores = self._scores(self._window)
        self.state.update(scores)
        chant = self.chant.update_chant(scores, self._recent_chant)
        self.state.active = self.state.active or chant
        weak_voice = not self.state.active and _weak_singing(scores)
        pitch_now = weak_voice and _pitch_support(self._window, self.np)
        # One unsupported hop can bridge a syllable or breath, but still needs
        # a current weak singing score and cannot renew itself.
        keep_one_hop = weak_voice and self._pitch_confirmed and scores.get('Music', 0.) >= .025
        self._pitch_active = pitch_now or keep_one_hop
        self._pitch_confirmed = pitch_now
