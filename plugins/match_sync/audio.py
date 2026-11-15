"""Audio device ownership, immutable GUI snapshots and the presentation clock."""
from __future__ import annotations

from collections import deque
import sys
import threading
import time

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from .diagnostics import AudioReadMetrics, StageTimings
from .engine import RATE, mix_pcm, resample_pcm, route_pcm_s16_stereo


class AudioFeed(QObject):
    """Own the output device and its timer entirely on the audio thread."""
    def __init__(self, pump, output, fmt):
        super().__init__()
        self.pump, self.output, self.fmt = pump, output, fmt
        self.timer = None

    @Slot()
    def start(self):
        pump = self.pump
        pump.sink = QAudioSink(self.output, self.fmt, self)
        pump.sink.stateChanged.connect(self.state_changed)
        pump.sink.setBufferSize(RATE * 4 * 200 // 1000)
        pump.device = pump.sink.start()
        if pump.device is None:
            pump.status.emit("无法启动音频输出")
            self.stop()
            return
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.fill)
        self.timer.start()
        self.fill()
        pump.status.emit("声音已接管，结束观看后恢复原程序的静音设定")

    @Slot()
    def fill(self):
        self.pump.fill()

    @Slot()
    def flush(self):
        pump = self.pump
        with pump.lock:
            if pump.sink is None:
                return
            clock = pump._clock() if pump.flush_clock is None else pump.flush_clock
            pump.sink.reset()
            pump.device = pump.sink.start()
            pump.anchor = clock
            pump.written = pump.played = 0
            pump.media_written = 0.0
            pump.clock_segments.clear()
            pump.clock_snapshot = clock
            pump.clock_observation = pump.clock_floor = None
            pump.replay_cursors.clear()
            pump.read_metrics.reset()

    @Slot()
    def state_changed(self):
        if self.pump.sink is not None and self.pump.sink.error().value:
            self.pump.status.emit("音频设备出错；检查系统默认设备后停止并重新开始")
            self.stop()

    @Slot()
    def stop(self):
        if self.timer is not None:
            self.timer.stop()
        with self.pump.lock:
            sink, self.pump.sink = self.pump.sink, None
            self.pump.device = None
            self.pump.clock_observation = self.pump.clock_floor = None
        if sink is not None:
            sink.stop()
            sink.deleteLater()


class AudioPump(QObject):
    status = Signal(str)

    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.sink = None
        self.device = None
        self.anchor = 0.0
        self.written = 0
        self.media_written = 0.0
        self.clock_segments = deque()
        self.correction = 0.0
        self.last_fill = None
        self.lock = threading.RLock()
        self.thread = None
        self.feed = None
        self.snapshot = (1.0, ())
        self.played = 0
        self.clock_snapshot = None
        self.clock_observation = self.clock_floor = None
        self.flush_clock = None
        self.replay_offset = None
        self.replay_cursors = {}
        self.read_metrics = AudioReadMetrics()
        self.timings = StageTimings()
        self.independent_histories = frozenset()
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.update_snapshot)
        self.devices = QMediaDevices(self)
        self.device_id = None
        self.devices.audioOutputsChanged.connect(self._device_changed)

    def start(self):
        self.stop()
        self.anchor = time.monotonic()
        self.written = 0
        self.media_written = 0.0
        self.clock_segments.clear()
        self.correction = 0.0
        self.played = 0
        self.clock_snapshot = None
        output = QMediaDevices.defaultAudioOutput()
        self.device_id = bytes(output.id())
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if output.isNull() or not output.isFormatSupported(fmt):
            self.status.emit("音频输出不可用；请检查系统默认播放设备")
            return
        self.update_snapshot()
        self.thread = QThread(self)
        self.feed = AudioFeed(self, output, fmt)
        self.feed.moveToThread(self.thread)
        self.thread.started.connect(self.feed.start)
        self.thread.finished.connect(self.feed.deleteLater)
        self.thread.start()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.last_fill = None
        if self.thread is not None:
            QMetaObject.invokeMethod(self.feed, "stop", Qt.BlockingQueuedConnection)
            self.thread.quit()
            self.thread.wait()
            self.thread.deleteLater()
            self.thread = self.feed = None
        sink, self.sink = self.sink, None
        if sink is not None:
            sink.stop()
            sink.deleteLater()
        self.device = None
        self.clock_snapshot = None
        self.clock_observation = self.clock_floor = None
        self.snapshot = (1.0, ())
        self.read_metrics.reset()
        self.timings.samples.clear()

    def flush(self, target_clock=None):
        self.replay_offset = None
        self.update_snapshot()
        self.flush_clock = None if target_clock is None else target_clock - self.correction
        if self.thread is not None:
            QMetaObject.invokeMethod(self.feed, 'flush', Qt.BlockingQueuedConnection)

    def clock(self):
        observation = self.clock_observation
        if observation is not None:
            observed, played, queued, anchor, segments = observation
            played = min(queued, played + max(0, time.perf_counter() - observed) * RATE)
            value = anchor + played / RATE
            for first, end, media, rate in segments:
                value = anchor + media + min(max(0, played - first), end - first) / RATE * rate
                if played <= end:
                    break
            # Device observations arrive in blocks; a late report must not
            # briefly move the picture backwards. Flush/start reset this floor.
            floor = self.clock_floor
            self.clock_floor = value if floor is None else max(floor, value)
            return self.clock_floor + self.correction
        # Audio mixing/device I/O can outlast a GUI frame. Reuse the last
        # observed device position rather than queue the GUI behind that work.
        if self.lock.acquire(blocking=False):
            try:
                self.clock_snapshot = self._clock()
            finally:
                self.lock.release()
        value = self.clock_snapshot
        return (time.monotonic() if value is None else value) + self.correction

    def _clock(self):
        if self.sink is None:
            return time.monotonic()
        played = self.played if self.thread is not None else self.sink.processedUSecs() * RATE / 1_000_000
        while len(self.clock_segments) > 1 and self.clock_segments[0][1] <= played:
            self.clock_segments.popleft()
        if self.clock_segments:
            first, end, media, rate = self.clock_segments[0]
            return self.anchor + media + min(max(0, played - first), end - first) / RATE * rate
        return self.anchor + played / RATE

    def update_snapshot(self):
        # Widgets and room collections belong to the GUI thread. The output
        # thread receives only immutable values and thread-safe media histories.
        shifts = self.viewer.shifts()
        rate = self.viewer.playback_rate
        replaying = self.viewer.replay.active
        clock = self.clock()
        shown_clock, shown_shifts = self.viewer.replay.presentation(clock, shifts)
        if replaying:
            rate = 1.0
            if self.replay_offset is None:
                self.replay_offset = shown_clock - clock
            shown_clock = clock + self.replay_offset
            shown_shifts = self.viewer.replay.shifts_at(shown_clock)
            following = self.viewer.replay.shifts_at(shown_clock + .05)
        else:
            self.replay_offset = None
        inputs = []
        for room_id, row in self.viewer.rows.items():
            audible, channel, volume = row.audio_controls
            if (row.decoder is not None and audible and not row.paused
                    and room_id in shown_shifts
                    and (replaying or room_id in self.viewer.independent_playback or not self.viewer.sync_waiting and row.sync_state == "已同步")):
                if replaying:
                    change = following.get(room_id, shown_shifts[room_id]) - shown_shifts[room_id]
                    local_rate = 1 + change / .05 if abs(change) < .1 else 1.0
                else:
                    local_rate = 1.0 if room_id in self.viewer.independent_playback else rate + self.viewer.relative_rates.get(room_id, 1.0) - 1.0
                shift = shown_shifts[room_id] + (self.replay_offset if replaying else 0.)
                inputs.append((row.decoder.history, shift,
                               local_rate,
                               channel, volume * (1.0 if replaying else row.mix_gain)))
        independent = frozenset(row.decoder.history for key, row in self.viewer.rows.items()
                                if key in self.viewer.independent_playback and row.decoder is not None)
        if independent != self.independent_histories:
            self.independent_histories = independent
        snapshot = (rate, tuple(inputs))
        if snapshot != self.snapshot:
            self.snapshot = snapshot

    def fill(self):
        if self.thread is None:
            self.update_snapshot()
        started = time.perf_counter()
        with self.lock:
            self.timings.record('audio_lock_wait', time.perf_counter() - started)
            self._fill()

    def _fill(self):
        if self.sink is None or self.device is None:
            return
        now = time.perf_counter()
        if self.last_fill is not None and now - self.last_fill > .1:
            print(f"[二路音频] 供给间隔 {(now - self.last_fill) * 1000:.1f}ms", file=sys.stderr, flush=True)
        self.last_fill = now
        self.played = self.sink.processedUSecs() * RATE / 1_000_000
        self.clock_snapshot = self._clock()
        rate, sources = self.snapshot
        for _ in range(3):
            if self.sink is None or self.device is None:
                break
            frames = min(2048, self.sink.bytesFree() // 4)
            if frames < 240:
                break
            media_frames = max(1, round(frames * rate))
            clock = self.anchor + self.media_written + self.correction
            inputs = []
            positions = []
            started = time.perf_counter()
            for history, shift, local_rate, channel, volume in sources:
                target = clock + shift
                cursor = self.replay_cursors.get(history) if self.replay_offset is not None or history in self.independent_histories else None
                if cursor is not None and abs(clock - cursor[0]) < .01 and abs(target - cursor[1]) < .05:
                    target = cursor[1]
                source_frames = max(1, round(frames * local_rate))
                pcm = history.pcm_at(target, source_frames)
                positions.append((history, target, source_frames))
                pcm = route_pcm_s16_stereo(pcm, channel)
                inputs.append((resample_pcm(pcm, frames), volume))
            self.timings.record('audio_prepare', time.perf_counter() - started)
            started = time.perf_counter()
            mixed = mix_pcm(inputs, frames)
            self.timings.record('audio_mix', time.perf_counter() - started)
            started = time.perf_counter()
            count = self.device.write(mixed)
            self.timings.record('audio_device_write', time.perf_counter() - started)
            if count <= 0:
                break
            written = count // 4
            actual_rate = media_frames / frames
            self.clock_segments.append((self.written, self.written + written, self.media_written, actual_rate))
            self.media_written += written / RATE * actual_rate
            for (history, target, source_frames), source in zip(positions, sources):
                self.read_metrics.record(id(history), target,
                    source_frames / RATE * written / frames, source[2], source[3], source[4])
                if self.replay_offset is not None or history in self.independent_histories:
                    self.replay_cursors[history] = (clock + written / RATE * actual_rate,
                        target + source_frames / RATE * written / frames)
            self.written += written
        self.clock_snapshot = self._clock()
        self.clock_observation = (now, self.played, self.written, self.anchor, tuple(self.clock_segments))

    def _device_changed(self):
        current = bytes(QMediaDevices.defaultAudioOutput().id())
        if self.viewer.running and current != self.device_id:
            self.start()
