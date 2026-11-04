"""Native Qt media timeline: readable range, selection, markers and zoom."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QDoubleSpinBox, QWidget


def timecode(seconds, *, milliseconds=False):
    value = max(0, round(float(seconds or 0) * 1000))
    seconds, ms = divmod(value, 1000)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours:02}:{minutes:02}:{secs:02}' + (f'.{ms:03}' if milliseconds else '')


class TimeInput(QDoubleSpinBox):
    def __init__(self):
        super().__init__()
        self.setDecimals(3)
        self.setKeyboardTracking(False)
        self.setMinimumHeight(30)

    def textFromValue(self, value):
        return timecode(value, milliseconds=True)

    def valueFromText(self, text):
        parts = text.strip().split(':')
        try:
            result = 0
            for part in parts:
                result = result * 60 + float(part)
            return result
        except ValueError:
            return self.value()

    def validate(self, text, position):
        from PySide6.QtGui import QValidator
        parts = text.split(':')
        if len(parts) > 3 or any(c not in '0123456789:.' for c in text):
            return QValidator.Invalid, text, position
        try:
            values = [float(part) for part in parts]
        except ValueError:
            return QValidator.Intermediate, text, position
        if any(value >= 60 for value in values[1:]):
            return QValidator.Invalid, text, position
        value = self.valueFromText(text)
        state = QValidator.Acceptable if self.minimum() <= value <= self.maximum() else QValidator.Intermediate
        return state, text, position


class Timeline(QWidget):
    seekRequested = Signal(float)
    selectionChanged = Signal(float, float)
    viewChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = self.position = self.start = self.end = 0.0
        self.view_start = 0.0
        self.zoom = 1.0
        self.markers = []
        self.segments = []
        self.heat_points = []
        self.heat_threshold = 0
        self.dragging = None
        self.setMinimumHeight(140)
        self.setMinimumWidth(220)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('录像时间轴；拖动入点和出点调整选区，点击定位，滚轮缩放')

    @property
    def span(self):
        return max(.001, self.duration / self.zoom)

    def track(self):
        return QRectF(8, 62, max(1, self.width() - 16), 42)

    def heat_track(self):
        return QRectF(8, 5, max(1, self.width() - 16), 22)

    def x_for(self, seconds):
        return self.track().left() + (seconds - self.view_start) / self.span * self.track().width()

    def seconds_at(self, x):
        fraction = max(0, min(1, (x - self.track().left()) / self.track().width()))
        return max(0, min(self.duration, self.view_start + fraction * self.span))

    def set_duration(self, duration, *, reset=False):
        self.duration = max(0, float(duration))
        if reset:
            self.view_start = 0
            self.zoom = 1
        self.view_start = min(self.view_start, max(0, self.duration - self.span))
        self.update()
        self.viewChanged.emit()

    def set_selection(self, start, end):
        self.start, self.end = start, end
        self.update()

    def set_position(self, seconds):
        self.position = max(0, min(self.duration, float(seconds)))
        self.update()

    def set_zoom(self, value):
        self.zoom = max(1, min(64, float(value)))
        self.view_start = max(0, min(self.position - self.span / 2, self.duration - self.span))
        self.update()
        self.viewChanged.emit()

    def set_view_start(self, milliseconds):
        self.view_start = max(0, min(milliseconds / 1000, self.duration - self.span))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        track = self.track()
        ink = palette.color(QPalette.Text)
        muted = QColor(ink)
        muted.setAlpha(175)
        accent = palette.color(QPalette.Highlight)
        painter.fillRect(track, palette.brush(QPalette.AlternateBase))
        painter.setPen(muted)
        label_width = painter.fontMetrics().horizontalAdvance(timecode(self.duration, milliseconds=self.span < 30))
        ticks = max(2, min(6, int(track.width() / (label_width + 16))))
        for index in range(ticks):
            value = self.view_start + self.span * index / (ticks - 1)
            text = timecode(value, milliseconds=self.span < 30)
            width = painter.fontMetrics().horizontalAdvance(text)
            x = min(self.width() - width - 4, max(4, self.x_for(value) - width / 2))
            painter.drawText(QRectF(x, 31, width + 1, 23), Qt.AlignCenter, text)
        painter.save()
        heat = self.heat_track()
        painter.fillRect(heat, palette.brush(QPalette.AlternateBase))
        painter.setClipRect(heat)
        maximum = max((point['count'] for point in self.heat_points), default=0)
        if maximum:
            for point in self.heat_points:
                x = self.x_for(point['time'] - 2.5)
                width = max(1, self.x_for(point['time'] + 2.5) - x)
                if x + width < track.left() or x > track.right():
                    continue
                color = QColor(accent)
                color.setAlpha(180 if point['level'] == 'core' else 100 if point['level'] == 'extension' else 45)
                height = point['count'] / maximum * heat.height()
                painter.fillRect(QRectF(x, heat.bottom() - height, width, height), color)
            y = heat.bottom() - self.heat_threshold / maximum * heat.height()
            painter.setPen(QPen(muted, 1, Qt.DashLine))
            painter.drawLine(int(track.left()), int(y), int(track.right()), int(y))
        painter.setClipRect(track)
        # Actual completed segment boundaries; no fabricated audio waveform.
        painter.setPen(QPen(palette.color(QPalette.Mid), 1))
        elapsed = 0
        for segment in self.segments:
            elapsed += segment['duration']
            x = self.x_for(elapsed)
            if track.left() < x < track.right():
                painter.drawLine(int(x), int(track.top()), int(x), int(track.bottom()))
        fill = QColor(accent)
        fill.setAlpha(65)
        selected = QRectF(self.x_for(self.start), track.top(),
                         max(0, self.x_for(self.end) - self.x_for(self.start)), track.height())
        painter.fillRect(selected, fill)
        painter.setPen(QPen(accent, 2))
        painter.drawRect(selected)
        painter.restore()
        for value in (self.start, self.end):
            x = self.x_for(value)
            if track.left() <= x <= track.right():
                painter.fillRect(QRectF(x - 4, track.top() - 3, 8, track.height() + 6), accent)
        x = self.x_for(self.position)
        if track.left() <= x <= track.right():
            painter.setPen(QPen(QColor('#fb7185'), 2))
            painter.drawLine(int(x), int(heat.top()), int(x), int(track.bottom() + 6))
        last_label = -100
        for marker in self.markers:
            x = self.x_for(marker['time'])
            if track.left() <= x <= track.right():
                painter.fillRect(QRectF(x - 2, track.bottom() + 9, 4, 7), accent)
                if x - last_label >= 90:
                    painter.setPen(muted)
                    text = painter.fontMetrics().elidedText(marker['title'], Qt.ElideRight, 85)
                    left = max(4, min(x - 15, self.width() - 90))
                    painter.drawText(QRectF(left, track.bottom() + 18, 86, 18), Qt.AlignLeft, text)
                    last_label = x

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.duration:
            return super().mousePressEvent(event)
        x, y = event.position().x(), event.position().y()
        self.setFocus(Qt.MouseFocusReason)
        if self.track().top() - 4 <= y <= self.track().bottom() + 6 and abs(x - self.x_for(self.start)) <= 9:
            self.dragging = 'start'
        elif self.track().top() - 4 <= y <= self.track().bottom() + 6 and abs(x - self.x_for(self.end)) <= 9:
            self.dragging = 'end'
        else:
            self.dragging = 'seek'
            self.seekRequested.emit(self.seconds_at(x))

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self.dragging:
            value = self.seconds_at(x)
            if self.dragging == 'start':
                self.selectionChanged.emit(min(value, self.end), self.end)
            elif self.dragging == 'end':
                self.selectionChanged.emit(self.start, max(value, self.start))
            else:
                self.seekRequested.emit(value)
        else:
            near = min(abs(x - self.x_for(self.start)), abs(x - self.x_for(self.end))) <= 9
            self.setCursor(Qt.SizeHorCursor if near else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self.dragging = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event.angleDelta().y():
            self.set_zoom(self.zoom * (1.25 if event.angleDelta().y() > 0 else .8))
            event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            delta = (10 if event.modifiers() & Qt.ShiftModifier else 1)
            if event.key() == Qt.Key_Left:
                delta = -delta
            self.seekRequested.emit(max(0, min(self.duration, self.position + delta)))
        else:
            super().keyPressEvent(event)
