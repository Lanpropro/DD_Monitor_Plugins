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
        self.frame_key = None
        self.drag = None
        self.tools_visible = False
        self.hover_point = QPoint(-1, -1)
        self.aspect = 16 / 9
        self.setMouseTracking(True)
        # Ordinary child transparency restores the backing store behind moved crops.
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setToolTip('拖动画面移动；滚轮或右下角缩放；双击重新框选；右上角关闭。')
        self.setAccessibleName(label + '：悬浮画面')
        parent.installEventFilter(self)

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
        maximum = min(parent.width(), parent.height() * self.aspect)
        width = max(1, min(maximum, max(120, width)))
        height = min(parent.height(), round(width / self.aspect))
        self.setGeometry(round(max(0, min(parent.width() - width, x))),
                         round(max(0, min(parent.height() - height, y))), round(width), height)
        if previous != self.geometry():
            parent.update(previous)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
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
        text = painter.fontMetrics().elidedText(self.label + ' · 悬浮画面', Qt.ElideRight, max(0, self.width() - 64))
        title = QRectF(8, 8, min(self.width() - 56, painter.fontMetrics().horizontalAdvance(text) + 16), 24)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(title, 12, 12)
        painter.setBrush(theme.qcolor(theme.ACCENT) if self.close_rect().contains(self.hover_point) else QColor(0, 0, 0, 170))
        painter.drawRoundedRect(QRectF(self.close_rect()), 12, 12)
        painter.setPen(QColor('#e7ebf0'))
        painter.drawText(title.adjusted(8, 0, -8, 0), Qt.AlignVCenter, text)
        close = self.close_rect().center()
        painter.drawLine(close + QPoint(-4, -4), close + QPoint(4, 4))
        painter.drawLine(close + QPoint(-4, 4), close + QPoint(4, -4))
        painter.setPen(QPen(QColor('#e7ebf0'), 1.5))
        for offset in (5, 10):
            painter.drawLine(self.width() - offset, self.height() - 3,
                             self.width() - 3, self.height() - offset)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tools_visible = True
            if self.close_rect().contains(event.position().toPoint()):
                self.close()
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
                change = delta.x() if abs(delta.x()) >= abs(delta.y() * self.aspect) else delta.y() * self.aspect
                self.fit_geometry(geometry.x(), geometry.y(), geometry.width() + change)
            else:
                self.fit_geometry(geometry.x() + delta.x(), geometry.y() + delta.y(), geometry.width())
        else:
            self.setCursor(Qt.PointingHandCursor if self.close_rect().contains(event.position().toPoint())
                           else Qt.SizeFDiagCursor if self.resize_hit(event.position()) else Qt.OpenHandCursor)
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.tools_visible = self.underMouse()
        self.setCursor(Qt.OpenHandCursor)
        self.update()

    def wheelEvent(self, event):
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
