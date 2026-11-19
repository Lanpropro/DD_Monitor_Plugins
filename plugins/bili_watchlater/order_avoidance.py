"""Temporary painted positions during dragging; the item model never moves here."""
from PySide6.QtCore import QObject, QRect, QVariantAnimation
from ddm import motion


class OrderAvoidance(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.rows = []
        self.preview = []
        self.source = None
        self.gap = 0
        self.positions = {}
        self.origins = {}
        self.targets = {}
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(motion.CONTROL_FEEDBACK_MS)
        self.animation.setEasingCurve(motion.THEME_WAVE_EASING)
        self.animation.setStartValue(0.)
        self.animation.setEndValue(1.)
        self.animation.valueChanged.connect(self.advance)

    @property
    def active(self):
        return self.source is not None

    def begin(self, source):
        self.clear()
        self.rows = [row for row in range(self.view.model().rowCount()) if not self.view.isRowHidden(row)]
        self.source = source
        self.gap = self.rows.index(source)
        self.preview = [None if row == source else row for row in self.rows]
        self.positions = {row:float(slot) for slot,row in enumerate(self.rows) if row != source}
        self.targets = dict(self.positions)

    def geometry(self):
        first = self.view.visualRect(self.view.model().index(self.rows[0],0))
        return first.top()+self.view.verticalScrollBar().value(), max(1,first.height())

    def rect_for_slot(self, slot):
        top, height = self.geometry()
        return QRect(0,round(top+slot*height-self.view.verticalScrollBar().value()),
                     self.view.viewport().width(),height)

    def rect_for_row(self, row):
        return self.rect_for_slot(self.positions[row])

    def hover(self, point):
        top, height = self.geometry()
        position = (point.y()+self.view.verticalScrollBar().value()-top)/height
        if position < 0:
            self.move_gap(0)
            return
        if position >= len(self.preview):
            self.move_gap(len(self.preview)-1)
            return
        slot = int(position)
        row = self.preview[slot]
        if row is not None:
            remaining = [value for value in self.rows if value != self.source]
            self.move_gap(remaining.index(row)+int(position-slot > .5))

    def move_gap(self, slot):
        if slot == self.gap:
            return
        self.animation.stop()
        self.origins = dict(self.positions)
        self.gap = slot
        self.preview = [row for row in self.rows if row != self.source]
        self.preview.insert(slot,None)
        self.targets = {row:float(i) for i,row in enumerate(self.preview) if row is not None}
        if motion.enabled():
            self.animation.start()
        else:
            self.positions = dict(self.targets)
            self.view.viewport().update()

    def advance(self, progress):
        self.positions = {row:self.origins[row]+(target-self.origins[row])*progress
                          for row,target in self.targets.items()}
        self.view.viewport().update()

    def before_row(self):
        if self.gap == self.rows.index(self.source):
            return self.source  # Returning to the original gap must preserve hidden items too.
        return self.preview[self.gap+1] if self.gap+1 < len(self.preview) else self.view.model().rowCount()

    def reset_target(self):
        if self.active:
            self.move_gap(self.rows.index(self.source))

    def clear(self):
        self.animation.stop()
        self.rows = self.preview = []
        self.source = None
        self.positions = self.origins = self.targets = {}
