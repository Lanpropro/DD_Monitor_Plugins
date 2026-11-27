"""Group replay uses historical presentation positions, leaving live alignment running."""
from bisect import bisect_right
from collections import deque
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QSlider, QStyle, QToolTip, QVBoxLayout, QWidget
from ddm import theme

from .engine import RATE
from .panels import ConsoleIconButton
from .replay_cache import REPLAY_SECONDS
from .replay_preview import ReplayPreview


RECONNECT_GAP_SECONDS = 5.0


class ReplayTimeline:
    def __init__(self, seconds=REPLAY_SECONDS):
        self.seconds = seconds
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
        while len(self.states) > 1 and self.states[1][0] < now - self.seconds:
            self.states.popleft()
        while self.messages and self.messages[0][0] < now - self.seconds:
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
    def valid(stamp, shifts, histories, bounds_cache=None):
        return ReplayTimeline._playable_segments(stamp, shifts, histories, bounds_cache) is not None

    @staticmethod
    def _playable_segments(stamp, shifts, histories, bounds_cache=None):
        if not histories or any(key not in shifts for key in histories):
            return None
        segments = []
        for key, history in histories.items():
            target = stamp + shifts[key]
            cached = bounds_cache.get(key) if bounds_cache is not None else None
            if cached is not None and cached[1][0] <= target <= cached[1][1]:
                segment, bounds = cached
            else:
                lookup = getattr(history, 'replay_bounds_at', None)
                segment, bounds = lookup(target) if lookup is not None else (history, history.bounds_at(target))
                if segment is None or bounds is None:
                    return None
                if bounds_cache is not None:
                    bounds_cache[key] = (segment, bounds)
            origin = segment.origin
            audio = segment.audio
            if (origin is None or not bounds[0] <= target <= bounds[1]
                    or not origin + max(0, audio.end - audio.capacity) / RATE <= target < origin + audio.end / RATE):
                return None
            segments.append(segment)
        return tuple(segments)

    def _ranges(self, now, histories):
        ranges = []
        lower = upper = None
        newer = None
        missed = False
        reconnect = any(getattr(history, 'retained', ()) for history in histories.values())
        # Reuse continuous video intervals within this scan only. A new scan
        # must observe eviction/reconnection; seek still validates independently.
        bounds_cache = {}
        for stamp, shifts in reversed(self.states):
            if stamp < now - self.seconds:
                break
            segments = self._playable_segments(stamp, shifts, histories, bounds_cache)
            if segments is None:
                if lower is not None:
                    if not reconnect or lower - stamp > RECONNECT_GAP_SECONDS:
                        break
                    missed = True
                continue
            if lower is None:
                upper = stamp
            elif segments != newer:
                if lower - stamp > RECONNECT_GAP_SECONDS:
                    break
                ranges.append((lower, upper))
                upper = stamp
            elif missed:
                break  # A gap within a single connection is not a reconnect.
            lower = stamp
            newer = segments
            missed = False
        if lower is not None:
            ranges.append((lower, upper))
        return ranges

    def available(self, now, histories):
        ranges = self._ranges(now, histories)
        return (ranges[-1][0], ranges[0][1]) if ranges else None

    def seek_target(self, stamp, now, histories):
        ranges = self._ranges(now, histories)
        if not ranges:
            return None
        stamp = min(max(stamp, ranges[-1][0]), ranges[0][1])
        if self.valid(stamp, self.shifts_at(stamp), histories):
            return stamp
        return next((start for start, _end in reversed(ranges) if start > stamp), None)

    def seek(self, stamp, now, histories):
        stamp = self.seek_target(stamp, now, histories)
        if stamp is None:
            return False
        self.delay = max(.001, now - stamp)
        self.cursor = stamp
        return True

    def advance_gap(self, stamp, now, histories):
        ranges = self._ranges(now, histories)
        for (start, _), (_, older_end) in zip(ranges, ranges[1:]):
            if older_end <= stamp < start:
                self.delay = max(.001, now - start)
                self.cursor = start
                return start
        return None

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


class TimelineSlider(QSlider):
    dragging = False

    def position_value(self, event):
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
            round(event.position().x()), max(1, self.width() - 1))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            self.press_x = event.position().x()
            self.setSliderDown(True)
            self.setValue(self.position_value(event))
            self.sliderMoved.emit(self.value())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.dragging |= abs(event.position().x() - self.press_x) >= 3
            self.setValue(self.position_value(event))
            self.sliderMoved.emit(self.value())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.isSliderDown():
            self.setValue(self.position_value(event))
            self.setSliderDown(False)
            self.dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class PlaybackTimeEdit(QLineEdit):
    def __init__(self, parent):
        super().__init__(parent)
        self.scrubbing = False
        self._pressed = False

    @staticmethod
    def scrub_gain(pixels, seconds):
        speed = abs(pixels) / max(.001, seconds)
        return .05 + min(3.0, speed / 350) ** 1.2

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._start_x = self._last_x = event.globalPosition().x()
            self._last_time = time.perf_counter()
            value = self.parentWidget().parse_time(self.text())
            self._drag_value = float(value if value is not None else self.parentWidget().slider.value() / 1000)
            self._drag_origin = self.parentWidget().window_start

    def mouseMoveEvent(self, event):
        if self._pressed and (self.scrubbing or abs(event.globalPosition().x() - self._start_x) >= 4):
            now = time.perf_counter()
            delta = event.globalPosition().x() - self._last_x
            self._drag_value += delta * self.scrub_gain(delta, now - self._last_time)
            self._drag_value = max(0, min(self.parentWidget().slider.maximum() / 1000, self._drag_value))
            self._last_x, self._last_time = event.globalPosition().x(), now
            self.scrubbing = True
            self.setCursor(Qt.SizeHorCursor)
            self.parentWidget().preview_elapsed(self._drag_value)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        if self.scrubbing and event.button() == Qt.LeftButton:
            self.scrubbing = False
            self.setCursor(Qt.IBeamCursor)
            self.parentWidget().seek_elapsed(self._drag_value, self._drag_origin)
            self.clearFocus()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def focusInEvent(self, event):
        self.parentWidget().edit_origin = self.parentWidget().window_start
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.parentWidget().refresh(force=True)


class ReplayRuler(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.seconds = self.duration = 0.0
        self.setFixedHeight(32)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor('#f1f2f5'), 1))
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPixelSize(10)
        painter.setFont(font)
        center = round(self.seconds)
        for tick in range(center - 10, center + 11):
            if not 0 <= tick <= self.duration:
                continue
            x = self.width() / 2 + (tick - self.seconds) * self.width() / 20
            major = tick % 5 == 0
            painter.drawLine(QPointF(x, 0), QPointF(x, 12 if major else 6))
            if major:
                painter.drawText(QRectF(max(0, min(self.width() - 44, x - 22)), 14, 44, 16), Qt.AlignCenter, ReplayBar.format_time(tick))


class ReplayBar(QWidget):
    def __init__(self, viewer):
        slot = viewer.picture.status_label
        slot.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        super().__init__(slot)
        self.viewer = viewer
        self.setFixedHeight(26)
        self.window_start = self.edit_origin = 0.0
        self.drag_origin = None
        self.preview = ReplayPreview(viewer)
        self.last_update = 0.0
        centered = QHBoxLayout(slot)
        centered.setContentsMargins(0, 0, 0, 0)
        centered.addStretch(1)
        centered.addWidget(self, 0, Qt.AlignCenter)
        centered.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        timeline = QWidget()
        timeline.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        track = QVBoxLayout(timeline)
        track.setContentsMargins(0, 0, 0, 0)
        track.setSpacing(0)
        self.slider = TimelineSlider(Qt.Horizontal)
        self.slider.sliderPressed.connect(self.begin_drag)
        self.slider.setMinimumWidth(0)
        self.slider.setFixedHeight(12)
        self.slider.setSingleStep(1000)
        self.slider.setPageStep(10000)
        self.slider.setAccessibleName('整组回看进度')
        self.slider.sliderReleased.connect(self.seek)
        self.slider.sliderMoved.connect(lambda value: self.preview_elapsed(value / 1000))
        track.addWidget(self.slider)
        ticks = QHBoxLayout()
        ticks.setContentsMargins(0, 0, 0, 0)
        self.start_tick, self.middle_tick, self.end_tick = QLabel('00:00'), QLabel('00:00'), QLabel('00:00')
        for label in (self.start_tick, self.middle_tick, self.end_tick):
            label.setFixedHeight(12)
            label.setStyleSheet('font-size: 10px; background: transparent;')
        ticks.addWidget(self.start_tick)
        ticks.addStretch(1)
        ticks.addWidget(self.middle_tick)
        ticks.addStretch(1)
        ticks.addWidget(self.end_tick)
        track.addLayout(ticks)
        self.time_edit = PlaybackTimeEdit(self)
        self.time_edit.setFixedSize(56, 24)
        self.time_edit.setAlignment(Qt.AlignCenter)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPixelSize(theme.FONT_CONTROL)
        self.time_edit.setFont(font)
        self.time_edit.setAccessibleName('当前播放时间，可输入调整')
        self.time_edit.setStyleSheet('''QLineEdit {background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 0;}
            QLineEdit:focus {background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(110,120,135,0), stop:0.5 rgba(110,120,135,85), stop:1 rgba(110,120,135,0));
                border-color: palette(highlight);}''')
        self.time_edit.setToolTip('输入 mm:ss 并按 Enter 跳转；00:00 为当前可回看窗口的起点')
        self.time_edit.returnPressed.connect(self.enter_time)
        self.back = ConsoleIconButton('live', '返回直播', self)
        self.back.setObjectName('TileCtrl')
        self.back.setStyleSheet(f'''QPushButton {{background: transparent; border: none; padding: 0; border-radius: 6px;}}
            QPushButton:hover {{background: {theme.ACCENT_SOFT};}}''')
        self.back.clicked.connect(viewer.return_live)
        layout.addWidget(timeline, 1)
        layout.addWidget(self.time_edit)
        layout.addWidget(self.back)
        self.details = QWidget(viewer.picture)
        self.details.setObjectName('ReplayDetails')
        self.details.setAttribute(Qt.WA_StyledBackground)
        self.details.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.details.setStyleSheet('#ReplayDetails {background: rgba(16, 18, 23, 180); border: none; border-radius: 8px;}'
            ' #ReplayDetails QWidget {background: transparent; border: none;}'
            ' #ReplayDetails QLabel {color: #f1f2f5; background: transparent; font-size: 11px;}')
        detail_layout = QVBoxLayout(self.details)
        detail_layout.setContentsMargins(0, 5, 0, 5)
        detail_layout.setSpacing(2)
        self.detail_position = QLabel()
        self.detail_position.setFont(font)
        self.detail_position.setAlignment(Qt.AlignCenter)
        detail_layout.addWidget(self.detail_position)
        self.ruler = ReplayRuler(self.details)
        detail_layout.addWidget(self.ruler)
        self.details.hide()
        for widget in (viewer.picture.bottom, slot, self, self.slider):
            widget.installEventFilter(self)
        self.refresh(force=True)

    @staticmethod
    def format_time(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    def begin_drag(self):
        self.drag_origin = self.window_start

    def seek(self):
        self.seek_elapsed(self.slider.value() / 1000, self.drag_origin)
        self.drag_origin = None

    def seek_elapsed(self, seconds, origin=None):
        self.details.hide()
        self.preview.dismiss()
        if seconds * 1000 >= self.slider.maximum() - 200:
            self.viewer.return_live()
        else:
            self.viewer.seek_replay((self.window_start if origin is None else origin) + seconds)

    def show_preview(self, seconds, origin=None):
        if not self.slider.isSliderDown() or not self.slider.dragging:
            self.preview.dismiss()
            return
        stamp = (self.window_start if origin is None else origin) + seconds
        self.preview.show_target(stamp, f'{self.format_time(seconds)} / {self.format_time(self.slider.maximum() / 1000)}')

    def preview_elapsed(self, seconds):
        origin = self.time_edit._drag_origin if self.time_edit.scrubbing else self.drag_origin
        self.show_preview(seconds, origin)
        self.time_edit.setText(self.format_time(seconds))
        self.slider.setValue(round(seconds * 1000))
        whole, millis = divmod(round(seconds * 1000), 1000)
        self.detail_position.setText(f'{self.format_time(whole)}.{millis:03d}  /  {self.format_time(self.slider.maximum() / 1000)}')
        self.ruler.seconds = seconds
        self.ruler.duration = self.slider.maximum() / 1000
        self.ruler.update()
        self._position_details()
        self.details.show()
        self.details.raise_()

    def _position_details(self):
        # A transparent playback bar is a separate top-level window on Windows.
        picture = self.details.parentWidget()
        position = picture.mapFromGlobal(self.slider.mapToGlobal(QPoint()))
        self.details.setGeometry(position.x(), max(0, position.y() - 72), self.slider.width(), 66)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Move, QEvent.Resize) and self.details.isVisible():
            self._position_details()
        return super().eventFilter(watched, event)

    @staticmethod
    def parse_time(text):
        parts = text.strip().split(':')
        if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
            return None
        if len(parts) > 1 and not all(int(part) < 60 for part in parts[1:]):
            return None
        return sum(int(part) * 60 ** index for index, part in enumerate(reversed(parts)))

    def enter_time(self):
        seconds = self.parse_time(self.time_edit.text())
        origin = self.edit_origin if self.time_edit.hasFocus() else self.window_start
        if seconds is None or seconds * 1000 > self.slider.maximum():
            QToolTip.showText(self.time_edit.mapToGlobal(self.time_edit.rect().bottomLeft()),
                '请输入可用范围内的时间，例如 02:30', self.time_edit)
            return
        self.seek_elapsed(seconds, origin)
        self.time_edit.clearFocus()
        self.refresh(force=True)

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_update < .25:
            return
        self.last_update = now
        self.setVisible(bool(self.viewer.rows))
        width = max(44, round(self.parentWidget().width() * 2 / 3))
        self.setFixedWidth(width)
        self.time_edit.setVisible(width >= 130)
        self.middle_tick.setVisible(width >= 210)
        self.end_tick.setVisible(width >= 130)
        self.back.setVisible(width >= 210)
        timeline = self.viewer.replay
        bounds = timeline.available(now, self.viewer.replay_histories())
        self.window_start = bounds[0] if bounds else now
        duration = min(timeline.seconds, max(0, now - self.window_start)) if bounds else 0
        enabled = duration > .5 and self.viewer.running
        if not enabled:
            self.preview.dismiss()
        self.slider.setEnabled(enabled)
        self.time_edit.setEnabled(enabled)
        self.back.setEnabled(timeline.active)
        self.slider.setToolTip(f'当前可回看 {self.format_time(duration)}，最多 {timeline.seconds // 60} 分钟')
        current = max(0, min(duration, now - timeline.delay - self.window_start))
        if not self.slider.isSliderDown() and not self.time_edit.scrubbing:
            self.slider.setRange(0, round(duration * 1000))
            self.slider.setValue(round(current * 1000))
        if not self.time_edit.hasFocus() and not self.slider.isSliderDown():
            self.time_edit.setText(self.format_time(current))
        self.middle_tick.setText(self.format_time(duration / 2))
        self.end_tick.setText(self.format_time(duration))

    def hideEvent(self, event):
        self.details.hide()
        self.preview.dismiss()
        super().hideEvent(event)
