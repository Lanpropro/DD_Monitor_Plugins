"""Media cards; behavior moved intact from widgets."""
from __future__ import annotations
import time
from typing import Callable
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFontMetrics, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QSizePolicy, QVBoxLayout, QWidget
from .models import VOD_MIME, WatchLaterItem, format_added, format_count, format_duration, format_views
from .entry_icon import BrowserIcon
from .page_panels import GalleryToolButton, PlacementButton, ThumbnailSurface
from .card_presentation import UploaderAvatar, avatar_thumbnail, cover_description, draw_cover_footer, draw_finished_icon, finished_icon_rect, publication_text
from .gallery_controls import (ClickableLabel, ElidedLabel, _icon_color, relative_font)

def browser_button(parent):
    button = GalleryToolButton(parent)
    button.setIcon(QIcon(BrowserIcon()))
    button.setIconSize(QSize(18, 18))
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



BADGE_ALIGN_PARTS = Qt.AlignLeft | Qt.AlignTop

BADGE_ALIGN_DURATION = Qt.AlignRight | Qt.AlignBottom



def cover_badges(item: WatchLaterItem) -> list[tuple[str, int]]:
    """封面徽章 ``(文案, 对齐)`` 列表；顺序 = 绘制顺序。"""
    badges: list[tuple[str, int]] = []
    if item.parts > 1:
        badges.append((f"共 {item.parts} P", BADGE_ALIGN_PARTS))
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
    menu.wl_play_action = play
    menu.wl_remove_action = remove
    menu.wl_icon_color = _icon_color(widget.palette())
    return menu



def refresh_item_context_menu(menu: QMenu, widget) -> None:
    """v1.0 需求 2：复用已有菜单前刷新「移除」可用态 / 图标配色。

    右键菜单每次重建是闪烁的根源（旧实例在 ``aboutToHide`` 里被
    ``deleteLater``，边隐藏边销毁，Windows 上那块区域要晚一帧才重画）。
    改为一张卡片只建一次菜单、每次右键复用，这里只更新会变的部分。
    """
    play = getattr(menu, "wl_play_action", None)
    if play is not None:
        play.setText(widget.play_button.text())
        play.setEnabled(widget.play_button.isEnabled())
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



class CoverWidget(QWidget):
    """16:9 cover with duration, part and progress overlays.

    封面只发选择手势；放入画面墙由独立操作按钮负责。
    """

    clicked = Signal()
    double_clicked = Signal()

    # v1.0：224x126 是精确整数 16:9（224/126 == 16/9），相对 980px 默认窗口
    # 更醒目；旧的 168x94 只有 1.787:1，会被 paintEvent 居中裁掉边缘。
    COVER_WIDTH = 224
    COVER_HEIGHT = 126
    #: 操作说明供无障碍阅读，避免悬浮详情遮住预览。
    DEFAULT_HINT = "点击选中缩略图；拖拽到画面墙格子直接置入"

    def __init__(self, parent: QWidget | None = None,
                 flexible: bool = False) -> None:
        super().__init__(parent)
        self._item: WatchLaterItem | None = None
        self._pixmap: QPixmap | None = None
        self._preview_frame = None
        self._preview_status = ""
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
        self.setToolTip("")

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
        # Details remain accessible without covering the storyboard preview.
        self.setToolTip("")
        self.setAccessibleDescription(cover_description(item))
        if changed:
            self.setAccessibleName(item.title)
            self.update()

    def set_hint(self, hint: str) -> None:
        """换掉操作提示（网格/列表的拖拽说明不同），保留标题与 UP 主。"""
        self._hint = str(hint or "")
        if self._item is not None:
            self.setToolTip("")
        else:
            self.setToolTip("")

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

    def set_preview_status(self, text):
        if self._preview_status != text:
            self._preview_status = text
            self.update()

    def set_preview_frame(self, pixmap):
        self._preview_frame = pixmap
        self._invalidate_scaled()
        self.update()

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
        data = (tuple(cover_badges(item)), item.duration, item.progress, item.is_finished,
                item.view_count, item.danmaku_count) if item else None
        palette = self.palette()
        pixmap = self._preview_frame if self._preview_frame is not None else self._pixmap
        key = (pixmap.cacheKey() if pixmap is not None else None,
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
        started = time.perf_counter()
        layer = self._composited_cover()
        painter = QPainter(self)
        try:
            painter.drawPixmap(0, 0, layer)
            if self._preview_status:
                painter.setFont(relative_font(self.font(), .8))
                label = painter.fontMetrics().elidedText(self._preview_status, Qt.ElideRight, max(1, self.width()-28))
                width = painter.fontMetrics().horizontalAdvance(label) + 16
                rect = QRectF(8, 8, width, painter.fontMetrics().height()+8)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(15, 18, 22, 190))
                painter.drawRoundedRect(rect, 6, 6)
                painter.setPen(QColor('white'))
                painter.drawText(rect, Qt.AlignCenter, label)
        finally:
            painter.end()
        trace = getattr(self, '_motion_trace', None)
        if trace is not None and trace.timer.isActive():
            trace.sample('cover_paint', (time.perf_counter()-started)*1000)

    def _paint_content(self, painter) -> None:
        painter.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        radius = 12 if self._flexible else 8
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        pixmap = self._preview_frame if self._preview_frame is not None else self._pixmap
        if pixmap is not None and not pixmap.isNull():
            key = (
                pixmap.cacheKey(),
                self.width(),
                self.height(),
            )
            if self._scaled is None or self._scaled_key != key:
                self._scaled = pixmap.scaled(
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
            if self._flexible:
                draw_cover_footer(painter, rect, item, relative_font(self.font(), .82))
            for text, alignment in cover_badges(item):
                bottom = bool(alignment & Qt.AlignBottom)
                if bottom and self._flexible:
                    continue
                self._draw_badge(
                    painter,
                    text,
                    alignment,
                    6,
                    8 if bottom else 6,
                )
            if item.is_finished:
                draw_finished_icon(painter, rect)
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
        if self._item is not None and self._item.is_finished and alignment == BADGE_ALIGN_PARTS:
            available = int(finished_icon_rect(QRectF(self.rect())).left()) - margin_x - 4
            if available < 16:
                return
            width = min(width, available)
            text = metrics.elidedText(text, Qt.ElideRight, max(1, width - 10))
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
        self.cover.set_hint("点击选中缩略图；在布局缩略图中选择位置")
        self.cover.double_clicked.connect(self._double_close)

        # v1.0 需求：：文字块与封面上下贴齐 —— 标题顶边 = 封面上边、
        # 进度行底边 = 封面下边。info 布局高度就等于行内容高度（封面 126），
        # 中间的空隙全部给 stretch，不再堆在顶部或底部。
        info = QVBoxLayout()
        self.info_layout = info
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)

        self.title_label = ClickableLabel(self, lines=2)
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
        self.play_button = PlacementButton("置入视频", self)
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

        self.remove_button = GalleryToolButton(self)
        self.remove_button.setIcon(action_icon('trash', 18))
        self.remove_button.setToolTip('从稍后再看移除…')
        self.remove_button.setAccessibleName('从稍后再看移除')
        self.remove_button.clicked.connect(self._remove)
        actions.addWidget(self.remove_button)

        layout.addLayout(actions, 0)
        self.set_remove_available(True)
        self._sync_cover_item()

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
        """Keep video details out of pointer tooltips."""
        self.setToolTip("")
        self.title_label.setToolTip("")

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
        self.remove_button.setEnabled(self._destructive_ready and not reason)
        self.remove_button.setToolTip(reason or '从稍后再看移除…')

    def set_multi_mode(self, active: bool, selected: bool = False) -> None:
        """v1.0 需求 10：开关多选态 / 刷新选中高亮（不重建卡片）。"""
        active, selected = bool(active), bool(selected)
        if active == self._multi_active and selected == self._multi_selected:
            return
        self._multi_active = active
        self._multi_selected = selected
        self._sync_selection_marker()
        self.update()

    def _context_menu(self) -> QMenu:
        """右键菜单（与行内「更多」共用同一组动作）。"""
        return build_item_context_menu(self)

    def _show_menu(self, position) -> None:
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

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton and self._press_pos is not None
                and not self._dragged and not self._multi_active):
            self._press_pos = None
            self._select()
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
    绘制；标题两行省略、UP 主一行。封面和标题只选中，独立按钮置入或替换，
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
        layout.setContentsMargins(4, 4, 4, 8)
        layout.setSpacing(8)

        self.cover = CoverWidget(self, flexible=True)
        self.cover.set_hint("点击选中缩略图；在布局缩略图中选择位置")
        self.cover.clicked.connect(self._select)
        # 双击的后半程只吞掉，保留第一次单击的选中状态。
        self.cover.double_clicked.connect(self._double_close)
        # v1.0 需求 7：不加 AlignHCenter —— 要让封面横向占满内容宽（与标题
        # 同宽），否则 Expanding 策略只在 sizeHint 宽度内居中。
        layout.addWidget(self.cover, 0)

        self.title_label = ClickableLabel(self, lines=2)
        self.title_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.title_label.set_full_text(item.title)
        self.title_label.setFont(relative_font(self.font(), 1.0, bold=True))
        self.title_label.clicked.connect(self._select)
        self.title_label.double_clicked.connect(self._double_close)
        layout.addWidget(self.title_label)
        self.uploader_row = QWidget(self)
        uploader = QHBoxLayout(self.uploader_row)
        uploader.setContentsMargins(0, 0, 0, 0)
        uploader.setSpacing(9)
        self.avatar = UploaderAvatar(self.uploader_row)
        uploader.addWidget(self.avatar)
        details = QVBoxLayout()
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(2)
        self.owner_label = ElidedLabel(self)
        self.owner_label.setForegroundRole(QPalette.PlaceholderText)
        self.owner_label.setFont(relative_font(self.font(), .9))
        self.owner_label.set_full_text(item.owner_name)
        self.owner_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        details.addWidget(self.owner_label)
        self.date_label = ElidedLabel(self)
        self.date_label.setForegroundRole(QPalette.PlaceholderText)
        self.date_label.setFont(relative_font(self.font(), .78))
        self.date_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        details.addWidget(self.date_label)
        uploader.addLayout(details, 1)
        layout.addWidget(self.uploader_row)
        self._sync_uploader()

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        actions.addStretch()
        self.actions_layout = actions
        self.play_button = PlacementButton("置入视频", self)
        self.play_button.setAccessibleName("置入或替换视频")
        self.play_button.setAutoDefault(False)
        self.play_button.clicked.connect(self._play)
        actions.addWidget(self.play_button)
        self.open_button = browser_button(self)
        self.open_button.clicked.connect(self._open)
        actions.addWidget(self.open_button)
        self.remove_button = GalleryToolButton(self)
        self.remove_button.setIcon(action_icon('trash', 18))
        self.remove_button.setToolTip('从稍后再看移除…')
        self.remove_button.setAccessibleName('从稍后再看移除')
        self.remove_button.clicked.connect(self._remove)
        actions.addWidget(self.remove_button)
        uploader.addLayout(actions)
        layout.addStretch(1)

        self._destructive_ready = False
        self._sync_tooltips()
        self._sync_cover_item()

    def fit_cell(self, width: int, height: int) -> None:
        """Keep a natural 16:9 cover and one shared uploader/action row."""
        side = 20 if width < 160 else 30
        self.play_button.set_compact(width < 360, side)
        self.open_button.setFixedSize(side, side)
        self.remove_button.setFixedSize(side, side)
        self.uploader_row.layout().setSpacing(4 if width < 270 else 9)
        self.actions_layout.setSpacing(2 if width < 160 else 4)
        layout = self.layout()
        margins = layout.contentsMargins()
        title_h = self.title_label.fontMetrics().lineSpacing() * 2 + 4
        self.avatar.setVisible(width >= 210)
        meta_h = max(34, self.owner_label.sizeHint().height() + self.date_label.sizeHint().height() + 2)
        # cover/title/meta/stretch 共 count() 项 → count()-1 个 spacing 间隙；
        # 旧写法只算 2 个间隙，固定高比布局最小高少约 2px，meta 行底部被裁。
        gaps = max(0, layout.count() - 1) * layout.spacing()
        overhead = margins.top() + margins.bottom() + title_h + meta_h + gaps
        cover_w, cover_h = fit_grid_cover(
            width,
            max(height, (width - margins.left() - margins.right()) * 9 // 16 + overhead),
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
        self.uploader_row.setFixedHeight(meta_h)
        self.setFixedHeight(cover_h + overhead)

    def _sync_uploader(self):
        self.avatar_url = avatar_thumbnail(self.item.owner_face)
        self.avatar.set_author(self.item.owner_name, self.avatar_url)
        date, hint = publication_text(self.item)
        self.date_label.set_full_text(date)
        self.date_label.setToolTip(hint)

    def refresh_avatar(self, provider):
        if self.avatar_url:
            self.avatar.set_pixmap(provider(self.avatar_url))

    def _sync_tooltips(self) -> None:
        """Keep video details out of pointer tooltips."""
        self.setToolTip("")
        self.title_label.setToolTip("")

    def set_danmaku_count(self, count: int | None) -> None:
        try:
            value = None if count is None else max(0, int(count))
        except (TypeError, ValueError):
            return
        self.item.danmaku_count = value
        self.cover.setAccessibleDescription(cover_description(self.item))
        self.cover.update()

    def set_view_count(self, count: int | None) -> None:
        """Refresh the count overlay when its asynchronous value arrives."""
        try:
            value = None if count is None else max(0, int(count))
        except (TypeError, ValueError):
            return
        self.item.view_count = value
        self.cover.setAccessibleDescription(cover_description(self.item))
        self.cover.update()

    def update_item(self, item: WatchLaterItem) -> None:
        if item is self.item:
            return
        self.item = item
        self.setAccessibleName(item.title)
        self.title_label.set_full_text(item.title)
        self.owner_label.set_full_text(item.owner_name)
        self._sync_uploader()
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
        self.remove_button.setEnabled(self._destructive_ready and not reason)
        self.remove_button.setToolTip(reason or '从稍后再看移除…')

    def set_multi_mode(self, active: bool, selected: bool = False) -> None:
        """v1.0 需求 10：开关多选态 / 刷新选中高亮（不重建卡片）。"""
        active, selected = bool(active), bool(selected)
        if active == self._multi_active and selected == self._multi_selected:
            return
        self._multi_active = active
        self._multi_selected = selected
        self._sync_selection_marker()
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

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton and self._press_pos is not None
                and not self._dragged and not self._multi_active):
            self._press_pos = None
            self._select()
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
