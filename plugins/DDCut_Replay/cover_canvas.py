"""Native still-image canvas with one undoable edit per pointer gesture."""
from copy import deepcopy
import math
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget
from .cover_model import SIZES
from .cover_render import crop_rect, render_cover
from .graphic_geometry import geometry


class CoverCanvas(QWidget):
    selected = Signal(str)
    editRequested = Signal(str, dict)
    deleteRequested = Signal()

    def __init__(self):
        super().__init__()
        self.data = None
        self.ratio, self.selection = 'wide', ''
        self.cache, self.drag = {}, None
        self.setMinimumSize(240, 160)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('封面画布：拖动、缩放和旋转')

    def area(self):
        width, height = SIZES[self.ratio]
        scale = min(max(1, self.width()-30)/width, max(1, self.height()-30)/height)
        actual = QRectF((self.width()-width*scale)/2, (self.height()-height*scale)/2, width*scale, height*scale)
        virtual = QRectF(actual.left()-(1920-width)/2*scale, actual.top(), 1920*scale, 1080*scale)
        return actual, virtual

    def current(self):
        return next((item for item in self.data['views'][self.ratio]['elements'] if item['id'] == self.selection), None) if self.data else None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#12151b'))
        if not self.data:
            return
        actual, virtual = self.area()
        data = self.data
        if self.drag:
            data = deepcopy(data)
            if self.drag['mode'] == 'background':
                data['views'][self.ratio]['frame'] = self.drag['draft']
            else:
                item = next(item for item in data['views'][self.ratio]['elements'] if item['id'] == self.selection)
                item.update(self.drag['draft'])
        try:
            image = render_cover(data, self.ratio, cache=self.cache)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(actual, image)
        except (OSError, ValueError) as error:
            painter.setPen(Qt.white)
            painter.drawText(self.rect().adjusted(15, 15, -15, -15), Qt.TextWordWrap, str(error))
            return
        item = next((row for row in data['views'][self.ratio]['elements'] if row['id'] == self.selection), None)
        if item:
            _, _, corners, rotate = geometry(item, virtual)
            painter.setClipRect(actual.adjusted(-10, -10, 10, 10))
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor('#00b5df'), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(QPolygonF(corners))
            painter.drawLine((corners[0]+corners[1])/2, rotate)
            painter.setBrush(Qt.white)
            for point in corners:
                painter.drawRect(QRectF(point.x()-4, point.y()-4, 8, 8))
            painter.drawEllipse(rotate, 5, 5)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.data:
            return
        self.setFocus()
        point = event.position()
        actual, virtual = self.area()
        item, corner, mode = self.current(), None, 'move'
        if item:
            _, _, corners, rotate = geometry(item, virtual)
            corner = next((i for i, value in enumerate(corners) if (point-value).manhattanLength() < 14), None)
            mode = 'rotate' if (point-rotate).manhattanLength() < 16 else 'size' if corner is not None else 'move'
        if mode == 'move':
            item = None
            for row in reversed(self.data['views'][self.ratio]['elements']):
                transform, rect, _, _ = geometry(row, virtual)
                if row['opacity'] > 0 and rect.contains(transform.inverted()[0].map(point)) and actual.contains(point):
                    item = row
                    break
        self.selection = item['id'] if item else ''
        self.selected.emit(self.selection)
        if item:
            self.drag = dict(mode=mode, item=dict(item), draft=dict(item), origin=point, corner=corner)
        elif actual.contains(point) and self.data['background']:
            self.drag = dict(mode='background', item=dict(self.data['views'][self.ratio]['frame']),
                             draft=dict(self.data['views'][self.ratio]['frame']), origin=point)
        self.update()

    def mouseMoveEvent(self, event):
        if not self.drag:
            return
        drag, point = self.drag, event.position()
        item, draft = drag['item'], dict(drag['item'])
        actual, virtual = self.area()
        if drag['mode'] == 'background':
            picture = self.cache.get(self.data['background'])
            if picture is not None and not picture.isNull():
                crop = crop_rect(picture, SIZES[self.ratio], item)
                delta = point-drag['origin']
                draft.update(x=max(0, min(1, (crop.center().x()-delta.x()/actual.width()*crop.width())/picture.width())),
                             y=max(0, min(1, (crop.center().y()-delta.y()/actual.height()*crop.height())/picture.height())))
        elif drag['mode'] == 'move':
            delta = point-drag['origin']
            draft.update(x=max(-1, min(2, item['x']+delta.x()/virtual.width())), y=max(-1, min(2, item['y']+delta.y()/virtual.height())))
        else:
            transform, rect, corners, _ = geometry(item, virtual)
            center = transform.map(QPointF())
            if drag['mode'] == 'rotate':
                a, b = point-center, drag['origin']-center
                draft['rotation'] = (item['rotation']+math.degrees(math.atan2(a.y(), a.x())-math.atan2(b.y(), b.x()))+180)%360-180
            else:
                index = drag['corner']
                anchor = corners[(index+2)%4]
                delta = QTransform().rotate(-item['rotation']).map(point-anchor)
                sx, sy = [(-1, -1), (1, -1), (1, 1), (-1, 1)][index]
                dx, dy = sx*rect.width(), sy*rect.height()
                scale = (delta.x()*dx+delta.y()*dy)/(dx*dx+dy*dy)
                lower, upper = max(.02/item['w'], .02/item['h']), min(2/item['w'], 2/item['h'])
                if item['kind'] == 'text':
                    lower, upper = max(lower, 8/item['font_size']), min(upper, 240/item['font_size'])
                scale = max(lower, min(upper, scale))
                center = anchor+QTransform().rotate(item['rotation']).map(QPointF(dx*scale/2, dy*scale/2))
                w, h = item['w']*scale, item['h']*scale
                draft.update(w=w, h=h, x=max(-1, min(2, (center.x()-virtual.left())/virtual.width()-w/2)),
                             y=max(-1, min(2, (center.y()-virtual.top())/virtual.height()-h/2)))
                if item['kind'] == 'text':
                    draft['font_size'] = item['font_size']*scale
        drag['draft'] = draft
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drag and event.button() == Qt.LeftButton:
            drag, self.drag = self.drag, None
            if drag['draft'] != drag['item']:
                self.editRequested.emit('frame' if drag['mode'] == 'background' else 'element', drag['draft'])
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.drag = None
            self.update()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event):
        self.drag = None
        super().hideEvent(event)
