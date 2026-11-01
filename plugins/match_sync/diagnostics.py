"""Bounded playback evidence; the writer/sampler never calls Qt or waits on media locks."""
from collections import deque
from datetime import datetime
import hashlib
import json
from logging.handlers import RotatingFileHandler
import logging
import marshal
import os
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt


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
        self.surface = {}
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
            write('start', schema=2, picture_code=hashlib.sha256(marshal.dumps(PictureReader.get.__code__)).hexdigest())
            sampled = 0
            last_stack = last_report = 0.0
            reported_stall = False
            while not self.stopped.wait(.05):
                now = time.perf_counter()
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
