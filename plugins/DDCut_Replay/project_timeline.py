"""Main magnetic video track and anchored visual/audio tracks."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget
from ddm import theme
from .project import duration, placed_clips, placed_layers
from .subtitles import placed_cues
from .timeline import timecode


class ProjectTimeline(QWidget):
    selected = Signal(str)
    seekRequested = Signal(float)
    editRequested = Signal(dict)
    filesDropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.project = None
        self.position = 0
        self.selection = ''
        self.zoom = 1
        self.snap = True
        self.blocks = []
        self.drag = None
        self.setMinimumHeight(180)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('剪辑工程多轨时间轴')

    def set_project(self, project, selected=''):
        self.project, self.selection = project, selected
        self.reflow()

    def reflow(self):
        if not self.project:
            return
        parent_width = self.parentWidget().width() if self.parentWidget() else 800
        self.scale = max(.5, (parent_width - 100) / max(10, duration(self.project))) * self.zoom
        self.setMinimumWidth(max(parent_width - 4, round(duration(self.project) * self.scale + 120)))
        visual = sorted({row['track'] for row, _, _ in placed_layers(self.project) if row['kind'] != 'audio'}, reverse=True)
        audio = sorted({row['track'] for row, _, _ in placed_layers(self.project) if row['kind'] == 'audio'})
        captions = placed_cues(self.project)
        self.rows = ([('subtitle', 0)] if captions else []) + [('visual', track) for track in visual] + [('main', 0)] + [('audio', track) for track in audio]
        self.setMinimumHeight(max(180, 30 + len(self.rows) * 46))
        self.blocks = []
        main_y = 30 + self.rows.index(('main', 0)) * 46
        for clip, start, end in placed_clips(self.project):
            self.blocks.append((clip, start, end, QRectF(self.x(start), main_y, max(5, (end-start)*self.scale), 42), True))
        for item, start, end in placed_layers(self.project):
            row = ('audio' if item['kind'] == 'audio' else 'visual', item['track'])
            y = 30 + self.rows.index(row) * 46
            self.blocks.append((item, start, end, QRectF(self.x(start), y, max(5, (end-start)*self.scale), 38), False))
        for cue, start, end in captions:
            self.blocks.append((dict(cue, kind='subtitle'), start, end, QRectF(self.x(start), 30, max(5, (end-start)*self.scale), 38), False))
        self.update()

    def x(self, stamp):
        return 84 + stamp * self.scale

    def seconds(self, x):
        return max(0, (x - 84) / self.scale)

    def snapped(self, stamp, exclude=''):
        fps = self.project['render']['fps']
        stamp = round(max(0, stamp) * fps) / fps
        if self.snap:
            points = [0, self.position]
            for item, start, end, _, _ in self.blocks:
                if item['id'] != exclude:
                    points += [start, end]
            closest = min(points, key=lambda value: abs(value-stamp))
            if abs(closest-stamp) * self.scale <= 8:
                stamp = closest
        return stamp

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor('#10151d' if not theme.is_light() else theme.CONTENT))
        painter.setPen(theme.qcolor(theme.TEXT2))
        if not self.project:
            return
        step = max(1, round(80 / self.scale))
        for stamp in range(0, int(duration(self.project)) + step + 1, step):
            x = self.x(stamp)
            painter.drawText(QRectF(x, 1, 90, 24), Qt.AlignLeft | Qt.AlignVCenter, timecode(stamp))
            painter.setPen(theme.qcolor(theme.BORDER))
            painter.drawLine(QPointF(x, 26), QPointF(x, self.height()))
            painter.setPen(theme.qcolor(theme.TEXT2))
        for index, (kind, track) in enumerate(self.rows):
            y = 30 + index * 46
            painter.drawText(QRectF(4, y, 78, 38), Qt.AlignVCenter, '主视频' if kind == 'main' else '字幕' if kind == 'subtitle' else {'audio': '音频', 'visual': '画面'}[kind] + str(track))
        for item, start, end, rect, main in self.blocks:
            color = QColor('#205678' if main else '#524075' if item['kind'] in ('text', 'image') else '#23634e')
            painter.setBrush(color)
            painter.setPen(QPen(theme.qcolor(theme.ACCENT if item['id'] == self.selection else theme.BORDER), 2 if item['id'] == self.selection else 1))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
            info = self.project['media'].get(item.get('source'), {})
            peaks = info.get('peaks', [])
            if peaks:
                painter.save()
                painter.setClipRect(rect)
                painter.setPen(QColor(110, 210, 185, 130))
                for x in range(round(rect.left()), round(rect.right()), 3):
                    source_time = (item['start'] if main else item.get('in', 0)) + (x-rect.left()) / self.scale * item.get('speed', 1)
                    index = min(len(peaks)-1, max(0, int(source_time / max(.001, info['duration']) * len(peaks))))
                    height = peaks[index] * 15
                    painter.drawLine(QPointF(x, rect.bottom()-4), QPointF(x, rect.bottom()-4-height))
                painter.restore()
            label = info.get('title', '') if main else {'text': '文字', 'image': '贴纸', 'video': '画中画', 'audio': '音频', 'subtitle': '字幕'}[item['kind']]
            if not main and item['kind'] in ('text', 'subtitle'):
                label += ' · ' + item['text']
            painter.setPen(QColor('#e6edf5'))
            painter.drawText(rect.adjusted(7, 2, -5, -6), Qt.AlignTop | Qt.AlignLeft,
                             painter.fontMetrics().elidedText(label, Qt.ElideRight, max(1, int(rect.width()-12))))
        x = self.x(self.position)
        painter.setPen(QPen(QColor('#fb7185'), 2))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        if self.drag:
            painter.setPen(QPen(theme.qcolor(theme.ACCENT), 2, Qt.DashLine))
            x = self.x(self.drag['preview'])
            painter.drawLine(QPointF(x, 25), QPointF(x, self.height()))

    def mousePressEvent(self, event):
        if not self.project or event.button() != Qt.LeftButton:
            return
        self.setFocus()
        point = event.position()
        hit = next((block for block in reversed(self.blocks) if block[3].contains(point)), None)
        if hit is None or point.y() < 26:
            self.seekRequested.emit(min(duration(self.project), self.seconds(point.x())))
            return
        item, start, end, rect, main = hit
        self.selection = item['id']
        self.selected.emit(item['id'])
        edge = 'left' if abs(point.x()-rect.left()) < 7 else 'right' if abs(point.x()-rect.right()) < 7 else 'move'
        self.drag = {'id': item['id'], 'edge': edge, 'main': main, 'start': start, 'end': end,
                     'origin': self.seconds(point.x()), 'preview': start}
        self.update()

    def mouseMoveEvent(self, event):
        if self.drag:
            delta = self.seconds(event.position().x()) - self.drag['origin']
            initial = self.drag['end'] if self.drag['edge'] == 'right' else self.drag['start']
            self.drag['preview'] = self.snapped(initial + delta, self.drag['id'])
            self.update()

    def mouseReleaseEvent(self, event):
        if self.drag:
            move = self.drag
            self.drag = None
            if abs(self.seconds(event.position().x()) - move['origin']) * self.scale > 3:
                self.editRequested.emit(move)
            self.update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.filesDropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()
