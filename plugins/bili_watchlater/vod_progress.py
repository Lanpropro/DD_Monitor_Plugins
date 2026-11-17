"""Video progress controls and preview interaction."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget
from .vod_surface import video_geometry

STRIP_HEIGHT = 26


BAR_HEIGHT = 28   # 底栏进度条固定高（宿主 TileControls 约束 <= 30）


LINE_HEIGHT = 3   # 视频区底边蓝色进度线


_CLOCK_SAMPLE = "00:00:00"


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%02d:%02d" % (minutes, secs)


class VodProgressStrip(QWidget):
    """视频区底部的 seek 条：当前时间 + 滑杆 + 总时长。

    原生子窗口（视频是 HWND，非原生控件会被盖住）。样式固定深色，不随
    调色板事件写样式表 —— v1.0 的 changeEvent 递归坑从结构上消失。
    """

    seek_requested = Signal(float)  # 绝对秒数

    def __init__(self, tile: QWidget) -> None:
        super().__init__(tile)
        self.setObjectName("watchlaterProgress")
        # 先初始化再装 eventFilter：installEventFilter 之后滑杆事件随时可能
        # 到达（polish/hover），eventFilter 会读 _preview_provider。
        self._preview_provider = None
        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(STRIP_HEIGHT)
        self.setStyleSheet("""
            QWidget#watchlaterProgress {
                background: rgba(8, 9, 11, 170);
                border-top: 1px solid rgba(255, 255, 255, 28);
            }
            QLabel { color: rgba(214, 218, 224, 220); background: transparent; }
            QSlider { background: transparent; }
            QSlider::groove:horizontal {
                height: 4px; background: rgba(214, 218, 224, 70);
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #00a1d6; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 10px; margin: -4px 0; border-radius: 5px;
                border: none; background: #00a1d6;
            }
            QSlider::handle:horizontal:hover { background: #ffffff; }
        """)
        self.setAccessibleName("播放进度")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(8)

        self.setMouseTracking(True)

        self.current_label = QLabel("00:00", self)
        row.addWidget(self.current_label, 0)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setObjectName("watchlaterSeek")
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setTracking(False)
        self.slider.setPageStep(50)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.setMouseTracking(True)
        self.slider.installEventFilter(self)
        self.slider.setToolTip("播放进度（拖动跳转）")
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.sliderPressed.connect(self._pressed)
        self.slider.sliderMoved.connect(self._moved)
        self.slider.sliderReleased.connect(self._released)
        row.addWidget(self.slider, 1)

        self.duration_label = QLabel("00:00", self)
        row.addWidget(self.duration_label, 0)

        # v1.0 需求：：与底栏进度条一致，时间标签固定宽度，滑杆不被文本推移。
        self._lock_clock_width()

        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self._preview_provider = None
        self.hide()

    def _lock_clock_width(self) -> None:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(_CLOCK_SAMPLE) + 2
        for label in (self.current_label, self.duration_label):
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignCenter)

    # ------------------------------------------------------------------ state
    def set_duration(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        self._duration = max(0.0, value)
        self.duration_label.setText(format_clock(self._duration))

    def set_position(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        self._position = max(0.0, value)
        if not self._seeking:
            self.current_label.setText(format_clock(self._position))
            if self._duration > 0:
                ratio = max(0.0, min(1.0, self._position / self._duration))
                self.slider.setValue(int(ratio * 1000))

    def reset(self) -> None:
        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self.slider.setValue(0)
        self.current_label.setText("00:00")
        self.duration_label.setText("00:00")

    # ------------------------------------------------------------------ seek
    def _pressed(self) -> None:
        self._seeking = True
        self._cancel_preview()

    def _moved(self, value: int) -> None:
        if self._duration > 0:
            self.current_label.setText(
                format_clock(value / 1000.0 * self._duration)
            )

    def _released(self) -> None:
        # tracking=False 时 sliderReleased 早于 value 提交，读取最终拖动位置。
        value = self.slider.sliderPosition()
        self._seeking = False
        if self._duration > 0:
            target = value / 1000.0 * self._duration
            self._position = target
            self.current_label.setText(format_clock(target))
            self.seek_requested.emit(target)

    # --------------------------------------------------------------- preview
    def set_preview_provider(self, provider: Any) -> None:
        """挂/摘画面预览源（vod_preview.VodPreviewProvider，鸭子类型）。

        ``None`` 时行为与 v1.0 完全一致：不建弹窗、不发任何请求。
        """
        if provider is self._preview_provider:
            return
        self._cancel_preview()
        self._preview_provider = provider

    def _cancel_preview(self) -> None:
        provider = self._preview_provider
        if provider is not None:
            try:
                provider.cancel_preview()
            except Exception:
                pass

    def _preview_hover(self, strip_x: float) -> None:
        provider = self._preview_provider
        if provider is None or self._duration <= 0 or not self.isVisible():
            return
        geo = self.slider.geometry()
        if geo.width() <= 0:
            return
        ratio = (float(strip_x) - geo.x()) / geo.width()
        ratio = max(0.0, min(1.0, ratio))
        seconds = ratio * self._duration
        anchor = QPoint(geo.x() + int(ratio * geo.width()), 0)
        try:
            provider.request_preview(self.mapToGlobal(anchor), seconds)
        except Exception:
            pass

    def mouseMoveEvent(self, event) -> None:
        if (self._preview_provider is not None
                and not (event.buttons() & Qt.LeftButton)):
            self._preview_hover(event.position().x())
        super().mouseMoveEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.slider:
            kind = event.type()
            if kind == QEvent.Type.MouseMove:
                if (self._preview_provider is not None
                        and not (event.buttons() & Qt.LeftButton)):
                    self._preview_hover(self.slider.x() + event.position().x())
                    return True  # 仅消费预览悬停；拖动必须交还 QSlider。
            if kind == QEvent.Type.Leave:
                self._cancel_preview()
        return super().eventFilter(watched, event)

    def leaveEvent(self, event) -> None:
        self._cancel_preview()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._cancel_preview()
        super().hideEvent(event)

    # -------------------------------------------------------------- geometry
    def sync_geometry(self, tile) -> None:
        rect = video_geometry(tile)
        if not rect.isValid() or rect.width() <= 0:
            return
        target = QRect(rect.x(), rect.bottom() - STRIP_HEIGHT + 1,
                       rect.width(), STRIP_HEIGHT)
        if self.geometry() != target:
            self.setGeometry(target)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 原生视频子窗之上；只在显示/恢复时调整层级，不在进度轮询中反复 raise。
        self.raise_()


class VodBarProgress(QWidget):
    """全屏底栏里的进度条（v1.0 需求：控件侧）。

    构造 **parent=None**：宿主 ``TileControls.add`` 会 ``setParent(wrapper)``
    再插进 ``tile.bottom.layout()``（全屏时随底栏变独立 Tool 窗，同隐同显）。
    不要 ``WA_NativeWindow`` —— 底栏有自己的合成 backing store。
    seek 契约与 ``VodProgressStrip`` 完全一致。
    """

    seek_requested = Signal(float)  # 绝对秒数

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("watchlaterBarProgress")
        # 同 VodProgressStrip：先初始化，再让滑杆事件有可能到达。
        self._preview_provider = None
        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(BAR_HEIGHT)
        self.setStyleSheet("""
            QWidget#watchlaterBarProgress { background: transparent; }
            QLabel { color: rgba(214, 218, 224, 220); background: transparent; }
            QSlider { background: transparent; }
            QSlider::groove:horizontal {
                height: 4px; background: rgba(214, 218, 224, 70);
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #00a1d6; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 10px; margin: -4px 0; border-radius: 5px;
                border: none; background: #00a1d6;
            }
            QSlider::handle:horizontal:hover { background: #ffffff; }
        """)
        self.setAccessibleName("全屏播放进度")

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(6)

        self.setMouseTracking(True)

        self.current_label = QLabel("00:00", self)
        row.addWidget(self.current_label, 0)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setObjectName("watchlaterBarSeek")
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setTracking(False)
        self.slider.setPageStep(50)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.setMouseTracking(True)
        self.slider.installEventFilter(self)
        self.slider.setToolTip("播放进度（拖动跳转）")
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.sliderPressed.connect(self._pressed)
        self.slider.sliderMoved.connect(self._moved)
        self.slider.sliderReleased.connect(self._released)
        row.addWidget(self.slider, 1)

        self.duration_label = QLabel("00:00", self)
        row.addWidget(self.duration_label, 0)
        # v1.0 需求：：时间文本宽度一变（00:05 → 1:02:33），QLabel 重排会把
        # 中间的滑杆整体推移，全屏下看起来就是「进度条诡异平移」。按最长
        # 时钟串预留固定宽度，滑杆左右边缘从此不动。
        self._lock_clock_width()

        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self._preview_provider = None

    def _lock_clock_width(self) -> None:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(_CLOCK_SAMPLE) + 2
        for label in (self.current_label, self.duration_label):
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignCenter)

    # ------------------------------------------------------------------ state
    def set_duration(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        self._duration = max(0.0, value)
        self.duration_label.setText(format_clock(self._duration))

    def set_position(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        self._position = max(0.0, value)
        if not self._seeking:
            self.current_label.setText(format_clock(self._position))
            if self._duration > 0:
                ratio = max(0.0, min(1.0, self._position / self._duration))
                self.slider.setValue(int(ratio * 1000))

    def reset(self) -> None:
        self._duration = 0.0
        self._position = 0.0
        self._seeking = False
        self.slider.setValue(0)
        self.current_label.setText("00:00")
        self.duration_label.setText("00:00")

    # ------------------------------------------------------------------ seek
    def _pressed(self) -> None:
        self._seeking = True
        self._cancel_preview()

    def _moved(self, value: int) -> None:
        if self._duration > 0:
            self.current_label.setText(
                format_clock(value / 1000.0 * self._duration)
            )

    def _released(self) -> None:
        # tracking=False 时 sliderReleased 早于 value 提交，读取最终拖动位置。
        value = self.slider.sliderPosition()
        self._seeking = False
        if self._duration > 0:
            target = value / 1000.0 * self._duration
            self._position = target
            self.current_label.setText(format_clock(target))
            self.seek_requested.emit(target)

    # --------------------------------------------------------------- preview
    def set_preview_provider(self, provider: Any) -> None:
        if provider is self._preview_provider:
            return
        self._cancel_preview()
        self._preview_provider = provider

    def _cancel_preview(self) -> None:
        provider = self._preview_provider
        if provider is not None:
            try:
                provider.cancel_preview()
            except Exception:
                pass

    def _preview_hover(self, bar_x: float) -> None:
        provider = self._preview_provider
        if provider is None or self._duration <= 0 or not self.isVisible():
            return
        geo = self.slider.geometry()
        if geo.width() <= 0:
            return
        ratio = (float(bar_x) - geo.x()) / geo.width()
        ratio = max(0.0, min(1.0, ratio))
        seconds = ratio * self._duration
        anchor = QPoint(geo.x() + int(ratio * geo.width()), 0)
        try:
            provider.request_preview(self.mapToGlobal(anchor), seconds)
        except Exception:
            pass

    def mouseMoveEvent(self, event) -> None:
        if (self._preview_provider is not None
                and not (event.buttons() & Qt.LeftButton)):
            self._preview_hover(event.position().x())
        super().mouseMoveEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.slider:
            kind = event.type()
            if kind == QEvent.Type.MouseMove:
                if (self._preview_provider is not None
                        and not (event.buttons() & Qt.LeftButton)):
                    self._preview_hover(self.slider.x() + event.position().x())
                    return True  # 仅消费预览悬停；拖动必须交还 QSlider。
            if kind == QEvent.Type.Leave:
                self._cancel_preview()
        return super().eventFilter(watched, event)

    def leaveEvent(self, event) -> None:
        self._cancel_preview()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._cancel_preview()
        super().hideEvent(event)


class VodProgressLine(QWidget):
    """视频区底边的蓝色进度线（v1.0 需求：控件侧）。

    原生子窗口 + 鼠标穿透，贴在 ``video_geometry`` 底边全宽；纯
    ``paintEvent`` 绘制（轨道 ``rgba(255,255,255,40)``，左起按 ratio 填
    B 站蓝），不碰样式表。
    """

    def __init__(self, tile: QWidget) -> None:
        super().__init__(tile)
        self.setObjectName("watchlaterProgressLine")
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(LINE_HEIGHT)
        self._ratio = 0.0
        self.hide()

    def set_ratio(self, ratio: Any) -> None:
        try:
            value = float(ratio)
        except (TypeError, ValueError):
            value = 0.0
        value = max(0.0, min(1.0, value))
        if value != self._ratio:
            self._ratio = value
            self.update()

    def sync_geometry(self, tile) -> None:
        rect = video_geometry(tile)
        if not rect.isValid() or rect.width() <= 0:
            return
        target = QRect(rect.x(), rect.bottom() - LINE_HEIGHT + 1,
                       rect.width(), LINE_HEIGHT)
        if self.geometry() != target:
            self.setGeometry(target)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(255, 255, 255, 40))
        width = int(self.rect().width() * self._ratio)
        if width > 0:
            painter.fillRect(QRect(0, 0, width, self.rect().height()),
                             QColor("#00a1d6"))
        painter.end()
