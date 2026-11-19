"""Video error/ended status and subtitle button presentation."""
from __future__ import annotations
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget
from .vod_surface import video_geometry

_ACCENT = "#00a1d6"  # B 站蓝，与宿主悬停强调色一致


_PILL_BG = "rgba(12, 13, 16, 200)"


_PILL_TEXT = "rgba(214, 218, 224, 235)"


_PILL_QSS = """
QPushButton {
    background: %s;
    color: %s;
    border: none;
    border-radius: 12px;
    padding: 4px 14px;
    font-weight: 600;
}
QPushButton:hover { background: %s; color: #04161f; }
QPushButton:pressed { background: %s; color: #04161f; }
QPushButton:focus { outline: none; }
""" % (_PILL_BG, _PILL_TEXT, _ACCENT, _ACCENT)


def _status_action_icon(kind: str):
    """v1.0 需求：：结束卡按钮的 16×16 线条图标（不引图片资源）。"""
    from PySide6.QtGui import QIcon, QPainterPath, QPen, QPixmap

    def draw(painter: QPainter) -> None:
        pen = QPen(QColor(232, 236, 242, 235))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if kind == "replay":
            rect = QRectF(2.6, 2.6, 10.8, 10.8)
            painter.drawArc(rect, 40 * 16, 300 * 16)
            painter.setBrush(QColor(232, 236, 242, 235))
            painter.setPen(Qt.NoPen)
            path = QPainterPath()
            path.moveTo(11.6, 1.6)
            path.lineTo(13.9, 5.0)
            path.lineTo(9.9, 5.3)
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "next":
            painter.setBrush(QColor(232, 236, 242, 235))
            painter.setPen(Qt.NoPen)
            path = QPainterPath()
            path.moveTo(3.0, 2.6)
            path.lineTo(9.8, 8.0)
            path.lineTo(3.0, 13.4)
            path.closeSubpath()
            painter.drawPath(path)
            painter.setPen(pen)
            painter.drawLine(QPointF(12.4, 2.8), QPointF(12.4, 13.2))
        else:  # browser
            painter.drawRect(QRectF(2.6, 6.4, 7.4, 7.0))
            painter.drawLine(QPointF(7.4, 8.6), QPointF(13.2, 2.8))
            painter.drawLine(QPointF(9.6, 2.8), QPointF(13.2, 2.8))
            painter.drawLine(QPointF(13.2, 2.8), QPointF(13.2, 6.4))

    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    try:
        draw(painter)
    finally:
        painter.end()
    if pixmap.isNull():
        return None
    return QIcon(pixmap)


class VodStatusCard(QWidget):
    """错误 / 结束状态卡：播放与暂停期间隐藏，画面完全交还宿主。

    不抢焦点（卡片与按钮都 NoFocus），宿主全局快捷键不受影响。
    """

    retry_requested = Signal()
    replay_requested = Signal()
    browser_requested = Signal()
    restore_requested = Signal()
    #: v1.0 任务 7：播放结束后「下一个视频」。
    next_requested = Signal()

    def __init__(self, tile: QWidget) -> None:
        super().__init__(tile)
        self.setObjectName("watchlaterStatus")
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("""
            QWidget#watchlaterStatus { background: rgba(8, 9, 11, 215); }
            QLabel { color: rgba(230, 234, 240, 245); background: transparent; }
            QLabel#watchlaterStatusTitle { font-size: 15px; font-weight: 700; }
            QLabel#watchlaterStatusMessage { color: rgba(180, 186, 196, 220); }
        """)
        self.setStyleSheet(self.styleSheet() + _PILL_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        self._outer = outer
        outer.addStretch(1)

        self.title_label = QLabel("", self)
        self.title_label.setObjectName("watchlaterStatusTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        outer.addWidget(self.title_label)

        self.message_label = QLabel("", self)
        self.message_label.setObjectName("watchlaterStatusMessage")
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)
        outer.addWidget(self.message_label)

        outer.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._row = row
        self._compact = False
        self._labels = {}
        row.addStretch(1)
        self.primary_button = self._pill("", row)
        # v1.0 任务 7：结束态才有「下一个视频」，排在「重播」右边。
        self.next_button = self._pill("下一个视频", row)
        self.next_button.setToolTip("播放稍后再看列表里的下一个视频")
        self.next_button.setVisible(False)
        self.browser_button = self._pill("用浏览器打开", row)
        self._set_button_text(self.browser_button, "用浏览器打开")
        self._set_button_text(self.next_button, "下一个视频")
        # v1.0 需求：：结束态只留「重播 / 下一个视频 / 用浏览器打开」三个按钮。
        # 「还原为直播态」不再出现在卡片上（仍可从格子右键菜单还原），但保留
        # 控件对象与信号，避免外部接线报 AttributeError。
        self.restore_button = self._pill("还原为直播态", row)
        self.restore_button.hide()
        row.removeWidget(self.restore_button)
        self.restore_button.setParent(self)
        for button, kind in ((self.primary_button, "replay"),
                             (self.next_button, "next"),
                             (self.browser_button, "browser")):
            icon = _status_action_icon(kind)
            if icon is not None:
                button.setIcon(icon)
                button.setIconSize(QSize(16, 16))
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self.primary_button.clicked.connect(self._primary)
        self.next_button.clicked.connect(self.next_requested)
        self.browser_button.clicked.connect(self.browser_requested)
        self.restore_button.clicked.connect(self.restore_requested)
        self._mode = ""
        self.hide()

    # ------------------------------------------------ narrow / portrait PiP
    def set_compact(self, compact: bool) -> None:
        """v1.0 需求：：竖屏/窄画中画里三个按钮改为等宽图标，不再挤成一堆。"""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._outer.setContentsMargins(8, 8, 8, 8) if compact else \
            self._outer.setContentsMargins(16, 12, 16, 12)
        self._row.setSpacing(5 if compact else 8)
        for button in (self.primary_button, self.next_button,
                       self.browser_button):
            label = self._labels.get(button, button.text())
            if compact:
                button.setText("")
                button.setToolTip(label)
                button.setFixedWidth(34)
                button.setIconSize(QSize(16, 16))
            else:
                button.setText(label)
                button.setMinimumWidth(0)
                button.setMaximumWidth(16777215)

    def _set_button_text(self, button, text: str) -> None:
        """状态切换时设置文案；compact 态只记在 tooltip 里。"""
        text = str(text or "")
        self._labels[button] = text
        button.setToolTip(text)
        button.setText("" if self._compact else text)

    def _pill(self, text: str, row: QHBoxLayout) -> QPushButton:
        button = QPushButton(text, self)
        button.setAutoDefault(False)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        row.addWidget(button, 0)
        return button

    def _primary(self) -> None:
        if self._mode == "error":
            self.retry_requested.emit()
        elif self._mode == "ended":
            self.replay_requested.emit()

    # ------------------------------------------------------------------ modes
    def show_error(self, message: str) -> None:
        self._mode = "error"
        self.title_label.setText("播放失败")
        self.message_label.setText(str(message or "取流失败，请用浏览器打开"))
        self._set_button_text(self.primary_button, "重试")
        self.next_button.setVisible(False)
        self._show()

    def show_ended(self, has_next: bool = True) -> None:
        """结束态：只有这里会露出「下一个视频」。

        ``has_next`` 为 False（已是列表最后一条 / 列表里找不到它）时按钮
        置灰并给出说明，避免点了没反应。
        """
        self._mode = "ended"
        self.title_label.setText("播放结束")
        self.message_label.setText("看完了，要再看一遍吗？")
        self._set_button_text(self.primary_button, "重播")
        self.set_next_available(has_next)
        self._show()

    def set_next_available(self, available: bool) -> None:
        """v1.0 任务 7：没有下一条时禁用并解释原因（不隐藏，位置不跳）。"""
        available = bool(available)
        self.next_button.setVisible(self._mode == "ended")
        self.next_button.setEnabled(available)
        self.next_button.setToolTip(
            "播放稍后再看列表里的下一个视频" if available
            else "已经是稍后再看列表里的最后一个视频"
        )
        if self._mode == "ended":
            self.message_label.setText(
                "看完了，要再看一遍吗？" if available
                else "看完了，这已经是列表里的最后一个。"
            )

    def _show(self) -> None:
        tile = self.parentWidget()
        if tile is not None:
            self.sync_geometry(tile)
        self.show()
        self.raise_()

    # -------------------------------------------------------------- geometry
    def sync_geometry(self, tile) -> None:
        rect = video_geometry(tile)
        if rect.isValid() and rect.width() > 0 and self.geometry() != rect:
            self.setGeometry(rect)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()


class VodSubtitleButton(QToolButton):
    """格子底栏的字幕开关按钮（B 站字幕）。

    v1.0 需求：：经官方 ``register_tile_control`` 以 priority 59 进
    ``TileControls`` 容器，容器本身紧挨宿主「弹」按钮的**左侧** —— 这就是
    用户要的「字幕与弹幕互换位置」。高度约束在底栏 36px 内（26px），文案是
    完整的「字幕」两字，宽度 52px 不截断。checkable 表示字幕开/关；无字幕
    时由插件隐藏。不抢焦点（NoFocus），不影响宿主快捷键。
    """

    #: 两字「字幕」+ 左右内边距的保守宽度；字体度量更宽时自动加宽。
    MIN_WIDTH = 52
    HEIGHT = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("watchlaterSubtitle")
        self.setText("字幕")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("字幕开关（B 站字幕）")
        self.setAccessibleName("字幕开关")
        self.setFixedSize(self._fit_width("字幕"), self.HEIGHT)
        self.setStyleSheet("""
            QToolButton#watchlaterSubtitle {
                background: rgba(12, 13, 16, 200);
                color: rgba(214, 218, 224, 235);
                border: none; border-radius: 6px; font-weight: 600;
            }
            QToolButton#watchlaterSubtitle:hover { background: #00a1d6; color: #04161f; }
            QToolButton#watchlaterSubtitle:checked { background: #00a1d6; color: #04161f; }
        """)

    def _fit_width(self, text: str) -> int:
        """按字体度量留宽，保证「字幕」两字在任何 DPI/字号下都不被截断。"""
        metrics = QFontMetrics(self.font())
        return max(self.MIN_WIDTH, metrics.horizontalAdvance(text) + 22)

    def set_checked(self, on: bool) -> None:
        self.setChecked(bool(on))
