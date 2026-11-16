"""Settings-style browsing surfaces and an explicit destination selector."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget
from ddm import theme
from ddm.ui import HintLabel, SelectControl, SettingsSurface, continuous_path


class ThumbnailSurface(SettingsSurface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._browse_selected = False

    def set_browse_selected(self, selected):
        self._browse_selected = bool(selected)
        self.update()

    def set_placement_action(self, text, enabled):
        self.play_button.setText(text)
        self.play_button.setEnabled(enabled)

    def paintEvent(self, event):
        super().paintEvent(event)
        selected = self._browse_selected or getattr(self, '_multi_selected', False)
        if not (selected or self.hasFocus() or getattr(self, '_hover', False)):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(theme.qcolor(theme.ACCENT if selected or self.hasFocus() else theme.BORDER_STRONG), 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(continuous_path(QRectF(self.rect()).adjusted(1, 1, -1, -2), theme.RADIUS_LG))
        if getattr(self, '_multi_selected', False):
            mark = QRectF(self.width() - 32, 14, 20, 20)
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(theme.ACCENT))
            painter.drawEllipse(mark)
            painter.setPen(QPen(theme.qcolor(theme.TEXT1), 2))
            painter.drawLine(QPointF(mark.left() + 5, mark.top() + 10), QPointF(mark.left() + 9, mark.top() + 14))
            painter.drawLine(QPointF(mark.left() + 9, mark.top() + 14), QPointF(mark.left() + 15, mark.top() + 6))


class TargetSelect(SelectControl):
    opening = Signal()

    def showPopup(self):
        self.opening.emit()
        super().showPopup()


class PlacementSelector(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.provider = None
        self._signature = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(HintLabel('目标格', self))
        self.combo = TargetSelect(self)
        self.combo.setMinimumWidth(180)
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo.setAccessibleName('置入或替换的目标格')
        self.combo.opening.connect(self.refresh)
        self.combo.currentIndexChanged.connect(self.changed)
        row.addWidget(self.combo, 1)
        self.hint = HintLabel('先选择目标格', self)
        row.addWidget(self.hint)
        self.changed.connect(self._sync_hint)

    def refresh(self, preferred=None):
        options = self.provider() if callable(self.provider) else []
        selected = preferred if preferred is not None else self.target()
        signature = tuple((id(tile), label, occupied) for tile, label, occupied in options)
        if signature == self._signature and preferred is None:
            return
        self._signature = signature
        self.combo.blockSignals(True)
        self.combo.clear()
        choice = -1
        for index, (tile, label, occupied) in enumerate(options):
            self.combo.addItem(label, tile)
            self.combo.setItemData(index, occupied, Qt.UserRole + 1)
            if tile is selected:
                choice = index
        if choice < 0 and options and selected is None:
            choice = next((i for i, option in enumerate(options) if not option[2]), 0)
        self.combo.setCurrentIndex(choice)
        self.combo.setEnabled(bool(options))
        self.combo.blockSignals(False)
        self.changed.emit()

    def target(self):
        return self.combo.currentData()

    def action_text(self):
        return '替换视频' if self.combo.currentData(Qt.UserRole + 1) else '置入视频'

    def _sync_hint(self):
        self.hint.setText('目标格不可用' if self.target() is None else
                          '已有内容 · 点击替换' if self.combo.currentData(Qt.UserRole + 1) else '空格 · 点击置入')
