"""PiP-style cached-avatar room cards with independent checkbox selection."""
import math
import weakref
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QGridLayout, QHBoxLayout, QSizePolicy, QWidget
from ddm import theme
from ddm.ui import Avatar, ElidedLabel, HintLabel


class RoomChoice(QAbstractButton):
    def __init__(self, tile, source, index, presentation, parent):
        super().__init__(parent)
        self.tile = weakref.ref(tile)
        self.setCheckable(True)
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        name = presentation.get('name') or source.uname or source.room_id
        self.setAccessibleName(str(name))
        self.setToolTip(f'{name} · {source.platform} · {source.room_id}')
        box = QHBoxLayout(self)
        box.setContentsMargins(10, 10, 22, 10)
        box.setSpacing(8)
        avatar = Avatar(str(name), index, size=36, parent=self)
        avatar.set_pixmap_image(presentation.get('pixmap'))
        avatar.setAttribute(Qt.WA_TransparentForMouseEvents)
        box.addWidget(avatar)
        label = ElidedLabel(str(name), self)
        label.setMinimumWidth(0)
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
        box.addWidget(label, 1)
        theme.signals().changed.connect(self.update)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.5)
        painter.setBrush(theme.qcolor(theme.CONTENT_HOVER if self.underMouse() else theme.CONTENT))
        painter.setPen(QPen(theme.qcolor(theme.ACCENT if self.isChecked() or self.hasFocus() else theme.BORDER), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        if self.isChecked():
            painter.setPen(QPen(theme.qcolor(theme.ACCENT), 2))
            x = self.width() - 17
            painter.drawLine(x - 4, 14, x - 1, 17)
            painter.drawLine(x - 1, 17, x + 5, 10)


class RoomGrid(QGridLayout):
    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        rows = max(1, math.ceil(self.count() / (2 if width >= 300 else 1)))
        return rows * 72 + (rows - 1) * self.spacing()

    def minimumHeightForWidth(self, width):
        return self.heightForWidth(width)


class SourcePicker(QWidget):
    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buttons = weakref.WeakKeyDictionary()
        self._current = None
        self._initialized = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.grid = RoomGrid(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.empty = HintLabel('请先播放直播间', self)
        self.grid.addWidget(self.empty)
        self.setAccessibleName('选择传译直播间，可多选')

    def refresh(self, sources, selected=None, presentation=None):
        for tile in list(self.buttons):
            if tile not in sources:
                self.buttons.pop(tile).deleteLater()
        for index, (tile, source) in enumerate(sources.items()):
            if tile not in self.buttons:
                details = presentation(tile) if callable(presentation) else {}
                button = RoomChoice(tile, source, index, details, self)
                button.toggled.connect(lambda checked, ref=weakref.ref(tile): self._toggled(ref, checked))
                self.buttons[tile] = button
        if selected in self.buttons:
            self.buttons[selected].setChecked(True)
            self._current = weakref.ref(selected)
        elif not self._initialized and self.buttons:
            self.buttons[next(iter(self.buttons))].setChecked(True)
        self._initialized = bool(self.buttons)
        self.empty.setVisible(not self.buttons)
        self._arrange()

    def _toggled(self, reference, checked):
        if checked:
            self._current = reference
        self.selectionChanged.emit()

    def selected_tiles(self):
        return [tile for tile, button in self.buttons.items() if button.isChecked()]

    def currentData(self):
        tile = self._current() if self._current else None
        if tile not in self.buttons or not self.buttons[tile].isChecked():
            tile = next(iter(self.selected_tiles()), None)
        return weakref.ref(tile) if tile is not None else None

    def count(self):
        return len(self.buttons)

    def sizeHint(self):
        return QSize(300, self.grid.heightForWidth(300))

    def minimumSizeHint(self):
        return QSize(0, 72)

    def resizeEvent(self, event):
        self._arrange()
        super().resizeEvent(event)

    def _arrange(self):
        while self.grid.count():
            self.grid.takeAt(0)
        columns = 2 if self.width() >= 300 and len(self.buttons) > 1 else 1
        if self.buttons:
            for index, button in enumerate(self.buttons.values()):
                self.grid.addWidget(button, index // columns, index % columns)
                button.show()
        else:
            self.grid.addWidget(self.empty, 0, 0)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if columns == 2 else 0)
        self.updateGeometry()
