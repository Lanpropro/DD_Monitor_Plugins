"""Watch-later list window for Bilibili (v1.0: standalone popup).

Opened by the sidebar workspace button and by the tile right-click picker as
the SAME window (user requirement 7).  Video cards can be dragged straight
onto wall tiles (VOD_MIME, requirement 6); explicit placement buttons use the
selected destination. Covers and titles only select a thumbnail.
"""
from __future__ import annotations

import ctypes
import math
import sys
import time
from typing import Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QByteArray,
    QDataStream,
    QEasingCurve,
    QEvent,
    QIODevice,
    QPoint,
    QPointF,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPalette,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ddm import theme
from ddm.ui import SegmentedControl, SelectControl, TitleLabel
from .button_feedback import StableActionButton as ActionButton
from .page_panels import BusyRefreshButton, GalleryToolButton, PlacementSelector
from .placement_picker import PlacementPicker
from .preferences import read_preferences, save_preferences
from .settings_dialog import WatchLaterSettings
from .browse_state import BrowseState
from .card_preview import CardPreview
from .page_shell import BrowserTitleBar, BrowserWindow, InlineConfirmation, background_color
from .entry_icon import GalleryIcon
from .removal_undo import RemovalUndo

from .covers import CoverLoader
from .danmaku_count import DanmakuCountProvider
from .loading_skeleton import SkeletonLoadingView
from .models import (
    FILTER_ALL,
    FILTER_OPTIONS,
    SORT_OPTIONS,
    SORT_RECENT,
    WatchLaterItem,
    home_url,
    video_url,
    watch_later_web_url,
)
from .gallery_controls import CenteredColumn, ErrorBar, ElidedLabel, StatusView, ViewModeSwitch, relative_font
from .scroll_input import MiddleDragScroller
from .media_cards import MULTISELECT_IDLE, MULTISELECT_SELECT, PAGE_CONTENT_MARGIN, PAGE_ROW_SPACING, GridCard, RowCard, action_icon, multiselect_cancel, multiselect_is_confirming, selection_toggle
from .wheel_scroll import WheelScrollAccumulator
from .list_controller import Account as _Account, ListController, CACHE_TTL_SECONDS
from .gallery_renderer import GalleryRenderer, COVER_REFETCH_DEBOUNCE_MS, _VISIBLE_ROW_MARGIN, PAGE_INITIAL_ROWS, PAGE_GROW_ROWS, PAGE_PREFETCH_PX, GRID_COLUMNS, GRID_MIN_COLUMNS, GRID_MAX_COLUMNS, GRID_TARGET_CELL_WIDTH, GRID_INITIAL_CARDS, GRID_GROW_CARDS, GRID_PREFETCH_PX, GRID_KEEP_ROWS, GRID_BUILD_BATCH, GROW_SETTLE_MS, GRID_VISIBLE_MARGIN, GRID_COVER_BUDGET, ROW_BUILD_BATCH, ROW_BUILD_YIELD_MIN, GRID_BUILD_YIELD_MIN, grid_columns_for_width, first_visible_index

# v1.0 性能：缓存 60s 太短，超过一分钟的每次重开都要重新拉列表并走
# 骨架屏；延长到 5 分钟，手动「刷新」仍可随时强制更新。
SEARCH_DEBOUNCE_MS = 200
# 原生窗口装饰会让 client geometry 比 move() 的坐标右/下偏几像素。
_FRAME_MARGIN = 4
# v1.0 需求 6：几何彻底不可用时的默认窗口尺寸（与 plugin._show_page 的
# resize(980, 660) 一致），保证居中算出来的矩形永远是有意义的大小。
DEFAULT_POPUP_WIDTH = 980
DEFAULT_POPUP_HEIGHT = 660

# v1.0：弹窗几何持久化。宿主 PluginContext 没有可用的 settings/config 通道，
# 插件目录也可能只读，所以用 QSettings，并显式指定组织/应用名，避免污染宿主
# 或其他插件的键空间。只保存窗口几何，绝不写 Cookie/凭证。
GEOMETRY_ORG = "DDMonitorCE"
GEOMETRY_APP = "bili_watchlater"
GEOMETRY_KEY = "window/geometry"
GEOMETRY_SAVE_DELAY_MS = 400
# QWidget::saveGeometry() 的 QDataStream 头：magic + major + minor + 两个 QRect
# （frameGeometry、client geometry）。只需读到第二个矩形即可恢复位置。
_GEOMETRY_MAGIC = 0x1D9D0CB
_GEOMETRY_MAJOR = 3
_GEOMETRY_MINOR = 0


def default_geometry_store() -> QSettings:
    """生产存储后端；测试可替换模块级 GEOMETRY_STORE_FACTORY。"""
    return QSettings(GEOMETRY_ORG, GEOMETRY_APP)


#: 返回任何支持 value(key) / setValue(key, value) 的对象；测试可注入假实现。
GEOMETRY_STORE_FACTORY = default_geometry_store


def clamp_geometry(rect: QRect, available: QRect, margin: int) -> QRect:
    """把 rect 夹进 available：先夹尺寸，再把 left/top 夹进可用区。

    夹取后整窗都在可用区内（距边缘至少 margin，屏幕比两倍 margin 还小时
    退化为 1px），用于拔掉显示器后把存档坐标拉回当前屏幕。
    """
    margin = max(0, int(margin))
    max_width = max(1, available.width() - 2 * margin)
    max_height = max(1, available.height() - 2 * margin)
    width = max(1, min(rect.width(), max_width))
    height = max(1, min(rect.height(), max_height))
    left = max(
        available.left() + margin,
        min(rect.left(), available.right() + 1 - margin - width),
    )
    top = max(
        available.top() + margin,
        min(rect.top(), available.bottom() + 1 - margin - height),
    )
    return QRect(left, top, width, height)


def geometry_size_ok(rect: QRect | None, minimum: QSize | None) -> bool:
    """v1.0 需求 6：尺寸是否够格当窗口用（0/1px 的退化值一律不格）。

    退化存档是「贴左下角一条」的源头：``clamp_geometry`` 会按 1px 高把 top
    算到屏幕底边，随后 ``setGeometry`` 又被 minimumSize 撑成 720×480，于是
    窗口大半截落在屏幕外。
    """
    if rect is None or not rect.isValid():
        return False
    if minimum is None:
        return True
    return rect.width() >= max(1, int(minimum.width())) and rect.height() >= max(
        1, int(minimum.height())
    )


def usable_geometry(
    rect: QRect | None,
    minimum: QSize | None,
    available_rects: list[QRect],
    margin: int,
) -> QRect | None:
    """几何能否直接用：尺寸够 + 至少 margin 见方落在某块屏幕里。"""
    if not geometry_size_ok(rect, minimum):
        return None
    if not available_rects:
        return None
    need = max(1, margin)
    for available in available_rects:
        inter = rect.intersected(available)
        if inter.isValid() and inter.width() >= need and inter.height() >= need:
            return QRect(rect)
    return None


def resolve_geometry(
    saved: QRect | None,
    available_rects: list[QRect],
    fallback: QRect,
    margin: int,
    minimum: QSize | None = None,
) -> QRect:
    """决定恢复用的矩形：优先存档，其次 fallback（居中）。

    多屏时取与存档交集最大的屏幕；存档完全落在已断开的屏幕上（交集为
    零）则取中心最近的屏幕，再统一夹取，保证窗口一定可见。

    v1.0 需求 6：``minimum`` 非空时先把存档尺寸抬到窗口最小尺寸再夹取位置，
    否则「1×1 存档」会被算到底边、再被 Qt 撑成 720×480 溢出屏幕；同时
    尺寸不足最小值的存档直接判为不可用，回退 fallback。
    """
    if saved is None or not saved.isValid() or not available_rects:
        return fallback
    if not geometry_size_ok(saved, minimum):
        return fallback
    if minimum is not None:
        # 尺寸先抬到最小尺寸，后面夹取位置时用的就是最终尺寸。
        saved = QRect(
            saved.left(),
            saved.top(),
            max(saved.width(), int(minimum.width())),
            max(saved.height(), int(minimum.height())),
        )
    best = available_rects[0]
    best_area = -1
    for available in available_rects:
        inter = saved.intersected(available)
        area = inter.width() * inter.height() if inter.isValid() else 0
        if area > best_area:
            best = available
            best_area = area
    if best_area <= 0:
        center = saved.center()
        best = min(
            available_rects,
            key=lambda available: (
                (available.center().x() - center.x()) ** 2
                + (available.center().y() - center.y()) ** 2
            ),
        )
    return clamp_geometry(saved, best, margin)


def _rect_from_geometry(value: Any) -> QRect | None:
    """把 store 里的值（QRect 或 saveGeometry() 字节）解成 QRect。"""
    if isinstance(value, QRect):
        rect = QRect(value)
        return rect if rect.isValid() else None
    if not isinstance(value, (QByteArray, bytes, bytearray, memoryview)):
        return None
    blob = QByteArray(bytes(value))
    stream = QDataStream(blob)
    stream.setVersion(QDataStream.Version.Qt_5_0)
    try:
        if stream.readUInt32() != _GEOMETRY_MAGIC:
            return None
        stream.readUInt16()  # major，仅做兼容占位
        stream.readUInt16()  # minor
        frame = QRect()
        stream >> frame
        rect = QRect()
        stream >> rect
    except Exception:
        return None
    if stream.status() != QDataStream.Status.Ok or not rect.isValid():
        return None
    return rect


def _geometry_blob(value: Any) -> QByteArray:
    """把 QRect / 字节串统一成 saveGeometry() 兼容的 QDataStream 字节。"""
    if isinstance(value, QByteArray):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return QByteArray(bytes(value))
    rect = QRect(value)
    blob = QByteArray()
    stream = QDataStream(blob, QIODevice.OpenModeFlag.WriteOnly)
    stream.setVersion(QDataStream.Version.Qt_5_0)
    stream.writeUInt32(_GEOMETRY_MAGIC)
    stream.writeUInt16(_GEOMETRY_MAJOR)
    stream.writeUInt16(_GEOMETRY_MINOR)
    stream << rect << rect
    return blob


def geometry_to_store(
    store: Any, value: QRect | QByteArray | bytes, key: str = GEOMETRY_KEY
) -> None:
    """写入几何；value 可以是 saveGeometry() 的字节串或 QRect。"""
    store.setValue(key, _geometry_blob(value))


def geometry_from_store(store: Any, key: str = GEOMETRY_KEY) -> QRect | None:
    """读取几何；无存档/存档损坏时返回 None。"""
    try:
        raw = store.value(key)
    except Exception:
        return None
    if raw is None:
        return None
    return _rect_from_geometry(raw)


# v1.0 性能：入场动画改为纯淡入（本地复制，绝不 import ddm.motion）。
# 保留与宿主左侧插件快捷键一致的 bezier + 160ms；不再逐帧 setGeometry，
# 否则窗口宽度每帧变化都会让全部行的 ElidedLabel 重算省略号。
ENTRANCE_MS = 160
ENTRANCE_SCALE_FROM = 0.92
_SPI_GETCLIENTAREAANIMATION = 0x1042



def _entrance_easing() -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(0.23, 1.0), QPointF(0.32, 1.0), QPointF(1.0, 1.0)
    )
    return curve


def _system_animation_enabled() -> bool:
    """尊重 Windows 系统动画开关；非 win32 或读取失败时默认开启。"""
    if sys.platform == "win32":
        try:
            allowed = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(
                _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(allowed), 0
            ):
                return bool(allowed.value)
        except Exception:
            return True
    return True




def _render_field(name):
    return property(lambda self: getattr(self.renderer, name),
                    lambda self, value: setattr(self.renderer, name, value))


def _list_field(name):
    """Legacy page attributes forward to the controller's single state owner."""
    return property(lambda page:getattr(page.list_controller,name),
                    lambda page,value:setattr(page.list_controller,name,value))


class WatchLaterPage(BrowserWindow):
    _rows = _render_field('_rows')
    _rows_by_key = _render_field('_rows_by_key')
    _rows_by_cover = _render_field('_rows_by_cover')
    _cards = _render_field('_cards')
    _cards_by_key = _render_field('_cards_by_key')
    _cards_by_cover = _render_field('_cards_by_cover')
    _cover_views = _render_field('_cover_views')
    _grid_columns = _render_field('_grid_columns')
    _grid_cell_size = _render_field('_grid_cell_size')
    _filter_cache = _render_field('_filter_cache')
    _scroll_refetch_connected = _render_field('_scroll_refetch_connected')
    _building_rows = _render_field('_building_rows')
    _render_pending = _render_field('_render_pending')
    _render_limit = _render_field('_render_limit')
    _render_total = _render_field('_render_total')
    _grow_pending = _render_field('_grow_pending')
    _grow_busy = _render_field('_grow_busy')
    _avatars_by_url = _render_field('_avatars_by_url')
    _danmaku = _render_field('_danmaku')
    _cover_scroll_timer = _render_field('_cover_scroll_timer')
    _grid_resize_timer = _render_field('_grid_resize_timer')
    _grow_settle_timer = _render_field('_grow_settle_timer')

    _items = _list_field('items')
    _items_version = _list_field('items_version')
    _cookies = _list_field('cookies')
    _has_session = _list_field('has_session')
    _has_csrf = _list_field('has_csrf')
    _generation = _list_field('generation')
    _loaded_once = _list_field('loaded_once')
    _loaded_at = _list_field('loaded_at')
    _last_update_epoch = _list_field('last_update_epoch')
    _request_seq = _list_field('request_seq')
    _operation_seq = _list_field('operation_seq')
    _active_list = _list_field('active_list')
    _active_operation = _list_field('active_operation')
    _busy_operation = _list_field('busy_operation')
    _pending_remove = _list_field('pending_remove')
    _requests = _list_field('requests')
    _multi_remove_queue = _list_field('remove_queue')
    _shutting_down = _list_field('closing')

    closed = Signal()
    preferences_changed = Signal(object)
    items_changed = Signal(object)
    #: v1.0 需求：：选片模式的取消出口。v1.0 删掉了横幅上的「退出选片」
    #: 按钮（需求：），页面侧不再主动 emit；信号本身必须保留——plugin.py
    #: 仍连接它，删掉会让插件在挂接页面时 AttributeError。
    pick_cancelled = Signal()

    def __init__(
        self,
        context: Any,
        parent: QWidget | None = None,
        wall: Any = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.list_controller = ListController(self,context)
        self.preferences = read_preferences(context)
        self._settings_dialog = None
        self._settings_panel_cache = None
        self._placement_picker = None
        self._layout_provider = None
        self.wall = wall
        self._visible = False
        self._selected_key = None
        self._placement_handler = None
        # v1.0 需求 5：打开稍后再看窗口优先以网格模式排列视频。
        self._view_mode = self.preferences["default_view"]
        self.renderer = GalleryRenderer(self)
        self._destructive_ready: bool | None = None
        self._login_expired = False
        self._last_open_at = 0.0
        self._last_open_url = ""
        # v1.0：弹窗「播放」回调与选片目标横幅（右键选片与侧栏同一窗口）。
        self._play_handler = None
        self._pick_label: str | None = None
        # v1.0 需求 10/11：多选快速删除状态机 + 快速选片入口。
        self._multiselect_state = MULTISELECT_IDLE
        self._multi_selected_keys: set[str] = set()
        self._quick_pick_active = False
        # v1.0 需求 3：窗口不允许最小化（去掉最小化按钮 + 拒绝最小化状态）。
        self._window_flag_guard = False
        self._minimize_guard = False

        self.covers = CoverLoader(self)
        self.covers.image_ready.connect(self._cover_ready)
        self.covers.diagnostic.connect(self._log)


        # v1.0 需求：：宿主 _show_page 只 resize 不定位，弹窗会贴屏幕左上角；
        # 给出下限尺寸，showEvent 里再居中并夹取到可用屏幕内。
        self.setMinimumSize(720, 480)

        # v1.0 需求：：入场缩放动画（仅窗口模式、系统动画开启时运行）。
        self._entrance_target = QRect()
        self._entrance_applying = False
        self._entrance_anim = QVariantAnimation(self)
        self._entrance_anim.setDuration(ENTRANCE_MS)
        self._entrance_anim.setEasingCurve(_entrance_easing())
        self._entrance_anim.setStartValue(0.0)
        self._entrance_anim.setEndValue(1.0)
        self._entrance_anim.valueChanged.connect(self._entrance_frame)
        self._entrance_anim.finished.connect(self._entrance_finished)

        # v1.0：窗口位置记忆。_geometry_settings 懒创建（首次 show/hide 时），
        # 测试可直接赋值一个假 store 覆盖。
        self._geometry_settings: Any = None
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(GEOMETRY_SAVE_DELAY_MS)
        self._geometry_save_timer.timeout.connect(self._save_geometry_now)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(
            lambda: self._render(reset_scroll=True)
        )
        # Debounced "visible covers" refill.  CoverLoader drops URLs when its
        # pending queue is full, so rows far down the list need their get()
        # retried once the user scrolls them into view and the queue drains.
        self._cover_scroll_timer = QTimer(self)
        self._cover_scroll_timer.setSingleShot(True)
        self._cover_scroll_timer.setInterval(COVER_REFETCH_DEBOUNCE_MS)
        self._cover_scroll_timer.timeout.connect(
            self._refetch_visible_covers
        )

        self._grid_resize_timer = QTimer(self)
        self._grid_resize_timer.setSingleShot(True)
        self._grid_resize_timer.setInterval(100)
        self._grid_resize_timer.timeout.connect(self._fit_grid)

        self._grow_settle_timer = QTimer(self)
        self._grow_settle_timer.setSingleShot(True)
        self._grow_settle_timer.timeout.connect(self._grow_page)

        self._build_ui()
        self.browse_state = BrowseState(self)
        self.card_preview = CardPreview(self)
        self.removal_undo = RemovalUndo(self)
        from .motion_trace import MotionTrace
        self.motion_trace = MotionTrace(self)
        self.external_cover_urls = lambda: []
        self._refresh_multi_button()
        self._connect_scroll_refetch()
        self._show_loading()
        self._set_refreshing(False)
        self._update_destructive_controls()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(0)
        self.window_header = BrowserTitleBar(self)
        self.window_header.back_requested.connect(self._back_to_browser)
        outer.addWidget(self.window_header)
        self.navigation = QStackedWidget(self)
        outer.addWidget(self.navigation, 1)
        self.browser_view = QWidget(self.navigation)
        self.navigation.addWidget(self.browser_view)
        self._inline_panel = None
        root = QVBoxLayout(self.browser_view)
        # 页面底部为固定操作行，不遮挡缩略图。
        root.setContentsMargins(20, 12, 20, 10)
        root.setSpacing(0)
        self.column = CenteredColumn(self)
        root.addWidget(self.column)
        body = self.column.body_layout
        body.setSpacing(16)
        self.setObjectName("WatchLaterPage")
        self.setAutoFillBackground(False)
        theme.signals().changed.connect(self._sync_browser_palette)
        self._sync_browser_palette()

        header = QHBoxLayout()
        self.header_layout = header  # v1.0 需求 12：便于对齐回归断言
        # v1.0 需求 12：头部按钮与网格卡片内容共用同一套左右边距/间距。
        header.setContentsMargins(
            PAGE_CONTENT_MARGIN, 0, PAGE_CONTENT_MARGIN, 0
        )
        header.setSpacing(PAGE_ROW_SPACING)
        self.title_label = TitleLabel("稍后再看", self)
        self.title_label.setTextFormat(Qt.PlainText)
        identity = QVBoxLayout()
        identity.setSpacing(5)
        identity.addWidget(self.title_label)

        self.count_label = QLabel("", self)
        self.count_label.setTextFormat(Qt.PlainText)
        self.count_label.setForegroundRole(QPalette.PlaceholderText)
        self.count_label.setMinimumWidth(0)
        self.count_label.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Preferred
        )
        identity.addWidget(self.count_label)
        header.addLayout(identity, 1)

        self.status_label = ElidedLabel(self)
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setForegroundRole(QPalette.PlaceholderText)
        self.status_label.hide()

        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.setToolTip("重新读取稍后再看")
        self.refresh_button.setAccessibleName("刷新稍后再看")
        self.refresh_button.clicked.connect(
            lambda: self._load(manual=True)
        )
        # 保留旧调用入口，界面只显示右下角刷新，避免重复操作入口。
        self.refresh_button.hide()

        # v1.0 需求：：列表/网格切换从筛选行挪到标题行右侧，改成两个
        # checkable 图标按钮（QPainter 画的三横线 / 2×2 方块，无图片资源）。
        # v1.0 需求：：右上角「更多」菜单整体删除，这里只留视图切换。
        # v1.0 需求 11：加回「快速选片」入口（v1.0 删「更多」菜单时丢掉）。
        # 点击进选片模式：显示目标格横幅，点视频即投放到格子 —— 只接 page
        # 已有的 set_pick_target / 播放回调，不改 plugin.py 的接口与信号。
        self.quick_pick_button = QPushButton("快速选片", self)
        self.quick_pick_button.setAutoDefault(False)
        self.quick_pick_button.setCheckable(True)
        self.quick_pick_button.setCursor(Qt.PointingHandCursor)
        self.quick_pick_button.setIcon(action_icon("play", 16))
        self.quick_pick_button.setToolTip("选择稍后再看视频…（进入选片模式）")
        self.quick_pick_button.setAccessibleName("快速选片")
        self.quick_pick_button.clicked.connect(self._on_quick_pick_clicked)
        self.quick_pick_button.hide()

        self.view_switch = ViewModeSwitch(self, self._view_mode)
        self.view_switch.mode_changed.connect(self._on_view_mode_changed)
        body.addLayout(header)

        # v1.0 选片模式提示：右键「选择稍后再看视频…」进入时显示目标格。
        # v1.0 需求：：删除顶部整条蓝 bar —— 改为内联进 header 的一行普通
        # 提示文字 + 退出按钮（无蓝色背景/边框，不再是一条横幅）。
        self.pick_banner = QWidget(self)
        banner_layout = QHBoxLayout(self.pick_banner)
        banner_layout.setContentsMargins(0, 0, 0, 0)
        banner_layout.setSpacing(6)
        self.pick_banner.setMaximumWidth(260)
        self.pick_banner.setMinimumWidth(0)
        self.pick_banner.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.pick_banner_label = ElidedLabel(self.pick_banner)
        self.pick_banner_label.setTextFormat(Qt.PlainText)
        self.pick_banner_label.setWordWrap(False)
        # v1.0：横幅只剩这行文字（「退出选片」按钮已删）。ElidedLabel 默认
        # 用 Ignored 水平策略，独占一行时会塌成 0 宽；这里恢复 Preferred，
        # 由 pick_banner 的 260px 上限来截断。
        self.pick_banner_label.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Preferred
        )
        self.pick_banner_label.setObjectName("watchlaterPickBanner")
        self.pick_banner_label.setForegroundRole(QPalette.PlaceholderText)
        self.pick_banner_label.setStyleSheet(
            "QLabel#watchlaterPickBanner {"
            " background: transparent; border: none; padding: 0; }"
        )
        banner_layout.addWidget(self.pick_banner_label, 1)
        # v1.0 需求：：删除「退出选片」按钮，横幅只剩「播放至 第 N 格」文字。
        # pick_cancelled 信号保留：plugin.py 仍连接到它，删掉会 AttributeError。
        self.pick_banner.hide()
        # 右键选片提示挂在目标格条内，不占用页头或覆盖标题。
        self.browse_tools = QWidget(self)
        tools = QVBoxLayout(self.browse_tools)
        tools.setContentsMargins(4, 0, 4, 0)
        tools.setSpacing(0)
        filters = QHBoxLayout()
        self.filters_layout = filters  # v1.0 需求 12：便于对齐回归断言
        # v1.0 需求 12：筛选行按钮与网格卡片内容共用同一套左右边距/间距。
        filters.setContentsMargins(
            PAGE_CONTENT_MARGIN, 0, PAGE_CONTENT_MARGIN, 0
        )
        filters.setSpacing(PAGE_ROW_SPACING)
        self.search = QLineEdit(self)
        self.search.setObjectName("WatchLaterSearch")
        self.search.setPlaceholderText("搜索标题 / UP主 / BV号")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("在本地搜索标题、UP 主或 BV 号")
        self.search.setAccessibleName("搜索稍后再看")
        self.search.textChanged.connect(self._search_changed)
        self.search.setMinimumWidth(120)
        self.search.setMaximumWidth(320)
        self.search.addAction(QIcon(GalleryIcon('search')), QLineEdit.LeadingPosition)
        header.addWidget(self.search, 1)

        self.filter_combo = SelectControl(self)
        for value, label in FILTER_OPTIONS:
            self.filter_combo.addItem(label, value)
        self.filter_combo.setToolTip("按观看状态筛选")
        self.filter_combo.setAccessibleName("筛选观看状态")
        self.filter_combo.currentIndexChanged.connect(
            self._browse_filter_changed
        )
        self.filter_combo.hide()  # Keep filtering data in this existing single source of truth.
        self.filter_tabs = SegmentedControl(FILTER_OPTIONS, FILTER_ALL, self)
        self.filter_tabs.valueChanged.connect(
            lambda value: self.filter_combo.setCurrentIndex(self.filter_combo.findData(value)))
        self.filter_combo.currentIndexChanged.connect(
            lambda _index: self.filter_tabs.setValue(self.filter_combo.currentData()))
        filters.addWidget(self.filter_tabs)
        filters.addStretch(1)

        self.sort_combo = SelectControl(self)
        for value, label in SORT_OPTIONS:
            self.sort_combo.addItem(label, value)
        self.sort_combo.setCurrentIndex(0)
        self.sort_combo.setToolTip("选择排序方式")
        self.sort_combo.setAccessibleName("排序方式")
        self.sort_combo.currentIndexChanged.connect(
            self._browse_filter_changed
        )
        filters.addWidget(self.sort_combo, 0)

        # v1.0 需求 10：排序「最近添加」(sort_combo) 旁的垃圾桶 →
        # 多选快速删除。图标复用 widgets.action_icon 画的 16×16 主题色线条。
        self.multi_delete_button = GalleryToolButton(self)
        self.multi_delete_button.setCursor(Qt.PointingHandCursor)
        self.multi_delete_button.setIcon(action_icon("trash", 16))
        self.multi_delete_button.setToolTip("多选快速删除")
        self.multi_delete_button.setAccessibleName("多选快速删除")
        self.multi_delete_button.clicked.connect(self._on_multi_delete_clicked)
        filters.addWidget(self.multi_delete_button, 0)

        self.multi_cancel_button = ActionButton("取消", self)
        self.multi_cancel_button.setAutoDefault(False)
        self.multi_cancel_button.setCursor(Qt.PointingHandCursor)
        self.multi_cancel_button.setToolTip("退出多选，不删除任何视频")
        self.multi_cancel_button.setAccessibleName("取消多选删除")
        self.multi_cancel_button.clicked.connect(self._cancel_multiselect)

        # Esc 退出多选（只在多选态启用，避免抢菜单/其它快捷键的 Esc）。
        self._multi_cancel_shortcut = QShortcut(
            QKeySequence(Qt.Key_Escape), self
        )
        self._multi_cancel_shortcut.setContext(Qt.WindowShortcut)
        self._multi_cancel_shortcut.activated.connect(self._escape_navigation)
        filters.addWidget(self.view_switch)
        self.settings_button = GalleryToolButton(self)
        self.settings_button.setIcon(QIcon(GalleryIcon('settings')))
        self.settings_button.setToolTip('稍后再看设置')
        self.settings_button.setAccessibleName('稍后再看设置')
        self.settings_button.clicked.connect(self._open_settings)
        filters.addWidget(self.settings_button)
        body.addLayout(filters)
        self.placement = PlacementSelector(self.browse_tools)
        self.placement.changed.connect(self._sync_placement_actions)
        self.placement.hide()  # Retain destination state for the context-menu entry.
        self.layout_hint = ElidedLabel(self.browse_tools)
        self.layout_hint.setForegroundRole(QPalette.PlaceholderText)
        self.layout_hint.set_full_text('点击视频，在当前布局缩略图中选择置入位置')
        tools.addWidget(self.layout_hint)
        tools.addWidget(self.pick_banner)
        body.addWidget(self.browse_tools)

        self.error_bar = ErrorBar(self)
        self.error_bar.retry_requested.connect(
            lambda: self._load(manual=True)
        )
        body.addWidget(self.error_bar)

        self.stack = QStackedWidget(self)
        self.stack.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        body.addWidget(self.stack, 1)

        self.list_scroll = QScrollArea(self)
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QScrollArea.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.list_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        self.list_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.list_scroll.viewport().setAutoFillBackground(False)
        self.list_widget = QWidget(self.list_scroll)
        self.list_widget.setAutoFillBackground(False)
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)
        self.list_scroll.setWidget(self.list_widget)

        # v1.0 需求：：4 列网格视图（独立 scroll + QGridLayout）。
        self.grid_scroll = QScrollArea(self)
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFrameShape(QScrollArea.NoFrame)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.grid_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.grid_scroll.viewport().setAutoFillBackground(False)
        self.grid_widget = QWidget(self.grid_scroll)
        self.grid_widget.setAutoFillBackground(False)
        self.grid_layout = QGridLayout(self.grid_widget)
        # v1.0 需求 12：右 margin 归零，最右列卡片内容右边缘正好等于
        # header/filters 的右边缘（两边都只内缩 PAGE_CONTENT_MARGIN）。
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(18)
        self.grid_layout.setAlignment(Qt.AlignTop)
        self.grid_scroll.viewport().installEventFilter(self)
        for col in range(GRID_MAX_COLUMNS):
            # 预留到最大列数的 stretch；超出当前列数的列为 0 宽，不占地方。
            self.grid_layout.setColumnStretch(col, 1 if col < self._grid_columns else 0)
        self.grid_scroll.setWidget(self.grid_widget)
        self._wheel_scrollers = tuple(WheelScrollAccumulator(scroll, on_wheel=self._wheel_cancel_autoscroll)
                                      for scroll in (self.list_scroll, self.grid_scroll))

        self.loading_view = SkeletonLoadingView(self)
        self.loading_view.set_state(
            "正在加载稍后再看…", loading=True
        )
        self.login_view = StatusView(self)
        self.login_view.primary_requested.connect(self._request_login)
        self.login_view.secondary_requested.connect(
            self._open_web_watchlater
        )
        self.error_view = StatusView(self)
        self.error_view.primary_requested.connect(
            lambda: self._load(manual=True)
        )
        self.empty_view = StatusView(self)
        self.empty_view.primary_requested.connect(self._open_home)
        self.noresult_view = StatusView(self)
        self.noresult_view.primary_requested.connect(self._clear_filters)

        self.stack.addWidget(self.list_scroll)
        self.stack.addWidget(self.grid_scroll)
        self.stack.addWidget(self.loading_view)
        self.stack.addWidget(self.login_view)
        self.stack.addWidget(self.error_view)
        self.stack.addWidget(self.empty_view)
        self.stack.addWidget(self.noresult_view)
        self.stack.setCurrentWidget(self.loading_view)

        # v1.0 需求 10：两种视图都支持中键 autoscroll（与浏览器一致：按一下
        # 出现锚点，之后移动鼠标即持续滚动，任何点击/按键/滚轮退出）。
        self._middle_drag = (
            MiddleDragScroller(self.list_scroll),
            MiddleDragScroller(self.grid_scroll),
        )

        # 刷新进入页头；回顶和状态提示留在滚动区外。
        self.fab_bar = QWidget(self)
        self.fab_bar.setObjectName("WatchLaterFooter")
        fab_layout = QHBoxLayout(self.fab_bar)
        fab_layout.setContentsMargins(12, 10, 12, 4)
        fab_layout.setSpacing(8)
        self.fab_refresh = BusyRefreshButton(self)
        self.fab_refresh.setToolTip("刷新稍后再看（F5）")
        self.fab_refresh.setAccessibleName("刷新稍后再看")
        self.fab_refresh.setIcon(QIcon(GalleryIcon('refresh')))
        self.fab_refresh.setIconSize(QSize(18, 18))
        self.footer_hint = ElidedLabel(self.fab_bar)
        self.footer_hint.setForegroundRole(QPalette.PlaceholderText)
        self.footer_hint.set_full_text('选择视频后点选布局位置')
        fab_layout.addWidget(self.footer_hint, 1)
        fab_layout.addWidget(self.status_label, 1)
        self.fab_refresh.clicked.connect(lambda: self._load(manual=True))
        header.addWidget(self.fab_refresh)
        self.fab_top = GalleryToolButton(self)
        self.fab_top.setToolTip("回到列表顶部")
        self.fab_top.setAccessibleName("回到顶部")

        self.fab_top.setIcon(QIcon(GalleryIcon("top")))
        self.fab_top.clicked.connect(self._scroll_to_top)
        filters.addWidget(self.fab_top)
        self.multi_confirm_button = ActionButton('删除选中', self.fab_bar)
        self.multi_confirm_button.clicked.connect(self._execute_multiselect_delete)
        fab_layout.addWidget(self.multi_confirm_button)
        fab_layout.addWidget(self.multi_cancel_button)
        body.addWidget(self.fab_bar)
        self.fab_bar.hide()
        self.fab_bar.raise_()
        # F5 全局（窗口内）刷新。
        self._refresh_shortcut = QShortcut(QKeySequence(Qt.Key_F5), self)
        self._refresh_shortcut.setContext(Qt.WindowShortcut)
        self._refresh_shortcut.setAutoRepeat(False)
        self._refresh_shortcut.activated.connect(lambda: self._load(manual=True))
        self.stack.currentChanged.connect(self._update_fabs)

        self._update_header(0, 0)

    def _sync_browser_palette(self):
        self.setProperty('appearanceLight', theme.is_light())
        palette = self.palette()
        palette.setColor(QPalette.Window, theme.qcolor(background_color()))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QLineEdit#WatchLaterSearch {{
                background: {theme.CONTENT}; color: {theme.TEXT1};
                border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_MD}px;
                padding: 6px 10px; min-height: 22px;
            }}
            QLineEdit#WatchLaterSearch:focus {{border-color: {theme.ACCENT};}}
            QWidget#WatchLaterFooter {{
                background: transparent;
                border-top: 1px solid {theme.BORDER_STRONG};
            }}
            QFrame#WatchLaterSettingsDivider {{background: {theme.BORDER}; border: none;}}
            QLabel#WatchLaterSettingTitle {{font-weight: 500;}}
        """)
        self.update()

    def _show_inline_panel(self, panel, title):
        self._inline_panel = panel
        self.navigation.addWidget(panel)
        self.navigation.setCurrentWidget(panel)
        self.window_header.set_page(title)
        self._refresh_shortcut.setEnabled(False)
        self._multi_cancel_shortcut.setEnabled(True)
        self._cover_scroll_timer.stop()
        self._grow_settle_timer.stop()
        self._grow_pending = False
        self._pause_covers()
        panel.finished.connect(lambda _result: self._finish_inline_panel(panel))
        panel.setFocus(Qt.OtherFocusReason)

    def _finish_inline_panel(self, panel):
        if self._inline_panel is not panel:
            return
        self._inline_panel = None
        self.navigation.setCurrentWidget(self.browser_view)
        if panel is not self._settings_panel_cache:
            self.navigation.removeWidget(panel)
            panel.deleteLater()
        self.window_header.set_page()
        self._refresh_shortcut.setEnabled(True)
        self._refresh_multi_button()
        if self._visible and not self.list_controller.closing:
            self.covers.resume()
            self._schedule_visible_covers()
            (self.settings_button if isinstance(panel, WatchLaterSettings) else self.search).setFocus(Qt.OtherFocusReason)

    def _back_to_browser(self):
        if self._inline_panel is not None:
            self._inline_panel.reject()

    def _escape_navigation(self):
        if self._inline_panel is not None:
            self._back_to_browser()
        else:
            self._cancel_multiselect()

    def set_placement_handlers(self, provider, handler, layout_provider=None):
        self.placement.provider = provider
        self._placement_handler = handler
        self._layout_provider = layout_provider
        self.placement.refresh()

    def refresh_targets(self, preferred=None):
        self.placement.refresh(preferred)

    def _sync_card_action(self, card):
        card.set_placement_action("置入视频", self.placement.combo.count() > 0)
        card.set_browse_selected(card.item.key == self._selected_key)

    def _sync_placement_actions(self):
        for card in (*self._rows, *self._cards):
            self._sync_card_action(card)

    def _select_item(self, item):
        self._selected_key = item.key
        self._sync_placement_actions()
        self._open_placement_picker(item)

    def _open_settings(self):
        self.card_preview.stop()
        if self._settings_dialog is not None:
            return
        self._back_to_browser()
        from .settings_trace import SettingsTrace
        trace = SettingsTrace(self) if getattr(self, '_settings_trace_count', 0) < 3 else None
        self._settings_trace_count = getattr(self, '_settings_trace_count', 0) + 1
        dialog = self._settings_panel_cache
        if dialog is None:
            dialog = WatchLaterSettings(self.preferences, self)
            self._settings_panel_cache = dialog
            dialog.changed.connect(self._apply_preferences)
            dialog.finished.connect(lambda _result: setattr(self, '_settings_dialog', None))
        else:
            dialog.reopen(self.preferences)
        if trace is not None:
            trace.mark('construct' if not dialog.property('openedBefore') else 'reuse')
        dialog.setProperty('openedBefore', True)
        self._settings_dialog = dialog
        self._show_inline_panel(dialog, '设置')
        if trace is not None:
            trace.mark('attach')
            trace.watch(dialog)

    def _apply_preferences(self, values):
        previous = self.preferences
        from .settings_trace import SettingsTrace
        changed = [key for key in values if previous.get(key) != values[key]]
        trace = SettingsTrace(self, 'settings change keys=' + ','.join(changed)) if changed and getattr(self, '_settings_change_count', 0) < 12 else None
        self._settings_change_count = getattr(self, '_settings_change_count', 0) + 1
        if callable(getattr(self.context, 'set_setting', None)):
            save_preferences(self.context, values)
        if trace is not None:
            trace.mark('persist')
        self.preferences = values
        self.removal_undo.notice.reposition()
        if previous['video_preview'] and not values['video_preview']:
            self.card_preview.stop()
        if previous['remember_state'] and not values['remember_state']:
            self.browse_state.clear()
        if values['default_view'] != previous['default_view']:
            self._on_view_mode_changed(values['default_view'])
        if any(values[key] != previous[key] for key in ('grid_columns', 'card_size')):
            self._fit_grid()
        if trace is not None:
            trace.mark('view')
        self.preferences_changed.emit(values)
        if trace is not None:
            trace.mark('playback')
            panel = self._settings_dialog
            trace.watch(panel._controls()[changed[0]] if panel is not None else self)

    def _open_placement_picker(self, item):
        self.card_preview.stop()
        if not callable(self._layout_provider):
            return
        if self._placement_picker is not None:
            return
        self._back_to_browser()
        snapshot = self._layout_provider()
        picker = PlacementPicker(item, snapshot, self.placement.target(), self)
        self._placement_picker = picker
        picker.cell_chosen.connect(lambda index: self._commit_placement(item, picker, index))
        picker.finished.connect(lambda _result: setattr(self, '_placement_picker', None))
        self._show_inline_panel(picker, '选择置入位置')

    def _commit_placement(self, item, picker, index):
        snapshot = self._layout_provider()
        if snapshot.signature() != picker.map.snapshot.signature():
            picker.map.set_snapshot(snapshot)
            picker.hint.setText('布局或格子内容已变化，请重新选择位置')
            return
        cell = next((cell for cell in snapshot.cells if cell.index == index), None)
        if cell is None or cell.target is None:
            return
        picker.accept()
        self._placement_handler(item, cell.target)
        self.placement.refresh(cell.target)

    def _active_scroll(self) -> QScrollArea:
        return self.renderer.active_scroll()

    def _cancel_autoscroll(self) -> None:
        """v1.0 需求 14：页面隐藏/关闭时退出中键 autoscroll 并放开鼠标 grab。"""
        for scroller in getattr(self, "_middle_drag", ()) or ():
            try:
                scroller.cancel()
            except Exception:
                pass

    def _wheel_cancel_autoscroll(self) -> None:
        """需求 3：滚轮只在中键 autoscroll 真的进行中时才去结束它。

        事件过滤器顺序已保证 MiddleDragScroller 先看到 Wheel 并自行 _end()，
        这里仅作为与安装顺序解耦的兜底；普通滚轮（未进中键模式）几乎零开销，
        不会每个 Wheel 都白跑一次 cancel()/releaseMouse()。
        """
        for scroller in getattr(self, "_middle_drag", ()) or ():
            try:
                if scroller.is_active():
                    scroller.cancel()
            except Exception:
                pass


    def _flush_wheel_scrollers(self) -> None:
        """需求 3：隐藏 / 切视图前把累计但未应用的滚动量一次性落定。"""
        for scroller in getattr(self, "_wheel_scrollers", ()) or ():
            try:
                scroller.flush()
            except Exception:
                pass


    def _shutdown_wheel_scrollers(self) -> None:
        """需求 3：真正销毁页面前摘掉两个滚轮事件过滤器（幂等）。"""
        for scroller in getattr(self, "_wheel_scrollers", ()) or ():
            try:
                scroller.shutdown()
            except Exception:
                pass


    def _render_after_remove(self) -> None:
        return self.renderer.render_after_remove()


    def _initial_limit(self) -> int:
        return self.renderer.initial_limit()

    def _visible_anchor_key(self) -> str:
        return self.renderer.visible_anchor_key()

    def _ensure_anchor_materialized(self, key: str) -> None:
        return self.renderer.ensure_anchor_materialized(key)

    def _restore_anchor(self, key: str) -> bool:
        return self.renderer.restore_anchor(key)

    def _on_view_mode_changed(self, mode: str) -> None:
        if self.list_controller.closing or mode == self._view_mode:
            return
        anchor = self._visible_anchor_key()
        self._flush_wheel_scrollers()
        self._grow_settle_timer.stop()
        self._cancel_autoscroll()
        self._view_mode = "grid" if mode == "grid" else "list"
        self.view_switch.set_mode(self._view_mode)
        self._release_covers()
        # 未登录/加载失败等状态不因切换视图而被覆盖。
        if self.list_controller.loaded_once:
            # v1.0 需求：：切换视图不再强制回到顶部，而是把同一视频留在顶边。
            self._ensure_anchor_materialized(anchor)
            self._render()
            self._restore_anchor(anchor)
        self._update_fabs()

    def _scroll_to_top(self) -> None:
        self._active_scroll().verticalScrollBar().setValue(0)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._refuse_minimize()

    # ------------------------------------------- v1.0 需求 3：禁止最小化
    def _apply_window_flags(self) -> bool:
        """使用自绘无边框窗口并禁用最小化；返回是否修改过窗口标志。

        只在窗口模式（``isWindow()``）生效：内嵌到宿主面板时标志归宿主管，
        改了会连带把嵌入式页面变成独立窗口。
        """
        if self.list_controller.closing or not self.isWindow():
            return False
        flags = (self.windowFlags() | Qt.FramelessWindowHint) & ~Qt.WindowMinimizeButtonHint
        if self.windowFlags() == flags:
            return False
        self.setWindowFlags(flags)
        return True

    def _refuse_minimize(self) -> None:
        """Alt+Space / 系统快捷键 / 任务栏点最小化 → 立刻退回正常状态。

        摘掉标题栏按钮还不够：``Alt+Space`` 的系统菜单、``Win+↓``、任务栏
        右键都不看 Qt 的窗口标志。这里把 WindowMinimized 位抹掉并 show()，
        窗口原地回到正常态（不改几何）。
        """
        if self.list_controller.closing or self._minimize_guard:
            return
        if not self.isWindow():
            return
        state = self.windowState()
        if not (state & Qt.WindowMinimized):
            return
        self._minimize_guard = True
        try:
            self.setWindowState(state & ~Qt.WindowMinimized)
            if not self.isVisible():
                self.show()
        finally:
            self._minimize_guard = False

    def _update_fabs(self, *_args) -> None:
        if not hasattr(self, 'fab_bar'):
            return
        scroll = self._active_scroll()
        has_view = self.stack.currentWidget() is scroll
        self.fab_top.setVisible(True)
        self.fab_top.setEnabled(has_view and scroll.verticalScrollBar().value() > 0)
        self.fab_bar.setVisible(self._visible and not self.list_controller.closing)

    def eventFilter(self, watched, event) -> bool:
        if (event.type() == QEvent.Resize
                and watched is self.grid_scroll.viewport()
                and not self.list_controller.closing):
            self._grid_resize_timer.start()
        return super().eventFilter(watched, event)

    def _fit_grid(self) -> None:
        return self.renderer.fit_grid()

    def _apply_grid_columns(self, columns: int) -> None:
        return self.renderer.apply_grid_columns(columns)

    def _search_changed(self, _text: str) -> None:
        self.browse_state.pending = None
        self._search_timer.start()

    def _clear_filters(self) -> None:
        self._search_timer.stop()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        for combo in (self.filter_combo, self.sort_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.filter_tabs.setValue(self.filter_combo.currentData())
        self._render(reset_scroll=True)

    def _flash_status(self, text: str) -> None:
        self.status_label.set_full_text(text)
        self.footer_hint.hide()
        self.status_label.show()
        self._status_timer.start(3000)

    def _clear_status(self) -> None:
        self.status_label.set_full_text('')
        self.status_label.hide()
        self.footer_hint.show()

    def _update_header(
        self, shown: int, total: int, filtered: bool = False
    ) -> None:
        if not self.list_controller.loaded_once:
            self.count_label.setText("")
            return
        if total == 0:
            text = "共 0 个"
        elif filtered:
            text = f"显示 {shown} / 共 {total} 个"
        else:
            text = f"共 {total} 个"
        if self.list_controller.last_update_epoch:
            text += " · " + time.strftime(
                "%H:%M", time.localtime(self.list_controller.last_update_epoch)
            ) + " 更新"
        self.count_label.setText(text)

    def _set_refreshing(self, active: bool) -> None:
        ready = (not active and not self.list_controller.busy_operation and self.list_controller.has_session
                 and not self.list_controller.closing)
        self.refresh_button.setText("刷新中…" if active else "刷新")
        self.refresh_button.setEnabled(ready)
        self.fab_refresh.set_busy(active)
        self.fab_refresh.setEnabled(ready)
        self._refresh_shortcut.setEnabled(ready)
        self._update_fabs()

    def _update_destructive_controls(self) -> None:
        ready = (
            self.list_controller.has_session
            and self.list_controller.has_csrf
            and bool(self.list_controller.items)
            and not self.list_controller.busy_operation
            and not self.list_controller.closing
        )
        # v1.0 需求：：右上角「更多」菜单已删除，「清空稍后再看」入口随之
        # 消失（_confirm_clear / _begin_clear 保留给未来的宿主入口）。
        # v1 recomputed the per-row reason on every render, and each row's
        # set_remove_available() recomputed it again.  Only touch rows when the
        # answer actually changed.
        if self._destructive_ready != ready:
            self._destructive_ready = ready
            for row in (*self._rows, *self._cards):
                row.set_remove_available(ready)
        self._refresh_multi_button()

    def _remove_reason(self, item: WatchLaterItem) -> str:
        return self.list_controller.remove_reason(item)

    # -------------------------------------------------------------- states
    def _show_loading(self) -> None:
        # v1.0 需求 3：骨架形态跟随当前视图（默认网格），不再固定列表样式。
        self.loading_view.set_mode(self._view_mode)
        self.stack.setCurrentWidget(self.loading_view)
        self.loading_view.set_state("正在加载稍后再看…", loading=True)
        self._update_header(0, len(self.list_controller.items))

    def _show_login_state(self, expired: bool = False) -> None:
        self._login_expired = expired
        if expired:
            self.login_view.set_state(
                "登录已失效",
                "登录已失效，请重新登录 B 站。",
                primary="重新登录",
                secondary="打开网页版",
            )
            self.count_label.setText("登录已失效")
        else:
            self.login_view.set_state(
                "登录 B 站后查看稍后再看",
                "使用 DD监控室CE 当前登录的 B 站账号读取稍后再看。",
                primary="登录 B 站",
                secondary="打开网页版",
            )
            self.count_label.setText("未登录")
        self.stack.setCurrentWidget(self.login_view)
        self._set_refreshing(False)

    def _show_list_error(self, message: str, *, login_expired: bool) -> None:
        if login_expired:
            self._clear_account_data()
            self._show_login_state(expired=True)
            return
        if self.list_controller.items:
            self.error_bar.show_error("刷新失败：" + message)
            self._render()
            return
        self.error_view.set_state(
            "加载失败", message, primary="重试"
        )
        self.stack.setCurrentWidget(self.error_view)
        self._set_refreshing(False)

    def _browse_filter_changed(self, _index):
        self.browse_state.pending = None
        self._render(reset_scroll=True)

    def _render(self, reset_scroll: bool = False, *,
                keep_position: bool = False) -> None:
        return self.renderer.render(reset_scroll, keep_position=keep_position)

    def _release_covers(self) -> None:
        return self.renderer.release_covers()

    def _drop_view(self, view, layout) -> None:
        return self.renderer.drop_view(view, layout)

    def _prune_inactive_view(self, items: list[WatchLaterItem]) -> None:
        return self.renderer.prune_inactive_view(items)

    def _clear_rows(self) -> None:
        return self.renderer.clear_rows()

    def _make_row(self, item: WatchLaterItem) -> RowCard:
        return self.renderer.make_row(item)

    def _rebuild_cards(self, items: list[WatchLaterItem]) -> None:
        return self.renderer.rebuild_cards(items)

    def _rebuild_rows(self, items: list[WatchLaterItem]) -> None:
        return self.renderer.rebuild_rows(items)

    def _yield_during_build(self) -> None:
        return self.renderer.yield_during_build()

    def _cover_ready(self, url: str) -> None:
        return self.renderer.cover_ready(url)

    # -------------------------------------------------- visible-cover refill
    def _danmaku_provider(self) -> DanmakuCountProvider | None:
        return self.renderer.danmaku_provider()

    def _danmaku_ready(self, bvid: str, count: int) -> None:
        return self.renderer.danmaku_ready(bvid, count)

    def _view_count_ready(self, bvid: str, count: object) -> None:
        return self.renderer.view_count_ready(bvid, count)

    def _connect_scroll_refetch(self) -> None:
        return self.renderer.connect_scroll_refetch()

    def _disconnect_scroll_refetch(self) -> None:
        return self.renderer.disconnect_scroll_refetch()

    def _schedule_visible_covers(self, *_args) -> None:
        return self.renderer.schedule_visible_covers(*_args)

    # ------------------------------------------------------ incremental paging
    def _maybe_grow_page(self) -> None:
        return self.renderer.maybe_grow_page()

    def _grow_page(self) -> None:
        return self.renderer.grow_page()

    def _refetch_visible_covers(self) -> None:
        return self.renderer.refetch_visible_covers()

    # -------------------------------------------------------------- account
    def _read_account(self) -> _Account:
        return self.list_controller.read_account()

    def _clear_account_data(self, *, clear_credentials: bool=True) -> None:
        return self.list_controller.clear_account_data(clear_credentials=clear_credentials)

    def on_account_changed(self, generation: Any=None) -> None:
        return self.list_controller.on_account_changed(generation)

    # ---------------------------------------------------------------- load
    def _cache_fresh(self) -> bool:
        return self.list_controller.cache_fresh()

    def _load(self, *, manual: bool) -> None:
        return self.list_controller.load(manual=manual)

    def _list_completed(self, request_id: int, generation: int, snapshot: Any) -> None:
        return self.list_controller.list_completed(request_id, generation, snapshot)

    def _list_failed(self, request_id: int, generation: int, error: Any) -> None:
        return self.list_controller.list_failed(request_id, generation, error)

    # ---------------------------------------------------------- destructive
    def _confirm_remove(self, item: WatchLaterItem) -> None:
        if self.list_controller.closing or self.list_controller.busy_operation:
            return
        reason = self._remove_reason(item)
        if reason:
            self._flash_status(reason)
            return
        self._begin_remove(item)

    def _confirm_action(self, title, message, action, callback):
        self.card_preview.stop()
        self._back_to_browser()
        generation = self.list_controller.generation
        panel = InlineConfirmation(title, message, action, self)
        self._show_inline_panel(panel, title)
        panel.finished.connect(lambda result: callback() if result == 1 and not self.list_controller.closing
                               and generation == self.list_controller.generation else None)

    def _begin_remove(self, item: WatchLaterItem) -> None:
        return self.list_controller.begin_remove(item)

    def _remove_completed(self, request_id: int, generation: int, _payload: Any) -> None:
        return self.list_controller.remove_completed(request_id, generation, _payload)

    def _confirm_clear(self) -> None:
        if self.list_controller.closing or self.list_controller.busy_operation:
            return
        if not self.list_controller.has_csrf:
            self._flash_status("登录信息不完整，请重新登录后再试")
            return
        if not self.list_controller.items:
            self._flash_status("稍后再看已经是空的")
            return
        self._confirm_action('清空稍后再看', '此操作会删除当前 B 站账号的全部稍后再看，无法撤销。',
                             '清空', self._begin_clear)

    def _begin_clear(self) -> None:
        return self.list_controller.begin_clear()

    def _clear_completed(self, request_id: int, generation: int, _payload: Any) -> None:
        return self.list_controller.clear_completed(request_id, generation, _payload)

    def _operation_failed(self, request_id: int, generation: int, error: Any) -> None:
        return self.list_controller.operation_failed(request_id, generation, error)

    def _end_operation(self) -> None:
        return self.list_controller.end_operation()

    def _cancel_refresh_for_operation(self) -> None:
        return self.list_controller.cancel_refresh_for_operation()

    # --------------------------------------------------------------- actions
    # ----------------------------------------------------- v1.0 播放到格子
    def set_play_handler(self, handler) -> None:
        """插件注入「播放到格子」回调（浏览模式进空格，选片模式进目标格）。"""
        self._play_handler = handler

    def set_pick_target(self, label) -> None:
        """选片模式横幅；label=None 退出选片模式（与侧栏浏览同一窗口）。"""
        self._pick_label = str(label) if label else None
        if self._pick_label:
            short = self._pick_label
            if len(short) > 14:
                short = short[:13] + "…"
            self.pick_banner_label.set_full_text("播放至 " + short)
            tip = f"正在为「{self._pick_label}」选片；点击卡片上的置入／替换按钮操作"
            self.pick_banner_label.setToolTip(tip)
            self.pick_banner.setToolTip(tip)
            self.pick_banner.show()
        else:
            self.pick_banner_label.set_full_text("")
            self.pick_banner.setToolTip("")
            self.pick_banner.hide()
        # v1.0 需求 11：横幅态与「快速选片」按钮选中态保持一致
        # （plugin._show_page / _page_closed 也走这条路径）。
        self._quick_pick_active = bool(self._pick_label)
        button = getattr(self, "quick_pick_button", None)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(self._quick_pick_active)
            button.setText(
                "退出选片" if self._quick_pick_active else "快速选片"
            )
            button.blockSignals(False)

    # ---------------------------------------------------------- v1.0 快速选片
    def _on_quick_pick_clicked(self) -> None:
        """v1.0 需求 11：标题行「快速选片」→ 进/退选片模式。

        进选片时用 page 已有的 :meth:`set_pick_target` 显示目标格横幅；
        点击视频仍走既有 ``_play_item`` → ``play_handler``（plugin 注入的
        播放回调），所以「点视频即投放到目标格」链路完全复用、不改 plugin.py。
        再点一次退出并 emit ``pick_cancelled`` 通知 plugin 清目标格。
        """
        if self.list_controller.closing:
            return
        if self._quick_pick_active:
            self.set_pick_target(None)
            self.pick_cancelled.emit()
            return
        self.set_pick_target("目标格子")
        self._flash_status("选片模式：点击视频即投放到格子")

    # -------------------------------------------------- v1.0 多选快速删除 UI
    def _refresh_multi_button(self) -> None:
        confirming = multiselect_is_confirming(self._multiselect_state)
        self._multi_cancel_shortcut.setEnabled(confirming or self._inline_panel is not None)
        self.multi_cancel_button.setVisible(confirming)
        self.multi_confirm_button.setVisible(confirming)
        count = len(self._multi_selected_keys)
        self.multi_confirm_button.setText(f'删除选中（{count}）')
        self.multi_confirm_button.setEnabled(bool(count) and bool(self._destructive_ready))
        self.multi_delete_button.setToolTip('退出多选' if confirming else '多选删除')

    def _on_multi_delete_clicked(self) -> None:
        if self.list_controller.closing:
            return
        if multiselect_is_confirming(self._multiselect_state):
            self._cancel_multiselect()
        else:
            self._multiselect_state = MULTISELECT_SELECT
            self._enter_multiselect()

    def _enter_multiselect(self) -> None:
        self._multi_selected_keys = set()
        self._apply_multiselect_to_views()
        self._refresh_multi_button()
        self._flash_status("点视频进行多选，使用底部按钮删除或取消")

    def _cancel_multiselect(self) -> None:
        """取消/退出多选：清空选中且不删任何东西。"""
        self._multiselect_state = multiselect_cancel(self._multiselect_state)
        self._multi_selected_keys = set()
        self.list_controller.cancel_pending_removals()
        self._apply_multiselect_to_views()
        self._refresh_multi_button()
        self._clear_status()

    def _apply_multiselect_to_views(self) -> None:
        active = multiselect_is_confirming(self._multiselect_state)
        keys = self._multi_selected_keys
        for view in (*self._rows, *self._cards):
            view.set_multi_mode(active, view.item.key in keys)

    def _on_multi_toggle(self, item: WatchLaterItem) -> None:
        if self._multiselect_state != MULTISELECT_SELECT:
            return
        self._multi_selected_keys = selection_toggle(
            self._multi_selected_keys, item.key
        )
        key = item.key
        for view in (*self._rows, *self._cards):
            if view.item.key == key:
                view.set_multi_mode(True, key in self._multi_selected_keys)
        self._refresh_multi_button()

    def _execute_multiselect_delete(self) -> None:
        """二次确认后的真正删除：串行走现有单项 RemoveRequest 链路。"""
        keys = set(self._multi_selected_keys)
        if not keys:
            self._flash_status("请先选择要删除的视频")
            return  # 没选中就保持确认态，绝不静默删
        items = [it for it in self.list_controller.items if it.key in keys]
        reason = self._remove_reason(items[0]) if items else ""
        if reason:
            self._flash_status(reason)
            return
        self._multiselect_state = MULTISELECT_IDLE
        self._multi_selected_keys = set()
        self._apply_multiselect_to_views()
        self._refresh_multi_button()
        self.list_controller.remove_many(items)

    def _pump_multi_remove(self) -> None:
        return self.list_controller.pump_multi_remove()

    def _play_item(self, item: WatchLaterItem) -> None:
        if callable(self._layout_provider):
            self._select_item(item)
            return
        if callable(self._placement_handler):
            self.placement.refresh()
            target = self.placement.target()
            if target is None:
                self._flash_status("请先选择可用的目标格")
                return
            self._placement_handler(item, target)
            self.placement.refresh(target)
            return
        handler = self._play_handler
        if callable(handler):
            try:
                handler(item)
                return
            except Exception:
                pass
        self._flash_status("未连接画面墙，无法播放到格子")

    def _on_double_play(self, item: WatchLaterItem) -> None:
        """Close after the card has already dispatched its playback signal.

        Row/card double clicks emit play_requested first. Grid covers dispatch
        on the first release. Replaying here would select another empty tile
        and turn one double click into an unintended tile swap.
        """
        was_pick = self._quick_pick_active
        if not was_pick and not self.list_controller.closing:
            self.close()

    def _open_item(self, item: WatchLaterItem) -> None:
        url = video_url(item)
        if not url:
            self._flash_status("无法生成视频链接")
            return
        now = time.monotonic()
        if (
            url == self._last_open_url
            and now - self._last_open_at < 0.6
        ):
            return
        self._last_open_url = url
        self._last_open_at = now
        if not self._open_external(url):
            self._flash_status("无法打开浏览器，请检查默认浏览器设置")

    def _copy_item(self, item: WatchLaterItem) -> None:
        url = video_url(item)
        if not url:
            self._flash_status("无法生成视频链接")
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            self._flash_status("无法访问剪贴板")
            return
        clipboard.setText(url)
        self._flash_status("链接已复制")

    def _open_web_watchlater(self) -> None:
        if not self._open_external(watch_later_web_url()):
            self._flash_status("无法打开浏览器，请检查默认浏览器设置")

    def _open_home(self) -> None:
        if not self._open_external(home_url()):
            self._flash_status("无法打开浏览器，请检查默认浏览器设置")

    def _open_external(self, url: str) -> bool:
        if not isinstance(url, str) or not url.startswith("https://"):
            return False
        return bool(QDesktopServices.openUrl(QUrl(url)))

    def _request_login(self) -> None:
        try:
            result = self.context.request_login(
                "bilibili", reauthenticate=self._login_expired
            )
        except Exception:
            result = False
        if result is False:
            try:
                delivered = bool(
                    self.context.notify(
                        "请从主界面账号入口登录 B 站", kind="warning"
                    )
                )
            except Exception:
                delivered = False
            if not delivered:
                self._flash_status("请从主界面账号入口登录 B 站")

    # --------------------------------------------------------------- threads
    @staticmethod
    def _cancel_request(worker: Any) -> None:
        return ListController.cancel_request(worker)

    def _track(self, worker: Any) -> None:
        return self.list_controller.track(worker)

    def _request_finished(self) -> None:
        return self.list_controller.request_finished()

    def _retire(self, worker: Any) -> None:
        return self.list_controller.retire(worker)

    def _log(self, message: str) -> None:
        try:
            self.context.log(message)
        except Exception:
            pass

    # ------------------------------------------------------------------ wall
    def _publish_to_wall(self) -> None:
        """Push the current list and session state to the tile controller."""
        self.items_changed.emit(self.items_snapshot())
        wall = self.wall
        if wall is None or self.list_controller.closing:
            return
        try:
            wall.set_items(self.list_controller.items)
            wall.set_session_state(
                has_session=self.list_controller.has_session and self.list_controller.has_csrf,
                busy=bool(self.list_controller.busy_operation),
            )
        except Exception:
            pass

    def items_snapshot(self) -> list[WatchLaterItem]:
        """Read-only copy of the current list for the tile picker."""
        return list(self.list_controller.items)

    def has_session(self) -> bool:
        return bool(self.list_controller.has_session)

    def pick_item_for_tile(self, parent=None) -> WatchLaterItem | None:
        """Show the picker over the in-memory list; never touches the network."""
        from .picker import WatchLaterPicker

        if self.list_controller.closing:
            return None
        if not self.list_controller.items:
            self._flash_status("稍后再看列表为空，请先刷新")
            return None
        return WatchLaterPicker.pick(self.list_controller.items, parent or self)

    def refresh_from_wall(self) -> None:
        """Refresh requested from a tile menu; keeps the current tile state."""
        if self.list_controller.closing or self.list_controller.busy_operation:
            return
        self._load(manual=True)

    def remove_from_wall(self, item: WatchLaterItem) -> None:
        """Remove one item requested from a tile menu, with the same guards."""
        if self.list_controller.closing or self.list_controller.busy_operation:
            return
        reason = self._remove_reason(item)
        if reason:
            self._flash_status(reason)
            return
        self._confirm_remove(item)

    # ------------------------------------------------------------ lifecycle
    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self.list_controller.closing:
            return
        # v1.0 需求 3：先摘掉最小化按钮。setWindowFlag 会让 Qt 把窗口藏起来
        # （原生窗口重建），所以补一次 show() 再走完本轮 showEvent；这一 hides
        # 是「假隐藏」，用 _window_flag_guard 让 hideEvent 整段跳过，否则会
        # 把重建那一帧的几何写进存档。
        changed = False
        self._window_flag_guard = True
        try:
            changed = self._apply_window_flags()
            if changed:
                self.show()
        finally:
            self._window_flag_guard = False
        if changed:
            return
        self._visible = True
        self.browse_state.prepare()
        self.placement.refresh()
        self._update_fabs()
        self.covers.resume()
        if self._danmaku is not None:
            self._danmaku.resume()
        # v1.0 需求：/10：仅隐藏→可见时触发；窗口模式下恢复上次位置（无存档
        # 时居中夹取），再入场动画。spontaneous（最小化后还原）不重新定位，
        # 避免覆盖用户手动摆放的窗口。
        if self.isWindow() and not event.spontaneous():
            if not self._apply_saved_geometry():
                self._center_on_parent()
            self._start_entrance()
        if self.list_controller.closing:
            return
        self._connect_scroll_refetch()
        self._schedule_visible_covers()
        if self._cache_fresh():
            # v1.0：重开窗口回到首屏分页，不沿用上次展开的行数。
            # v1.0 需求 1：show() 与卡片构建分离 —— 先让窗口带骨架画出来，
            # 首屏卡片构建延到事件循环（异步填内容），不等 16 张卡同步建完。
            QTimer.singleShot(0, lambda: self._render(reset_scroll=True))
            return
        self._load(manual=False)

    # ------------------------------------ v1.0 需求：/ v1.0：居中与位置记忆
    def _centered_geometry(self, available: QRect) -> QRect:
        """按父窗口（宿主主窗）中心计算目标矩形，并夹取到 available 内。"""
        size = self._usable_size()
        # v1.0：淡入动画不再把窗口缩到 92%，因此居中必须把原生窗口
        # 装饰也算进去，否则 client geometry 会比 move() 右/下偏几像素，
        # 在小屏幕上溢出可用区（离屏 800px 屏幕上实测溢出 2px）。
        margin = _FRAME_MARGIN
        width = min(size.width(), available.width() - 2 * margin)
        height = min(size.height(), available.height() - 2 * margin)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.frameGeometry().center()
        else:
            center = available.center()
        left = center.x() - width // 2
        top = center.y() - height // 2
        left = max(
            available.left() + margin,
            min(left, available.right() - width + 1 - margin),
        )
        top = max(
            available.top() + margin,
            min(top, available.bottom() - height + 1 - margin),
        )
        return QRect(left, top, max(1, width), max(1, height))

    def _usable_size(self) -> QSize:
        """v1.0 需求 6：居中用的尺寸必须有意义。

        ``self.size()`` 在「刚 setParent、还没被 resize 过」或原生窗口被销毁
        重建的那一帧可能是 0×0 / 1×1；用它算居中会得到 1×1 的矩形，再被
        setGeometry 的 minimumSize 撑开，窗口就滑到屏幕角上。
        """
        size = self.size()
        minimum = self.minimumSize()
        if size.width() < max(1, minimum.width()) or size.height() < max(
            1, minimum.height()
        ):
            size = self.minimumSize()
        if size.width() < 2 or size.height() < 2:
            size = QSize(DEFAULT_POPUP_WIDTH, DEFAULT_POPUP_HEIGHT)
        return size

    def _center_on_parent(self) -> None:
        """把弹窗按父窗口（宿主主窗）中心居中，并夹取到可用屏幕范围内。"""
        try:
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is None:
                return
            target = self._centered_geometry(screen.availableGeometry())
            if self.geometry() != target:
                # 尺寸退化时 _centered_geometry 已经改用 minimumSize/默认值，
                # setGeometry 一次就同时把位置和尺寸落定。
                self.setGeometry(target)
        except Exception:
            pass

    def _geometry_store(self) -> Any:
        """惰性获取几何存储；测试可直接赋值 self._geometry_settings。"""
        if self._geometry_settings is None:
            self._geometry_settings = GEOMETRY_STORE_FACTORY()
        return self._geometry_settings

    def _apply_saved_geometry(self) -> bool:
        """恢复上次位置/尺寸；无存档或存档失效时返回 False（调用方再居中）。"""
        if not self.isWindow():
            return False
        try:
            saved = geometry_from_store(self._geometry_store(), GEOMETRY_KEY)
            if saved is None:
                return False
            available = [
                screen.availableGeometry()
                for screen in QGuiApplication.screens()
                if screen is not None
            ]
            if not available:
                return False
            target = resolve_geometry(
                saved,
                available,
                self._centered_geometry(available[0]),
                _FRAME_MARGIN,
                # v1.0 需求 6：小于最小尺寸的存档（0/1px 的退化值）直接丢弃，
                # 交回「按父窗居中」，绝不硬套到屏幕角上。
                self.minimumSize(),
            )
            # 兜底再校验一次：居中结果也必须真的落在某块屏幕里。
            if usable_geometry(
                target, self.minimumSize(), available, _FRAME_MARGIN
            ) is None:
                return False
            if self.geometry() != target:
                self.setGeometry(target)
            return True
        except Exception:
            return False

    def _schedule_geometry_save(self) -> None:
        """move/resize 后 400ms 去抖保存，避免拖动时反复写 QSettings。"""
        if self._entrance_applying or self._window_flag_guard or not self.isWindow():
            return
        self._geometry_save_timer.start()

    def _save_geometry_now(self) -> None:
        """立即落盘（hide/close 调用，绕过去抖计时器）。"""
        self._geometry_save_timer.stop()
        if not self.isWindow():
            return
        # v1.0 需求 3：改窗口标志时 Qt 会先 hide 再 show（见 showEvent），
        # 这一 hide 不是用户行为，不能把「标志重建那一帧」的几何写进存档，
        # 否则下次打开会丢掉用户摆好的位置/尺寸。
        if self._window_flag_guard:
            return
        # v1.0 需求 6：最小化/退化尺寸的这一刻绝不落盘 —— 最小化窗口在
        # Windows 上的 client 尺寸没有意义，写进去下次就会恢复成怪尺寸。
        if self.windowState() & Qt.WindowMinimized:
            return
        geometry = self.geometry()
        if (
            geometry.width() < max(1, self.minimumWidth())
            or geometry.height() < max(1, self.minimumHeight())
        ):
            return
        try:
            store = self._geometry_store()
            geometry_to_store(store, self.saveGeometry(), GEOMETRY_KEY)
            # QSettings 默认延迟刷写；关窗后进程可能马上退出，这里同步一次。
            sync = getattr(store, "sync", None)
            if callable(sync):
                sync()
        except Exception:
            pass

    # --------------------------------------- v1.0 需求：：入场淡入（不缩放）
    def _start_entrance(self) -> None:
        if not _system_animation_enabled():
            return
        # 记录居中后的目标几何（供手工 move/resize 打断时判定是否需要复位），
        # 但动画期间不再改变几何：逐帧 setGeometry 会改窗口宽度，触发
        # QScrollArea 全量重排和每行 ElidedLabel 的 QFontMetrics 重算。
        self._entrance_target = self.geometry()
        self._entrance_anim.stop()
        self._entrance_anim.setCurrentTime(0)
        self._entrance_frame(0.0)
        self._entrance_anim.start()

    def _entrance_frame(self, progress) -> None:
        try:
            value = min(1.0, max(0.0, float(progress)))
        except (TypeError, ValueError):
            value = 1.0
        self._entrance_applying = True
        try:
            self.setWindowOpacity(value)
        finally:
            self._entrance_applying = False

    def _entrance_finished(self) -> None:
        self._restore_entrance()

    def _restore_entrance(self) -> None:
        """动画结束/被打断时把透明度复位；几何只在动画还在跑时复位。

        v1.0 需求 6：入场动画早已是纯淡入、不再改几何，所以动画结束后的
        hide/close 绝不能把窗口搬回 ``_entrance_target`` —— 否则用户在本次
        显示期间拖动/缩放过窗口，关闭时会被悄悄回滚，落盘的也一直是上次
        显示时的老位置（实际运行环境复现：移到 300,200 关闭后存档仍是 533,206）。
        """
        running = self._entrance_anim.state() == QAbstractAnimation.Running
        self._entrance_anim.stop()
        self._entrance_applying = True
        try:
            self.setWindowOpacity(1.0)
            if (
                running
                and self._entrance_target.isValid()
                and self.geometry() != self._entrance_target
            ):
                self.setGeometry(self._entrance_target)
        finally:
            self._entrance_applying = False

    def _abort_entrance(self) -> None:
        """用户手动 move/resize 时停动画并立即复位（_entrance_applying 守卫）。"""
        if self._entrance_applying:
            return
        if self._entrance_anim.state() == QAbstractAnimation.Running:
            self._restore_entrance()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        self._abort_entrance()
        super().moveEvent(event)
        self._schedule_geometry_save()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        if self._window_flag_guard:
            # v1.0 需求 3：setWindowFlag 重建原生窗口带来的「假隐藏」——
            # 不动封面、不落盘几何、不停计时器，紧接着的 show() 会继续用。
            super().hideEvent(event)
            return
        self._visible = False
        self._back_to_browser()
        self._grow_pending = False
        self._flush_wheel_scrollers()
        self.browse_state.capture()
        self._grow_settle_timer.stop()
        self._cancel_autoscroll()
        self._search_timer.stop()
        self._cover_scroll_timer.stop()
        self._grid_resize_timer.stop()
        self._disconnect_scroll_refetch()
        self._pause_covers()
        self._release_covers()
        self._update_fabs()
        self._restore_entrance()
        self._save_geometry_now()
        if self._danmaku is not None:
            self._danmaku.pause()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._abort_entrance()
        super().resizeEvent(event)
        self._update_fabs()
        self._grid_resize_timer.start()
        self._schedule_visible_covers()
        self._schedule_geometry_save()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._visible = False
        self._back_to_browser()
        self._grow_pending = False
        self._flush_wheel_scrollers()
        self._grow_settle_timer.stop()
        self._cancel_autoscroll()
        self._cover_scroll_timer.stop()
        self._disconnect_scroll_refetch()
        self._pause_covers()
        if self._danmaku is not None:
            self._danmaku.pause()
        self._restore_entrance()
        self._save_geometry_now()
        self.closed.emit()
        event.accept()

    def _pause_covers(self):
        if not self.external_cover_urls():
            self.covers.pause()

    def activate(self) -> None:
        if self.list_controller.closing:
            return
        if self._inline_panel is None:
            self.setFocus(Qt.OtherFocusReason)

    def shutdown(self) -> None:
        if self.list_controller.closing:
            return
        self.list_controller.closing = True
        self.removal_undo.clear()
        self.card_preview.stop()
        self._visible = False
        self._back_to_browser()
        self._grid_resize_timer.stop()
        self._grow_settle_timer.stop()
        self._shutdown_wheel_scrollers()
        self._cancel_autoscroll()
        self._set_refreshing(False)
        self._grow_pending = False
        self._search_timer.stop()
        self._status_timer.stop()
        self._cover_scroll_timer.stop()
        self._disconnect_scroll_refetch()
        self.list_controller.shutdown()
        try:
            self.covers.image_ready.disconnect(self._cover_ready)
        except (RuntimeError, TypeError):
            pass
        self.covers.shutdown()
        # v1.0 需求：/12：停入场动画，弹幕提供器清池清缓存。
        self._entrance_anim.stop()
        if self._danmaku is not None:
            for signal, slot in (
                (self._danmaku.count_ready, self._danmaku_ready),
                (self._danmaku.view_ready, self._view_count_ready),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            self._danmaku.shutdown()
            self._danmaku = None
        self.list_controller.clear_shutdown_data()
