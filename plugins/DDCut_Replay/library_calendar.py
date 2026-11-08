"""Local-day recording lanes, sharing the library's selection and playback."""
from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ddm import theme
from .library_data import interval, on_date


class ArchiveDay(QWidget):
    selected = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.entries, self.hits = [], []
        self.selected_paths = set()
        self.day = datetime.now().date()
        self.setMinimumWidth(250)
        self.setMouseTracking(True)

    def set_entries(self, entries, day):
        self.entries = [entry for entry in entries if on_date(entry, day)]
        self.day = day
        self.setMinimumHeight(48 + 60 * max(1, len(self.entries)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.CONTENT))
        painter.setPen(theme.qcolor(theme.TEXT3))
        left, width = 110, max(1, self.width() - 126)
        begin = datetime.combine(self.day, datetime.min.time()).timestamp()
        end = datetime.combine(self.day + timedelta(days=1), datetime.min.time()).timestamp()
        for hour in range(0, 25, 3):
            x = left + width * hour / 24
            painter.drawText(QRectF(x - 15, 0, 34, 25), Qt.AlignCenter, f'{hour:02}:00')
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
            label = datetime.fromtimestamp(start).strftime('%H:%M:%S') + '–' + datetime.fromtimestamp(finish).strftime('%H:%M:%S') + '  ' + str(room.get('title') or entry.get('title') or '')
            painter.setPen(theme.qcolor(theme.TEXT2))
            painter.drawText(QRectF(left, y + 22, width, 26), Qt.AlignVCenter,
                             painter.fontMetrics().elidedText(label, Qt.ElideRight, int(width)))
            self.hits.append((row, entry))
        if not self.entries:
            painter.drawText(self.rect(), Qt.AlignCenter, '当天没有录像 · 可切换日期或清除日期筛选')

    def hit(self, point):
        return next((entry for rect, entry in self.hits if rect.contains(point)), None)

    def mouseMoveEvent(self, event):
        entry = self.hit(event.position())
        self.setToolTip('' if not entry else '\n'.join([str((entry.get('room') or {}).get('title') or entry.get('title') or ''),
            datetime.fromtimestamp(interval(entry)[0]).strftime('%Y-%m-%d %H:%M:%S'), entry['path']]))

    def mousePressEvent(self, event):
        entry = self.hit(event.position())
        if entry and event.button() == Qt.LeftButton:
            self.selected.emit(entry['path'], False)

    def mouseDoubleClickEvent(self, event):
        entry = self.hit(event.position())
        if entry and event.button() == Qt.LeftButton:
            self.selected.emit(entry['path'], True)
