"""Scroll input; behavior moved intact from widgets."""
from __future__ import annotations
import math
import time
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

#: v1.0 旧换算（autoscroll_value 用）：鼠标每离开锚点这么多像素，倍率 +1。
#: 函数本身保留给 离线验收脚本 与既有单测；MiddleDragScroller 已不再
#: 用它（v1.0 改为 QTimer 每拍按 :func:`autoscroll_step` 推进）。
AUTOSCROLL_STEP = 40

#: 锚点指示器的直径（逻辑像素）。
AUTOSCROLL_INDICATOR_SIZE = 22


# ---------------------------------------------- v1.0 需求 7：浏览器式 autoscroll
#: QTimer 拍间隔（16~30ms）：持续滚动，而不是「move 一次 setValue 一次」。
AUTOSCROLL_TICK_MS = 16

#: 锚点附近 |delta| < zone 视为停住（每拍 step=0）。
AUTOSCROLL_ZONE = 8

#: 每远离锚点 divisor 像素，每拍多滚 1 像素（越远越快）。
AUTOSCROLL_DIVISOR = 8

#: 每拍滚动增量的上限。
AUTOSCROLL_MAX_STEP = 30



AUTOSCROLL_MAX_SPEED = AUTOSCROLL_MAX_STEP * (1000.0 / AUTOSCROLL_TICK_MS)

AUTOSCROLL_SMOOTH_TAU = 0.055

AUTOSCROLL_MAX_DT = 0.05


def autoscroll_speed(
    delta: float,
    *,
    zone: float = AUTOSCROLL_ZONE,
    divisor: float = AUTOSCROLL_DIVISOR,
    max_speed: float = AUTOSCROLL_MAX_SPEED,
) -> float:
    """中键 autoscroll 的目标速度（像素/秒，带符号），滚轮同款的连续版本。

    与 :func:`autoscroll_step` 语义一致但不再离散取整：锚点附近停住，越远
    越快（每远离 ``divisor`` 像素 ≈ 每帧多走 1 像素），并有速度上限。
    ``MiddleDragScroller.tick`` 用它乘以真实 dt 累计位移，从而不受 QTimer
    抖动影响；旧的 ``autoscroll_step`` 整数版保留给离线验收脚本。
    """
    d = float(delta)
    zone = max(0.0, float(zone))
    divisor = max(1.0, float(divisor))
    if abs(d) < zone:
        return 0.0
    per_frame_pixel = 1000.0 / AUTOSCROLL_TICK_MS
    speed = min(float(max_speed),
                (abs(d) - zone) / divisor * per_frame_pixel)
    return speed if d > 0 else -speed



def autoscroll_step(
    delta: int,
    *,
    zone: int = AUTOSCROLL_ZONE,
    divisor: int = AUTOSCROLL_DIVISOR,
    max_step: int = AUTOSCROLL_MAX_STEP,
) -> int:
    """v1.0 需求 7：浏览器式中键 autoscroll 每拍的滚动增量（纯函数，带符号）。

    * ``|delta| < zone`` → ``0``：鼠标回到锚点附近基本停住；
    * 越远越快：``min(max_step, max(1, |delta| // divisor))``；
    * 鼠标在锚点下方（delta>0）内容向下滚（正）、上方为负，符号相反。

    ``delta`` = 当前鼠标 Y - 锚点 Y。QTimer 每拍把
    ``bar.setValue(bar.value() + autoscroll_step(delta))``，实现「按距离决定
    速度」的持续滚动，而非旧版「位移 → 一次性 setValue」的手指滑动感。
    """
    d = int(delta)
    zone = max(0, int(zone))
    divisor = max(1, int(divisor))
    cap = max(1, int(max_step))
    if abs(d) < zone:
        return 0
    step = min(cap, max(1, abs(d) // divisor))
    return step if d > 0 else -step



def autoscroll_value(
    start_value: int,
    anchor_y: int,
    current_y: int,
    *,
    step: int = AUTOSCROLL_STEP,
) -> int:
    """浏览器式中键 autoscroll 的「位移 → 滚动值」换算（纯函数）。

    鼠标在锚点下方（delta > 0）内容向下滚（value 增大），与浏览器一致；
    倍率随距离线性增大：``delta * max(1, |delta| // step)``，所以离锚点
    越远滚得越快，靠近锚点时是 1:1 的精细滚动。
    """
    delta = int(current_y) - int(anchor_y)
    factor = max(1, abs(delta) // max(1, int(step)))
    return int(start_value) + delta * factor



class AutoscrollIndicator(QWidget):
    """中键 autoscroll 的圆形锚点（无边框浮层，跟随滚动区 viewport）。

    纯绘制：半透明底 + 一圈描边 + 上下两个小箭头，颜色取宿主调色板，
    深浅主题都看得见；``WA_TransparentForMouseEvents`` 保证它不挡鼠标。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFixedSize(AUTOSCROLL_INDICATOR_SIZE,
                          AUTOSCROLL_INDICATOR_SIZE)
        self.hide()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            palette = self.palette()
            text = palette.color(QPalette.WindowText)
            base = palette.color(QPalette.Window)
            fill = QColor(base)
            fill.setAlpha(200)
            radius = self.width() / 2.0 - 1.5
            center = QPointF(self.width() / 2.0, self.height() / 2.0)
            painter.setBrush(fill)
            pen = QPen(text)
            pen.setWidthF(1.6)
            painter.setPen(pen)
            painter.drawEllipse(center, radius, radius)
            # 上下箭头（示意可上下连续滚动）
            painter.setPen(Qt.NoPen)
            painter.setBrush(text)
            cx = center.x()
            top = QPainterPath()
            top.moveTo(cx - 3.0, center.y() - 2.0)
            top.lineTo(cx + 3.0, center.y() - 2.0)
            top.lineTo(cx, center.y() - 5.5)
            top.closeSubpath()
            painter.drawPath(top)
            bottom = QPainterPath()
            bottom.moveTo(cx - 3.0, center.y() + 2.0)
            bottom.lineTo(cx + 3.0, center.y() + 2.0)
            bottom.lineTo(cx, center.y() + 5.5)
            bottom.closeSubpath()
            painter.drawPath(bottom)
        finally:
            painter.end()



class MiddleDragScroller(QObject):
    """v1.0 需求 7：真正的浏览器式中键 autoscroll（按距离决定速度）。

    v1.0 版是「位移 → 一次性 setValue」，鼠标动一下列表跳一下，像手指划屏。
    现在：

    * 中键**按一下**（不必按住）→ 记录锚点 Y、显示圆形指示器、进入 autoscroll；
    * 松开按键也一样：一个 ``AUTOSCROLL_TICK_MS`` 的 QTimer **持续滚动**，
      每拍 ``step = autoscroll_step(当前鼠标Y - 锚点Y)``，
      ``bar.setValue(value + step)``；越远越快，回到锚点附近基本停住；
    * 鼠标 move 只更新「当前 Y」，**不再每次都 setValue**；
    * 任意点击/滚轮/Esc/窗口隐藏 → 停表、释放 grab、隐藏指示器。

    左键点选、拖拽入格、双击不受影响。
    """

    def __init__(self, area: QScrollArea, parent: QObject | None = None) -> None:
        super().__init__(parent if parent is not None else area)
        self._area = area
        self._anchor_y: int | None = None
        self._start_value = 0
        # v1.0 需求 7：当前鼠标 Y（move 只更新它），QTimer 每拍据此推进。
        self._current_y = 0
        # 时间累计滚动状态（与 WheelScrollAccumulator 同一套思路）：
        # 上一拍时间戳、平滑后的当前速度、尚未应用的亚像素位移。
        self._last_tick: float | None = None
        self._speed = 0.0
        self._pending = 0.0
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(AUTOSCROLL_TICK_MS)
        self._tick_timer.timeout.connect(self.tick)
        self._indicator: AutoscrollIndicator | None = None
        self._app_filter = False
        self._detach_pending = False
        area.viewport().installEventFilter(self)

    # ------------------------------------------------------------- helpers
    def _dead(self) -> bool:
        """True = 底层 QObject 已被销毁。

        PySide6 在 C++ 端析构后会清空 wrapper 的 ``__dict__``，此时再访问
        ``self._area`` 就是 ``AttributeError``（不是 RuntimeError），而全局
        事件过滤器可能还在给我们投事件。所有入口先用它兜一层。
        """
        return getattr(self, "_area", None) is None

    def is_active(self) -> bool:
        return getattr(self, "_anchor_y", None) is not None

    def is_dragging(self) -> bool:
        """旧名字保留（页面/测试用它判断「中键手势进行中」）。"""
        return self.is_active()

    def indicator(self) -> AutoscrollIndicator | None:
        return self._indicator

    def _bar(self):
        return self._area.verticalScrollBar()

    def _ensure_indicator(self) -> AutoscrollIndicator | None:
        if self._dead():
            return None
        try:
            viewport = self._area.viewport()
        except RuntimeError:
            return None
        if self._indicator is None:
            self._indicator = AutoscrollIndicator(viewport)
        return self._indicator

    def _show_indicator(self, position: QPoint) -> None:
        indicator = self._ensure_indicator()
        if indicator is None:
            return
        try:
            viewport = self._area.viewport()
            half = AUTOSCROLL_INDICATOR_SIZE // 2
            x = max(0, min(position.x() - half,
                           max(0, viewport.width() - AUTOSCROLL_INDICATOR_SIZE)))
            y = max(0, min(position.y() - half,
                           max(0, viewport.height() - AUTOSCROLL_INDICATOR_SIZE)))
            indicator.move(x, y)
            indicator.show()
            indicator.raise_()
        except RuntimeError:
            pass

    def _attach(self) -> None:
        """装应用级过滤器：不按键时也能收到 MouseMove（浏览器语义）。"""
        self._detach_pending = False
        if getattr(self, "_app_filter", False):
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._app_filter = True

    def _detach(self) -> None:
        if self.is_active():
            # 解绑排队期间又重新进入 autoscroll：保持过滤器，等这次结束再解绑。
            return
        self._detach_pending = False
        if not getattr(self, "_app_filter", False):
            return
        self._app_filter = False
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.removeEventFilter(self)
        except (RuntimeError, TypeError):
            pass

    def _end(self) -> None:
        self._anchor_y = None
        self._last_tick = None
        self._speed = 0.0
        self._pending = 0.0
        # v1.0 需求 7：退出一定停表，别让 QTimer 继续滚。
        timer = getattr(self, "_tick_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
        indicator = getattr(self, "_indicator", None)
        if indicator is not None:
            try:
                indicator.hide()
            except RuntimeError:
                self._indicator = None
        if not self._dead():
            try:
                self._area.viewport().unsetCursor()
            except RuntimeError:
                pass
        # v1.0 需求 14：退出时一定放开鼠标，异常路径也不例外。
        self._ungrab()
        if getattr(self, "_app_filter", False) and not self._detach_pending:
            # 不能在事件派发过程中移除过滤器，排到下一轮事件循环。
            self._detach_pending = True
            QTimer.singleShot(0, self._detach)

    def start(self, position: QPoint) -> None:
        """进入 autoscroll；``position`` 是 viewport 内的鼠标坐标。"""
        if self._dead():
            return
        try:
            bar = self._bar()
            start = bar.value()
        except RuntimeError:
            return
        self._anchor_y = int(position.y())
        self._start_value = start
        self._current_y = int(position.y())
        # 首拍只建立时间基准，不带入上一次手势的速度 / 残量。
        self._last_tick = None
        self._speed = 0.0
        self._pending = 0.0
        self._tick_timer.start()
        try:
            viewport = self._area.viewport()
            viewport.setCursor(Qt.SizeAllCursor)
            # v1.0 需求 14：抓鼠标。不抓的话，松开中键后「没有按键的移动」
            # 只有鼠标跟踪开着的控件才收得到，卡片上收到的是 HoverMove，
            # 而且鼠标一移出 viewport 就彻底收不到 —— 正是「按了中键往下
            # 拖列表不动」。grabMouse 之后所有移动都直接投递给 viewport。
            viewport.grabMouse()
        except RuntimeError:
            pass
        self._show_indicator(position)
        self._attach()

    def cancel(self) -> None:
        """页面隐藏/关闭时强制退出（连 grab 一起释放，别把鼠标扣住）。"""
        self._end()
        self._detach()

    def _ungrab(self) -> None:
        if self._dead():
            return
        try:
            viewport = self._area.viewport()
        except RuntimeError:
            return
        try:
            if QWidget.mouseGrabber() is viewport:
                viewport.releaseMouse()
        except RuntimeError:
            pass

    @staticmethod
    def _global_point(event) -> QPoint:
        try:
            return event.globalPosition().toPoint()
        except AttributeError:  # 极老的 Qt 才有
            return event.globalPos()

    def _local_point(self, on_viewport: bool, event) -> QPoint:
        """事件坐标 → viewport 局部坐标。

        * 事件直接投给 viewport（含从卡片**冒泡**上来的那个按下）：Qt 已经把
          局部坐标换算成 viewport 坐标（实测卡片内 (30,40) → viewport
          (30,384)，与全局 (33,387) 一致），所以 ``position()`` 就是准的，
          而且不依赖 ``globalPosition()`` —— 合成的 QMouseEvent 可能带一个
          占位全局坐标（旧式五参构造给的是 (10,10)）。
        * 事件投给别的控件（应用级过滤器的兜底路径）：只能用全局坐标反算。
        """
        if on_viewport:
            return event.position().toPoint()
        try:
            return self._area.viewport().mapFromGlobal(
                self._global_point(event)
            )
        except RuntimeError:
            return event.position().toPoint()

    def tick(self) -> None:
        """滚轮同款：按**真实经过时间**累计位移，每拍只应用整像素。

        旧实现每 20ms 直接 ``setValue(value + 整数 autoscroll_step)``，两个
        问题叠加成「一顿一顿」：① ``|delta| // divisor`` 是离散整数，鼠标
        稍远就一拍跳十几像素；② QTimer 在 Windows 上有几毫秒抖动，同样的
        步长遇到 22ms / 28ms 的拍，实际速度忽快忽慢。

        现在改成：距离 → 连续目标速度（:func:`autoscroll_speed`），指数
        平滑跟随，乘以 ``time.monotonic()`` 的真实 dt 累计成浮点位移，
        取整后 setValue，亚像素残量留到下一拍。定时器抖动只会改变每拍
        位移量、不改变整体速度；鼠标回到锚点时速度平滑衰减到 0。
        """
        if self._dead() or self._anchor_y is None:
            return
        now = time.monotonic()
        last = self._last_tick
        self._last_tick = now
        if last is None:
            return                       # 首拍只建立时间基准
        dt = now - last
        if dt <= 0:
            return
        dt = min(dt, AUTOSCROLL_MAX_DT)
        try:
            bar = self._bar()
        except RuntimeError:
            return
        target = autoscroll_speed(self._current_y - self._anchor_y)
        alpha = 1.0 - math.exp(-dt / AUTOSCROLL_SMOOTH_TAU)
        self._speed += (target - self._speed) * alpha
        if abs(self._speed) < 1.0:
            self._speed = 0.0
            self._pending = 0.0
            return
        self._pending += self._speed * dt
        amount = int(round(self._pending))
        if amount == 0:
            return
        old = bar.value()
        value = max(bar.minimum(), min(bar.maximum(), old + amount))
        if value == old:
            # 到顶 / 到底：丢掉残量，避免无限堆积。
            self._pending = 0.0
            return
        bar.setValue(value)
        self._pending -= (bar.value() - old)

    # -------------------------------------------------------------- filter
    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if self._dead():
            # 页面已销毁但全局过滤器还挂着：摘掉自己，别再被回调。
            self._detach()
            return False
        try:
            viewport = self._area.viewport()
        except RuntimeError:
            # 页面/滚动区已销毁，还有排队的鼠标事件在投递：直接放行。
            self._anchor_y = None
            self._app_filter = False
            return False
        kind = event.type()
        on_viewport = watched is viewport

        if kind == QEvent.MouseButtonPress:
            if (
                on_viewport
                and event.button() == Qt.MiddleButton
                and not self.is_active()
            ):
                # 中键「按一下」就进入 autoscroll（不需要按住）。
                self.start(self._local_point(True, event))
                return True
            if self.is_active():
                # 左/右/中键再按 → 退出 autoscroll。中键必须吃掉，否则
                # viewport 会把它当成「再进一次」；左右键照常下发，
                # 点选、右键菜单、双击都不受影响。
                consume = event.button() == Qt.MiddleButton
                self._end()
                return consume
            return False

        if not self.is_active():
            return False

        if kind in (QEvent.MouseMove, QEvent.HoverMove):
            # v1.0 需求 7：move 只更新「当前鼠标 Y」，绝不即时 setValue；
            # 真正的持续滚动交给 QTimer.tick()。HoverMove 也要认（松开中键
            # 后无按键，Qt 只把悬停事件投给带 WA_Hover 的卡片）。
            if on_viewport:
                self._current_y = event.position().toPoint().y()
            elif QWidget.mouseGrabber() is not viewport:
                # grab 被抢走/平台不支持时，按全局坐标反算，移出滚动区也跟。
                self._current_y = self._local_point(False, event).y()
            return False
        if kind in (QEvent.Wheel, QEvent.MouseButtonDblClick):
            self._end()
            return False
        if kind in (QEvent.KeyPress, QEvent.ShortcutOverride):
            # Esc 以及任何按键都退出，但事件继续下发（不吞快捷键）。
            self._end()
            return False
        return False
