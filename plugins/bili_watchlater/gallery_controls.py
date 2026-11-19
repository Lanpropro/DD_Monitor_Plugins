"""Gallery controls; behavior moved intact from widgets."""
from __future__ import annotations
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget
from .page_panels import two_line_title
from .button_feedback import StableActionButton as ActionButton

def relative_font(base: QFont, factor: float, *, bold: bool = False) -> QFont:
    font = QFont(base)
    if base.pointSizeF() > 0:
        font.setPointSizeF(max(7.0, base.pointSizeF() * factor))
    elif base.pixelSize() > 0:
        font.setPixelSize(max(9, round(base.pixelSize() * factor)))
    font.setBold(bold)
    return font



class ElidedLabel(QLabel):
    """A single-line plain-text label that elides instead of growing wide."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(Qt.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setAccessibleName(text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = max(0, self.contentsRect().width())
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, width
        )
        if self.text() != elided:
            super().setText(elided)



class ClickableLabel(ElidedLabel):
    """Plain title selection with optional two-line wrapping."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None, *, lines=1) -> None:
        self._lines = lines
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._double_release = False

    def _apply_elide(self):
        if self._lines == 1:
            return super()._apply_elide()
        QLabel.setText(self, two_line_title(self._full_text, self.font(), self.contentsRect().width()))

    def sizeHint(self):
        size = super().sizeHint()
        if self._lines == 2:
            size.setHeight(self.fontMetrics().lineSpacing() * 2 + 4)
        return size

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._double_release = True
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self._double_release:
            self._double_release = False
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)



def _icon_color(palette: QPalette | None = None) -> QColor:
    pal = palette if palette is not None else QGuiApplication.palette()
    color = pal.color(QPalette.WindowText)
    if not color.isValid():
        color = QColor("#c8cdd4")
    return color



def _icon_pixmap(size: int, painter_fn) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter_fn(painter, size)
    finally:
        painter.end()
    return pixmap



def list_view_icon(size: int = 16, color: QColor | None = None) -> QIcon:
    """列表图标：三条横线（纯 QPainter 绘制，不引入图片资源）。"""
    tint = color if isinstance(color, QColor) else _icon_color()

    def draw(painter: QPainter, side: int) -> None:
        pen = painter.pen()
        pen.setColor(tint)
        pen.setWidthF(max(1.4, side / 10.0))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        left = side * 0.18
        right = side * 0.82
        for index in range(3):
            y = side * (0.28 + 0.22 * index)
            painter.drawLine(QPointF(left, y), QPointF(right, y))

    pixmap = _icon_pixmap(size, draw)
    return QIcon(pixmap)



def grid_view_icon(size: int = 16, color: QColor | None = None) -> QIcon:
    """网格图标：2x2 方块。"""
    tint = color if isinstance(color, QColor) else _icon_color()

    def draw(painter: QPainter, side: int) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(tint)
        margin = side * 0.16
        gap = side * 0.12
        cell = (side - 2 * margin - gap) / 2.0
        for row in range(2):
            for col in range(2):
                x = margin + col * (cell + gap)
                y = margin + row * (cell + gap)
                painter.drawRoundedRect(
                    QRectF(x, y, cell, cell), max(1.0, side / 12.0),
                    max(1.0, side / 12.0),
                )

    pixmap = _icon_pixmap(size, draw)
    return QIcon(pixmap)



class ViewModeSwitch(QWidget):
    """v1.0 需求：：header 右侧的「列表 / 网格」图标切换。

    两个 checkable 图标按钮互斥（QButtonGroup），高度固定 26px 与标题行
    垂直居中；accessibleName / tooltip 与旧文字按钮一致。
    """

    mode_changed = Signal(str)

    HEIGHT = 26
    ICON_SIZE = 16

    def __init__(self, parent: QWidget | None = None,
                 mode: str = "grid") -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.list_button = self._make_button("列表", "切换列表视图")
        self.grid_button = self._make_button("网格", "切换 4×4 网格视图")
        self.list_button.setIcon(list_view_icon(self.ICON_SIZE))
        self.grid_button.setIcon(grid_view_icon(self.ICON_SIZE))
        layout.addWidget(self.list_button)
        layout.addWidget(self.grid_button)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self.list_button)
        self._group.addButton(self.grid_button)
        self._current = "grid"
        self.list_button.clicked.connect(lambda: self._emit("list"))
        self.grid_button.clicked.connect(lambda: self._emit("grid"))
        self.set_mode(mode)

    def _make_button(self, name: str, tip: str) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        button.setIconSize(QSize(self.ICON_SIZE, self.ICON_SIZE))
        button.setFixedHeight(self.HEIGHT)
        button.setCursor(Qt.PointingHandCursor)
        button.setAccessibleName(name)
        button.setToolTip(tip)
        button.setStyleSheet(
            "QToolButton { border: none; border-radius: 6px; padding: 4px 6px; }"
            "QToolButton:hover { background: rgba(127, 127, 127, 46); }"
            "QToolButton:checked { background: rgba(0, 161, 214, 90); }"
        )
        return button

    def mode(self) -> str:
        return self._current

    def set_mode(self, mode: str) -> None:
        """同步选中态，不发 mode_changed（供页面在代码里设定当前视图）。"""
        grid = mode == "grid"
        self._current = "grid" if grid else "list"
        for button, checked in (
            (self.list_button, not grid),
            (self.grid_button, grid),
        ):
            blocked = button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(blocked)

    def _emit(self, mode: str) -> None:
        if mode == self._current:
            return
        self._current = mode
        self.mode_changed.emit(mode)



class ErrorBar(QFrame):
    retry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(8)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setForegroundRole(QPalette.PlaceholderText)
        layout.addWidget(self.label, 1)
        self.retry_button = QPushButton("重试", self)
        self.retry_button.setAutoDefault(False)
        self.retry_button.setAccessibleName("重试加载稍后再看")
        self.retry_button.clicked.connect(self.retry_requested)
        layout.addWidget(self.retry_button, 0)
        self.hide()

    def show_error(self, text: str) -> None:
        self.label.setText(text)
        self.show()

    def clear_error(self) -> None:
        self.label.clear()
        self.hide()



class StatusView(QWidget):
    primary_requested = Signal()
    secondary_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 40, 24, 40)
        outer.addStretch(1)
        self.content = QWidget(self)
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.title = QLabel(self)
        self.title.setTextFormat(Qt.PlainText)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        self.title.setFont(relative_font(self.font(), 1.3, bold=True))
        layout.addWidget(self.title)

        self.message = QLabel(self)
        self.message.setTextFormat(Qt.PlainText)
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setForegroundRole(QPalette.PlaceholderText)
        self.message.setMaximumWidth(560)
        layout.addWidget(self.message, 0, Qt.AlignHCenter)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(220)
        layout.addWidget(self.progress, 0, Qt.AlignHCenter)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.primary_button = ActionButton("", self)
        self.primary_button.setAutoDefault(False)
        self.primary_button.setMinimumWidth(104)
        self.primary_button.clicked.connect(self.primary_requested)
        buttons.addWidget(self.primary_button)
        self.secondary_button = ActionButton("", self)
        self.secondary_button.setAutoDefault(False)
        self.secondary_button.clicked.connect(self.secondary_requested)
        buttons.addWidget(self.secondary_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        outer.addWidget(self.content, 0, Qt.AlignHCenter)
        outer.addStretch(1)

    def set_state(
        self,
        title: str,
        message: str = "",
        *,
        primary: str = "",
        secondary: str = "",
        loading: bool = False,
    ) -> None:
        self.title.setText(title)
        self.message.setText(message)
        self.message.setVisible(bool(message))
        self.progress.setVisible(loading)
        self.primary_button.setText(primary)
        self.primary_button.setVisible(bool(primary))
        self.primary_button.setAccessibleName(primary or title)
        self.secondary_button.setText(secondary)
        self.secondary_button.setVisible(bool(secondary))
        self.secondary_button.setAccessibleName(secondary or title)
        self._fit_text()

    def _fit_text(self):
        width = max(1, min(560, self.width() - 48))
        self.message.setFixedWidth(width)
        for label in (self.message, self.title):
            # heightForWidth includes an existing minimum; release the previous
            # fixed height before measuring or each render would grow the group.
            label.setMinimumHeight(0)
            label.setMaximumHeight(16777215)
            label.setFixedHeight(max(label.fontMetrics().lineSpacing(), label.heightForWidth(width)) + 4)
        self.content.layout().invalidate()
        self.content.setFixedHeight(self.content.layout().sizeHint().height())
        self.content.layout().activate()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_text()



class CenteredColumn(QWidget):
    """Caps the embedded page at roughly 1560 logical pixels."""

    # v1.0 需求 4：网格要能出 5/6 列，内容列也被放大到 1560 —— 否则
    # 1100px 上限下「窗口越大列越多」永远停在 4 列。
    MAX_WIDTH = 1560

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        width = min(self.MAX_WIDTH, max(0, self.width()))
        left = max(0, (self.width() - width) // 2)
        self.body.setGeometry(left, 0, width, self.height())

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(self.MAX_WIDTH, 400)
