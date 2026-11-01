"""In-application crops keep source pixels and are independent of the video tile."""
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget

from ddm import theme
from .upscale import CropEnhancer


class FloatingCrop(QWidget):
    closed = Signal()
    recrop = Signal()
    background_exposed = Signal(QRect)
    gesture_finished = Signal()

    def __init__(self, parent, label, crop):
        super().__init__(parent)
        self.label, self.crop = label, QRectF(*crop)
        self.image = QImage()
        self.avatar = QImage()
        self.avatar_key = None
        self.frame_key = None
        self.quality_mode = 'source'
        self.enhancer = None
        self.enhanced = QImage()
        self.enhance_timer = QTimer(self)
        self.enhance_timer.setInterval(16)
        self.enhance_timer.timeout.connect(self._refresh_enhancement)
        self.drag = None
        self.lock_mode = 0
        self.picture_widget = None
        self.tools_visible = False
        self.hover_point = QPoint(-1, -1)
        self.aspect = 16 / 9
        self.placement = None
        self.setMouseTracking(True)
        # Ordinary child transparency restores the backing store behind moved crops.
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setToolTip(label + '\n拖动移动；滚轮或四角缩放；微调按钮或双击调整选区；定位图标切换锁定；右上角关闭。')
        self.setAccessibleName(label + '：悬浮画面')
        parent.installEventFilter(self)

    def set_avatar(self, pixmap):
        if pixmap is not None and not pixmap.isNull() and pixmap.cacheKey() != self.avatar_key:
            self.avatar_key = pixmap.cacheKey()
            self.avatar = pixmap.toImage()
            self.avatar.setDevicePixelRatio(1)
            self.update()

    def set_frame(self, frame):
        if frame is None:
            self.reset_enhancement()
            self.frame_key = None
            self.image = QImage()
            self.update()
            return
        if frame[0] == self.frame_key:
            return
        if self.frame_key is not None and frame[0] < self.frame_key:
            self.reset_enhancement()
        self.frame_key = frame[0]
        self.image = frame[1] if isinstance(frame[1], QImage) else QImage.fromData(frame[1])
        if not self.image.isNull():
            self.aspect = self.image.width() * self.crop.width() / max(1, self.image.height() * self.crop.height())
            if self.quality_mode == 'superres':
                self.enhancer.submit(self.frame_key, self.image, self.crop)
                self._refresh_enhancement()
        self.update()

    def set_quality_mode(self, mode):
        if mode == self.quality_mode:
            return
        self.quality_mode = mode
        self.reset_enhancement()
        if mode == 'superres':
            if self.enhancer is None:
                self.enhancer = CropEnhancer()
            self.enhance_timer.start()
            if not self.image.isNull():
                self.enhancer.submit(self.frame_key, self.image, self.crop)
        else:
            self.enhance_timer.stop()
        self.update()

    def reset_enhancement(self):
        self.enhanced = QImage()
        if self.enhancer is not None:
            self.enhancer.reset()

    def diagnostics(self):
        return dict(mode=self.quality_mode, source_size=(self.image.width(), self.image.height()),
                    enhanced=not self.enhanced.isNull(),
                    upscale=self.enhancer.diagnostics() if self.enhancer is not None else {})

    def _refresh_enhancement(self):
        image = self.enhancer.image_at(self.frame_key) if self.frame_key is not None else None
        image = image if image is not None else QImage()
        if image.cacheKey() != self.enhanced.cacheKey():
            self.enhanced = image
            self.update()

    def fit_geometry(self, x, y, width, remember=True):
        parent = self.parentWidget()
        previous = self.geometry()
        bounds = self.movement_bounds()
        maximum = min(bounds.width(), bounds.height() * self.aspect)
        width = max(1, min(maximum, max(120, width)))
        height = min(bounds.height(), round(width / self.aspect))
        self.setGeometry(round(max(bounds.x(), min(bounds.right() + 1 - width, x))),
                         round(max(bounds.y(), min(bounds.bottom() + 1 - height, y))), round(width), height)
        if remember and bounds.width() > 0 and bounds.height() > 0:
            self.placement = ((self.x() - bounds.x()) / bounds.width(),
                              (self.y() - bounds.y()) / bounds.height(), self.width() / bounds.width())
        if previous != self.geometry():
            parent.update(previous)
            self.background_exposed.emit(previous)

    def movement_bounds(self):
        parent = self.parentWidget()
        if self.lock_mode == 1 and self.picture_widget is not None:
            picture = self.picture_widget
            area = QRect(picture.mapTo(parent, QPoint()), picture.size())
            return area.intersected(parent.rect())
        return parent.rect()

    def lock_rect(self):
        return QRect(self.width() - 60, 8, 24, 24)

    def adjust_rect(self):
        return QRect(self.width() - 88, 8, 24, 24)

    def cycle_lock(self):
        self.lock_mode = (self.lock_mode + 1) % 3
        self.drag = None
        self.fit_geometry(self.x(), self.y(), self.width())
        self.setToolTip(self.label + '\n' + ('随意移动', '锁定在格子内', '锁定在当前位置')[self.lock_mode]
                        + '；点击定位图标切换；右上角关闭。')
        self.update()

    def eventFilter(self, watched, event):
        if watched in (self.parentWidget(), self.picture_widget) and event.type() in (QEvent.Resize, QEvent.Move):
            bounds = self.movement_bounds()
            if self.placement is not None:
                x, y, width = self.placement
                self.fit_geometry(bounds.x() + x * bounds.width(), bounds.y() + y * bounds.height(),
                                  width * bounds.width(), remember=False)
        return False

    def close_rect(self):
        return QRect(self.width() - 32, 8, 24, 24)

    def enterEvent(self, event):
        self.tools_visible = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.drag is None:
            self.tools_visible = False
        self.hover_point = QPoint(-1, -1)
        self.update()
        super().leaveEvent(event)

    def resize_hit(self, point):
        x = -1 if point.x() < 18 else 1 if point.x() >= self.width() - 18 else 0
        y = -1 if point.y() < 18 else 1 if point.y() >= self.height() - 18 else 0
        return (x, y) if x and y else None

    @staticmethod
    def resize_cursor(corner):
        return Qt.SizeFDiagCursor if corner[0] == corner[1] else Qt.SizeBDiagCursor

    def paint_picture(self, painter):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        brush = QBrush(theme.qcolor(theme.CONTENT))
        if not self.image.isNull():
            source = QRectF(self.crop.x() * self.image.width(), self.crop.y() * self.image.height(),
                            self.crop.width() * self.image.width(), self.crop.height() * self.image.height()).intersected(QRectF(self.image.rect()))
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            if not source.isEmpty():
                image = self.image
                if not self.enhanced.isNull():
                    image = self.enhanced
                    source = QRectF(image.rect())
                brush = QBrush(image)
                transform = QTransform.fromScale(self.width() / source.width(), self.height() / source.height())
                transform.translate(-source.x(), -source.y())
                brush.setTransform(transform)
        painter.setBrush(brush)
        painter.drawRoundedRect(QRectF(self.rect()), 10, 10)

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_picture(painter)
        if not self.tools_visible:
            return
        painter.setPen(Qt.NoPen)
        if not self.avatar.isNull():
            avatar = QBrush(self.avatar)
            transform = QTransform()
            transform.translate(8, 8)
            transform.scale(24 / self.avatar.width(), 24 / self.avatar.height())
            avatar.setTransform(transform)
            painter.setBrush(avatar)
            painter.drawEllipse(QRectF(8, 8, 24, 24))
        painter.setBrush(theme.qcolor(theme.ACCENT) if self.lock_rect().contains(self.hover_point) else QColor(0, 0, 0, 170))
        painter.drawRoundedRect(QRectF(self.lock_rect()), 12, 12)
        painter.save()
        painter.translate(QRectF(self.lock_rect()).center())
        painter.setPen(QPen(QColor('#e7ebf0'), 1.5))
        painter.setBrush(Qt.NoBrush)
        if self.lock_mode == 2:
            painter.drawRoundedRect(QRectF(-5, -1, 10, 8), 1, 1)
            painter.drawArc(QRectF(-3, -7, 6, 9), 0, 180 * 16)
        elif self.lock_mode == 1:
            painter.drawRoundedRect(QRectF(-7, -5, 14, 10), 1, 1)
            painter.drawLine(-3, 0, 3, 0)
            painter.drawLine(0, -3, 0, 3)
        else:
            painter.drawLine(-6, 0, 6, 0)
            painter.drawLine(0, -6, 0, 6)
            for x, y in ((-6, 0), (6, 0), (0, -6), (0, 6)):
                painter.drawLine(x, y, x - (2 if x > 0 else -2 if x < 0 else 2), y - (2 if y > 0 else -2 if y < 0 else 2))
        painter.restore()
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(theme.ACCENT) if self.adjust_rect().contains(self.hover_point) else QColor(0, 0, 0, 170))
        painter.drawRoundedRect(QRectF(self.adjust_rect()), 12, 12)
        painter.save()
        painter.translate(QRectF(self.adjust_rect()).center())
        painter.setPen(QPen(QColor('#e7ebf0'), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(-5, -5, 10, 10))
        for x, y in ((-5, -5), (5, -5), (-5, 5), (5, 5)):
            painter.fillRect(QRectF(x - 1.5, y - 1.5, 3, 3), QColor('#e7ebf0'))
        painter.restore()
        painter.setBrush(theme.qcolor(theme.ACCENT) if self.close_rect().contains(self.hover_point) else QColor(0, 0, 0, 170))
        painter.drawRoundedRect(QRectF(self.close_rect()), 12, 12)
        painter.setPen(QColor('#e7ebf0'))
        close = QRectF(self.close_rect()).center()
        painter.drawLine(close + QPointF(-4, -4), close + QPointF(4, 4))
        painter.drawLine(close + QPointF(-4, 4), close + QPointF(4, -4))
        painter.setPen(QPen(QColor('#e7ebf0'), 1.5))
        if self.lock_mode != 2:
            for x, y, dx, dy in ((0, 0, 1, 1), (self.width(), 0, -1, 1),
                                 (0, self.height(), 1, -1), (self.width(), self.height(), -1, -1)):
                for offset in (5, 10):
                    painter.drawLine(x + dx * offset, y + dy * 3, x + dx * 3, y + dy * offset)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tools_visible = True
            if self.close_rect().contains(event.position().toPoint()):
                self.close()
                return
            if self.lock_rect().contains(event.position().toPoint()):
                self.cycle_lock()
                return
            if self.adjust_rect().contains(event.position().toPoint()):
                self.drag = None
                self.recrop.emit()
                return
            if self.lock_mode == 2:
                event.accept()
                return
            self.raise_()
            self.drag = (event.globalPosition().toPoint(), self.geometry(), self.resize_hit(event.position()))
            self.setCursor(self.resize_cursor(self.drag[2]) if self.drag[2] else Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        self.tools_visible = True
        self.hover_point = event.position().toPoint()
        if self.drag is not None:
            origin, geometry, resizing = self.drag
            delta = event.globalPosition().toPoint() - origin
            if resizing:
                dx, dy = resizing
                change = (dx * delta.x() * self.aspect ** 2 + dy * delta.y() * self.aspect) / (self.aspect ** 2 + 1)
                anchor_x = geometry.x() if dx > 0 else geometry.x() + geometry.width()
                anchor_y = geometry.y() if dy > 0 else geometry.y() + geometry.height()
                bounds = self.movement_bounds()
                room_x = bounds.right() + 1 - anchor_x if dx > 0 else anchor_x - bounds.left()
                room_y = bounds.bottom() + 1 - anchor_y if dy > 0 else anchor_y - bounds.top()
                width = max(1, min(room_x, room_y * self.aspect, max(120, geometry.width() + change)))
                self.fit_geometry(anchor_x if dx > 0 else anchor_x - width,
                                  anchor_y if dy > 0 else anchor_y - round(width / self.aspect), width)
            else:
                self.fit_geometry(geometry.x() + delta.x(), geometry.y() + delta.y(), geometry.width())
        else:
            point = event.position().toPoint()
            action = ('关闭画中画' if self.close_rect().contains(point) else '微调画中画选区' if self.adjust_rect().contains(point)
                      else '切换定位：自由／格子内／固定位置' if self.lock_rect().contains(point) else '')
            corner = self.resize_hit(event.position())
            self.setToolTip(action or self.label + ('：位置已固定' if self.lock_mode == 2 else '：拖动移动，四角或滚轮缩放'))
            self.setCursor(Qt.PointingHandCursor if action else Qt.ArrowCursor if self.lock_mode == 2
                           else self.resize_cursor(corner) if corner else Qt.OpenHandCursor)
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.gesture_finished.emit()
        self.tools_visible = self.underMouse()
        self.setCursor(Qt.ArrowCursor if self.lock_mode == 2 else Qt.OpenHandCursor)
        self.update()

    def wheelEvent(self, event):
        if self.lock_mode == 2:
            event.accept()
            return
        width = self.width() * 1.12 ** (event.angleDelta().y() / 120)
        self.fit_geometry(self.x() + (self.width() - width) / 2,
                          self.y() + (self.width() - width) / self.aspect / 2, width)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag = None
            self.recrop.emit()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.background_exposed.emit(self.geometry())

    def closeEvent(self, event):
        self.enhance_timer.stop()
        if self.enhancer is not None:
            self.enhancer.stop()
        self.closed.emit()
        super().closeEvent(event)
