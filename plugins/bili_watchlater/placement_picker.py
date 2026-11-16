"""Keyboard-accessible destination map, using a read-only wall snapshot."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QDialog, QHBoxLayout, QVBoxLayout, QWidget
from ddm import theme
from ddm.ui import ActionButton, HintLabel, TitleLabel, continuous_path


class CellChoice(QAbstractButton):
    def __init__(self, cell, parent):
        super().__init__(parent)
        self.cell = cell
        self.setEnabled(cell.target is not None)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor)
        action = '置入视频' if cell.kind == 'empty' else '替换视频' if self.isEnabled() else '不可置入'
        self.setAccessibleName(f'第 {cell.index + 1} 格 · {cell.title} · {action}')
        self.setToolTip(self.accessibleName())
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
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = continuous_path(rect, theme.RADIUS_MD)
        painter.setClipPath(path)
        painter.fillPath(path, theme.qcolor(theme.CONTENT_HOVER if self.underMouse() and self.isEnabled() else theme.CONTENT))
        image = self.cell.image
        if image is not None and not image.isNull():
            source = QRectF(image.rect())
            ratio = rect.width() / max(1, rect.height())
            if source.width() / max(1, source.height()) > ratio:
                width = source.height() * ratio
                source.adjust((source.width() - width) / 2, 0, -(source.width() - width) / 2, 0)
            else:
                height = source.width() / ratio
                source.adjust(0, (source.height() - height) / 2, 0, -(source.height() - height) / 2)
            painter.drawPixmap(rect, image, source)
            painter.fillRect(rect, QColor(0, 0, 0, 75))
        painter.setClipping(False)
        painter.setPen(QPen(theme.qcolor(theme.ACCENT if self.hasFocus() or self.isDown() else theme.BORDER_STRONG),
                            1.8 if self.hasFocus() else 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(theme.qcolor('#ffffff' if image is not None and not image.isNull() else theme.TEXT2))
        painter.drawText(rect.adjusted(9, 4, -9, -4), Qt.AlignTop | Qt.AlignLeft, str(self.cell.index + 1))
        if self.cell.kind == 'empty' and rect.height() > 55:
            center = rect.center()
            painter.drawLine(int(center.x() - 8), int(center.y()), int(center.x() + 8), int(center.y()))
            painter.drawLine(int(center.x()), int(center.y() - 8), int(center.x()), int(center.y() + 8))
        label = self.fontMetrics().elidedText(self.cell.title, Qt.ElideRight, max(1, int(rect.width()) - 18))
        painter.drawText(rect.adjusted(9, 4, -9, -6), Qt.AlignBottom | Qt.AlignHCenter, label)


class LayoutMap(QWidget):
    chosen = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setMinimumSize(320, 230)
        self.buttons = []
        self.snapshot = None

    def set_snapshot(self, snapshot, preferred=None):
        for button in self.buttons:
            button.hide()
            button.deleteLater()
        self.snapshot = snapshot
        self.buttons = []
        for cell in snapshot.cells:
            button = CellChoice(cell, self)
            button.clicked.connect(lambda checked=False, index=cell.index: self.chosen.emit(index))
            self.buttons.append(button)
            button.show()
        self._arrange()
        focus = next((button for button in self.buttons if button.cell.target is preferred and button.isEnabled()), None)
        if focus is None:
            focus = next((button for button in self.buttons if button.cell.kind == 'empty'), None)
        if focus is not None:
            focus.setFocus()

    def _arrange(self):
        if self.snapshot is None:
            return
        aspect = self.snapshot.aspect
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        width = min(area.width(), area.height() * aspect)
        height = width / aspect
        canvas = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
        for button in self.buttons:
            rect = button.cell.rect
            button.setGeometry(round(canvas.x() + rect.x() * width), round(canvas.y() + rect.y() * height),
                               round(rect.width() * width), round(rect.height() * height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()


class PlacementPicker(QDialog):
    cell_chosen = Signal(int)

    def __init__(self, item, snapshot, preferred=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择置入位置')
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.WindowModal)
        self.resize(540, 410)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        outer.addWidget(TitleLabel('选择置入位置', self))
        title = HintLabel(item.title, self)
        title.setTextFormat(Qt.PlainText)
        outer.addWidget(title)
        self.map = LayoutMap(self)
        self.map.set_snapshot(snapshot, preferred)
        self.map.chosen.connect(self.cell_chosen)
        outer.addWidget(self.map, 1)
        self.hint = HintLabel(f'{snapshot.title} · 空格置入，已有内容替换；弹幕格不可选', self)
        outer.addWidget(self.hint)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = ActionButton('取消', self)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        outer.addLayout(footer)
