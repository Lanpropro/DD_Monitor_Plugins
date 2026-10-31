"""In-application crops keep source pixels and are independent of the video tile."""
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ddm import theme


class FloatingCrop(QWidget):
    closed = Signal()
    recrop = Signal()
    HEADER = 24

    def __init__(self, parent, label, crop):
        super().__init__(parent)
        self.label, self.crop = label, QRectF(*crop)
        self.image = QImage()
        self.frame_key = None
        self.drag = None
        self.aspect = 16 / 9
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
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
        maximum = min(parent.width(), max(1, parent.height() - self.HEADER) * self.aspect)
        width = max(1, min(maximum, max(120, width)))
        height = min(parent.height(), round(width / self.aspect) + self.HEADER)
        self.setGeometry(round(max(0, min(parent.width() - width, x))),
                         round(max(0, min(parent.height() - height, y))), round(width), height)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.fit_geometry(self.x(), self.y(), self.width())
        return False

    def close_rect(self):
        return QRect(self.width() - 24, 0, 24, self.HEADER)

    def resize_hit(self, point):
        return point.x() >= self.width() - 18 and point.y() >= self.height() - 18

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.CONTENT))
        if not self.image.isNull():
            source = QRectF(self.crop.x() * self.image.width(), self.crop.y() * self.image.height(),
                            self.crop.width() * self.image.width(), self.crop.height() * self.image.height())
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(QRectF(0, self.HEADER, self.width(), self.height() - self.HEADER), self.image, source)
        painter.setPen(theme.qcolor(theme.TEXT1))
        text = painter.fontMetrics().elidedText(self.label + ' · 悬浮画面', Qt.ElideRight, max(0, self.width() - 40))
        painter.drawText(QRect(8, 0, self.width() - 36, self.HEADER), Qt.AlignVCenter, text)
        close = self.close_rect().center()
        painter.drawLine(close + QPoint(-4, -4), close + QPoint(4, 4))
        painter.drawLine(close + QPoint(-4, 4), close + QPoint(4, -4))
        painter.setPen(QPen(theme.qcolor(theme.TEXT1), 1.5))
        for offset in (5, 10):
            painter.drawLine(self.width() - offset, self.height() - 3,
                             self.width() - 3, self.height() - offset)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.close_rect().contains(event.position().toPoint()):
                self.close()
                return
            self.raise_()
            self.drag = (event.globalPosition().toPoint(), self.geometry(), self.resize_hit(event.position()))
            self.setCursor(Qt.SizeFDiagCursor if self.drag[2] else Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
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

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.setCursor(Qt.OpenHandCursor)

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
