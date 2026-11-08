"""Selection handles inside libmpv; gestures commit once, never on every move."""
import ctypes
import math
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF, QTransform
from .project import placed_layers
from .graphic_geometry import geometry


class ProjectStage(QObject):
    nativeInput = Signal(object, str, object)

    def __init__(self, editor):
        super().__init__(editor)
        self.editor, self.preview = editor, editor.preview
        self.core = None
        self.drag = None
        self.buffer = None
        self.signature = None
        self.visible = False
        self.preview.setMouseTracking(True)
        self.preview.setFocusPolicy(Qt.StrongFocus)
        self.preview.installEventFilter(self)
        self.nativeInput.connect(self.native_input, Qt.QueuedConnection)

    def attach(self):
        self.core = core = self.preview.core
        self.signature, self.buffer, self.visible = None, None, False
        if core is None:
            return
        # Native video windows receive their own mouse messages on Windows.
        core.observe_property('mouse-pos', lambda _name, point: self.nativeInput.emit(core, 'move', point))
        def click(state, *_):
            if state.startswith('d'):
                self.nativeInput.emit(core, 'down', core.mouse_pos)
            elif state.startswith('u'):
                self.nativeInput.emit(core, 'cancel' if 'c' in state else 'up', core.mouse_pos)
        core.register_key_binding('MBTN_LEFT', click)
        core.register_key_binding('MBTN_LEFT_DBL', lambda state, *_:
            self.nativeInput.emit(core, 'edit', core.mouse_pos) if state[:1] in ('d', 'p') else None)
        for key in ('ESC', 'LEFT', 'RIGHT', 'UP', 'DOWN'):
            def pressed(state, *_, key=key):
                if state[:1] in ('d', 'p', 'r'):
                    self.nativeInput.emit(core, key, None)
            core.register_key_binding(key, pressed)

    def frame(self):
        dimensions = self.preview.core.osd_dimensions if self.preview.core else None
        if dimensions and dimensions.get('w') and dimensions.get('h'):
            return QRectF(dimensions.get('ml', 0), dimensions.get('mt', 0),
                          dimensions['w']-dimensions.get('ml', 0)-dimensions.get('mr', 0),
                          dimensions['h']-dimensions.get('mt', 0)-dimensions.get('mb', 0))
        if not self.editor.document:
            return QRectF()
        render = self.editor.document.data['render']
        scale = min(self.preview.width()/render['width'], self.preview.height()/render['height'])
        w, h = render['width']*scale, render['height']*scale
        return QRectF((self.preview.width()-w)/2, (self.preview.height()-h)/2, w, h)

    def layers(self):
        if not self.editor.document:
            return []
        return sorted([item for item, start, end in placed_layers(self.editor.document.data)
                       if item['kind'] != 'audio' and start <= self.editor.position < end], key=lambda item: item['track'])

    def current(self):
        return next((item for item in self.layers() if item['id'] == self.editor.selected_id), None)

    def native_input(self, core, action, point):
        if core is not self.preview.core:
            return
        if action in ('ESC', 'cancel'):
            self.cancel()
        elif action in ('LEFT', 'RIGHT', 'UP', 'DOWN'):
            self.nudge(action)
        elif point:
            self.pointer(action, QPointF(point.get('x', 0), point.get('y', 0)))

    def eventFilter(self, watched, event):
        kinds = {QEvent.MouseButtonPress: 'down', QEvent.MouseMove: 'move', QEvent.MouseButtonRelease: 'up',
                 QEvent.MouseButtonDblClick: 'edit'}
        if event.type() in kinds and (event.type() == QEvent.MouseMove or event.button() == Qt.LeftButton):
            point = event.position()
            if self.preview.core:
                dimensions = self.preview.core.osd_dimensions or {}
                point = QPointF(point.x()*dimensions.get('w', self.preview.width())/max(1, self.preview.width()),
                                point.y()*dimensions.get('h', self.preview.height())/max(1, self.preview.height()))
            self.pointer(kinds[event.type()], point)
            return True
        if event.type() == QEvent.KeyPress:
            keys = {Qt.Key_Left: 'LEFT', Qt.Key_Right: 'RIGHT', Qt.Key_Up: 'UP', Qt.Key_Down: 'DOWN'}
            if event.key() == Qt.Key_Escape:
                self.cancel()
                return True
            if event.key() in keys:
                self.nudge(keys[event.key()], 10 if event.modifiers() & Qt.ShiftModifier else 1)
                return True
        if event.type() in (QEvent.Hide, QEvent.FocusOut):
            self.cancel()
        return super().eventFilter(watched, event)

    def pointer(self, action, point):
        if not self.editor.document or self.editor.preview_dirty:
            return
        frame = self.frame()
        if frame.isEmpty():
            return
        if action == 'edit':
            for item in reversed(self.layers()):
                transform, rect, _, _ = geometry(item, frame)
                if item['opacity'] > 0 and rect.contains(transform.inverted()[0].map(point)):
                    if item['kind'] == 'text':
                        self.cancel()
                        self.editor.select_item(item['id'])
                        self.editor.inspector_scroll.ensureWidgetVisible(self.editor.text)
                        self.editor.text.setFocus(Qt.MouseFocusReason)
                        self.editor.text.selectAll()
                    break
            return
        if action == 'down' and not self.drag:
            self.editor.playing = False
            if self.preview.core:
                self.preview.core.pause = True
                self.editor.position = self.editor.preview_begin+self.preview.position()
            current = self.current()
            mode = 'move'
            corner = None
            if current:
                transform, rect, corners, rotate = geometry(current, frame)
                corner = next((i for i, value in enumerate(corners) if (point-value).manhattanLength() < 14), None)
                if (point-rotate).manhattanLength() < 16:
                    mode = 'rotate'
                elif corner is not None:
                    mode = 'size'
                elif not rect.contains(transform.inverted()[0].map(point)):
                    current = None
            if current is None:
                for item in reversed(self.layers()):
                    transform, rect, _, _ = geometry(item, frame)
                    if item['opacity'] > 0 and rect.contains(transform.inverted()[0].map(point)):
                        current = item
                        break
            self.editor.select_item(current['id'] if current else '')
            if current:
                self.drag = dict(item=dict(current), draft=dict(current), origin=point, frame=frame, mode=mode, corner=corner)
                self.editor.preview_hint.setText('拖动调整位置，角点等比缩放，圆点旋转；松开更新预览，Esc取消。')
        elif action == 'move' and self.drag:
            drag = self.drag
            item, frame = drag['item'], drag['frame']
            draft = dict(item)
            transform, rect, corners, _ = geometry(item, frame)
            if drag['mode'] == 'move':
                delta = point-drag['origin']
                draft.update(x=item['x']+delta.x()/frame.width(), y=item['y']+delta.y()/frame.height())
                for key, dimension, extent in [('x', 'w', frame.width()), ('y', 'h', frame.height())]:
                    snapped = self.editor.snap.isChecked() and abs(draft[key]+draft[dimension]/2-.5)*extent < 8
                    drag['snap_'+key] = snapped
                    if snapped:
                        draft[key] = (1-draft[dimension])/2
            elif drag['mode'] == 'rotate':
                center = transform.map(QPointF())
                vector, initial = point-center, drag['origin']-center
                angle = math.degrees(math.atan2(vector.y(), vector.x())-math.atan2(initial.y(), initial.x()))
                draft['rotation'] = (item.get('rotation', 0)+angle+180)%360-180
            else:
                # Keep the opposite corner fixed and preserve aspect even when rotated.
                index = drag['corner']
                anchor = corners[(index+2)%4]
                inverse = QTransform().rotate(-item.get('rotation', 0))
                delta = inverse.map(point-anchor)
                sx, sy = [(-1, -1), (1, -1), (1, 1), (-1, 1)][index]
                dx, dy = sx*rect.width(), sy*rect.height()
                scale = (delta.x()*dx+delta.y()*dy)/(dx*dx+dy*dy)
                scale = max(max(.02/item['w'], .02/item['h']), min(min(2/item['w'], 2/item['h']), scale))
                if item['kind'] == 'text':
                    scale = max(8/item['font_size'], min(200/item['font_size'], scale))
                    draft['font_size'] = item['font_size']*scale
                center = anchor+QTransform().rotate(item.get('rotation', 0)).map(QPointF(dx*scale/2, dy*scale/2))
                w, h = item['w']*scale, item['h']*scale
                draft.update(w=w, h=h, x=(center.x()-frame.left())/frame.width()-w/2,
                             y=(center.y()-frame.top())/frame.height()-h/2)
            draft['x'], draft['y'] = max(-1, min(2, draft['x'])), max(-1, min(2, draft['y']))
            drag['draft'] = draft
        elif action == 'up' and self.drag:
            draft = self.drag['draft']
            self.drag = None
            keys = ('x', 'y', 'w', 'h', 'rotation') + (('font_size',) if draft['kind'] == 'text' else ())
            self.editor.place_item(draft['id'], {key: draft.get(key, 0) for key in keys})
        self.update()

    def cancel(self):
        self.drag = None
        self.signature = None

    def nudge(self, direction, pixels=1):
        item = self.current()
        if item is not None and not self.editor.playing:
            render = self.editor.document.data['render']
            key = 'x' if direction in ('LEFT', 'RIGHT') else 'y'
            delta = pixels/float(render['width'] if key == 'x' else render['height'])
            self.editor.place_item(item['id'], {key: max(-1, min(2, item[key]+delta*(-1 if direction in ('LEFT', 'UP') else 1)))})

    def clear(self):
        if self.visible and self.core is self.preview.core and self.core is not None:
            self.core.overlay_remove(63)
        self.visible, self.signature, self.buffer = False, None, None
        self.drag = None

    def update(self):
        core = self.preview.core
        item = self.drag['draft'] if self.drag else self.current()
        if core is None or core is not self.core or self.editor.playing or self.editor.preview_dirty or item is None:
            if self.visible:
                self.clear()
            return
        dimensions = core.osd_dimensions or {}
        width, height = dimensions.get('w', 0), dimensions.get('h', 0)
        if not width or not height:
            return
        frame = self.frame()
        guides = tuple(self.drag.get('snap_'+key, False) for key in ('x', 'y')) if self.drag else (False, False)
        signature = (width, height, frame.getRect(), guides, tuple(item.get(key, 0) for key in ('x', 'y', 'w', 'h', 'rotation')))
        if signature == self.signature:
            return
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        _, _, corners, rotate = geometry(item, frame)
        painter.setPen(QPen(QColor('#00b5e2'), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF(corners))
        if guides[0]:
            painter.drawLine(QPointF(frame.center().x(), frame.top()), QPointF(frame.center().x(), frame.bottom()))
        if guides[1]:
            painter.drawLine(QPointF(frame.left(), frame.center().y()), QPointF(frame.right(), frame.center().y()))
        painter.drawLine((corners[0]+corners[1])/2, rotate)
        painter.setBrush(QColor('#ffffff'))
        for point in corners:
            painter.drawRect(QRectF(point.x()-4, point.y()-4, 8, 8))
        painter.drawEllipse(rotate, 5, 5)
        painter.end()
        buffer = ctypes.create_string_buffer(image.constBits().tobytes())
        core.overlay_add(63, 0, 0, '&'+str(ctypes.addressof(buffer)), 0, 'bgra', width, height, image.bytesPerLine())
        self.buffer, self.signature, self.visible = buffer, signature, True
