"""Watch-later glyph and pointer feedback, painted at the requested resolution."""
import math
from PySide6.QtCore import QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QToolButton

from ddm import motion, theme


class WatchLaterIcon(QIconEngine):
    def __init__(self, scale=1.0):
        super().__init__()
        self.scale = scale

    def clone(self):
        return WatchLaterIcon(self.scale)

    def pixmap(self, size, mode, state):
        result = QPixmap(size)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return result

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(rect.width(), rect.height())
        painter.translate(QRectF(rect).center())
        painter.scale(side / 24, side / 24)
        pen = QPen(theme.qcolor(theme.TEXT2), 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(QRectF(-9, -9, 18, 18), 42 * 16, 276 * 16)
        painter.scale(self.scale, self.scale)
        triangle = QPainterPath()
        triangle.moveTo(-2.5, -4)
        triangle.lineTo(4, 0)
        triangle.lineTo(-2.5, 4)
        triangle.closeSubpath()
        painter.drawPath(triangle)
        painter.restore()


class BrowserIcon(WatchLaterIcon):
    def clone(self):
        return BrowserIcon()

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(rect.topLeft())
        painter.scale(rect.width() / 24, rect.height() / 24)
        painter.setPen(QPen(theme.qcolor(theme.TEXT2), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(10, 5)
        path.lineTo(5, 5)
        path.lineTo(5, 19)
        path.lineTo(19, 19)
        path.lineTo(19, 14)
        path.moveTo(13, 4)
        path.lineTo(20, 4)
        path.lineTo(20, 11)
        path.moveTo(20, 4)
        path.lineTo(11, 13)
        painter.drawPath(path)
        painter.restore()


class GalleryIcon(WatchLaterIcon):
    """Monochrome gallery actions, painted at the actual icon resolution."""
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def clone(self):
        return GalleryIcon(self.kind)

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(rect.topLeft())
        painter.scale(rect.width() / 24, rect.height() / 24)
        painter.setPen(QPen(theme.qcolor(theme.TEXT2), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        if self.kind == 'search':
            painter.drawEllipse(QRectF(4, 4, 12, 12))
            painter.drawLine(15, 15, 20, 20)
        elif self.kind == 'refresh':
            painter.drawArc(QRectF(4, 4, 16, 16), 40 * 16, 285 * 16)
            path.moveTo(20, 4)
            path.lineTo(20, 10)
            path.lineTo(14, 10)
        elif self.kind == 'top':
            path.moveTo(5, 5)
            path.lineTo(19, 5)
            path.moveTo(12, 20)
            path.lineTo(12, 10)
            path.moveTo(7, 15)
            path.lineTo(12, 10)
            path.lineTo(17, 15)
        elif self.kind == 'back':
            path.moveTo(14, 5)
            path.lineTo(7, 12)
            path.lineTo(14, 19)
        elif self.kind == 'close':
            path.moveTo(6, 6)
            path.lineTo(18, 18)
            path.moveTo(18, 6)
            path.lineTo(6, 18)
        elif self.kind == 'maximize':
            path.addRoundedRect(QRectF(5, 5, 14, 14), 2, 2)
        elif self.kind == 'settings':
            for index in range(32):
                angle = index * math.pi / 16
                radius = 9 if index % 4 in (1, 2) else 7
                point = (12 + math.cos(angle) * radius, 12 + math.sin(angle) * radius)
                if index == 0:
                    path.moveTo(*point)
                else:
                    path.lineTo(*point)
            path.closeSubpath()
            painter.drawEllipse(QRectF(9, 9, 6, 6))
        painter.drawPath(path)
        painter.restore()


class WatchLaterButton(QToolButton):
    """Keep the outer ring still; rebound only the play triangle on hover."""
    def __init__(self):
        super().__init__()
        self._play_scale = 1.0
        self._poses = [(0, 1.0), (1, 1.0)]
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setStartValue(0.0)
        self._hover_animation.setEndValue(1.0)
        self._hover_animation.valueChanged.connect(self._frame)
        self.setIcon(QIcon(WatchLaterIcon()))

    def _frame(self, progress):
        for (start, first), (end, last) in zip(self._poses, self._poses[1:]):
            if progress <= end:
                amount = motion.SWITCH_TRANSITION_EASING.valueForProgress((progress - start) / (end - start))
                self._play_scale = first + (last - first) * amount
                icon = QIcon(WatchLaterIcon(self._play_scale))
                action = self.defaultAction()
                if action is None:
                    self.setIcon(icon)
                else:
                    action.setIcon(icon)
                break

    def _feedback(self, entering=False, animate=True):
        # The rail still owns press/hover scale; replace its generic hop pose.
        feedback = getattr(self, '_dock_feedback', None)
        if feedback is not None:
            feedback.hover(animate=False)
        self._hover_animation.stop()
        if not animate or not motion.enabled():
            self._poses = [(0, 1.0), (1, 1.0)]
            self._frame(1.0)
            return
        self._poses = ([(0, self._play_scale), (.4, 1.22), (.7, .96), (1, 1.0)]
                       if entering else [(0, self._play_scale), (1, 1.0)])
        self._hover_animation.setDuration(280 if entering else motion.CONTROL_FEEDBACK_MS)
        self._hover_animation.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._feedback(entering=True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._feedback()

    def hideEvent(self, event):
        self._feedback(animate=False)
        super().hideEvent(event)

    def keyPressEvent(self, event):
        self._feedback(animate=False)
        super().keyPressEvent(event)
