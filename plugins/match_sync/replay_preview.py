"""Small, asynchronous cached-frame preview for timeline gestures."""
from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QHBoxLayout, QWidget

from ddm import theme
from .video import PictureReader


PREVIEW_SIZES = (('小', 200), ('中', 280), ('大', 360))


class PreviewSizeButton(QAbstractButton):
    def __init__(self, label, width, parent):
        super().__init__(parent)
        self.label, self.preview_width = label, width
        self.setCheckable(True)
        self.setFixedSize(92, 76)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f'回看预览尺寸：{label}')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self.width() / 92, self.height() / 76)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(theme.ACCENT_SOFT if self.isChecked() else (theme.CONTENT_HOVER if self.underMouse() else theme.CONTENT)))
        painter.drawRoundedRect(QRectF(1, 1, 90, 74), 6, 6)
        screen = QRectF(10, 9, 72, 40)
        painter.setBrush(QColor('#101216'))
        painter.drawRoundedRect(screen, 4, 4)
        width = self.preview_width / 8
        painter.setBrush(theme.qcolor(theme.ACCENT))
        painter.drawRoundedRect(QRectF(14, 45 - width * 9 / 16, width, width * 9 / 16), 3, 3)
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(theme.qcolor(theme.TEXT1))
        painter.drawText(QRectF(0, 53, 92, 20), Qt.AlignCenter, self.label)


class PreviewSizePicker(QWidget):
    changed = Signal(int)

    def __init__(self, width, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.group = QButtonGroup(self)
        for label, value in PREVIEW_SIZES:
            button = PreviewSizeButton(label, value, self)
            self.group.addButton(button, value)
            button.setChecked(value == width)
            layout.addWidget(button)
        layout.addStretch(1)
        self.setMinimumSize(layout.minimumSize())
        self.group.idClicked.connect(self.changed)


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
        width = max(1, min(self.viewer.replay_preview_width, parent.width() - 48,
                           round(max(1, parent.height() - 72) * 16 / 9)))
        self.setGeometry(8, max(0, parent.height() - round(width * 9 / 16) - 56),
                         width + 24, round(width * 9 / 16) + 48)

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
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(12, 8, -12, -12)
        painter.setPen(Qt.NoPen)
        for spread in range(8, 0, -1):
            painter.setBrush(QColor(0, 0, 0, 7))
            painter.drawRoundedRect(rect.adjusted(-spread, -spread + 3, spread, spread + 3), 10 + spread, 10 + spread)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 9, 9)
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor('#15181e'))
        picture = rect.adjusted(0, 0, 0, -28)
        if not self.image.isNull():
            size = self.image.size().scaled(picture.size().toSize(), Qt.KeepAspectRatio)
            destination = QRectF(0, 0, size.width(), size.height())
            destination.moveCenter(picture.center())
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(destination, self.image)
        else:
            painter.setPen(QColor('#c4c8d0'))
            painter.drawText(picture, Qt.AlignCenter, '正在读取预览…')
        painter.setPen(QColor('#f1f2f5'))
        painter.drawText(QRectF(rect.left(), rect.bottom() - 28, rect.width(), 28), Qt.AlignCenter, self.caption)
