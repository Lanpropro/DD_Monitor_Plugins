"""Group replay uses historical presentation positions, leaving live alignment running."""
from bisect import bisect_right
from collections import deque
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QWidget

from .engine import RATE
from .panels import console_button_style
from .replay_cache import REPLAY_SECONDS


class ReplayTimeline:
    def __init__(self):
        self.states = deque()
        self.messages = deque()
        self.delay = 0.0
        self.cursor = None

    @property
    def active(self):
        return self.delay > 0

    def observe(self, now, live_clock, shifts):
        if not self.states or now - self.states[-1][0] >= .2:
            self.states.append((now, {key: live_clock + shift - now for key, shift in shifts.items()}))
        while len(self.states) > 1 and self.states[1][0] < now - REPLAY_SECONDS:
            self.states.popleft()
        while self.messages and self.messages[0][0] < now - REPLAY_SECONDS:
            self.messages.popleft()

    def shifts_at(self, stamp):
        index = bisect_right(self.states, stamp, key=lambda state: state[0]) - 1
        if not self.states:
            return {}
        index = max(0, index)
        before, shifts = self.states[index]
        if index + 1 >= len(self.states):
            return shifts
        after, following = self.states[index + 1]
        fraction = max(0, min(1, (stamp - before) / (after - before)))
        return {key: value + (following[key] - value) * fraction
                if key in following and abs(following[key] - value) < .1 else value
                for key, value in shifts.items()}

    @staticmethod
    def valid(stamp, shifts, histories):
        if not histories or any(key not in shifts for key in histories):
            return False
        for key, history in histories.items():
            target = stamp + shifts[key]
            bounds = history.bounds_at(target)
            origin = history.origin
            audio = history.audio
            if (bounds is None or origin is None or not bounds[0] <= target <= bounds[1]
                    or not origin + max(0, audio.end - audio.capacity) / RATE <= target < origin + audio.end / RATE):
                return False
        return True

    def available(self, now, histories):
        lower = upper = None
        for stamp, shifts in reversed(self.states):
            if stamp < now - REPLAY_SECONDS:
                break
            if not self.valid(stamp, shifts, histories):
                if upper is not None:
                    break
                continue
            if upper is None:
                upper = stamp
            lower = stamp
        return (lower, upper) if lower is not None else None

    def seek(self, stamp, now, histories):
        bounds = self.available(now, histories)
        if bounds is None:
            return False
        stamp = min(max(stamp, bounds[0]), bounds[1])
        if not self.valid(stamp, self.shifts_at(stamp), histories):
            return False
        self.delay = max(.001, now - stamp)
        self.cursor = stamp
        return True

    def live(self):
        self.delay = 0.0
        self.cursor = None

    def presentation(self, live_clock, shifts, now=None):
        now = time.monotonic() if now is None else now
        if not self.active:
            return live_clock, shifts
        stamp = now - self.delay
        return stamp, self.shifts_at(stamp)

    def record_message(self, stamp, event):
        self.messages.append((stamp, dict(event)))

    def messages_between(self, start, end):
        first = bisect_right(self.messages, start, key=lambda message: message[0])
        last = bisect_right(self.messages, end, key=lambda message: message[0])
        return [dict(self.messages[index][1]) for index in range(first, last)]


class ReplayBar(QWidget):
    def __init__(self, viewer):
        slot = viewer.picture.status_label
        slot.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        super().__init__(slot)
        self.viewer = viewer
        self.setObjectName('MatchSyncReplayBar')
        self.setFixedHeight(26)
        centered = QHBoxLayout(slot)
        centered.setContentsMargins(0, 0, 0, 0)
        centered.addStretch(1)
        centered.addWidget(self, 0, Qt.AlignCenter)
        centered.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.toggle = QPushButton('重放')
        self.toggle.setFixedWidth(44)
        self.toggle.setFixedHeight(26)
        self.toggle.setStyleSheet(console_button_style('QPushButton'))
        self.toggle.clicked.connect(self.open_replay)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, REPLAY_SECONDS * 1000)
        self.slider.setInvertedAppearance(True)
        self.slider.setInvertedControls(True)
        self.slider.setSingleStep(1000)
        self.slider.setPageStep(10000)
        self.slider.setAccessibleName('整组重放，最多回看十分钟')
        self.slider.setToolTip('拖动后松开：主画面、各路声音和弹幕一起回看')
        self.slider.sliderReleased.connect(self.seek)
        self.label = QLabel('直播')
        self.slider.sliderMoved.connect(lambda value: self.label.setText(f'−{value // 60000:02d}:{value // 1000 % 60:02d}'))
        self.back = QPushButton('返回直播')
        self.back.setFixedWidth(64)
        self.back.setFixedHeight(26)
        self.back.setStyleSheet(console_button_style('QPushButton'))
        self.back.clicked.connect(viewer.return_live)
        layout.addWidget(self.toggle)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.label)
        layout.addWidget(self.back)
        self.expanded = False
        self.last_update = 0.0
        self.refresh(force=True)

    def open_replay(self):
        if self.viewer.replay.active and self.back.isHidden():
            self.viewer.return_live()
            return
        self.expanded = not self.expanded
        self.refresh(force=True)

    def seek(self):
        self.viewer.seek_replay(time.monotonic() - self.slider.value() / 1000)

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_update < .25:
            return
        self.last_update = now
        width = min(360, max(44, self.parentWidget().width() - 40))
        self.setFixedWidth(width if self.expanded or self.viewer.replay.active else 44)
        timeline = self.viewer.replay
        bounds = timeline.available(now, self.viewer.replay_histories())
        maximum = min(REPLAY_SECONDS, max(0, now - bounds[0])) if bounds else 0
        enabled = maximum > .5 and self.viewer.running
        self.toggle.setEnabled(enabled)
        self.toggle.setToolTip(f'整组回看：当前可用 {int(maximum)} 秒，最多 10 分钟')
        self.slider.setVisible((self.expanded or timeline.active) and width >= 100)
        self.label.setVisible((self.expanded or timeline.active) and width >= 250)
        self.back.setVisible(timeline.active and width >= 320)
        self.toggle.setText('直播' if timeline.active and self.back.isHidden() else '重放')
        self.slider.setEnabled(enabled)
        if not self.slider.isSliderDown():
            self.slider.setMinimum(max(0, round((now - bounds[1]) * 1000)) if bounds else 0)
            self.slider.setMaximum(round(maximum * 1000))
            self.slider.setValue(round(timeline.delay * 1000))
        seconds = max(0, round(timeline.delay))
        self.label.setText(f'−{seconds // 60:02d}:{seconds % 60:02d}' if timeline.active else '直播')
