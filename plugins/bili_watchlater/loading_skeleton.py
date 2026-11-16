"""v1.0 需求：：首次加载的骨架屏（占位加载动画）。

paintEvent 直接画 5~6 行仿 RowCard 的骨架（168x94 圆角封面块 + 两条文字条 +
按钮胶囊），底色用固定中性灰（深浅主题都可见）；一条半透明白色高光由
QVariantAnimation 驱动、1200ms 循环从左扫到右（shimmer）。动画只在可见时
运行：showEvent 启动、hideEvent 停止。骨架路径按尺寸缓存，避免每帧重建。
"""
from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

ROW_COUNT = 6
COVER_W = 168
COVER_H = 94
ROW_GAP = 10
PAD = 12
SHIMMER_MS = 1200
BASE_FILL = QColor(128, 132, 140, 40)
SHIMMER_PEAK = QColor(255, 255, 255, 70)

# v1.0 需求 3：加载骨架与当前视图形态一致。默认视图是网格（4 列），骨架
# 也画成一列 GRID_COLUMNS 个占位块（封面块 + 两条文字条），列表模式退回
# 原来的行占位。列数取 page.GRID_COLUMNS 的默认值 4（此处不 import page，
# 避免循环依赖；page 通过 set_mode 传入形态）。
GRID_COLUMNS = 4


class SkeletonLoadingView(QWidget):
    """整页占位加载动画；接口与 StatusView.set_state 兼容（title/loading）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # v1.0 需求 3：默认网格形态（与 page._view_mode == "grid" 一致）。
        self._mode = "grid"
        self.setMinimumHeight(ROW_COUNT * (COVER_H + ROW_GAP))
        self._shimmer = 0.0
        self._path: QPainterPath | None = None
        self._path_size: QSize | None = None

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(SHIMMER_MS)
        self._anim.setLoopCount(-1)  # 无限循环，直到 hideEvent 停止
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_frame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.addStretch(1)
        self.caption = QLabel("正在加载稍后再看…", self)
        self.caption.setTextFormat(Qt.PlainText)
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setForegroundRole(QPalette.PlaceholderText)
        layout.addWidget(self.caption, 0, Qt.AlignHCenter)

    # StatusView 兼容：page 仍以 set_state("正在加载稍后再看…", loading=True) 调用。
    def set_state(
        self,
        title: str = "",
        message: str = "",
        *,
        primary: str = "",
        secondary: str = "",
        loading: bool = True,
    ) -> None:
        text = title or "正在加载稍后再看…"
        self.caption.setText(text)

    def mode(self) -> str:
        """当前骨架形态："grid" 或 "list"（供回归断言与 page 同步）。"""
        return self._mode

    def set_mode(self, mode: str) -> None:
        """v1.0 需求 3：按当前视图形态切换骨架（grid/list）。"""
        mode = "grid" if mode == "grid" else "list"
        if mode == self._mode:
            return
        self._mode = mode
        self._path = None
        self._path_size = None
        self.update()

    def _on_frame(self, value) -> None:
        try:
            self._shimmer = float(value)
        except (TypeError, ValueError):
            self._shimmer = 0.0
        self.update()

    def _build_path(self) -> QPainterPath:
        """按当前尺寸构建骨架形状（封面块 + 文字条 + 按钮胶囊），缓存复用。"""
        path = QPainterPath()
        if self._mode == "grid":
            self._build_grid_path(path)
        else:
            self._build_list_path(path)
        return path

    def _build_grid_path(self, path: QPainterPath) -> None:
        """v1.0 需求 3：网格骨架 = 一列 GRID_COLUMNS 个占位块（封面 + 文字条）。"""
        columns = max(1, int(GRID_COLUMNS))
        gap = ROW_GAP
        avail = max(80, self.width() - 2 * PAD)
        cell_w = max(64, (avail - (columns - 1) * gap) // columns)
        cover_h = max(45, cell_w * 9 // 16)
        row_stride = cover_h + 2 * gap + 34
        top = PAD
        for row in range(ROW_COUNT):
            y = top + row * row_stride
            if y + cover_h + 40 > self.height() - 24:
                break
            for col in range(columns):
                x = PAD + col * (cell_w + gap)
                if x + cell_w > self.width() - PAD + 1:
                    break
                cover = QRectF(x, y, cell_w, cover_h)
                path.addRoundedRect(cover, 8, 8)
                bar1 = QRectF(x, y + cover_h + 8, cell_w * 0.85, 14)
                path.addRoundedRect(bar1, 6, 6)
                bar2 = QRectF(x, y + cover_h + 26, cell_w * 0.55, 12)
                path.addRoundedRect(bar2, 6, 6)

    def _build_list_path(self, path: QPainterPath) -> None:
        """列表骨架：仿 RowCard 的行占位（封面块 + 文字条 + 按钮胶囊）。"""
        top = PAD
        avail_w = max(COVER_W + 200, self.width() - 2 * PAD)
        text_x = PAD + COVER_W + 16
        text_w = max(80, avail_w - COVER_W - 16 - 120)
        for index in range(ROW_COUNT):
            y = top + index * (COVER_H + ROW_GAP)
            if y + COVER_H > self.height() - 24:
                break
            cover = QRectF(PAD, y, COVER_W, COVER_H)
            path.addRoundedRect(cover, 8, 8)
            bar1 = QRectF(text_x, y + 12, text_w * 0.85, 16)
            path.addRoundedRect(bar1, 6, 6)
            bar2 = QRectF(text_x, y + 40, text_w * 0.55, 14)
            path.addRoundedRect(bar2, 6, 6)
            pill_w = 92
            pill_x = max(text_x, self.width() - PAD - pill_w)
            pill = QRectF(pill_x, y + COVER_H - 34, pill_w, 30)
            path.addRoundedRect(pill, 15, 15)

    def _ensure_path(self) -> QPainterPath:
        size = self.size()
        if self._path is None or self._path_size != size:
            self._path = self._build_path()
            self._path_size = size
        return self._path

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            path = self._ensure_path()
            painter.fillPath(path, BASE_FILL)
            # 半透明白高光：一条竖向渐变带随 _shimmer 从左扫到右。
            band_w = max(80.0, self.width() * 0.35)
            span = self.width() + 2 * band_w
            center_x = -band_w + self._shimmer * span
            gradient = QLinearGradient(
                QPointF(center_x - band_w / 2, 0),
                QPointF(center_x + band_w / 2, 0),
            )
            edge = QColor(SHIMMER_PEAK)
            edge.setAlpha(0)
            gradient.setColorAt(0.0, edge)
            gradient.setColorAt(0.5, SHIMMER_PEAK)
            gradient.setColorAt(1.0, edge)
            painter.setClipPath(path)
            painter.fillRect(self.rect(), gradient)
        finally:
            painter.end()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._anim.state() != QAbstractAnimation.Running:
            self._anim.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._anim.stop()
        super().hideEvent(event)
