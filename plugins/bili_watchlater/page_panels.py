"""Media-gallery surfaces; destination state stays separate from presentation."""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolButton, QWidget
from ddm import theme
from ddm.ui import ActionButton, HintLabel, SelectControl, continuous_path


class ThumbnailSurface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._browse_selected = False
        self._selection_mark = None
        theme.signals().changed.connect(self.update)

    def set_browse_selected(self, selected):
        self._browse_selected = bool(selected)
        self._sync_selection_marker()
        self.update()

    def set_placement_action(self, text, enabled):
        self.play_button.setText(text)
        self.play_button.setEnabled(enabled)

    def _sync_selection_marker(self):
        active = getattr(self, '_multi_active', False)
        if active and self._selection_mark is None:
            self._selection_mark = SelectionMark(self.cover)
            self._selection_mark.move(10, 10)
        if self._selection_mark is not None:
            self._selection_mark.selected = getattr(self, '_multi_selected', False)
            self._selection_mark.setVisible(active)
            self._selection_mark.raise_()
            self._selection_mark.update()

    def paintEvent(self, event):
        selected = self._browse_selected or getattr(self, '_multi_selected', False)
        if not (selected or self.hasFocus() or getattr(self, '_hover', False)):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if getattr(self, '_hover', False):
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(theme.CONTENT))
            painter.drawPath(continuous_path(QRectF(self.rect()).adjusted(0, 0, 0, -1), theme.RADIUS_LG))
        if selected or self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(theme.qcolor(theme.ACCENT), 1.5))
            rect = QRectF(self.cover.geometry()).adjusted(-2, -2, 2, 2)
            painter.drawPath(continuous_path(rect, theme.RADIUS_MD))


class SelectionMark(QWidget):
    """A child of the cover so the image cannot paint over the checkbox."""
    def __init__(self, parent):
        super().__init__(parent)
        self.selected = False
        self.setFixedSize(22, 22)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(theme.qcolor(theme.ACCENT if self.selected else '#99000000'))
        painter.setPen(QPen(theme.qcolor('#ffffff'), 1.5))
        painter.drawEllipse(QRectF(1, 1, 20, 20))
        if self.selected:
            painter.drawLine(QPointF(6, 11), QPointF(10, 15))
            painter.drawLine(QPointF(10, 15), QPointF(16, 8))


class PlacementButton(ActionButton):
    """Compact, always discoverable placement action, with host accent colors."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setProperty('customControlSurface', True)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        theme.signals().changed.connect(self.update)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 40, 30)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.4)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fill = theme.qcolor(theme.ACCENT)
        fill.setAlpha(48 if self.isDown() else 30 if self.underMouse() else 18)
        painter.setBrush(fill)
        painter.setPen(QPen(theme.qcolor(theme.ACCENT), 1) if self.hasFocus() else Qt.NoPen)
        painter.drawPath(continuous_path(rect, theme.RADIUS_MD))
        painter.setPen(QPen(theme.qcolor(theme.ACCENT), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.text().startswith('替换'):
            painter.drawLine(11, 12, 23, 12)
            painter.drawLine(23, 12, 20, 9)
            painter.drawLine(11, 18, 23, 18)
            painter.drawLine(11, 18, 14, 21)
        else:
            painter.drawLine(11, 15, 23, 15)
            painter.drawLine(17, 9, 17, 21)
        painter.drawText(QRectF(29, 0, self.width() - 36, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class GalleryToolButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setProperty('customControlSurface', True)
        theme.signals().changed.connect(self.update)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.4)
        painter.setBrush(theme.qcolor(theme.CONTENT_HOVER) if self.underMouse() or self.isDown() else Qt.NoBrush)
        painter.setPen(QPen(theme.qcolor(theme.ACCENT), 1) if self.hasFocus() else Qt.NoPen)
        painter.drawPath(continuous_path(QRectF(self.rect()).adjusted(1, 1, -1, -1), theme.RADIUS_MD))
        self.icon().paint(painter, 6, 6, 18, 18)


def two_line_title(text, font, width):
    """Wrap CJK and Latin text at the real label width, eliding only line two."""
    text = text.replace('\r', ' ').replace('\n', ' ')
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    first = layout.createLine()
    if not first.isValid():
        layout.endLayout()
        return ''
    first.setLineWidth(max(1, width))
    count = first.textLength()
    layout.endLayout()
    from PySide6.QtGui import QFontMetrics
    metrics = QFontMetrics(font)
    encoded = text.encode('utf-16-le')  # QTextLine offsets use UTF-16, including emoji pairs.
    first_text = encoded[:count * 2].decode('utf-16-le')
    remaining = encoded[count * 2:].decode('utf-16-le')
    return first_text.rstrip() + ('\n' + metrics.elidedText(remaining, Qt.ElideRight, max(1, width)) if remaining else '')


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
        row.setSpacing(10)
        row.addWidget(HintLabel('置入到', self))
        self.combo = TargetSelect(self)
        self.combo.setMinimumWidth(180)
        self.combo.setMaximumWidth(360)
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo.setAccessibleName('置入或替换的目标格')
        self.combo.opening.connect(self.refresh)
        self.combo.currentIndexChanged.connect(self.changed)
        row.addWidget(self.combo, 1)
        self.hint = HintLabel('先选择目标格', self)
        row.addWidget(self.hint)
        row.addStretch(1)
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
                          '已有内容，将替换' if self.combo.currentData(Qt.UserRole + 1) else '空视频格')
