"""In-application crops keep source pixels and are independent of the video tile."""
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget

from ddm import theme


class FloatingCrop(QWidget):
    closed = Signal()
    recrop = Signal()

    def __init__(self, parent, label, crop):
        super().__init__(parent)
        self.label, self.crop = label, QRectF(*crop)
        self.image = QImage()
        self.avatar = QImage()
        self.avatar_key = None
        self.frame_key = None
        self.drag = None
        self.lock_mode = 0
        self.picture_widget = None
        self.tools_visible = False
        self.hover_point = QPoint(-1, -1)
        self.aspect = 16 / 9
        self.setMouseTracking(True)
        # Ordinary child transparency restores the backing store behind moved crops.
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setToolTip(label + '\n随意移动；定位图标切换画面内／固定位置；滚轮或右下角缩放；双击重选；右上角关闭。')
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
            self.frame_key = None
            self.image = QImage()
            self.update()
            return
        if frame[0] == self.frame_key:
            return
        self.frame_key = frame[0]
        self.image = frame[1] if isinstance(frame[1], QImage) else QImage.fromData(frame[1])
        if not self.image.isNull():
            self.aspect = self.image.width() * self.crop.width() / max(1, self.image.height() * self.crop.height())
        self.update()

    def fit_geometry(self, x, y, width):
        parent = self.parentWidget()
        previous = self.geometry()
        bounds = self.movement_bounds()
        maximum = min(bounds.width(), bounds.height() * self.aspect)
        width = max(1, min(maximum, max(120, width)))
        height = min(bounds.height(), round(width / self.aspect))
        self.setGeometry(round(max(bounds.x(), min(bounds.right() + 1 - width, x))),
                         round(max(bounds.y(), min(bounds.bottom() + 1 - height, y))), round(width), height)
        if previous != self.geometry():
            parent.update(previous)

    def movement_bounds(self):
        parent = self.parentWidget()
        if self.lock_mode == 1 and self.picture_widget is not None:
            picture = self.picture_widget
            area = QRect(picture.mapTo(parent, QPoint()), picture.size())
            if not picture.image.isNull():
                size = picture._source_rect().size().scaled(picture.size(), Qt.KeepAspectRatio)
                area = QRect(picture.mapTo(parent, QPoint((picture.width() - size.width()) // 2,
                                                         (picture.height() - size.height()) // 2)), size)
            return area.intersected(parent.rect())
        return parent.rect()

    def lock_rect(self):
        return QRect(self.width() - 60, 8, 24, 24)

    def cycle_lock(self):
        self.lock_mode = (self.lock_mode + 1) % 3
        self.drag = None
        self.fit_geometry(self.x(), self.y(), self.width())
        self.setToolTip(self.label + '\n' + ('随意移动', '锁定在画面内', '锁定在当前位置')[self.lock_mode]
                        + '；点击定位图标切换；右上角关闭。')
        self.update()

    def eventFilter(self, watched, event):
        if watched in (self.parentWidget(), self.picture_widget) and event.type() in (QEvent.Resize, QEvent.Move):
            self.fit_geometry(self.x(), self.y(), self.width())
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
        return point.x() >= self.width() - 18 and point.y() >= self.height() - 18

    def paint_picture(self, painter):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        brush = QBrush(theme.qcolor(theme.CONTENT))
        if not self.image.isNull():
            source = QRectF(self.crop.x() * self.image.width(), self.crop.y() * self.image.height(),
                            self.crop.width() * self.image.width(), self.crop.height() * self.image.height()).intersected(QRectF(self.image.rect()))
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            if not source.isEmpty():
                brush = QBrush(self.image)
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
        painter.translate(self.lock_rect().center())
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
        painter.setBrush(theme.qcolor(theme.ACCENT) if self.close_rect().contains(self.hover_point) else QColor(0, 0, 0, 170))
        painter.drawRoundedRect(QRectF(self.close_rect()), 12, 12)
        painter.setPen(QColor('#e7ebf0'))
        close = self.close_rect().center()
        painter.drawLine(close + QPoint(-4, -4), close + QPoint(4, 4))
        painter.drawLine(close + QPoint(-4, 4), close + QPoint(4, -4))
        painter.setPen(QPen(QColor('#e7ebf0'), 1.5))
        for offset in (() if self.lock_mode == 2 else (5, 10)):
            painter.drawLine(self.width() - offset, self.height() - 3,
                             self.width() - 3, self.height() - offset)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tools_visible = True
            if self.close_rect().contains(event.position().toPoint()):
                self.close()
                return
            if self.lock_rect().contains(event.position().toPoint()):
                self.cycle_lock()
                return
            if self.lock_mode == 2:
                event.accept()
                return
            self.raise_()
            self.drag = (event.globalPosition().toPoint(), self.geometry(), self.resize_hit(event.position()))
            self.setCursor(Qt.SizeFDiagCursor if self.drag[2] else Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        self.tools_visible = True
        self.hover_point = event.position().toPoint()
        if self.drag is not None:
            origin, geometry, resizing = self.drag
            delta = event.globalPosition().toPoint() - origin
            if resizing:
                change = (delta.x() * self.aspect ** 2 + delta.y() * self.aspect) / (self.aspect ** 2 + 1)
                self.fit_geometry(geometry.x(), geometry.y(), geometry.width() + change)
            else:
                self.fit_geometry(geometry.x() + delta.x(), geometry.y() + delta.y(), geometry.width())
        else:
            self.setCursor(Qt.PointingHandCursor if self.close_rect().contains(event.position().toPoint()) or self.lock_rect().contains(event.position().toPoint())
                           else Qt.ArrowCursor if self.lock_mode == 2 else Qt.SizeFDiagCursor if self.resize_hit(event.position()) else Qt.OpenHandCursor)
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
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

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
