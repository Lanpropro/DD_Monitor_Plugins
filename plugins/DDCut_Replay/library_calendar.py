"""Navigable recording time ranges, sharing the library's selection and playback."""
from datetime import datetime

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ddm import theme
from .library_data import interval


class ArchiveDay(QWidget):
    selected = Signal(str, bool)
    rangeChanged = Signal()

    def __init__(self):
        super().__init__()
        self.entries, self.hits = [], []
        self.selected_paths = set()
        self.day = datetime.now().date()
        self.anchor = None
        self.hours = 24
        self.drag = None
        self.suppress_release = False
        self.setMinimumWidth(250)
        self.setMouseTracking(True)

    def set_entries(self, entries, day):
        self.day = day
        if self.anchor is None:
            self.anchor = datetime.combine(day, datetime.min.time()).timestamp() + 12 * 3600
        self.entries = [entry for entry in entries if self.matches(entry)]
        self.hits = []
        self.setMinimumHeight(48 + 60 * max(1, len(self.entries)))
        self.update()

    def bounds(self):
        anchor = self.anchor if self.anchor is not None else datetime.combine(self.day, datetime.min.time()).timestamp() + 12 * 3600
        return anchor - self.hours * 1800, anchor + self.hours * 1800

    def matches(self, entry):
        start, end = interval(entry)
        begin, finish = self.bounds()
        return bool(start and start < finish and (end > begin or start == begin))

    def reset_date(self, day):
        self.day = day
        self.anchor = datetime.combine(day, datetime.min.time()).timestamp() + 12 * 3600

    def set_hours(self, hours):
        self.hours = hours
        self.rangeChanged.emit()

    def move(self, direction):
        begin, end = self.bounds()
        self.anchor = (begin + end) / 2 + direction * self.hours * 1800
        self.rangeChanged.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.CONTENT))
        painter.setPen(theme.qcolor(theme.TEXT3))
        left, width = 110, max(1, self.width() - 145)
        begin, end = self.bounds()
        for tick in range(9):
            x = left + width * tick / 8
            label = datetime.fromtimestamp(begin + (end - begin) * tick / 8).strftime('%H:%M')
            painter.drawText(QRectF(x - 22, 0, 46, 25), Qt.AlignCenter, label)
            painter.setPen(theme.qcolor(theme.BORDER))
            painter.drawLine(int(x), 27, int(x), self.height())
            painter.setPen(theme.qcolor(theme.TEXT3))
        self.hits = []
        for index, entry in enumerate(self.entries):
            start, finish = interval(entry)
            y = 32 + index * 60
            room = entry.get('room') or {}
            row = QRectF(0, y, self.width(), 56)
            if entry['path'] in self.selected_paths:
                painter.fillRect(row, theme.qcolor(theme.ACCENT_SOFT))
            painter.setPen(theme.qcolor(theme.TEXT2))
            name = str(room.get('uname') or entry.get('title') or '本地视频')
            painter.drawText(QRectF(6, y, 98, 30), Qt.AlignVCenter,
                             painter.fontMetrics().elidedText(name, Qt.ElideRight, 98))
            x = left + (max(begin, start) - begin) / (end - begin) * width
            w = max(7, (min(end, finish) - max(begin, start)) / (end - begin) * width)
            rect = QRectF(x, y, min(w, left + width - x), 20)
            painter.setBrush(theme.qcolor(theme.ACCENT_SOFT))
            painter.setPen(theme.qcolor(theme.ACCENT))
            painter.drawRoundedRect(rect, 4, 4)
            label = datetime.fromtimestamp(start).strftime('%m-%d %H:%M:%S') + '–' + datetime.fromtimestamp(finish).strftime('%m-%d %H:%M:%S') + '  ' + str(room.get('title') or entry.get('title') or '')
            painter.setPen(theme.qcolor(theme.TEXT2))
            painter.drawText(QRectF(left, y + 22, width, 26), Qt.AlignVCenter,
                             painter.fontMetrics().elidedText(label, Qt.ElideRight, int(width)))
            self.hits.append((row, entry))
        if not self.entries:
            painter.drawText(self.rect(), Qt.AlignCenter, '当前时间范围没有录像 · 拖动时间尺或切换日期')

    def hit(self, point):
        return next((entry for rect, entry in self.hits if rect.contains(point)), None)

    def mouseMoveEvent(self, event):
        if self.drag:
            origin, anchor, moved = self.drag
            delta = event.position().x() - origin.x()
            if moved or abs(delta) >= 5:
                self.drag = origin, anchor, True
                self.anchor = anchor - delta / max(1, self.width() - 145) * self.hours * 3600
                self.rangeChanged.emit()
                return
        entry = self.hit(event.position())
        self.setToolTip('' if not entry else '\n'.join([str((entry.get('room') or {}).get('title') or entry.get('title') or ''),
            datetime.fromtimestamp(interval(entry)[0]).strftime('%Y-%m-%d %H:%M:%S'), entry['path']]))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.suppress_release = False
            begin, end = self.bounds()
            self.drag = event.position(), (begin + end) / 2, False

    def mouseReleaseEvent(self, event):
        drag, self.drag = self.drag, None
        if self.suppress_release:
            self.suppress_release = False
            return
        if drag and not drag[2] and event.button() == Qt.LeftButton:
            entry = self.hit(event.position())
            if entry:
                self.selected.emit(entry['path'], False)

    def mouseDoubleClickEvent(self, event):
        entry = self.hit(event.position())
        if entry and event.button() == Qt.LeftButton:
            self.suppress_release = True
            self.drag = None
            self.selected.emit(entry['path'], True)

    def wheelEvent(self, event):
        delta = event.pixelDelta().x() or event.angleDelta().x()
        if not delta and event.modifiers() & Qt.ShiftModifier:
            delta = event.pixelDelta().y() or event.angleDelta().y()
        if delta:
            begin, end = self.bounds()
            self.anchor = (begin + end) / 2 + delta / max(1, self.width() - 145) * self.hours * 3600
            self.rangeChanged.emit()
            event.accept()
        else:
            event.ignore()  # Ordinary vertical wheel remains list scrolling.
