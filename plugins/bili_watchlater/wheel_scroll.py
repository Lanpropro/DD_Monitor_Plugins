"""Chrome 式滚轮滚动：间隔扫描 + 滚动量累计（需求 3）。

QAbstractScrollArea 的原生滚轮处理是「一个 Wheel 事件 → 一次 setValue + 一次
布局/重绘」。滚轮惯性滚动时系统会以很高频率投递 Wheel，于是每个事件都要走
一遍 QScrollArea 的滚动、可见行测量、分页判定，GUI 线程被布局测量占满，肉眼
就是「滚一下卡一下」。

这里改成 Chrome 的做法：Wheel 事件只**把滚动量累加进 ``_pending``**，由固定
间隔（约一帧）的 QTimer 统一扫描，一帧把本帧累计量一次性应用到
verticalScrollBar（不再人为只应用 35% 而留下拖尾惯性）。
Wheel 被 ``accept()`` + ``return True`` 吞掉，QAbstractScrollArea 内部装在
viewport 上的原生过滤器不会再执行一次，避免双重滚动。

与 ``MiddleDragScroller`` 共存：Qt 的事件过滤器按「后装先执行」调用。本模块
由 ``page._build_ui`` 在 ``MiddleDragScroller`` **之前**安装，因此调用顺序是
MiddleDragScroller → WheelScrollAccumulator → QAbstractScrollArea 内部过滤器：
中键 autoscroll 先收到 Wheel 并 ``_end()``（它返回 False，不吞事件），随后
本模块才累计并吞掉。另外 ``on_wheel`` 回调（page._cancel_autoscroll）作为
第二重保险，即使将来安装顺序被改动，滚轮也一定能结束中键 autoscroll。

只依赖 PySide6，不引入第三方依赖、不联网、不读写磁盘。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollArea

#: 扫描间隔：约一帧（60fps ≈ 16.7ms）。
SCAN_INTERVAL_MS = 16
#: 一帧内应用多少累计量：1.0 = 本帧收到的滚轮量全部落地，不留人为惯性。
#: 旧值 0.35 会让连续滚轮时 _pending 稳定在约 3 帧的量上，松手后还要拖尾
#: ~150ms，肉眼就是「滚轮滞后 / 不跟手」。仍保持「每个 Wheel 只累计、
#: 一帧只 setValue 一次」的批处理语义，只是不再人为削弱每帧位移。
FRAME_FRACTION = 1.0
#: 单帧位移上限（像素）：一次猛甩的累计量超过它才分批，避免一帧跳一大段。
MAX_STEP_PER_FRAME = 240
#: 一个 notch（angleDelta=120）至少对应的像素数（Windows 默认 3 行×~20px）。
DEFAULT_NOTCH_PIXELS = 60.0
#: ``QApplication.wheelScrollLines()`` 不可用 / 为 0 时的行数。
DEFAULT_WHEEL_LINES = 3
#: 取不到 QFontMetrics 时的单行像素兜底。
DEFAULT_LINE_HEIGHT = 20
#: 小于这个残量就视为收敛，直接清零，避免浮点尾数让定时器空转。
CONVERGE_EPSILON = 0.5


class WheelScrollAccumulator(QObject):
    """把 Wheel 事件累计成像素滚动量，按固定间隔扫描应用。

    :param area: 目标 ``QScrollArea``（过滤器装在它的 viewport 上）。
    :param on_wheel: 每次收到有效 Wheel 时调用的回调，用来结束中键 autoscroll。
    :param parent: QObject 父对象，缺省用 viewport（页面销毁时一起析构）。
    """

    def __init__(
        self,
        area: QScrollArea,
        on_wheel: Callable[[], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent if parent is not None else area.viewport())
        self._area = area
        self._viewport = area.viewport()
        self._on_wheel = on_wheel
        self._pending = 0.0
        self._enabled = True
        self._installed = False
        self._timer = QTimer(self)
        self._timer.setInterval(SCAN_INTERVAL_MS)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._scan)
        # 装在 viewport 上：Qt 会把未被子控件接受的 Wheel 沿父链上溯，
        # 因此卡片上的滚轮同样会到达这里（Qt 平滑滚动的通行做法）。
        self._viewport.installEventFilter(self)
        self._installed = True

    # ------------------------------------------------------------------ API
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """关掉后 Wheel 直接放行（退回宿主原生滚动），并把累计量清零。"""
        self._enabled = bool(enabled)
        if not self._enabled:
            self._pending = 0.0
            self._timer.stop()

    def pending(self) -> float:
        """当前尚未应用的滚动量（像素，带符号）；测试/诊断可读取。"""
        return self._pending

    def is_scanning(self) -> bool:
        return self._timer.isActive()

    def flush(self) -> None:
        """页面隐藏 / 关闭 / 切视图前把剩余累计量一次性落定，不残留漂移。"""
        self._timer.stop()
        if not self._pending:
            return
        try:
            bar = self._area.verticalScrollBar()
        except RuntimeError:
            self._pending = 0.0
            return
        remaining = int(round(self._pending))
        self._pending = 0.0
        self._apply(bar, remaining)

    def shutdown(self) -> None:
        """摘掉过滤器、停表、丢弃累计量（页面销毁前调用，幂等）。"""
        self._timer.stop()
        self._pending = 0.0
        if self._installed:
            self._installed = False
            try:
                self._viewport.removeEventFilter(self)
            except (RuntimeError, TypeError):
                pass

    # ------------------------------------------------------------- wheel math
    @staticmethod
    def _notch_pixels() -> float:
        """一个 notch（120 度）对应多少像素：wheelScrollLines × 字体行高。"""
        getter = getattr(QApplication, "wheelScrollLines", None)
        lines = 0
        if callable(getter):
            try:
                lines = int(getter())
            except (TypeError, ValueError, RuntimeError):
                lines = 0
        if lines <= 0:
            lines = DEFAULT_WHEEL_LINES
        try:
            line_height = int(QApplication.fontMetrics().height())
        except (AttributeError, TypeError, ValueError, RuntimeError):
            line_height = DEFAULT_LINE_HEIGHT
        if line_height <= 0:
            line_height = DEFAULT_LINE_HEIGHT
        return max(DEFAULT_NOTCH_PIXELS, float(lines) * float(line_height))

    @classmethod
    def value_delta(cls, event: QWheelEvent) -> float:
        """Wheel → verticalScrollBar.value 的增量（像素，带符号）。

        Qt 约定：``angleDelta().y() > 0`` 是滚轮向上（远离用户），内容向上
        滚，scrollbar.value 应**减小**，故取负号。高分辨率触控板的
        ``pixelDelta()`` 用来给出更细的幅度，但方向一律以 angleDelta 为准
        （angleDelta 恒有且符号定义明确），避免个别平台 pixelDelta 反号时
        滚动方向颠倒；只有 angleDelta 缺失时才退回 ``-pixelDelta().y()``。
        """
        try:
            angle = int(event.angleDelta().y())
        except (AttributeError, TypeError, ValueError):
            angle = 0
        try:
            pixel = int(event.pixelDelta().y())
        except (AttributeError, TypeError, ValueError):
            pixel = 0
        if angle == 0 and pixel == 0:
            return 0.0
        if angle:
            sign = -1.0 if angle > 0 else 1.0
            if pixel:
                magnitude = abs(float(pixel))
            else:
                magnitude = abs(angle) / 120.0 * cls._notch_pixels()
            return sign * magnitude
        return -float(pixel)

    # -------------------------------------------------------------- internals
    @staticmethod
    def _apply(bar, step: int) -> int:
        """把 ``step`` 夹到滚动范围内应用，返回**实际生效**的 value 增量。"""
        if not step:
            return 0
        old = bar.value()
        target = max(bar.minimum(), min(bar.maximum(), old + step))
        if target == old:
            return 0
        bar.setValue(target)
        return bar.value() - old

    def _scan(self) -> None:
        """一帧一次：本帧累计量全部落地，只有超出单帧上限的部分留到下一帧。"""
        if not self._pending:
            self._timer.stop()
            return
        try:
            bar = self._area.verticalScrollBar()
        except RuntimeError:
            self._pending = 0.0
            self._timer.stop()
            return
        pending = self._pending
        step = pending * FRAME_FRACTION
        if step > MAX_STEP_PER_FRAME:
            step = float(MAX_STEP_PER_FRAME)
        elif step < -MAX_STEP_PER_FRAME:
            step = float(-MAX_STEP_PER_FRAME)
        if abs(step) < 1.0:
            # 收尾：不足 1px 时一次补完，避免无限残留 0.x 像素。
            step = pending
        amount = int(round(step))
        if amount == 0:
            amount = 1 if pending > 0 else -1
        applied = self._apply(bar, amount)
        if applied == 0:
            # 已到顶 / 到底，剩余量永远无法生效 → 丢弃，避免累计无限堆积。
            self._pending = 0.0
            self._timer.stop()
            return
        self._pending -= applied
        if abs(self._pending) < CONVERGE_EPSILON:
            self._pending = 0.0
        if not self._pending:
            self._timer.stop()

    # --------------------------------------------------------------- filter
    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if not self._enabled:
            return False
        if event.type() != QEvent.Wheel or watched is not self._viewport:
            return False
        delta = self.value_delta(event)
        if not delta:
            # 纯横向滚轮 / 无有效增量：放行给宿主原生处理，不吞。
            return False
        # 滚轮出现即结束中键 autoscroll（见模块 docstring 的顺序说明）。
        if self._on_wheel is not None:
            try:
                self._on_wheel()
            except Exception:
                pass
        # 反向滚轮立即取消残余惯性（Chrome 语义）；同向则继续累计。
        if self._pending and ((delta > 0) != (self._pending > 0)):
            self._pending = 0.0
        self._pending += delta
        if not self._timer.isActive():
            self._timer.start()
        try:
            event.accept()
        except (AttributeError, RuntimeError):
            pass
        # 返回 True：事件到此为止，QAbstractScrollArea 不会再 setValue 一次。
        return True
