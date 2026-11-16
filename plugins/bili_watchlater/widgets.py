"""Widgets for the embedded watch-later page.

Everything is painted from the host palette.  API strings are always rendered
as plain text; covers are drawn from an in-memory QPixmap cache.
"""
from __future__ import annotations

import math
import time
from typing import Callable

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .models import (
    VOD_MIME,
    WatchLaterItem,
    format_added,
    format_count,
    format_duration,
    format_views,
)

from .entry_icon import BrowserIcon
from .page_panels import ThumbnailSurface
from ddm.ui import ActionButton


def browser_button(parent):
    button = QToolButton(parent)
    button.setIcon(QIcon(BrowserIcon()))
    button.setIconSize(QSize(18, 18))
    button.setFixedSize(28, 28)
    button.setCursor(Qt.PointingHandCursor)
    button.setToolTip("在浏览器中打开视频页")
    button.setAccessibleName("在浏览器中打开视频页")
    return button


def _start_vod_drag(source: QWidget, item: WatchLaterItem,
                    pixmap: QPixmap | None) -> None:
    """v1.0（需求 6）：把视频卡拖进画面墙格子直接播放。

    只带插件自己的 VOD_MIME（bvid）+ 纯文本标题；宿主格子的拖放过滤器
    认这个格式，宿主直播间/格子/弹幕拖拽互不干扰。
    """
    drag = QDrag(source)
    mime = QMimeData()
    mime.setData(VOD_MIME, str(item.bvid or "").encode("utf-8"))
    mime.setText(str(item.title or item.bvid or ""))
    drag.setMimeData(mime)
    if pixmap is not None and not pixmap.isNull():
        cover = pixmap.scaled(
            168, 94,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        drag.setPixmap(cover)
        drag.setHotSpot(QPoint(cover.width() // 2, cover.height() // 2))
    drag.exec(Qt.CopyAction)


def relative_font(base: QFont, factor: float, *, bold: bool = False) -> QFont:
    font = QFont(base)
    if base.pointSizeF() > 0:
        font.setPointSizeF(max(7.0, base.pointSizeF() * factor))
    elif base.pixelSize() > 0:
        font.setPixelSize(max(9, round(base.pixelSize() * factor)))
    font.setBold(bold)
    return font


def item_tooltip(item: WatchLaterItem, hint: str = "") -> str:
    """v1.0：悬停提示的纯文本全文（标题 + UP 主 + 可选操作提示）。

    标题在列表/网格里会被省略号截断，悬停时必须能看到完整标题；拼装只
    发生在这一个地方，避免在已有 tooltip 上再覆盖一层重复内容。
    """
    lines = [str(item.title or "").strip(), str(item.owner_name or "").strip()]
    if hint:
        lines.append(str(hint).strip())
    return "\n".join(line for line in lines if line)


#: v1.0 需求 8：tooltip 每行最多这么多字符。中文没有空格，QToolTip 不会自己
#: 断行，长标题会横着拉到屏幕外（用户实测只看到前几个字）。
TOOLTIP_WRAP_CHARS = 34


def wrap_tooltip_text(text: str, limit: int = TOOLTIP_WRAP_CHARS) -> str:
    """把长文本按字符折行（纯函数）：优先在空格处断，其次硬断。"""
    limit = max(8, int(limit))
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit + 1)
            if cut < limit // 2:
                cut = limit
            lines.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        if line:
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- v1.0 共享
#: 需求 12：标题行/筛选行与网格卡片共用的内容左右边距（逻辑像素）。
#: 外层 root 有自己的 24px 边距，这里统一的是「头部按钮 vs 卡片内文字/封面」
#: 相对 body 的内缩量 —— 两边都取同一个常量，按钮就不会比卡片缩进或外凸。
PAGE_CONTENT_MARGIN = 4
#: 需求 12：标题行/筛选行/网格布局统一的行内间距。
PAGE_ROW_SPACING = 8

# 需求 10：多选快速删除的状态机（纯逻辑，可单测，不依赖 Qt 事件）。
MULTISELECT_IDLE = "idle"
MULTISELECT_SELECT = "select"


def multiselect_click(state: str) -> tuple[str, str]:
    """垃圾桶 / 确认删除按钮的点击状态机（纯函数）。

    返回 ``(新状态, 动作)``：

    * 第一次点（idle）→ ``(select, "enter")``：进入多选，按钮变红色二次确认态；
    * 第二次点（select）→ ``(idle, "delete")``：真正执行删除。

    按钮态本身即二次确认（不是静默删除），调用方在 ``delete`` 前还要检查
    选中集合非空。
    """
    if state == MULTISELECT_SELECT:
        return MULTISELECT_IDLE, "delete"
    return MULTISELECT_SELECT, "enter"


def multiselect_cancel(_state: str) -> str:
    """取消：回到 idle（调用方清空选中集合，且不执行删除）。"""
    return MULTISELECT_IDLE


def multiselect_is_confirming(state: str) -> bool:
    """进入多选后按钮即处于「透明红底 + 红字」的二次确认态。"""
    return state == MULTISELECT_SELECT


def selection_toggle(keys, key: str) -> set:
    """切换一个视频 key 的选中态，返回新集合（不修改入参，可单测）。"""
    result = set(keys or ())
    if key in result:
        result.discard(key)
    else:
        result.add(key)
    return result


#: v1.0 需求 9：Enter 后延迟这么久才弹一次标题，避免鼠标掠过就闪。
HOVER_TIP_DELAY_MS = 200


class HoverTitleTip(QObject):
    """v1.0 需求 8/9：稳定、可抑制的「完整标题 + UP 主」悬浮提示。

    v1.0 版每次 MouseMove/HoverMove 都检查并 ``showText``：右键按下前 Qt 先
    派发的一次 move 会把标题弹在菜单旁边；而右键/Wheel 触发的 hide 又会被
    紧随的一次残留 move 立刻重新弹出来，肉眼就是「闪一下 / 右键冒标题」。
    根因是 **没有抑制窗口 + 每次移动都重建显示**。这里改成：

    * 仅 ``QEvent.Enter`` 后延迟 ``HOVER_TIP_DELAY_MS`` 显示一次（QTimer）；
    * 卡片内移动不重建文本也不跟随（干脆不跟随），不重复 showText；
    * 右键/菜单/滚轮/拖拽/窗口失焦 → :meth:`suppress` 立即收起并压住，
      直到「真正的一次鼠标移动」才解除，解除后仍要等延迟才会再显示；
    * 真正离开整张卡片（全局矩形判断）才 hide。
    """

    def __init__(self, card: QWidget, text_provider: Callable[[], str]) -> None:
        super().__init__(card)
        self._card = card
        self._text_provider = text_provider
        self._shown = False
        self._armed = False        # Enter 之后允许（延迟）显示
        self._suppressed = False   # 右键/菜单/滚轮期间压住，等真正 move 解除
        self._delay_ms = HOVER_TIP_DELAY_MS
        # parent=self：卡片销毁时定时器一起销毁，不会留下悬挂回调。
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._delayed_show)

    # ------------------------------------------------------------- public
    def watch(self, *widgets: QWidget) -> None:
        for widget in widgets:
            if widget is not None:
                widget.installEventFilter(self)

    def tooltip_text(self) -> str:
        try:
            text = self._text_provider()
        except Exception:
            return ""
        return wrap_tooltip_text(text)

    def is_showing(self) -> bool:
        return self._shown

    def hover_delay(self) -> int:
        return self._delay_ms

    def set_hover_delay(self, msec: int) -> None:
        """测试可把延迟设成 0，用 ``processEvents`` 立即驱动一次显示。"""
        self._delay_ms = max(0, int(msec))

    def is_suppressed(self) -> bool:
        return self._suppressed

    def hide(self) -> None:
        self._timer.stop()
        self._shown = False
        self._armed = False
        try:
            QToolTip.hideText()
        except Exception:
            pass

    def suppress(self) -> None:
        """右键/菜单/滚轮/拖拽/失焦：无条件收起并压住，等下一次真正 move。"""
        self._timer.stop()
        self._suppressed = True
        self._shown = False
        self._armed = False
        try:
            QToolTip.hideText()
        except Exception:
            pass

    # ------------------------------------------------------------ helpers
    def _inside(self, global_pos: QPoint) -> bool:
        try:
            return self._card.rect().contains(self._card.mapFromGlobal(global_pos))
        except RuntimeError:
            return False

    def _show(self) -> None:
        self.show_at(QCursor.pos())

    def show_at(self, global_pos: QPoint) -> bool:
        """在指定全局坐标显示提示；被抑制/无文本/不在卡片内则不显示（可单测）。"""
        text = self.tooltip_text()
        if not text or self._suppressed:
            return False
        if not self._inside(global_pos):
            self.hide()
            return False
        try:
            QToolTip.showText(global_pos, text, self._card)
        except Exception:
            return False
        self._shown = True
        return True

    def _schedule(self) -> None:
        if (self._suppressed or not self._armed or self._shown
                or self._timer.isActive()):
            return
        self._timer.start(self._delay_ms)

    def _delayed_show(self) -> None:
        if self._suppressed or not self._armed or self._shown:
            return
        if not self._inside(QCursor.pos()):
            self.hide()
            return
        self._show()

    # ------------------------------------------------------------- filter
    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        kind = event.type()
        if kind == QEvent.ToolTip:
            # 自己的文本已经显示，别让系统再弹第二个 tooltip。
            return True
        if kind == QEvent.Enter:
            if not self._suppressed:
                self._armed = True
                self._schedule()
            return False
        if kind in (QEvent.MouseMove, QEvent.HoverMove):
            # 拖拽（按住左键移动）也要收掉标题。
            try:
                dragging = bool(event.buttons() & Qt.LeftButton)
            except (AttributeError, TypeError):
                dragging = False
            if dragging:
                self.suppress()
                return False
            if self._suppressed:
                # 菜单/滚轮之后的第一帧移动：只解除抑制，本次不排显示，
                # 等「真正的一次鼠标移动」再走 Enter/延迟流程。
                self._suppressed = False
                self._armed = True
                return False
            if self._shown:
                # 卡片内移动：不重建文本、不跟随、不重复 showText，避免闪。
                return False
            if self._armed:
                self._schedule()
            return False
        if kind in (QEvent.Leave, QEvent.HoverLeave):
            # 子控件之间互相切换也会收到 Leave，只有真的离开整张卡片才收起。
            if not self._inside(QCursor.pos()):
                self.hide()
            return False
        if kind in (QEvent.MouseButtonPress, QEvent.Wheel,
                    QEvent.WindowDeactivate, QEvent.ApplicationDeactivate):
            self.suppress()
            return False
        return False


# v1.0：封面徽章固定四个角位——分P 左上、已看完 右上、时长 右下，
# 三者互不重叠（旧版已看完在左上，分P 会被挤到右上）。
BADGE_ALIGN_PARTS = Qt.AlignLeft | Qt.AlignTop
BADGE_ALIGN_FINISHED = Qt.AlignRight | Qt.AlignTop
BADGE_ALIGN_DURATION = Qt.AlignRight | Qt.AlignBottom


def cover_badges(item: WatchLaterItem) -> list[tuple[str, int]]:
    """封面徽章 ``(文案, 对齐)`` 列表；顺序 = 绘制顺序。"""
    badges: list[tuple[str, int]] = []
    if item.parts > 1:
        badges.append((f"共 {item.parts} P", BADGE_ALIGN_PARTS))
    if item.is_finished:
        badges.append(("已看完", BADGE_ALIGN_FINISHED))
    if item.duration > 0:
        badges.append((format_duration(item.duration), BADGE_ALIGN_DURATION))
    return badges


# ----------------------------------------------- v1.0 需求 11：右键菜单图标
#: 右键菜单动作 → 图标种类；顺序与菜单项一致（播放/打开/复制/移除）。
ACTION_ICON_KINDS = ("play", "external", "copy", "trash")
_ICON_LINE_WIDTH = 2.0


def action_icon_pixmap(kind: str, size: int, color: QColor, scale: int = 1) -> QPixmap:
    """在 16×16 的逻辑网格上画一个线条图标（纯 QPainter，无图片资源）。

    2px 线宽 + 抗锯齿；放大 ``scale`` 倍（2x DPR）时线宽跟着缩放，边缘不糊。
    """
    pixmap = QPixmap(max(1, size * scale), max(1, size * scale))
    pixmap.fill(Qt.transparent)
    if scale > 1:
        pixmap.setDevicePixelRatio(scale)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        unit = size / 16.0
        painter.scale(unit, unit)
        pen = QPen(color, _ICON_LINE_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if kind == "play":
            # 播放三角（描边，不填充，和其余三个线条图标观感一致）
            path = QPainterPath()
            path.moveTo(4.5, 2.5)
            path.lineTo(13.5, 8.0)
            path.lineTo(4.5, 13.5)
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "external":
            # 外链：一个方框 + 右上角指向外的箭头
            painter.drawLine(QPointF(8.5, 3.5), QPointF(12.5, 3.5))
            painter.drawLine(QPointF(12.5, 3.5), QPointF(12.5, 7.5))
            painter.drawLine(QPointF(12.5, 3.5), QPointF(6.5, 9.5))
            path = QPainterPath()
            path.moveTo(9.5, 3.5)
            path.lineTo(3.5, 3.5)
            path.lineTo(3.5, 12.5)
            path.lineTo(12.5, 12.5)
            path.lineTo(12.5, 8.5)
            painter.drawPath(path)
        elif kind == "copy":
            # 两个重叠方框
            painter.drawRoundedRect(QRectF(2.5, 2.5, 7.0, 8.0), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(6.5, 6.0, 7.0, 8.0), 1.5, 1.5)
        elif kind == "trash":
            # 垃圾桶：桶盖 + 提手 + 桶身 + 两条竖线
            painter.drawLine(QPointF(2.5, 4.5), QPointF(13.5, 4.5))
            painter.drawLine(QPointF(6.0, 4.5), QPointF(6.0, 2.5))
            painter.drawLine(QPointF(6.0, 2.5), QPointF(10.0, 2.5))
            painter.drawLine(QPointF(10.0, 2.5), QPointF(10.0, 4.5))
            painter.drawLine(QPointF(4.0, 4.5), QPointF(4.8, 13.5))
            painter.drawLine(QPointF(4.8, 13.5), QPointF(11.2, 13.5))
            painter.drawLine(QPointF(11.2, 13.5), QPointF(12.0, 4.5))
            painter.drawLine(QPointF(6.6, 7.0), QPointF(6.9, 11.5))
            painter.drawLine(QPointF(9.4, 7.0), QPointF(9.1, 11.5))
        else:  # pragma: no cover - 防御性分支
            painter.drawEllipse(QRectF(3, 3, 10, 10))
    finally:
        painter.end()
    return pixmap


def action_icon(
    kind: str, size: int = 16, color: QColor | None = None
) -> QIcon:
    """菜单动作图标；颜色默认取 ``QPalette.WindowText``，深浅主题都清楚。"""
    tint = color if color is not None and color.isValid() else _icon_color()
    icon = QIcon()
    icon.addPixmap(action_icon_pixmap(kind, size, tint, 1))
    icon.addPixmap(action_icon_pixmap(kind, size, tint, 2))
    return icon


def item_action_icons(palette: QPalette | None = None) -> list[QIcon]:
    """四个菜单动作的图标（顺序 = ``ACTION_ICON_KINDS``）。"""
    color = _icon_color(palette)
    return [action_icon(kind, 16, color) for kind in ACTION_ICON_KINDS]


def build_item_context_menu(widget) -> QMenu:
    """v1.0 需求：：列表行与网格卡共用的右键菜单。

    两个卡片类的信号与守卫完全一致（``play/open/copy/remove_requested``
    与 ``_remove_reason``），所以菜单只在这一个地方拼装；移除仍然只发信号，
    确认对话框留在页面侧，这里没有任何静默删除的入口。

    v1.0 需求 11：四个动作各配一个 QPainter 画的 16×16 线条图标（无图片资源）。

    v1.0 需求 2：**不再**设 ``WA_ShowWithoutActivating``。QMenu 是 ``Qt.Popup``
    窗口，Qt 不支持「不激活的 popup」——设了之后 Windows 上弹窗既不抢焦点也
    拿不到自己的激活态，右键松手后系统随即把「未激活的 popup」收掉，下一帧
    又重新弹一次，肉眼就是闪一下 + 残影。菜单本来就该抢焦点（点空白/Esc 才能
    正常关闭），所以这里回到默认行为。菜单的复用见
    :func:`popup_item_context_menu`。
    """
    menu = QMenu(widget)
    menu.setToolTipsVisible(True)
    icons = item_action_icons(widget.palette())
    play = menu.addAction(widget.play_button.text())
    play.setEnabled(widget.play_button.isEnabled())
    play.setIcon(icons[0])
    play.triggered.connect(widget._play)
    open_action = menu.addAction("在浏览器打开")
    open_action.setIcon(icons[1])
    open_action.triggered.connect(widget._open)
    copy = menu.addAction("复制链接")
    copy.setIcon(icons[2])
    copy.triggered.connect(widget._copy)
    menu.addSeparator()
    remove = menu.addAction("从稍后再看移除…")
    remove.setIcon(icons[3])
    reason = widget._remove_reason(widget.item)
    ready = bool(getattr(widget, "_destructive_ready", False))
    remove.setEnabled(ready and not reason)
    remove.setToolTip(reason or "从稍后再看移除…")
    remove.triggered.connect(widget._remove)
    # v1.0 需求 2：复用菜单时要能就地刷新可变状态，记下动作与配色。
    menu.wl_remove_action = remove
    menu.wl_icon_color = _icon_color(widget.palette())
    return menu


def refresh_item_context_menu(menu: QMenu, widget) -> None:
    """v1.0 需求 2：复用已有菜单前刷新「移除」可用态 / 图标配色。

    右键菜单每次重建是闪烁的根源（旧实例在 ``aboutToHide`` 里被
    ``deleteLater``，边隐藏边销毁，Windows 上那块区域要晚一帧才重画）。
    改为一张卡片只建一次菜单、每次右键复用，这里只更新会变的部分。
    """
    remove = getattr(menu, "wl_remove_action", None)
    if remove is None:  # 不是本模块建的菜单，别乱改
        return
    reason = widget._remove_reason(widget.item)
    ready = bool(getattr(widget, "_destructive_ready", False))
    remove.setEnabled(ready and not reason)
    remove.setToolTip(reason or "从稍后再看移除…")
    tint = _icon_color(widget.palette())
    if tint != getattr(menu, "wl_icon_color", None):
        menu.wl_icon_color = tint
        icons = item_action_icons(widget.palette())
        plain = [action for action in menu.actions() if not action.isSeparator()]
        for action, icon in zip(plain, icons):
            action.setIcon(icon)


def popup_item_context_menu(widget, position: QPoint) -> QMenu:
    """v1.0 需求 2：一次右键只建/只弹一个菜单。

    * 首次右键建菜单，之后全程复用同一个实例（不再每次 ``new QMenu``）；
    * 绝不在 ``aboutToHide`` 里销毁 —— 边隐藏边析构会让弹出层与下面的
      列表各画一帧，正好是用户看到的「闪一下」；
    * 菜单已可见时直接返回，不会叠着弹第二个。
    """
    menu = getattr(widget, "_item_menu", None)
    if menu is None:
        menu = build_item_context_menu(widget)
        widget._item_menu = menu
    else:
        refresh_item_context_menu(menu, widget)
    if not menu.isVisible():
        menu.popup(widget.mapToGlobal(position))
    return menu


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
        self.setToolTip(text)
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
    """A single-line title label that opens the video on a left click."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._double_release = False

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


class CoverWidget(QWidget):
    """16:9 cover with duration, part and progress overlays.

    单击即播放；双击后半程只通知页面关闭，避免重复派发播放。
    """

    clicked = Signal()
    double_clicked = Signal()

    # v1.0：224x126 是精确整数 16:9（224/126 == 16/9），相对 980px 默认窗口
    # 更醒目；旧的 168x94 只有 1.787:1，会被 paintEvent 居中裁掉边缘。
    COVER_WIDTH = 224
    COVER_HEIGHT = 126
    #: 悬停提示里的操作说明（标题/UP 主由 item_tooltip 拼在前面）。
    DEFAULT_HINT = "点击在浏览器打开；拖拽到画面墙格子直接播放"

    def __init__(self, parent: QWidget | None = None,
                 flexible: bool = False) -> None:
        super().__init__(parent)
        self._item: WatchLaterItem | None = None
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._scaled_key: tuple[int, int, int] | None = None
        self._render_cache: QPixmap | None = None
        self._render_key = None
        self._last_click = 0.0
        self._press_pos: QPoint | None = None
        self._dragged = False
        self._double = False
        self._click_delay_ms = 0
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._emit_click_now)
        self._flexible = bool(flexible)
        self._hint = self.DEFAULT_HINT
        if self._flexible:
            # 网格卡片：宽度随列宽伸缩，高度按 16:9 由 heightForWidth 决定。
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self.setMinimumSize(80, 45)
        else:
            self.setFixedSize(self.COVER_WIDTH, self.COVER_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("视频封面")
        self.setToolTip(self._hint)

    def hasHeightForWidth(self) -> bool:  # type: ignore[override]
        return self._flexible

    def heightForWidth(self, width: int) -> int:  # type: ignore[override]
        if self._flexible:
            return max(45, int(round(width * 9 / 16)))
        return self.COVER_HEIGHT

    def set_item(self, item: WatchLaterItem, pixmap: QPixmap | None) -> None:
        changed = item is not self._item
        self._item = item
        self.set_pixmap(pixmap)
        # v1.0：封面也可能把标题藏在省略号后面，悬停给出完整标题 + UP 主。
        self.setToolTip(item_tooltip(item, self._hint))
        if changed:
            self.setAccessibleName(item.title)
            self.update()

    def set_hint(self, hint: str) -> None:
        """换掉操作提示（网格/列表的拖拽说明不同），保留标题与 UP 主。"""
        self._hint = str(hint or "")
        if self._item is not None:
            self.setToolTip(item_tooltip(self._item, self._hint))
        else:
            self.setToolTip(self._hint)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        old_key = self._pixmap.cacheKey() if self._pixmap is not None else None
        new_key = pixmap.cacheKey() if pixmap is not None else None
        if old_key == new_key:
            return
        self._pixmap = pixmap
        self._invalidate_scaled()
        self.update()

    def _invalidate_scaled(self) -> None:
        """Drop the cached scaled cover when source pixels or size change."""
        self._scaled = None
        self._scaled_key = None
        self._render_cache = None
        self._render_key = None

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._invalidate_scaled()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(self.COVER_WIDTH, self.COVER_HEIGHT)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        start = self._press_pos
        if (
            start is not None
            and (event.buttons() & Qt.LeftButton)
            and self._item is not None
            and (event.position().toPoint() - start).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._press_pos = None
            self._dragged = True
            _start_vod_drag(self, self._item, self._pixmap)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._press_pos = None
        if self._dragged:
            self._dragged = False
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            if self._double:
                # 这次 release 是双击的后半程：已经发过 double_clicked。
                self._double = False
                event.accept()
                return
            self._emit_click()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            # 双击 = 播放到格子（列表/网格统一）。取消还在排队的单击，
            # 否则列表封面双击会先开浏览器再播放。
            self._click_timer.stop()
            self._double = True
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def click_delay(self) -> int:
        return self._click_delay_ms

    def set_click_delay(self, msec: int) -> None:
        """单击延迟多少毫秒再发（有双击语义的地方用双击区间）。

        默认 0 = 松手立刻发（网格封面「单击即播放」保持零延迟）；列表卡片
        设成 ``QApplication.doubleClickInterval()``，这样双击封面时第一次
        松手不会先打开浏览器。
        """
        self._click_delay_ms = max(0, int(msec))

    def _emit_click(self) -> None:
        if self._click_delay_ms > 0:
            self._click_timer.start(self._click_delay_ms)
            return
        self._emit_click_now()

    def _emit_click_now(self) -> None:
        now = time.monotonic()
        if now - self._last_click < 0.4:
            return
        self._last_click = now
        self.clicked.emit()

    def _composited_cover(self) -> QPixmap:
        item = self._item
        data = (tuple(cover_badges(item)), item.duration, item.progress, item.is_finished) if item else None
        palette = self.palette()
        key = (self._pixmap.cacheKey() if self._pixmap is not None else None,
               self.width(), self.height(), self.devicePixelRatioF(), data, self.font().toString(),
               palette.color(QPalette.Mid).rgba(), palette.color(QPalette.PlaceholderText).rgba())
        if self._render_cache is not None and key == self._render_key:
            return self._render_cache
        dpr = self.devicePixelRatioF()
        layer = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        try:
            self._paint_content(painter)
        finally:
            painter.end()
        self._render_cache, self._render_key = layer, key
        return layer

    def paintEvent(self, event) -> None:
        layer = self._composited_cover()
        painter = QPainter(self)
        try:
            painter.drawPixmap(0, 0, layer)
        finally:
            painter.end()

    def _paint_content(self, painter) -> None:
        painter.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setClipPath(path)

        if self._pixmap is not None and not self._pixmap.isNull():
            key = (
                self._pixmap.cacheKey(),
                self.width(),
                self.height(),
            )
            if self._scaled is None or self._scaled_key != key:
                self._scaled = self._pixmap.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._scaled_key = key
            source = self._scaled
            left = max(0, (source.width() - self.width()) // 2)
            top = max(0, (source.height() - self.height()) // 2)
            painter.drawPixmap(
                self.rect(),
                source,
                QRect(left, top, self.width(), self.height()),
            )
        else:
            palette = self.palette()
            background = QColor(palette.color(QPalette.Mid))
            background.setAlpha(70)
            painter.fillRect(self.rect(), background)
            painter.setPen(palette.color(QPalette.PlaceholderText))
            painter.drawText(
                self.rect(), Qt.AlignCenter, "封面"
            )

        item = self._item
        if item is not None:
            # v1.0：分P 左上 / 已看完 右上 / 时长 右下，三处角位互不重叠。
            for text, alignment in cover_badges(item):
                bottom = bool(alignment & Qt.AlignBottom)
                self._draw_badge(
                    painter,
                    text,
                    alignment,
                    6,
                    8 if bottom else 6,
                )
            # v1.0 需求：：封面底边常驻小蓝线显示历史播放进度（B 站客户端同款）。
            if item.duration > 0:
                ratio = (1.0 if item.is_finished else
                         min(1.0, max(0.0, item.progress / float(item.duration))))
                track = QRectF(0, self.height() - 3, self.width(), 3)
                painter.fillRect(track, QColor(0, 0, 0, 110))
                if ratio > 0:
                    painter.fillRect(
                        QRectF(0, self.height() - 3, self.width() * ratio, 3),
                        QColor(0, 161, 214, 235),
                    )

    def _draw_badge(
        self,
        painter: QPainter,
        text: str,
        alignment: int,
        margin_x: int,
        margin_y: int,
    ) -> None:
        font = relative_font(self.font(), 0.78)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 10
        height = metrics.height() + 2
        if alignment & Qt.AlignRight:
            x = self.width() - width - margin_x
        else:
            x = margin_x
        if alignment & Qt.AlignBottom:
            y = self.height() - height - margin_y
        else:
            y = margin_y
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 175))
        painter.drawRoundedRect(QRectF(x, y, width, height), 4, 4)
        painter.setPen(QColor(255, 255, 255, 235))
        painter.drawText(
            QRectF(x, y, width, height), Qt.AlignCenter, text
        )


class RowCard(ThumbnailSurface):
    """One focusable list row.  No child holds a network reply."""

    selection_requested = Signal(object)
    open_requested = Signal(object)
    copy_requested = Signal(object)
    remove_requested = Signal(object)
    play_requested = Signal(object)   # v1.0：播放到格子（需求 6/7）
    selection_toggled = Signal(object)  # v1.0 需求 10：多选模式下切换选中
    # v1.0 需求 4：双击 = 播放到格子 + 关闭窗口（选片模式除外，由页面裁决）。
    double_play_requested = Signal(object)

    def __init__(
        self,
        item: WatchLaterItem,
        cover_provider: Callable[[str], QPixmap | None],
        remove_reason: Callable[[WatchLaterItem], str],
        parent: QWidget | None = None,
        *,
        lazy_cover: bool = False,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self._cover_provider = cover_provider
        self._lazy_cover = lazy_cover
        self._remove_reason = remove_reason
        self._hover = False
        self._press_pos: QPoint | None = None
        self._dragged = False
        # v1.0 需求 10：多选快速删除态（由页面统一开关）。
        self._multi_active = False
        self._multi_selected = False
        self.setAttribute(Qt.WA_Hover, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(item.title)
        self.setAccessibleDescription(
            f"{item.owner_name}，{format_duration(item.duration)}"
        )
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 12, 9)
        # v1.0 需求：：封面与文字区间隙 8–10px（原来 12px 偏松）。
        layout.setSpacing(8)

        self.cover = CoverWidget(self)
        layout.addWidget(self.cover, 0, Qt.AlignTop)
        self.cover.clicked.connect(self._select)
        self.cover.set_hint("点击选中缩略图；使用置入／替换按钮操作")
        self.cover.double_clicked.connect(self._double_close)

        # v1.0 需求：：文字块与封面上下贴齐 —— 标题顶边 = 封面上边、
        # 进度行底边 = 封面下边。info 布局高度就等于行内容高度（封面 126），
        # 中间的空隙全部给 stretch，不再堆在顶部或底部。
        info = QVBoxLayout()
        self.info_layout = info
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)

        self.title_label = ClickableLabel(self)
        self.title_label.set_full_text(item.title)
        self.title_label.setFont(relative_font(self.font(), 1.08, bold=True))
        self.title_label.clicked.connect(self._select)
        self.title_label.double_clicked.connect(self._double_close)
        info.addWidget(self.title_label)

        self.meta_label = ElidedLabel(self)
        self.meta_label.setForegroundRole(QPalette.PlaceholderText)
        self.meta_label.setFont(relative_font(self.font(), 0.92))
        self.meta_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.meta_label.set_full_text(self._meta_text(item))
        info.addWidget(self.meta_label)
        # v1.0 需求：：多余高度全部给这段 stretch —— 标题/UP主贴住封面
        # 上边，进度行被压到封面下边，两端的空白都收进这一处，不再在行底
        # 留一大块空白。
        info.addStretch(1)

        self.progress_label = ElidedLabel(self)
        self.progress_label.setForegroundRole(QPalette.PlaceholderText)
        self.progress_label.setFont(relative_font(self.font(), 0.92))
        self.progress_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text = self._progress_line_text(item)
        self.progress_label.set_full_text(text)
        self.progress_label.setVisible(bool(text))
        info.addWidget(self.progress_label)
        self._sync_tooltips()

        layout.addLayout(info, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        # 放入目标格使用独立按钮；封面只选中，拖拽仍可直接置入。
        self.play_button = ActionButton("置入视频", self)
        self.play_button.setAutoDefault(False)
        self.play_button.setDefault(False)
        self.play_button.setCursor(Qt.PointingHandCursor)
        self.play_button.setToolTip("将视频置入或替换到选定的目标格")
        self.play_button.setAccessibleName("置入或替换视频")
        self.play_button.clicked.connect(self._play)
        actions.addWidget(self.play_button)

        self.open_button = browser_button(self)
        self.open_button.clicked.connect(self._open)
        actions.addWidget(self.open_button)

        self.more_button = QToolButton(self)
        self.more_button.setText("更多")
        self.more_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        self.more_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.more_button.setCursor(Qt.PointingHandCursor)
        self.more_button.setToolTip("更多操作")
        self.more_button.setAccessibleName("更多操作")
        self.menu = QMenu(self.more_button)
        self.menu.setToolTipsVisible(True)
        self.copy_action = self.menu.addAction("复制链接")
        self.remove_action = self.menu.addAction("从稍后再看移除…")
        self.copy_action.triggered.connect(self._copy)
        self.remove_action.triggered.connect(self._remove)
        self.more_button.setMenu(self.menu)
        actions.addWidget(self.more_button)

        layout.addLayout(actions, 0)
        self.set_remove_available(True)
        self._sync_cover_item()
        # v1.0 需求 8：卡片任意位置悬停都主动弹出完整标题 + UP 主。
        self._title_tip = HoverTitleTip(self, lambda: item_tooltip(self.item))
        self._title_tip.watch(
            self,
            self.cover,
            self.title_label,
            self.meta_label,
            self.progress_label,
        )

    @staticmethod
    def _progress_line_text(item: WatchLaterItem) -> str:
        # v1.0 需求：：已看完由封面「已看完」徽章 + 满格蓝线表达，信息区不再
        # 重复第三行文字，避免大字号/高 DPI 下与标题/ meta 行堆叠。
        if item.is_finished:
            return ""
        return item.progress_text

    @staticmethod
    def _meta_text(item: WatchLaterItem) -> str:
        parts = [item.owner_name]
        if item.duration > 0:
            parts.append(format_duration(item.duration))
        added = format_added(item.add_at)
        if added:
            parts.append(added)
        # v1.0 需求：：播放量（未知时不显示），如「12.3万播放」。
        views = format_views(item.view_count)
        if views:
            parts.append(views)
        # v1.0 需求：：弹幕数未知（None）时不显示。
        if item.danmaku_count is not None:
            parts.append(format_count(item.danmaku_count) + "弹幕")
        return " · ".join(parts)

    def _sync_tooltips(self) -> None:
        """v1.0 需求：：标题被省略号截断时，悬停给出完整标题 + UP 主。

        只覆盖行/标题这两处（meta 行自己的 tooltip 就是 meta 全文，不能换）。
        """
        tip = item_tooltip(self.item)
        self.setToolTip(tip)
        self.title_label.setToolTip(tip)

    def set_danmaku_count(self, count: int | None) -> None:
        """弹幕数懒加载到位后只刷新 meta 行，不重建卡片。"""
        try:
            value = None if count is None else max(0, int(count))
        except (TypeError, ValueError):
            return
        self.item.danmaku_count = value
        text = self._meta_text(self.item)
        if self.meta_label.full_text() != text:
            self.meta_label.set_full_text(text)

    def set_view_count(self, count: int | None) -> None:
        """v1.0 需求：：播放量到位后只刷新 meta 行。"""
        try:
            value = None if count is None else max(0, int(count))
        except (TypeError, ValueError):
            return
        self.item.view_count = value
        text = self._meta_text(self.item)
        if self.meta_label.full_text() != text:
            self.meta_label.set_full_text(text)

    def update_item(self, item: WatchLaterItem) -> None:
        """Re-point an existing row at fresh data without rebuilding it.

        Skips the work entirely when the row already shows this exact item:
        a re-render triggered by a cover delivery or a status flash must not
        re-elide 673 titles.
        """
        if item is self.item:
            return
        self.item = item
        self.setAccessibleName(item.title)
        self.setAccessibleDescription(
            f"{item.owner_name}，{format_duration(item.duration)}"
        )
        self.title_label.set_full_text(item.title)
        self.meta_label.set_full_text(self._meta_text(item))
        text = self._progress_line_text(item)
        self.progress_label.set_full_text(text)
        self.progress_label.setVisible(bool(text))
        self._sync_tooltips()
        self._sync_cover_item()

    def _sync_cover_item(self) -> None:
        if not self._lazy_cover:
            self.refresh_cover()
            return
        old = self.cover._item
        pixmap = (self.cover._pixmap if old is not None
                  and old.cover_url == self.item.cover_url else None)
        self.cover.set_item(self.item, pixmap)

    def refresh_cover(self, provider=None) -> None:
        pixmap = (provider or self._cover_provider)(self.item.cover_url)
        self.cover.set_item(self.item, pixmap)

    def set_remove_available(self, destructive_ready: bool) -> None:
        self._destructive_ready = bool(destructive_ready)
        reason = self._remove_reason(self.item)
        self.remove_action.setEnabled(self._destructive_ready and not reason)
        if reason:
            self.remove_action.setToolTip(reason)
        else:
            self.remove_action.setToolTip("从稍后再看移除…")
        self.remove_action.setStatusTip(reason or "")

    def set_multi_mode(self, active: bool, selected: bool = False) -> None:
        """v1.0 需求 10：开关多选态 / 刷新选中高亮（不重建卡片）。"""
        active, selected = bool(active), bool(selected)
        if active == self._multi_active and selected == self._multi_selected:
            return
        self._multi_active = active
        self._multi_selected = selected
        self.update()

    def _context_menu(self) -> QMenu:
        """右键菜单（与行内「更多」共用同一组动作）。"""
        return build_item_context_menu(self)

    def _show_menu(self, position) -> None:
        # v1.0 需求 8：菜单打开前先收掉悬浮标题并压住，别在菜单旁冒标题。
        self._title_tip.suppress()
        # v1.0 需求 2：复用同一个菜单实例，避免「建 → 弹 → 隐藏时销毁」闪烁。
        self._menu = popup_item_context_menu(self, position)

    def _open(self) -> None:
        # v1.0 需求 2：多选模式下单击封面/标题 = 切换选中，绝不打开浏览器。
        if self._multi_active:
            self.selection_toggled.emit(self.item)
            return
        self.open_requested.emit(self.item)

    def _select(self) -> None:
        if self._multi_active:
            self.selection_toggled.emit(self.item)
        else:
            self.selection_requested.emit(self.item)

    def _play(self) -> None:
        # v1.0 需求 2：多选模式下点播放 = 切换选中，绝不播放。
        if self._multi_active:
            self.selection_toggled.emit(self.item)
            return
        self.play_requested.emit(self.item)

    def _double_play(self) -> None:
        self._select()

    def _double_close(self) -> None:
        pass

    def _copy(self) -> None:
        self.copy_requested.emit(self.item)

    def _remove(self) -> None:
        self.remove_requested.emit(self.item)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._select()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            # v1.0 需求 2：多选模式下第一次按下已切换过选中，双击后半程
            # 吞掉，不二次切换（单击/双击都只净切一次）。
            if self._multi_active:
                event.accept()
                return
            # 双击也只选择缩略图。
            self._double_play()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # v1.0 需求 10：多选模式下左键点卡片 = 切换选中，不走拖拽/播放。
        if self._multi_active and event.button() == Qt.LeftButton:
            self.selection_toggled.emit(self.item)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        start = self._press_pos
        if (
            start is not None
            and (event.buttons() & Qt.LeftButton)
            and (event.position().toPoint() - start).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._press_pos = None
            self._dragged = True
            _start_vod_drag(self, self.item, self.cover._pixmap)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)


#: 网格封面最小高度（低于它就完全看不出画面了）。
GRID_COVER_MIN_HEIGHT = 45


def fit_grid_cover(
    cell_width: int,
    cell_height: int,
    *,
    margin_x: int,
    overhead: int,
    min_height: int = GRID_COVER_MIN_HEIGHT,
) -> tuple[int, int]:
    """v1.0 需求 6：网格封面**永远占满列宽**，与下方标题/UP 主严格同宽。

    ``overhead`` 是卡片里除封面以外的固定高度（上下边距 + 标题 + meta +
    布局间隙）。高度够就按宽度推 16:9；高度不足时 **绝不缩宽度**，只把高度
    夹到可用值 —— 封面 widget 变成「宽扁」矩形，由
    :class:`CoverWidget` 的 ``paintEvent`` 用
    ``Qt.KeepAspectRatioByExpanding`` 把 16:9 图等比放大后居中裁切，视觉上
    依旧铺满整张卡片宽度，左右不再留白。

    v1.0 版在高度不足时按高度反算宽度（``cover_w = cover_h*16/9``），于是
    封面比文字窄一截、左右留白，正是本次要修的 bug。
    """
    min_height = max(1, int(min_height))
    available_width = max(1, int(cell_width) - max(0, int(margin_x)))
    available_height = max(min_height, int(cell_height) - max(0, int(overhead)))
    cover_w = available_width
    cover_h = max(min_height, cover_w * 9 // 16)
    if cover_h > available_height:
        # 只夹高度，宽度保持占满（裁切交给 CoverWidget.paintEvent）。
        cover_h = available_height
    return cover_w, cover_h


class GridCard(ThumbnailSurface):
    """带 16:9 大封面的自适应网格视频卡。

    封面随列宽 16:9 伸缩，底边蓝线进度 + 时长/分P/已看完徽章由 CoverWidget
    绘制；标题单行省略、UP 主一行。单击封面=播放到格子，双击卡片=播放，
    右键=播放/打开/复制/移除；封面与卡片均可拖拽进画面墙格子。
    信号与 RowCard 完全一致，页面可统一接线。
    """

    selection_requested = Signal(object)
    open_requested = Signal(object)
    copy_requested = Signal(object)
    remove_requested = Signal(object)
    play_requested = Signal(object)
    selection_toggled = Signal(object)  # v1.0 需求 10：多选模式下切换选中
    # v1.0 需求 4：双击 = 播放到格子 + 关闭窗口（选片模式除外，由页面裁决）。
    double_play_requested = Signal(object)

    def __init__(
        self,
        item: WatchLaterItem,
        cover_provider: Callable[[str], QPixmap | None],
        remove_reason: Callable[[WatchLaterItem], str],
        parent: QWidget | None = None,
        *,
        lazy_cover: bool = False,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self._cover_provider = cover_provider
        self._lazy_cover = lazy_cover
        self._remove_reason = remove_reason
        self._hover = False
        self._press_pos: QPoint | None = None
        self._dragged = False
        # v1.0 需求 10：多选快速删除态（由页面统一开关）。
        self._multi_active = False
        self._multi_selected = False
        self.setAttribute(Qt.WA_Hover, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(item.title)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.cover = CoverWidget(self, flexible=True)
        self.cover.set_hint("点击选中缩略图；使用置入／替换按钮操作")
        self.cover.clicked.connect(self._select)
        # 双击的后半程只吞掉，保留第一次单击的选中状态。
        self.cover.double_clicked.connect(self._double_close)
        # v1.0 需求 7：不加 AlignHCenter —— 要让封面横向占满内容宽（与标题
        # 同宽），否则 Expanding 策略只在 sizeHint 宽度内居中。
        layout.addWidget(self.cover, 0)

        self.title_label = ClickableLabel(self)
        self.title_label.set_full_text(item.title)
        self.title_label.setFont(relative_font(self.font(), 1.0, bold=True))
        self.title_label.clicked.connect(self._select)
        self.title_label.double_clicked.connect(self._double_close)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self.title_label, 1)
        self.open_button = browser_button(self)
        self.open_button.clicked.connect(self._open)
        title_row.addWidget(self.open_button)
        layout.addLayout(title_row)

        self.meta_label = ElidedLabel(self)
        self.meta_label.setForegroundRole(QPalette.PlaceholderText)
        self.meta_label.setFont(relative_font(self.font(), 0.9))
        self.meta_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.meta_label.set_full_text(self._meta_text(item))
        layout.addWidget(self.meta_label)
        self.play_button = ActionButton("置入视频", self)
        self.play_button.setAccessibleName("置入或替换视频")
        self.play_button.setAutoDefault(False)
        self.play_button.clicked.connect(self._play)
        layout.addWidget(self.play_button)
        layout.addStretch(1)

        self._destructive_ready = False
        self._sync_tooltips()
        self._sync_cover_item()
        # v1.0 需求 8：卡片任意位置悬停都主动弹出完整标题 + UP 主。
        self._title_tip = HoverTitleTip(self, lambda: item_tooltip(self.item))
        self._title_tip.watch(
            self, self.cover, self.title_label, self.meta_label
        )

    def fit_cell(self, width: int, height: int) -> None:
        """按列宽保留自然封面和文字高度，与下方文字同宽。

        v1.0 需求 7：旧实现按高度反算宽度，高度受限时封面比标题/UP 主窄一
        截，左边缘对不齐。改为优先用满可用宽度（16:9 由宽度推高度），只有
        整格高度真的放不下时才退回按高度反算。
        """
        layout = self.layout()
        margins = layout.contentsMargins()
        title_h = max(self.title_label.sizeHint().height(), self.open_button.height())
        meta_h = self.meta_label.sizeHint().height()
        # cover/title/meta/stretch 共 count() 项 → count()-1 个 spacing 间隙；
        # 旧写法只算 2 个间隙，固定高比布局最小高少约 2px，meta 行底部被裁。
        gaps = max(0, layout.count() - 1) * layout.spacing()
        overhead = margins.top() + margins.bottom() + title_h + meta_h + self.play_button.sizeHint().height() + gaps
        cover_w, cover_h = fit_grid_cover(
            width,
            height,
            margin_x=margins.left() + margins.right(),
            overhead=overhead,
        )
        # v1.0 需求 7：封面宽度交给布局（= 卡片内容宽 = 标题/UP 主宽度），
        # 只锁高度。整列宽度与 _fit_grid 的整数除可能有 1-4px 误差，写死宽度
        # 会让封面比文字窄一条；Expanding 策略则永远与文字严格同宽。
        self.cover.setMinimumWidth(1)
        self.cover.setFixedHeight(cover_h)
        self.cover.resize(cover_w, cover_h)
        self.title_label.setFixedHeight(title_h)
        self.meta_label.setFixedHeight(meta_h)
        self.setFixedHeight(cover_h + overhead)

    @staticmethod
    def _meta_text(item: WatchLaterItem) -> str:
        parts = [item.owner_name]
        # v1.0 需求：：播放量未知时不显示。
        views = format_views(item.view_count)
        if views:
            parts.append(views)
        if item.danmaku_count is not None:
            parts.append(format_count(item.danmaku_count) + "弹幕")
        return " · ".join(parts)

    def _sync_tooltips(self) -> None:
        """v1.0 需求：：悬停显示完整标题 + UP 主。"""
        self.setToolTip(item_tooltip(self.item))
        self.title_label.setToolTip(item_tooltip(self.item))

    def set_danmaku_count(self, count: int | None) -> None:
        try:
            value = None if count is None else max(0, int(count))
        except (TypeError, ValueError):
            return
        self.item.danmaku_count = value
        text = self._meta_text(self.item)
        if self.meta_label.full_text() != text:
            self.meta_label.set_full_text(text)

    def set_view_count(self, count: int | None) -> None:
        """v1.0 需求：：播放量到位后只刷新 meta 行。"""
        try:
            value = None if count is None else max(0, int(count))
        except (TypeError, ValueError):
            return
        self.item.view_count = value
        text = self._meta_text(self.item)
        if self.meta_label.full_text() != text:
            self.meta_label.set_full_text(text)

    def update_item(self, item: WatchLaterItem) -> None:
        if item is self.item:
            return
        self.item = item
        self.setAccessibleName(item.title)
        self.title_label.set_full_text(item.title)
        self.meta_label.set_full_text(self._meta_text(item))
        self._sync_tooltips()
        self._sync_cover_item()

    def _sync_cover_item(self) -> None:
        if not self._lazy_cover:
            self.refresh_cover()
            return
        old = self.cover._item
        pixmap = (self.cover._pixmap if old is not None
                  and old.cover_url == self.item.cover_url else None)
        self.cover.set_item(self.item, pixmap)

    def refresh_cover(self, provider=None) -> None:
        pixmap = (provider or self._cover_provider)(self.item.cover_url)
        self.cover.set_item(self.item, pixmap)

    def set_remove_available(self, destructive_ready: bool) -> None:
        # 菜单每次右键重建，这里只记状态，菜单动作按需读它。
        self._destructive_ready = bool(destructive_ready)

    def set_multi_mode(self, active: bool, selected: bool = False) -> None:
        """v1.0 需求 10：开关多选态 / 刷新选中高亮（不重建卡片）。"""
        active, selected = bool(active), bool(selected)
        if active == self._multi_active and selected == self._multi_selected:
            return
        self._multi_active = active
        self._multi_selected = selected
        self.update()

    def _open(self) -> None:
        # v1.0 需求 2：多选模式下单击标题 = 切换选中，绝不打开浏览器。
        if self._multi_active:
            self.selection_toggled.emit(self.item)
            return
        self.open_requested.emit(self.item)

    def _select(self) -> None:
        if self._multi_active:
            self.selection_toggled.emit(self.item)
        else:
            self.selection_requested.emit(self.item)

    def _play(self) -> None:
        # v1.0 需求 2：多选模式下点封面 = 切换选中，绝不播放。
        if self._multi_active:
            self.selection_toggled.emit(self.item)
            return
        self.play_requested.emit(self.item)

    def _double_play(self) -> None:
        self._select()

    def _double_close(self) -> None:
        pass

    def _copy(self) -> None:
        self.copy_requested.emit(self.item)

    def _remove(self) -> None:
        self.remove_requested.emit(self.item)

    def _context_menu(self) -> QMenu:
        return build_item_context_menu(self)

    def _show_menu(self, position) -> None:
        # v1.0 需求 8：菜单打开前先收掉悬浮标题并压住，别在菜单旁冒标题。
        self._title_tip.suppress()
        # v1.0 需求 2：同 RowCard，复用菜单实例，一次右键只弹一个。
        self._menu = popup_item_context_menu(self, position)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._select()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        self.update()
        super().focusOutEvent(event)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            # v1.0 需求 2：多选模式下第一次按下已切换过选中，双击后半程
            # 吞掉，不二次切换。
            if self._multi_active:
                event.accept()
                return
            # 双击也只选择缩略图。
            self._double_play()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # v1.0 需求 10：多选模式下左键点卡片 = 切换选中，不走拖拽/播放。
        if self._multi_active and event.button() == Qt.LeftButton:
            self.selection_toggled.emit(self.item)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        start = self._press_pos
        if (
            start is not None
            and (event.buttons() & Qt.LeftButton)
            and (event.position().toPoint() - start).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._press_pos = None
            self._dragged = True
            _start_vod_drag(self, self.item, self.cover._pixmap)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)


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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 40, 24, 40)
        layout.setSpacing(10)
        layout.addStretch(1)

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
        self.primary_button = QPushButton(self)
        self.primary_button.setAutoDefault(False)
        self.primary_button.setMinimumWidth(104)
        self.primary_button.clicked.connect(self.primary_requested)
        buttons.addWidget(self.primary_button)
        self.secondary_button = QPushButton(self)
        self.secondary_button.setAutoDefault(False)
        self.secondary_button.clicked.connect(self.secondary_requested)
        buttons.addWidget(self.secondary_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

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
