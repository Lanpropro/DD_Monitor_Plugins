"""Qt video presentation and crop selection; no playback coordinator state."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRect, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QFrame, QSizePolicy

from ddm import theme


class Canvas(QFrame):
    def __init__(self, parent=None, selecting=False):
        super().__init__(parent)
        self.image = QImage()
        self.waiting = False
        self.zoom_crop = False
        self.frame_key = None
        self.requested_frame_key = None
        self.frame = None
        self.missing_since = None
        self.preview_only = False
        self.preview_shift = None
        self.transparent_background = False
        self.fill_height = False
        self.presented = 0
        self.repeated = 0
        self.paint_probe = None
        self.selecting = selecting
        self.crop = QRectF(0, 0, 1, 1)
        self.anchor = None
        self.selection_ready = False
        self.selection_zoom = 1.
        self.selection_pan = QPointF()
        self.image_rect = QRectF()
        self.setMouseTracking(selecting)
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def set_frame(self, item):
        key = item[0] if item is not None else None
        if key == self.frame_key and (item is not None or self.image.isNull()):
            self.repeated += 1
            return
        if item is not None:
            self.presented += 1
        self.frame_key = key
        self.frame = item
        if self.paint_probe is not None and self.paint_probe.thread is not None and item is not None:
            self.paint_probe.submitted.record(time.perf_counter(), key)
        self.image = (item[1] if isinstance(item[1], QImage) else QImage.fromData(item[1])) if item is not None else QImage()
        self._sync_opaque_paint()
        self.update()

    def show_at(self, decoder, timestamp):
        frame = decoder.history.frame_at(timestamp)
        self.requested_frame_key = frame[0] if frame is not None else None
        if frame is not None:
            ready = decoder.picture_at(timestamp) if hasattr(decoder, "picture_at") else frame
            if ready is not None:
                self.set_frame(ready)
            return ready
        return None

    def playback_waiting(self, decoder, timestamp, frame, shared_wait=False):
        if frame is not None:
            self.missing_since = None
            return shared_wait
        now = time.monotonic()
        if self.missing_since is None:
            self.missing_since = now
        bounds = decoder.history.bounds_at(timestamp) if decoder is not None else None
        retained = (bounds is not None and bounds[0] <= timestamp <= bounds[1]
                    and self.frame_key is not None and 0 <= timestamp - self.frame_key <= .15
                    and now - self.missing_since <= .15)
        return shared_wait or not retained

    def _source_rect(self):
        image = self.image
        source = image.rect()
        if self.zoom_crop:
            rect = self.crop
            source = QRect(round(rect.x() * image.width()), round(rect.y() * image.height()),
                           max(1, round(rect.width() * image.width())),
                           max(1, round(rect.height() * image.height()))).intersected(source)
        return source

    def _sync_opaque_paint(self):
        source = self._source_rect()
        covered = (not self.image.isNull() and self.fill_height and source.height() > 0
                   and source.width() * self.height() <= self.width() * source.height())
        self.setAttribute(Qt.WA_OpaquePaintEvent, not self.transparent_background or covered)

    def resizeEvent(self, event):
        self._sync_opaque_paint()
        super().resizeEvent(event)

    def paintEvent(self, event):
        probe = self.paint_probe
        if probe is None or probe.thread is None:
            return self._paint_frame(event)
        started = time.perf_counter()
        try:
            self._paint_frame(event)
        finally:
            probe.paints.append((time.perf_counter(), (time.perf_counter() - started) * 1000))
            probe.painted.record(time.perf_counter(), self.frame_key)

    def _paint_frame(self, event):
        painter = QPainter(self)
        if not self.transparent_background:
            painter.fillRect(self.rect(), theme.qcolor(theme.CONTENT) if theme.is_light() else QColor("#101216"))
        if self.image.isNull():
            return
        image = self.image
        source = self._source_rect()
        if (self.fill_height and source.height() > 0
                and source.width() * self.height() <= self.width() * source.height()):
            size = QSizeF(source.width() * self.height() / source.height(), self.height())
        else:
            size = source.size().scaled(self.size(), Qt.KeepAspectRatio)
        self.image_rect = QRectF((self.width() - size.width()) / 2,
                                (self.height() - size.height()) / 2,
                                size.width(), size.height())
        if self.selecting:
            center = self.image_rect.center()
            self.image_rect = QRectF(center.x() - size.width() * self.selection_zoom / 2 + self.selection_pan.x(),
                                     center.y() - size.height() * self.selection_zoom / 2 + self.selection_pan.y(),
                                     size.width() * self.selection_zoom, size.height() * self.selection_zoom)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        started = time.perf_counter()
        painter.drawImage(self.image_rect, image, QRectF(source))
        if self.paint_probe is not None and self.paint_probe.thread is not None:
            self.paint_probe.timings.record('main_draw_image', time.perf_counter() - started)
        if self.selecting:
            painter.setPen(QPen(QColor("#38bdf8"), 2))
            rect = self.selection_rect()
            painter.drawRect(rect)
            if self.selection_ready:
                painter.setBrush(QColor('#38bdf8'))
                for point in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight(),
                              QPointF(rect.center().x(), rect.top()), QPointF(rect.center().x(), rect.bottom()),
                              QPointF(rect.left(), rect.center().y()), QPointF(rect.right(), rect.center().y())):
                    painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))

    def selection_rect(self):
        rect, image = self.crop, self.image_rect
        return QRectF(image.x() + rect.x() * image.width(), image.y() + rect.y() * image.height(),
                      rect.width() * image.width(), rect.height() * image.height())

    def reset_selection(self):
        self.crop = QRectF(0, 0, 1, 1)
        self.selection_ready = False
        self.anchor = None
        self.selection_zoom = 1.
        self.selection_pan = QPointF()
        self.update()

    def wheelEvent(self, event):
        if not self.selecting or self.image_rect.isEmpty():
            super().wheelEvent(event)
            return
        if self.anchor is not None:
            event.accept()
            return
        if not event.modifiers() & Qt.ControlModifier:
            delta = QPointF(event.pixelDelta())
            if delta.isNull():
                delta = QPointF(event.angleDelta()) * .5
            if event.modifiers() & Qt.ShiftModifier:
                delta = QPointF(delta.x() or delta.y(), 0)
            self.selection_pan += delta
            self.update()
            event.accept()
            return
        zoom = max(1., min(12., self.selection_zoom * 1.2 ** (event.angleDelta().y() / 120)))
        ratio = zoom / self.selection_zoom
        center = QPointF(self.width() / 2, self.height() / 2)
        self.selection_pan = event.position() - center - (event.position() - center - self.selection_pan) * ratio
        self.selection_zoom = zoom
        if zoom == 1.:
            self.selection_pan = QPointF()
        self.update()
        event.accept()

    def selection_hit(self, point):
        if not self.selection_ready:
            return 'new' if self.image_rect.contains(point) else ''
        rect = self.selection_rect()
        if not rect.adjusted(-7, -7, 7, 7).contains(point):
            return ''
        horizontal = 'l' if abs(point.x() - rect.left()) <= 7 else 'r' if abs(point.x() - rect.right()) <= 7 else ''
        vertical = 't' if abs(point.y() - rect.top()) <= 7 else 'b' if abs(point.y() - rect.bottom()) <= 7 else ''
        return horizontal + vertical or 'move'

    @staticmethod
    def selection_cursor(hit):
        return {'new': Qt.CrossCursor, 'move': Qt.SizeAllCursor, 'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor,
                't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor, 'lt': Qt.SizeFDiagCursor, 'rb': Qt.SizeFDiagCursor,
                'rt': Qt.SizeBDiagCursor, 'lb': Qt.SizeBDiagCursor}.get(hit, Qt.ArrowCursor)

    def _point(self, position):
        rect = self.image_rect
        return QPointF(max(0, min(1, (position.x() - rect.x()) / rect.width())),
                       max(0, min(1, (position.y() - rect.y()) / rect.height())))

    def mousePressEvent(self, event):
        if self.selecting and not self.image_rect.isEmpty() and event.button() == Qt.LeftButton:
            hit = self.selection_hit(event.position())
            if hit:
                self.anchor = (hit, self._point(event.position()), QRectF(self.crop))
                event.accept()

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            hit, start, original = self.anchor
            point = self._point(event.position())
            delta = point - start
            if hit == 'new':
                self.crop = QRectF(start, point).normalized()
            elif hit == 'move':
                self.crop = QRectF(original)
                self.crop.moveTo(max(0, min(1 - original.width(), original.x() + delta.x())),
                                 max(0, min(1 - original.height(), original.y() + delta.y())))
            else:
                rect = QRectF(original)
                if 'l' in hit:
                    rect.setLeft(max(0, min(rect.right() - 1 / self.image.width(), original.left() + delta.x())))
                if 'r' in hit:
                    rect.setRight(min(1, max(rect.left() + 1 / self.image.width(), original.right() + delta.x())))
                if 't' in hit:
                    rect.setTop(max(0, min(rect.bottom() - 1 / self.image.height(), original.top() + delta.y())))
                if 'b' in hit:
                    rect.setBottom(min(1, max(rect.top() + 1 / self.image.height(), original.bottom() + delta.y())))
                self.crop = rect
            self.update()
        elif self.selecting:
            self.setCursor(self.selection_cursor(self.selection_hit(event.position())))

    def mouseReleaseEvent(self, event):
        if self.anchor is not None:
            self.selection_ready = self.crop.width() > 0 and self.crop.height() > 0
        self.anchor = None
        if self.selecting:
            self.update()
