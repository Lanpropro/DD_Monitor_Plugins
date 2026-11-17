"""One interruptible opacity transition for the visible queue, never per-card timers."""
from PySide6.QtCore import QEvent, QObject, Qt, QVariantAnimation
from PySide6.QtGui import QCursor
from ddm import motion


class OrderFeedback(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.hovered = -1
        self.pressed = -1
        self.dragging = False
        self.weights = {}
        self.starts = {}
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(motion.CONTROL_FEEDBACK_MS)
        self.animation.setEasingCurve(motion.THEME_WAVE_EASING)
        self.animation.setStartValue(0.)
        self.animation.setEndValue(1.)
        self.animation.valueChanged.connect(self.advance)
        view.setMouseTracking(True)
        view.viewport().setMouseTracking(True)
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self.sync_pointer)

    def update_rows(self, rows):
        for row in rows:
            if 0 <= row < self.view.count():
                self.view.viewport().update(self.view.visualItemRect(self.view.item(row)))

    def advance(self, progress):
        rows = set(self.weights) | set(self.starts) | {self.hovered}
        self.weights = {row: start + ((1. if row == self.hovered else 0.)-start)*progress
                        for row, start in self.starts.items()}
        self.weights = {row: value for row, value in self.weights.items() if value > .001}
        self.update_rows(rows)

    def hover(self, row, *, immediate=False):
        if row == self.hovered and not immediate:
            return
        self.view.viewport().setCursor(Qt.PointingHandCursor if row >= 0 else Qt.ArrowCursor)
        self.animation.stop()
        rows = set(self.weights) | {row}
        self.hovered = row
        if immediate or not motion.enabled():
            self.weights = {row: 1.} if row >= 0 else {}
            self.starts = {}
            self.update_rows(rows)
            return
        self.starts = {key: self.weights.get(key, 0.) for key in rows if key >= 0}
        self.animation.start()

    def set_pressed(self, row):
        previous, self.pressed = self.pressed, row
        self.update_rows((previous, row))

    def sync_pointer(self, *_):
        point = self.view.viewport().mapFromGlobal(QCursor.pos())
        index = self.view.indexAt(point)
        row = index.row() if self.view.isVisible() and self.view.viewport().rect().contains(point) and not self.dragging else -1
        self.hover(row, immediate=True)

    def clear(self):
        self.animation.stop()
        rows = set(self.weights) | {self.pressed}
        self.hovered = self.pressed = -1
        self.weights = self.starts = {}
        self.view.viewport().setCursor(Qt.ArrowCursor)
        self.update_rows(rows)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.MouseMove and not self.dragging:
            if event.source() == Qt.MouseEventNotSynthesized:
                self.hover(self.view.indexAt(event.position().toPoint()).row())
        elif kind == QEvent.Enter and not self.dragging:
            self.sync_pointer()
        elif kind == QEvent.Leave:
            self.hover(-1)
            self.set_pressed(-1)
        elif kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.set_pressed(self.view.indexAt(event.position().toPoint()).row())
        elif kind == QEvent.MouseButtonRelease:
            self.set_pressed(-1)
        elif kind in (QEvent.Hide, QEvent.WindowDeactivate, QEvent.FocusOut):
            self.clear()
        return False
