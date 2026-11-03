"""Bounded playback evidence; the writer/sampler never calls Qt or waits on media locks."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime
import hashlib
import io
import json
from logging.handlers import RotatingFileHandler
import logging
import marshal
import os
import re
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QEvent, QObject, QTimer, Qt


def stream_error_codes(message):
    """Classify locally; never retain raw stderr, URLs, headers or exception text."""
    text = re.sub(r'\b[a-z][a-z0-9+.-]*://\S+', '[address]', message.lower())
    codes = []
    status = re.search(r'(?:http(?:/\d(?:\.\d)?| error)?|server returned)\s*[: ]\s*([45]\d{2})\b|\b([45]\d{2}) (?:client|server) error', text)
    if status:
        codes.append('http_' + (status[1] or status[2]))
    for code, pattern in (
            ('timeout', r'timed? out|timeout'),
            ('dns', r'failed to resolve|name or service not known|temporary failure in name resolution'),
            ('connection', r'connection (?:refused|reset)|network is unreachable|broken pipe'),
            ('tls', r'\b(?:tls|ssl)\b.*(?:failed|error)|certificate verify failed'),
            ('invalid_data', r'invalid data found|invalid nal|error while decoding'),
            ('decoder', r'unknown decoder|decoder .*not found|unsupported codec'),
            ('missing_audio', r'stream map .*0:a:0.*matches no streams'),
            ('permission', r'permission denied|access is denied'),
            ('memory', r'cannot allocate memory|out of memory')):
        if re.search(pattern, text):
            codes.append(code)
    return codes


class StreamAcquisition:
    """Small per-attempt startup receipt, exported by the existing background probe."""
    def __init__(self):
        self.enabled = os.environ.get('DDM_MATCH_DIAGNOSTICS', '1') != '0'
        self.lock = threading.Lock()
        self.started = None
        self.state = {}
        self.milestones = {}
        self.errors = deque(maxlen=8)
        self.previous = None

    def begin(self, attempt):
        if not self.enabled:
            return
        with self.lock:
            if self.state:
                self.previous = self._receipt()
            self.started = time.perf_counter()
            self.state = dict(attempt=attempt, phase='resolve')
            self.milestones = {}
            self.errors.clear()

    def phase(self, name):
        if self.enabled:
            with self.lock:
                self.state['phase'] = name

    def mark(self, name):
        if not self.enabled or self.started is None or name in self.milestones:
            return
        with self.lock:
            self.milestones.setdefault(name, round((time.perf_counter() - self.started) * 1000, 3))

    def error(self, error):
        if not self.enabled:
            return
        self.stderr(str(error))
        kind = type(error).__name__
        if kind not in ('TimeoutError', 'ConnectionError', 'PermissionError', 'FileNotFoundError',
                        'OSError', 'HTTPError', 'InvalidDataError', 'FFmpegError', 'ValueError', 'RuntimeError'):
            kind = 'other'
        with self.lock:
            self.state.update(error_phase=self.state.get('phase'), error_kind=kind)

    def stderr(self, text):
        if self.enabled:
            codes = stream_error_codes(text)
            with self.lock:
                for code in codes:
                    if code not in self.errors:
                        self.errors.append(code)

    def read_stderr(self, pipe):
        try:
            with io.BufferedReader(pipe, buffer_size=4096) as reader:
                while chunk := reader.readline(4096):
                    self.stderr(chunk.decode('utf-8', errors='replace'))
        except (OSError, ValueError):
            pass  # The capture worker may close its pipe during cancellation.

    def exited(self, code):
        if self.enabled:
            with self.lock:
                self.state['ffmpeg_exit_code'] = code
            self.mark('ffmpeg_exited')

    def finish(self, outcome):
        if self.enabled:
            self.mark('finished')
            with self.lock:
                self.state.update(outcome=outcome, phase='cancelled' if outcome == 'cancelled' else 'retry_wait')

    def _receipt(self):
        return dict(self.state, milestones_ms=dict(self.milestones), error_codes=list(self.errors))

    def snapshot(self):
        if not self.enabled:
            return {}
        if not self.lock.acquire(blocking=False):
            return dict(busy=True)
        try:
            result = self._receipt()
            if self.started is not None:
                result['elapsed_ms'] = round((time.perf_counter() - self.started) * 1000, 3)
            if self.previous is not None:
                result['previous_attempt'] = self.previous
            return result
        finally:
            self.lock.release()


class FrameCadence:
    """One bounded second of timestamp evidence, without retaining frame images."""
    def __init__(self):
        self.samples = deque(maxlen=512)
        self.enabled = os.environ.get('DDM_MATCH_DIAGNOSTICS', '1') != '0'

    def record(self, wall, media):
        if self.enabled and media is not None:
            self.samples.append((wall, media))

    def stats(self, now, fps=0):
        samples = tuple(self.samples)
        recent = [item for item in samples if now - 1 <= item[0] <= now]
        distinct = [item for index, item in enumerate(samples)
                    if index == 0 or item[1] != samples[index - 1][1]]
        pairs = [(a, b) for a, b in zip(distinct, distinct[1:]) if now - 1 <= b[0] <= now]
        gaps = sorted((b[0] - a[0]) * 1000 for a, b in pairs)
        steps = sorted((b[1] - a[1]) * 1000 for a, b in pairs)
        def percentile(values, fraction):
            return round(values[min(len(values) - 1, int((len(values) - 1) * fraction))], 3) if values else None
        return dict(calls=len(recent), distinct=sum(now - 1 <= item[0] <= now for item in distinct),
                    gap_p50_ms=percentile(gaps, .5), gap_p95_ms=percentile(gaps, .95),
                    gap_max_ms=percentile(gaps, 1), media_step_p95_ms=percentile(steps, .95),
                    gaps_over_25ms=sum(gap > 25 for gap in gaps),
                    gaps_over_50ms=sum(gap > 50 for gap in gaps),
                    skipped_frames=sum(max(0, round((b[1] - a[1]) * fps) - 1)
                                       for a, b in pairs if fps > 0 and b[1] >= a[1]),
                    backwards=sum(b[1] < a[1] for a, b in pairs))


class StageTimings:
    """Bounded one-second wall-time samples; retain numbers, never media objects."""
    def __init__(self):
        self.enabled = os.environ.get('DDM_MATCH_DIAGNOSTICS', '1') != '0'
        self.samples = deque(maxlen=2048)

    def record(self, stage, elapsed, items=1, payload_bytes=0, at=None):
        if self.enabled:
            self.samples.append((time.perf_counter() if at is None else at, stage,
                                 elapsed * 1000, items, payload_bytes))

    def stats(self, now=None):
        now = time.perf_counter() if now is None else now
        samples = tuple(self.samples)
        groups = {}
        for at, stage, elapsed, items, size in samples:
            if now - 1 <= at <= now:
                groups.setdefault(stage, []).append((elapsed, items, size))
        result = {}
        for stage, values in groups.items():
            durations = sorted(item[0] for item in values)
            result[stage] = dict(calls=len(values), items=sum(item[1] for item in values),
                                 payload_bytes=sum(item[2] for item in values),
                                 total_ms=round(sum(durations), 3),
                                 p50_ms=round(durations[(len(durations) - 1) // 2], 3),
                                 p95_ms=round(durations[int((len(durations) - 1) * .95)], 3),
                                 max_ms=round(durations[-1], 3))
        return dict(window_s=1, capacity_limited=bool(len(samples) == self.samples.maxlen
                    and samples[0][0] >= now - 1), stages=result)


class AudioReadMetrics:
    """Numeric PCM read continuity; no audio content or sound-quality score."""
    def __init__(self):
        self.enabled = os.environ.get('DDM_MATCH_DIAGNOSTICS', '1') != '0'
        self.samples = deque(maxlen=1024)
        self.previous = {}
        self.serial = 0

    def reset(self):
        self.previous.clear()
        self.samples.clear()

    def record(self, key, start, duration, rate, channel, gain, at=None):
        if not self.enabled:
            return
        now = time.perf_counter() if at is None else at
        previous = self.previous.get(key)
        if previous is None:
            self.serial += 1
            slot = self.serial
        else:
            slot = previous[0]
        # Returning after isolation is a new baseline, not a block discontinuity.
        gap = (start - previous[2]) * 1000 if previous is not None and now - previous[1] < .2 else None
        self.previous[key] = (slot, now, start + duration)
        if len(self.previous) > 32:
            self.previous.pop(next(iter(self.previous)))
        self.samples.append((now, slot, gap, rate, channel, gain))

    def stats(self, now=None):
        now = time.perf_counter() if now is None else now
        samples = tuple(self.samples)
        groups = {}
        for sample in samples:
            if now - 1 <= sample[0] <= now:
                groups.setdefault(sample[1], []).append(sample)
        sources = []
        for slot, values in sorted(groups.items()):
            gaps = [item[2] for item in values if item[2] is not None]
            sources.append(dict(audio_slot=slot, blocks=len(values),
                gap_max_ms=round(max([0., *gaps]), 4),
                overlap_max_ms=round(-min([0., *gaps]), 4),
                discontinuities_over_1ms=sum(abs(gap) > 1 for gap in gaps),
                rate_min=round(min(item[3] for item in values), 6),
                rate_max=round(max(item[3] for item in values), 6),
                channels=sorted({item[4] for item in values}),
                gain_min=round(min(item[5] for item in values), 3),
                gain_max=round(max(item[5] for item in values), 3)))
        return dict(window_s=1, capacity_limited=bool(len(samples) == self.samples.maxlen
                    and samples[0][0] >= now - 1), sources=sources)


def stack(thread_id):
    result = []
    frame = sys._current_frames().get(thread_id)
    try:
        while frame is not None and len(result) < 16:
            result.append(dict(file=Path(frame.f_code.co_filename).name,
                               function=frame.f_code.co_name, line=frame.f_lineno))
            frame = frame.f_back
    finally:
        del frame
    return result


class SwitchProbe(QObject):
    """Sparse mode-switch evidence; file IO and bounded stall sampling stay off GUI."""
    def __init__(self, parent, path):
        super().__init__(parent)
        self.path = Path(path)
        self.enabled = os.environ.get('DDM_MATCH_DIAGNOSTICS', '1') != '0'
        self.events = deque(maxlen=256)
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.gui_thread = threading.get_ident()
        self.serial = 0
        self.session = str(time.time_ns())
        self.started = 0.
        self.active = None
        self.switching = False
        self.target = None
        self.button_painted = False
        self.animation_ticks = deque(maxlen=64)
        self.target_timeout = QTimer(self)
        self.target_timeout.setSingleShot(True)
        self.target_timeout.setInterval(5000)
        self.target_timeout.timeout.connect(self._target_timeout)

    def record(self, phase, **fields):
        if self.enabled and self.serial and not self.stopped.is_set():
            self.events.append(dict(time=time.time(), pid=os.getpid(), schema=1,
                                    session=self.session, switch=self.serial, phase=phase,
                                    elapsed_ms=round((time.perf_counter() - self.started) * 1000, 3), **fields))
            self.wake.set()

    def begin(self, enabled):
        self._clear_target()
        self.serial += 1
        self.started = time.perf_counter()
        self.button_painted = False
        self.animation_ticks.clear()
        self.switching = True
        self.record('request', enabled=bool(enabled))
        if self.enabled and not self.stopped.is_set() and self.thread is None:
            self.thread = threading.Thread(target=self._run, name='match-switch-diagnostics', daemon=True)
            self.thread.start()

    @contextmanager
    def span(self, stage):
        if not self.enabled or not self.switching:
            yield
            return
        previous = self.active
        started = time.perf_counter()
        self.active = (self.serial, stage, started)
        self.record('stage_begin', stage=stage)
        try:
            yield
        finally:
            self.record('stage_end', stage=stage, duration_ms=round((time.perf_counter() - started) * 1000, 3))
            self.active = previous

    def button_paint(self):
        if not self.button_painted:
            self.button_painted = True
            self.record('button_paint_completed')

    def animation_tick(self, _value):
        if self.enabled:
            self.animation_ticks.append(time.perf_counter())

    def animation_finished(self):
        ticks = tuple(self.animation_ticks)
        self.record('button_animation_finished', ticks=len(ticks),
                    tick_gap_max_ms=round(max((b - a for a, b in zip(ticks, ticks[1:])), default=0) * 1000, 3))

    def finish(self, enabled):
        self.record('applied', enabled=enabled)
        self.switching = False

    def watch_target(self, widget):
        self._clear_target()
        if self.enabled and widget is not None:
            self.target = widget
            widget.installEventFilter(self)
            self.target_timeout.start()

    def _clear_target(self):
        self.target_timeout.stop()
        if self.target is not None:
            self.target.removeEventFilter(self)
            self.target = None

    def _target_timeout(self):
        self.record('target_paint_timeout')
        self._clear_target()

    def eventFilter(self, watched, event):
        if watched is self.target and event.type() == QEvent.Paint:
            # Dispatch time, not completion of child paints or physical presentation.
            self.record('target_paint_event')
            self._clear_target()
        return False

    def close(self):
        self._clear_target()
        self.active = None
        self.stopped.set()
        self.wake.set()

    def _run(self):
        handler = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(self.path, maxBytes=2 * 1024 ** 2, backupCount=2, encoding='utf-8')
            last_sample = 0.
            sampled_serial, samples = 0, 0
            while True:
                self.wake.wait(.05 if self.active else 1.)
                self.wake.clear()
                active = self.active
                now = time.perf_counter()
                if active is not None:
                    serial, stage, started = active
                    if serial != sampled_serial:
                        sampled_serial, samples = serial, 0
                    if now - started >= .15 and now - last_sample >= .25 and samples < 16:
                        self.events.append(dict(time=time.time(), pid=os.getpid(), schema=1,
                                                session=self.session, switch=serial, phase='slow_stage', stage=stage,
                                                duration_ms=round((now - started) * 1000, 3),
                                                stack=stack(self.gui_thread)))
                        samples += 1
                        last_sample = now
                while self.events:
                    payload = self.events.popleft()
                    handler.emit(logging.makeLogRecord(dict(msg=json.dumps(payload, ensure_ascii=False))))
                if self.stopped.is_set():
                    break
        except OSError as error:
            print(f'[二路切换诊断] {type(error).__name__}', file=sys.stderr)
        finally:
            if handler is not None:
                handler.close()


def picture_state(decoder, target, displayed, waiting):
    result = dict(target=target, displayed=displayed, waiting=waiting,
                  display_lag_ms=round((target - displayed) * 1000, 2) if displayed is not None else None)
    history = decoder.history
    # Reconnection can retain an older segment. Do not traverse it with blocking bounds().
    previous = getattr(history, 'previous', None)
    for candidate in (previous, history) if previous is not None else (history,):
        if not candidate.lock.acquire(blocking=False):
            result['history_busy'] = True
            continue
        try:
            bounds = (candidate.frames[0][0], candidate.frames[-1][0]) if candidate.frames else None
            if bounds and bounds[0] <= target <= bounds[1]:
                result.update(encoded_bounds=bounds, encoded_ahead_s=round(bounds[1] - target, 3),
                              received_frames=candidate.received_frames)
                intervals = tuple(getattr(candidate, 'intervals', ()))
                for index, (start, end) in enumerate(intervals):
                    if target <= end + .05:
                        continuous = (max(start, bounds[0]), min(end, bounds[1]))
                        result.update(contiguous_bounds=continuous,
                            contiguous_ahead_s=round(max(0, continuous[1] - target), 3)
                            if continuous[0] <= target <= continuous[1] else 0.)
                        if index + 1 < len(intervals):
                            result['following_bounds'] = intervals[index + 1]
                        break
                break
            result['encoded_bounds'] = bounds
        finally:
            candidate.lock.release()
    reader = decoder.pictures
    now = time.perf_counter()
    fps = getattr(decoder, 'fps', 0)
    result['source_fps'] = fps
    if hasattr(decoder, 'received_cadence'):
        result['received_cadence'] = decoder.received_cadence.stats(now, fps)
    if hasattr(reader, 'decoded_cadence'):
        result['decoded_cadence'] = reader.decoded_cadence.stats(now, fps)
    if reader.lock.acquire(blocking=False):
        try:
            keys = tuple(reader.images)
            result.update(rgb_count=len(keys), rgb_bounds=(min(keys), max(keys)) if keys else None,
                          decoded=reader.decoded, decode_ms=round(reader.decode_ms, 3),
                          codec_creations=reader.codec_creations, reset_reason=reader.reset_reason,
                          decode_phase=reader.decode_phase)
        finally:
            reader.lock.release()
    else:
        result['picture_busy'] = True
    return result


class PlaybackProbe(QObject):
    def __init__(self, parent, path):
        super().__init__(parent)
        self.path = Path(path)
        self.enabled = os.environ.get('DDM_MATCH_DIAGNOSTICS', '1') != '0'
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.pulse)
        self.stopped = threading.Event()
        self.thread = None
        self.gui_thread = threading.get_ident()
        self.last_tick = time.perf_counter()
        self.stage = 'idle'
        self.frame = None
        self.transitions = deque(maxlen=60)
        self.renders = deque(maxlen=240)
        self.paints = deque(maxlen=120)
        self.mouse_count = 0
        self.mouse_target = ''
        self.mouse_time = 0.0
        self.audio = None
        self.submitted = FrameCadence()
        self.painted = FrameCadence()
        self.clock_steps = FrameCadence()
        self.surface = {}
        self.pipeline_sources = ()
        self.clock_gaps = deque(maxlen=32)
        self.timings = StageTimings()
        self.cpu_observation = (time.perf_counter(), time.process_time())

    def start(self):
        if not self.enabled or (self.thread is not None and self.thread.is_alive()):
            return
        self.stopped.clear()
        self.frame = None
        self.transitions.clear()
        self.renders.clear()
        self.paints.clear()
        self.submitted.samples.clear()
        self.painted.samples.clear()
        self.clock_steps.samples.clear()
        self.clock_gaps.clear()
        self.surface = {}
        self.pipeline_sources = ()
        self.timings.samples.clear()
        self.cpu_observation = (time.perf_counter(), time.process_time())
        self.last_render = self.last_tick = time.perf_counter()
        self.stage = 'idle'
        self.thread = threading.Thread(target=self._run, name='match-sync-diagnostics', daemon=True)
        self.thread.start()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(.3)
            if not self.thread.is_alive():
                self.thread = None
        self.frame = None
        self.transitions.clear()
        self.pipeline_sources = ()

    def pulse(self):
        self.last_tick = time.perf_counter()

    def mouse(self, watched):
        self.mouse_count += 1
        self.mouse_target = type(watched).__name__
        self.mouse_time = time.perf_counter()

    def observe(self, decoder, target, displayed, waiting):
        if not hasattr(decoder, 'pictures'):
            self.frame = None
            return
        frame = (decoder, target, displayed, waiting)
        previous = self.frame
        if previous is None or previous[0] is not decoder or previous[3] != waiting:
            self.transitions.append((time.time(), frame))
        self.frame = frame
        self.clock_steps.record(time.perf_counter(), target)

    def begin(self):
        now = time.perf_counter()
        self.render_gap = (now - self.last_render) * 1000
        self.last_render = self.started = self.checkpoint = now
        self.stages = []
        self.stage = 'metadata'

    def mark(self, stage):
        now = time.perf_counter()
        self.stages.append((self.stage, round((now - self.checkpoint) * 1000, 3)))
        self.checkpoint, self.stage = now, stage

    def end(self):
        self.mark('idle')
        self.renders.append((time.perf_counter(), self.render_gap,
                             (time.perf_counter() - self.started) * 1000, tuple(self.stages)))

    def snapshot(self):
        now = time.perf_counter()
        cpu = time.process_time()
        previous_wall, previous_cpu = self.cpu_observation
        self.cpu_observation = (now, cpu)
        renders = [item for item in tuple(self.renders) if now - item[0] < 1]
        paints = [item for item in tuple(self.paints) if now - item[0] < 1]
        slowest = max(renders, key=lambda item: item[2], default=None)
        last_fill = self.audio.last_fill if self.audio is not None else None
        result = dict(stage=self.stage, render_calls=len(renders),
                      render_gap_ms=round(max((item[1] for item in renders), default=0), 3),
                      render_ms=round(slowest[2], 3) if slowest else 0,
                      stages_ms=dict(slowest[3]) if slowest else {},
                      paint_calls=len(paints), paint_ms=round(max((item[1] for item in paints), default=0), 3),
                      mouse_events=self.mouse_count, mouse_target=self.mouse_target,
                      mouse_age_ms=round((now - self.mouse_time) * 1000, 1) if self.mouse_time else None,
                      audio_gap_ms=round((now - last_fill) * 1000, 1) if last_fill is not None else None)
        frame = self.frame
        fps = getattr(frame[0], 'fps', 0) if frame is not None else 0
        result.update(surface=self.surface, submitted_cadence=self.submitted.stats(now, fps),
                      painted_cadence=self.painted.stats(now, fps), clock_cadence=self.clock_steps.stats(now))
        result['process_cpu_cores'] = round((cpu - previous_cpu) / max(.001, now - previous_wall), 3)
        result['gui_pipeline'] = self.timings.stats(now)
        result['clock_gap_recoveries'] = [dict(at=at, before=before, after=after)
            for at, before, after in tuple(self.clock_gaps) if now - 1 <= at <= now]
        if self.audio is not None:
            result['audio_reads'] = self.audio.read_metrics.stats(now)
            result['audio_pipeline'] = self.audio.timings.stats(now)
        result['source_pipelines'] = [dict(slot=slot, source_fps=decoder.fps,
            analysis_size=decoder.analysis_size, capture=decoder.timings.stats(now),
            display=decoder.pictures.timings.stats(now),
            acquisition=decoder.acquisition.snapshot() if hasattr(decoder, 'acquisition') else {})
            for slot, decoder in self.pipeline_sources]
        if frame is not None:
            result['picture'] = picture_state(*frame)
        return result

    def _run(self):
        handler = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(self.path, maxBytes=2 * 1024 ** 2, backupCount=2, encoding='utf-8')
            def write(event, **fields):
                payload = dict(time=datetime.now().astimezone().isoformat(timespec='milliseconds'),
                               pid=os.getpid(), event=event, **fields)
                handler.emit(logging.makeLogRecord(dict(msg=json.dumps(payload, ensure_ascii=False))))
            from .video import PictureReader
            from .media import Decoder, analysis_gray
            write('start', schema=3, picture_code=hashlib.sha256(marshal.dumps(PictureReader.get.__code__)).hexdigest(),
                  capture_code=hashlib.sha256(marshal.dumps(Decoder._capture.__code__)).hexdigest(),
                  analysis_gray_code=hashlib.sha256(marshal.dumps(analysis_gray.__code__)).hexdigest(),
                  audio_read_metrics=1, pts_gap_recovery=2, stack_budget_window_s=60, stream_acquisition=1)
            sampled = 0
            sample_window = time.perf_counter()
            last_stack = last_report = 0.0
            reported_stall = False
            while not self.stopped.wait(.05):
                now = time.perf_counter()
                if now - sample_window >= 60:
                    sampled, sample_window = 0, now
                # Coalesce rapid oscillations; preserve the transition target and timestamp.
                if self.transitions:
                    transitions = []
                    while self.transitions:
                        stamp, frame = self.transitions.popleft()
                        transitions.append(dict(at=stamp, **picture_state(*frame)))
                    write('frame_wait', transitions=transitions)
                gap = now - self.last_tick
                if gap < .15:
                    reported_stall = False
                if gap >= .15 and not reported_stall and sampled < 120 and now - last_stack >= 1:
                    write('ui_stall', gap_ms=round(gap * 1000, 2), stage=self.stage,
                          mouse_target=self.mouse_target, stack=stack(self.gui_thread))
                    sampled += 1
                    last_stack, reported_stall = now, True
                if now - last_report >= 1:
                    state = self.snapshot()
                    frame = self.frame
                    if frame is not None and state.get('picture', {}).get('waiting') and sampled < 120:
                        reader_thread = frame[0].pictures.thread
                        if reader_thread is not None:
                            state['picture_stack'] = stack(reader_thread.ident)
                            sampled += 1
                    write('playback', **state)
                    last_report = now
            write('stop')
        except OSError as error:
            print(f'[二路诊断] {type(error).__name__}', file=sys.stderr)
        finally:
            if handler is not None:
                handler.close()
