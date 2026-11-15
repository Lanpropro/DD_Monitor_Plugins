"""Revalidate a reviewed timer calibration after a media timeline changes."""
import logging
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from .clock_capture import capture_band, recognize_bands
from .clock_sync import ClockReading, calculate_offsets, suggest_transition


def read_clocks(bands, readings, anchors, direction):
    result = {}
    for key, frames in bands.items():
        values = readings.get(key, [])
        times = tuple(stamp for stamp, _image in frames)
        transition = suggest_transition(times, values, direction)
        if transition is None:
            raise ValueError('计时器未可靠翻秒，请重新确认对齐')
        result[key] = ClockReading(values[0], times, transition, anchors[key])
    return result


def verify_progress(previous, current, direction):
    """Do not silently carry a calibration across a stopped/reset round clock."""
    for key, reading in current.items():
        old = previous[key]
        before, error_before = old.at_anchor(direction)
        value, error = reading.at_anchor(direction)
        elapsed = reading.anchor - old.anchor
        if elapsed <= 0 or abs(value - before - direction * elapsed) > 2 + error + error_before:
            raise ValueError('计时进度已变化，可能暂停或换回合，请重新确认对齐')


def verify_repeat(first, second, direction, shifts, reference):
    """Two independent bands must agree in offset AND timer progression."""
    desired, errors = calculate_offsets(second, shifts, reference, direction)
    previous, old_errors = calculate_offsets(first, shifts, reference, direction)
    for key in second:
        a, ea = first[key].at_anchor(direction)
        b, eb = second[key].at_anchor(direction)
        elapsed = second[key].anchor - first[key].anchor
        media_elapsed = second[key].times[-1] - first[key].times[-1]
        if (elapsed < 1.5 or media_elapsed < 1.5
                or abs(b - a - direction * elapsed) > .2 + ea + eb):
            raise ValueError('两次计时进度不一致，请重新确认对齐')
        if abs(desired[key] - previous[key]) > .2 + errors[key] + old_errors[key]:
            raise ValueError('两次偏移不一致，请重新确认对齐')
    return desired


class RecoveryResult(QObject):
    finished = Signal(object)


class ClockRecovery(QObject):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.profile = None
        self.pending = set()
        self.failed = False
        self.busy = False
        self.worker = None
        self.active_job = None
        self.busy_token = self.cancelled = threading.Event()
        self.seen = {}
        self.first = None
        self.bridge = RecoveryResult(self)
        self.bridge.finished.connect(self.complete, Qt.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)

    def cancel(self):
        self.cancelled.set()
        self.cancelled = threading.Event()
        for key in self.pending:
            row = self.viewer.rows.get(key)
            if row is not None:
                row.awaiting_confirmation = False
        self.pending.clear()
        self.profile = None
        self.first = None
        self.failed = False
        self.timer.stop()

    def arm(self, readings, regions, direction):
        self.cancel()
        viewer = self.viewer
        self.profile = (viewer.alignment.reference, dict(readings), dict(regions), direction,
                        dict(viewer.shifts()), self.identities(readings))
        self.seen = {key: (decoder, history) for key, decoder, history in self.identities(readings)}
        self.timer.start()

    def identities(self, keys):
        return tuple((key, row.decoder, row.decoder.history) for key in keys
                     if (row := self.viewer.rows.get(key)) is not None and row.decoder is not None)

    def source_reset(self, key):
        if self.profile is None or key not in self.profile[1]:
            return
        row = self.viewer.rows.get(key)
        if row is None or row.decoder is None:
            return
        identity = (row.decoder, row.decoder.history)
        if self.seen.get(key) == identity:
            return
        self.seen[key] = identity
        reference, readings, _regions, _direction, _shifts, _identities = self.profile
        targets = set(readings) - {reference} if key == reference else {key}
        self.pending.update(targets)
        self.cancelled.set()
        self.cancelled = threading.Event()
        self.first = None
        self.failed = False
        self.deadline = time.monotonic() + 60
        self.next_sample = time.monotonic() + 2
        for target in targets:
            row = self.viewer.rows.get(target)
            if row is not None:
                row.awaiting_confirmation = True
                row.mix_gain = 0
                row.match_text = '来源时间轴已变化，等待计时器复核'
                row.refresh_status()

    def tick(self):
        if (self.busy and self.busy_token.is_set() and self.worker is not None
                and not self.worker.is_alive()):
            # Cancelled workers deliberately do not deliver a Qt callback.
            self.busy = False
        if self.profile is None:
            return
        viewer = self.viewer
        reference, readings, regions, direction, shifts, identities = self.profile
        current = viewer.shifts()
        if (not viewer.running or viewer.alignment.automatic or viewer.alignment.reference != reference
                or any(key not in viewer.rows or abs(current.get(key, float('inf')) - shifts[key]) > 1e-6
                       or key in viewer.independent_playback for key in readings)):
            self.cancel()
            return
        latest = self.identities(readings)
        if latest != identities:
            changed = {key for key, decoder, history in latest
                       if (key, decoder, history) not in identities}
            for key in changed:
                self.source_reset(key)
            self.profile = (reference, readings, regions, direction, shifts, latest)
        if not self.pending or self.failed:
            return
        if time.monotonic() > self.deadline:
            self.fail('恢复缓存或计时器复核超时，请重新确认对齐')
            return
        if (self.busy or time.monotonic() < self.next_sample or viewer.replay.active
                or viewer.playback_rate != 1 or getattr(viewer, 'clock_dialog', None) is not None
                or any(viewer.rows[key].paused for key in readings)):
            return
        keys = tuple(key for key in readings if key == reference or key in self.pending)
        requests = []
        # Sample the NEW histories near their retained tails, independently of
        # the stale playback offset; previous-history frames cannot validate a reset.
        for key in keys:
            row = viewer.rows[key]
            if row.decoder is None:
                return
            history = row.decoder.history
            bounds = history.bounds()
            if bounds is None or bounds[1] - bounds[0] < 2:
                return
            anchor = bounds[1] - .2
            if history.segment_at(anchor - 1.5) is not history or history.segment_at(anchor) is not history:
                return
            requests.append((key, history, anchor, regions[key]))
        sample_clock = time.monotonic()
        anchors = {key: sample_clock + shifts[key] for key in keys}
        token = self.cancelled
        job = self.active_job = object()
        self.busy_token = token
        signature = self.identities(keys)
        self.busy = True
        bridge = self.bridge
        def work():
            try:
                bands = {key: capture_band(history, anchor, region, token)
                         for key, history, anchor, region in requests}
                values, note = recognize_bands(bands, token)
                if note:
                    raise ValueError(note)
                measured = read_clocks(bands, values, anchors, direction)
                result = (job, token, signature, measured, '')
            except Exception as error:
                result = (job, token, signature, None, str(error) if isinstance(error, ValueError)
                          else '计时器复核失败，请重新确认对齐')
            # Bridge lifetime follows the GUI owner; a cancelled job never emits.
            if not token.is_set():
                bridge.finished.emit(result)
        self.worker = threading.Thread(target=work, name='match-clock-recovery', daemon=True)
        self.worker.start()

    @Slot(object)
    def complete(self, result):
        job, token, identities, measured, error = result
        if job is not self.active_job:
            return
        if token.is_set() or self.profile is None or identities != self.identities(key for key, *_rest in identities):
            self.busy = False
            return
        # User changes between dispatch and delivery also invalidate this result.
        self.next_sample = float('inf')
        self.tick()
        self.busy = False
        if token.is_set() or self.profile is None or self.failed:
            return
        if (self.viewer.replay.active or self.viewer.playback_rate != 1
                or getattr(self.viewer, 'clock_dialog', None) is not None
                or any(self.viewer.rows[key].paused for key, *_rest in identities)):
            self.first = None
            self.next_sample = time.monotonic() + 2
            return
        reference, readings, regions, direction, shifts, _old_identities = self.profile
        try:
            if error:
                raise ValueError(error)
            verify_progress(readings, measured, direction)
            if self.first is None:
                self.first = measured
                self.next_sample = time.monotonic() + 2
                return
            desired = verify_repeat(self.first, measured, direction, shifts, reference)
            from .clock_dialog import apply_offsets
            apply_offsets(self.viewer, desired, tuple(measured))
        except ValueError as error:
            self.fail(str(error))
            return
        for key in self.pending:
            row = self.viewer.rows[key]
            row.needs_epoch_samples = False
            row.match_text = '计时器两次复核通过，已恢复对齐'
            row.refresh_status()
        readings.update(measured)
        self.profile = (reference, readings, regions, direction, dict(self.viewer.shifts()),
                        self.identities(readings))
        self.pending.clear()
        self.first = None
        self.viewer.notice.setText('计时器复核通过，已恢复对齐')
        logging.getLogger(__name__).info('Clock recovery verified: sources=%s', len(measured))

    def fail(self, message):
        self.cancelled.set()
        self.failed = True
        self.first = None
        for key in self.pending:
            row = self.viewer.rows.get(key)
            if row is not None:
                row.match_text = message
                row.refresh_status()
        self.viewer.notice.setText(message + '；打开“计时器对齐”可沿用原选区')
        logging.getLogger(__name__).info('Clock recovery requires review: %s', message)

    def status_text(self):
        if not self.pending:
            return ''
        if self.failed:
            return '计时器对齐需要重新确认；打开“计时器对齐”可沿用原选区'
        return '来源时间轴已变化，正在复核计时器；相关副路暂时静音'
