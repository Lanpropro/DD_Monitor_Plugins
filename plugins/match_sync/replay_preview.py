"""Small, asynchronous cached-frame preview for timeline gestures."""
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ddm import theme
from .video import PictureReader


DEFAULT_PREVIEW_RATIO = 280 / 1280


def preview_geometry(width, height, ratio):
    content_width = max(1, min(round(width * ratio), width - 48,
                               round(max(1, height - 72) * 16 / 9)))
    return QRect(8, max(0, height - round(content_width * 9 / 16) - 56),
                 content_width + 24, round(content_width * 9 / 16) + 48)


class PreviewSizePicker(QWidget):
    changed = Signal(float)

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.ratio = viewer.replay_preview_ratio
        self.image = QImage(viewer.canvas.image)
        self._drag = None
        self.setMinimumSize(300, 210)
        self.setMouseTracking(True)
        self.setAccessibleName('回看预览大小：拖动右上角调整')
        self.setToolTip('拖动预览窗右上角调整大小；双击恢复默认')
        viewer.picture.video.installEventFilter(self)

    def showEvent(self, event):
        self.image = QImage(self.viewer.canvas.image)
        self.ratio = self.viewer.replay_preview_ratio
        self.update()
        super().showEvent(event)

    def stage_rect(self):
        viewport = self.viewer.picture.video.size()
        area = QRectF(self.rect()).adjusted(8, 8, -8, -32)
        factor = min(area.width() / max(1, viewport.width()), area.height() / max(1, viewport.height()))
        size = QSizeF(viewport.width() * factor, viewport.height() * factor)
        return QRectF(area.center().x() - size.width() / 2, area.center().y() - size.height() / 2, size.width(), size.height())

    def card_rect(self):
        viewport = self.viewer.picture.video.size()
        stage = self.stage_rect()
        scale = stage.width() / max(1, viewport.width())
        rect = QRectF(preview_geometry(viewport.width(), viewport.height(), self.ratio)).adjusted(12, 8, -12, -12)
        return QRectF(stage.x() + rect.x() * scale, stage.y() + rect.y() * scale,
                      rect.width() * scale, rect.height() * scale)

    def handle_rect(self):
        point = self.card_rect().topRight()
        return QRectF(point.x() - 8, point.y() - 8, 16, 16)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.update()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        stage = self.stage_rect()
        painter.fillRect(stage, QColor('#101216'))
        if not self.image.isNull():
            size = self.image.size().scaled(stage.size().toSize(), Qt.KeepAspectRatio)
            rect = QRectF(0, 0, size.width(), size.height())
            rect.moveCenter(stage.center())
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(rect, self.image)
        else:
            painter.setPen(theme.qcolor(theme.TEXT3))
            painter.drawText(stage, Qt.AlignCenter, '主画面')
        viewport = self.viewer.picture.video.size()
        factor = stage.width() / max(1, viewport.width())
        geometry = preview_geometry(viewport.width(), viewport.height(), self.ratio)
        painter.save()
        painter.setClipRect(stage)
        painter.translate(stage.topLeft())
        painter.scale(factor, factor)
        painter.translate(geometry.topLeft())
        paint_preview(painter, QRectF(0, 0, geometry.width(), geometry.height()), self.image, '02:30 / 10:00', placeholder='回看预览')
        painter.restore()
        corner = self.card_rect().topRight()
        painter.setPen(QPen(theme.qcolor(theme.ACCENT), 2))
        painter.drawLine(corner + QPointF(-8, 0), corner)
        painter.drawLine(corner, corner + QPointF(0, 8))
        painter.setPen(theme.qcolor(theme.TEXT1))
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(QRectF(0, self.height() - 28, self.width(), 24), Qt.AlignCenter,
                         f'预览宽度 {self.ratio:.0%} · 拖动右上角调整')

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.handle_rect().contains(event.position()):
            self._drag = (event.position().x(), self.ratio)
            self.setCursor(Qt.SizeBDiagCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            x, ratio = self._drag
            self.ratio = round(max(.1, min(.6, ratio + (event.position().x() - x) / max(1, self.stage_rect().width()))), 4)
            self.changed.emit(self.ratio)
            self.update()
            event.accept()
        else:
            self.setCursor(Qt.SizeBDiagCursor if self.handle_rect().contains(event.position()) else Qt.ArrowCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag is not None:
            self._drag = None
            self.changed.emit(self.ratio)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = None
            self.ratio = DEFAULT_PREVIEW_RATIO
            self.changed.emit(self.ratio)
            self.update()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class ReplayPreview(QWidget):
    def __init__(self, viewer):
        super().__init__(viewer.picture.video)
        self.viewer = viewer
        self.image = QImage()
        self.caption = ''
        self.request = None
        self.reader = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAccessibleName('回看目标画面预览')
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.poll)
        self.parentWidget().installEventFilter(self)
        self.hide()

    def reposition(self):
        parent = self.parentWidget()
        self.setGeometry(preview_geometry(parent.width(), parent.height(), self.viewer.replay_preview_ratio))

    def show_target(self, stamp, caption):
        key = self.viewer.alignment.reference or self.viewer.main.currentData()
        row = self.viewer.rows.get(key)
        history = row.decoder.history if row is not None and row.decoder is not None else None
        shifts = self.viewer.replay.shifts_at(stamp)
        if not self.viewer.running or history is None or key not in shifts:
            self.dismiss()
            return
        target = stamp + shifts[key]
        frame = history.frame_at(target)
        if frame is None:
            self.dismiss()
            return
        history = history.segment_at(target) or history
        request = (key, history, frame)
        if request != self.request:
            self.request = request
            self.image = QImage()
        self.caption = caption
        self.reposition()
        self.show()
        self.raise_()
        if not self.timer.isActive():
            self.timer.start()
        self.update()

    def poll(self):
        if self.request is None:
            return
        key, history, frame = self.request
        row = self.viewer.rows.get(key)
        current = self.viewer.alignment.reference or self.viewer.main.currentData()
        if not self.viewer.running or key != current or row is None or row.decoder is None:
            self.dismiss()
            return
        if self.reader is None:
            self.reader = PictureReader(thumbnail_width=360)
        self.reader.configure(2 * 1024 * 1024, 1, lead=0)
        result = self.reader.get(history, frame)
        if result is not None and self.image.cacheKey() != result[1].cacheKey():
            self.image = result[1]
            self.update()

    def dismiss(self):
        self.hide()
        self.timer.stop()
        self.request = None
        self.image = QImage()
        if self.reader is not None:
            self.reader.configure(2 * 1024 * 1024, 1, lead=0, active=False)

    def stop(self):
        self.dismiss()
        if self.reader is not None:
            self.reader.stop()
            self.reader = None

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.reposition()
        elif event.type() == QEvent.Hide:
            self.dismiss()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_preview(painter, self.rect(), self.image, self.caption)


def paint_preview(painter, bounds, image, caption, placeholder='正在读取预览…'):
    painter.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(bounds).adjusted(12, 8, -12, -12)
    painter.setPen(Qt.NoPen)
    for spread in range(8, 0, -1):
        painter.setBrush(QColor(0, 0, 0, 7))
        painter.drawRoundedRect(rect.adjusted(-spread, -spread + 3, spread, spread + 3), 10 + spread, 10 + spread)
    clip = QPainterPath()
    clip.addRoundedRect(rect, 9, 9)
    painter.setClipPath(clip)
    painter.fillRect(rect, QColor('#15181e'))
    picture = rect.adjusted(0, 0, 0, -28)
    if not image.isNull():
        size = image.size().scaled(picture.size().toSize(), Qt.KeepAspectRatio)
        destination = QRectF(0, 0, size.width(), size.height())
        destination.moveCenter(picture.center())
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(destination, image)
    else:
        painter.setPen(QColor('#c4c8d0'))
        painter.drawText(picture, Qt.AlignCenter, placeholder)
    painter.setPen(QColor('#f1f2f5'))
    painter.drawText(QRectF(rect.left(), rect.bottom() - 28, rect.width(), 28), Qt.AlignCenter, caption)
