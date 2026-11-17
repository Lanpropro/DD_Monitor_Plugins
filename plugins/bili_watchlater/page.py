"""Watch-later list window for Bilibili (v1.0: standalone popup).

Opened by the sidebar workspace button and by the tile right-click picker as
the SAME window (user requirement 7).  Video cards can be dragged straight
onto wall tiles (VOD_MIME, requirement 6); explicit placement buttons use the
selected destination. Covers and titles only select a thumbnail.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import sys
import time
from typing import Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QByteArray,
    QCoreApplication,
    QDataStream,
    QEasingCurve,
    QEvent,
    QEventLoop,
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
from .preferences import CARD_WIDTHS, read_preferences, save_preferences
from .settings_dialog import WatchLaterSettings
from .browse_state import BrowseState
from .card_preview import CardPreview
from .page_shell import BrowserTitleBar, BrowserWindow, InlineConfirmation, background_color
from .entry_icon import GalleryIcon

from .bili_api import BiliAPIError, describe_error
from .covers import CoverLoader
from .danmaku_count import DanmakuCountProvider
from .loading_skeleton import SkeletonLoadingView
from .models import (
    FILTER_ALL,
    FILTER_OPTIONS,
    SORT_OPTIONS,
    SORT_RECENT,
    WatchLaterItem,
    WatchLaterSnapshot,
    filtered_items,
    home_url,
    safe_int,
    video_url,
    watch_later_web_url,
)
from .widgets import (
    MULTISELECT_IDLE,
    MULTISELECT_SELECT,
    PAGE_CONTENT_MARGIN,
    PAGE_ROW_SPACING,
    CenteredColumn,
    ErrorBar,
    ElidedLabel,
    GridCard,
    MiddleDragScroller,
    RowCard,
    StatusView,
    ViewModeSwitch,
    action_icon,
    multiselect_cancel,
    multiselect_is_confirming,
    relative_font,
    selection_toggle,
)
from .wheel_scroll import WheelScrollAccumulator
from .request_retirement import dispose_finished_request, retire_page_request
from .workers import ClearRequest, ListRequest, RemoveRequest

# v1.0 性能：缓存 60s 太短，超过一分钟的每次重开都要重新拉列表并走
# 骨架屏；延长到 5 分钟，手动「刷新」仍可随时强制更新。
CACHE_TTL_SECONDS = 300.0
SEARCH_DEBOUNCE_MS = 200
COVER_REFETCH_DEBOUNCE_MS = 200
_VISIBLE_ROW_MARGIN = 2
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


# v1.0 首屏分页：642 行全量构建约 1.2 s GUI 线程开销、约 7000 个 QObject，
# 但首屏只看得到十几行。首次只物化 PAGE_INITIAL_ROWS 行，滚动接近底部时
# 再按 PAGE_GROW_ROWS 追加。数据层（搜索/筛选/排序/计数）仍是完整列表，
# 只是不把不可见的行做成 QWidget。改这两个常量即可调节首批/每批大小。
PAGE_INITIAL_ROWS = 50
PAGE_GROW_ROWS = 50
PAGE_PREFETCH_PX = 700

# v1.0 需求：：网格视图（默认 4 列，B 站客户端 4x4 观感），行随滚动分页追加。
# v1.0 需求 4：列数改为按可用宽度自适应 —— 目标单元格 240px，窗口越大列越多
# （1200px 出 5 列、1400px 出 6 列…），上下限 4/8 列。
GRID_COLUMNS = 3
GRID_MIN_COLUMNS = 2
GRID_MAX_COLUMNS = 6
GRID_TARGET_CELL_WIDTH = 300
# v1.0 需求 1：首屏只物化可视区能装下的卡片（980×660 默认窗口约 2~3 行，
# 每行 4 列 ≈ 8~12 张），从 32 降到 16，滚动接近底部再按 GRID_GROW_CARDS 补。
GRID_INITIAL_CARDS = 16
GRID_GROW_CARDS = 16
GRID_PREFETCH_PX = 640
GRID_KEEP_ROWS = 4
GRID_BUILD_BATCH = 4
GROW_SETTLE_MS = 60
GRID_VISIBLE_MARGIN = 1
GRID_COVER_BUDGET = 32

# v1.0 性能：冷启动全量构建时按批让出 GUI 线程。只在窗口可见时启用，
# 离屏测试/基准仍走完整同步路径，保证 _render() 返回时行列表完整。
ROW_BUILD_BATCH = 16
ROW_BUILD_YIELD_MIN = 64
# 网格冷首屏只有 32 张卡（低于 ROW_BUILD_YIELD_MIN），用更低的让出门槛
# 把首屏也分批，避免切到网格瞬间一次性建满 32 张卡卡顿。
GRID_BUILD_YIELD_MIN = 16

# v1.0 性能：入场动画改为纯淡入（本地复制，绝不 import ddm.motion）。
# 保留与宿主左侧插件快捷键一致的 bezier + 160ms；不再逐帧 setGeometry，
# 否则窗口宽度每帧变化都会让全部行的 ElidedLabel 重算省略号。
ENTRANCE_MS = 160
ENTRANCE_SCALE_FROM = 0.92
_SPI_GETCLIENTAREAANIMATION = 0x1042


def grid_columns_for_width(
    viewport_width: int,
    *,
    target: int = GRID_TARGET_CELL_WIDTH,
    minimum: int = GRID_MIN_COLUMNS,
    maximum: int = GRID_MAX_COLUMNS,
) -> int:
    """v1.0 需求 4：按可用宽度算网格列数（纯函数，可单测）。

    以 ``target`` 为目标单元格宽度四舍五入，再夹到 ``[minimum, maximum]``：

    * 932px（980 默认窗口）→ 3 列；
    * 1218px（1280 窗口）→ 4 列；
    * 1373px（1440 窗口）→ 5 列。

    只用整数运算，避免 ``round(4.5) == 4`` 这种银行家舍入在临界点上跳变。
    """
    try:
        width = int(viewport_width)
    except (TypeError, ValueError):
        width = 0
    target = max(1, int(target))
    lowest = max(1, int(minimum))
    highest = max(lowest, int(maximum))
    if width <= 0:
        return lowest
    columns = (width + target // 2) // target
    return max(lowest, min(highest, columns))


def first_visible_index(views: Any, offset: int) -> int:
    """v1.0 需求：：视口顶部第一个可见条目的下标。

    ``views`` 按显示顺序排列，且 bottom 单调递增（列表单列、网格逐行），
    所以用二分而不是遍历。切换视图时把这一条目当作锚点，比按滚动比例
    换算更准：列表一行一个、网格一行四个，条目密度差太多，比例会漂。
    列表为空返回 0；全部在上面时返回最后一个下标。
    """
    items = list(views or ())
    if not items:
        return 0
    offset = max(0, int(offset))
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi) // 2
        # bottom == offset 时该条已经完整滚出视口，锚点要取它下面那条。
        if items[mid].geometry().bottom() <= offset:
            lo = mid + 1
        else:
            hi = mid
    return min(lo, len(items) - 1)


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


@dataclass(slots=True)
class _Account:
    cookies: dict[str, str]
    generation: int
    has_session: bool
    has_csrf: bool
    error: BaseException | None = None


class WatchLaterPage(BrowserWindow):
    closed = Signal()
    preferences_changed = Signal(object)
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
        self.preferences = read_preferences(context)
        self._settings_dialog = None
        self._settings_panel_cache = None
        self._placement_picker = None
        self._layout_provider = None
        self.wall = wall
        self._shutting_down = False
        self._visible = False
        self._items: list[WatchLaterItem] = []
        self._selected_key = None
        self._placement_handler = None
        self._rows: list[RowCard] = []
        self._rows_by_key: dict[str, RowCard] = {}
        self._rows_by_cover: dict[str, list[RowCard]] = {}
        # v1.0 需求：：网格视图（4 列卡片）与列表视图共用同一份筛选结果。
        self._cards: list[GridCard] = []
        self._cards_by_key: dict[str, GridCard] = {}
        self._cards_by_cover: dict[str, list[GridCard]] = {}
        # v1.0 需求 5：打开稍后再看窗口优先以网格模式排列视频。
        self._view_mode = self.preferences["default_view"]
        self._cover_views: set[RowCard | GridCard] = set()
        # v1.0 需求 4：当前网格列数（按 viewport 宽度自适应，见 _fit_grid）。
        self._grid_columns = GRID_COLUMNS
        self._grid_cell_size: tuple[int, int] | None = None
        self._filter_cache: tuple[tuple, list[WatchLaterItem]] | None = None
        self._items_version = 0
        self._destructive_ready: bool | None = None
        self._cookies: dict[str, str] = {}
        self._has_session = False
        self._has_csrf = False
        self._generation = 0
        self._loaded_once = False
        self._loaded_at = 0.0
        self._last_update_epoch = 0.0
        self._request_seq = 0
        self._operation_seq = 0
        self._active_list: ListRequest | None = None
        self._active_operation: Any = None
        self._busy_operation = ""
        self._pending_remove: WatchLaterItem | None = None
        self._requests: set[Any] = set()
        self._login_expired = False
        self._last_open_at = 0.0
        self._last_open_url = ""
        self._scroll_refetch_connected = False
        # v1.0：分批构建期间的让出/重入守卫（同步语义不变）。
        self._building_rows = False
        self._render_pending = False
        # v1.0：首屏分页状态。_render_limit 是当前已物化的行数上限，
        # _render_total 是筛选后的完整条数；两者相等表示已全部展开。
        self._render_limit = self._initial_limit()
        self._render_total = 0
        self._grow_pending = False
        self._grow_busy = False
        # v1.0：弹窗「播放」回调与选片目标横幅（右键选片与侧栏同一窗口）。
        self._play_handler = None
        self._pick_label: str | None = None
        # v1.0 需求 10/11：多选快速删除状态机 + 快速选片入口。
        self._multiselect_state = MULTISELECT_IDLE
        self._multi_selected_keys: set[str] = set()
        self._multi_remove_queue: list[str] = []
        self._quick_pick_active = False
        # v1.0 需求 3：窗口不允许最小化（去掉最小化按钮 + 拒绝最小化状态）。
        self._window_flag_guard = False
        self._minimize_guard = False

        self.covers = CoverLoader(self)
        self.covers.image_ready.connect(self._cover_ready)
        self.covers.diagnostic.connect(self._log)

        # v1.0 需求：：弹幕数提供器懒创建（首次可见区补取时才建）。
        self._danmaku: DanmakuCountProvider | None = None

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
        self.covers.pause()
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
        if self._visible and not self._shutting_down:
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
        return self.grid_scroll if self._view_mode == "grid" else self.list_scroll

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
        """删除成功后原地重排：保留分页与滚动位置，且只重绘一次。

        旧实现直接 ``_render()``，``_items_version`` 一变就把 ``_render_limit``
        打回首屏（列表 50 行 / 网格 16 张）：滚动很深时几百个控件被
        deleteLater、滚动条 maximum 骤降，整页跳一下并逐行闪。现在：
        * ``keep_position=True`` → 不回退分页、不清隐藏视图的封面；
        * 重建期间 ``setUpdatesEnabled(False)`` → Qt 只在结束时统一重绘；
        * 最后把滚动值夹回新的 maximum，保持原来的视觉锚点。
        多选删除串行推进时每条都走这里，不会越删越回到顶部。
        """
        if self._shutting_down:
            return
        try:
            scroll = self._active_scroll()
            bar = scroll.verticalScrollBar()
            viewport = scroll.viewport()
        except (AttributeError, RuntimeError):
            self._render(keep_position=True)
            return
        saved = bar.value()
        viewport.setUpdatesEnabled(False)
        try:
            self._render(keep_position=True)
            bar.setValue(max(bar.minimum(), min(saved, bar.maximum())))
        finally:
            viewport.setUpdatesEnabled(True)


    def _initial_limit(self) -> int:
        return GRID_INITIAL_CARDS if self._view_mode == "grid" else PAGE_INITIAL_ROWS

    def _visible_anchor_key(self) -> str:
        """v1.0 需求：：当前视口顶部那条视频的 key（切换视图的定位锚点）。

        选「首个可见条目的 key」而不是「滚动比例」：列表一行一条、网格一行
        四条，同一比例在两套布局里落在完全不同的条目上；按 key 定位能把
        同一条视频摆回视口顶部，列表↔网格肉眼对得上。
        """
        bar = self._active_scroll().verticalScrollBar()
        grid = self._view_mode == "grid"
        views = self._cards if grid else self._rows
        if not views:
            return ""
        index = first_visible_index(views, bar.value())
        return str(getattr(views[index].item, "key", "") or "")

    def _ensure_anchor_materialized(self, key: str) -> None:
        """把分页窗口对到锚点条目，保证切换后它真的存在（否则无从定位）。

        窗口不够就扩到锚点；远远超出（列表滚到第 300 行再切网格）就收敛到
        锚点 + 一屏 —— 网格卡片比行重得多，没必要为了一个锚点重建 300 张。
        """
        if not key:
            return
        cached = self._filter_cache
        selected = cached[1] if cached is not None else self._items
        for index, item in enumerate(selected):
            if item.key == key:
                need = index + 1 + self._initial_limit()
                if self._render_limit < need or self._render_limit > 2 * need:
                    self._render_limit = need
                return

    def _restore_anchor(self, key: str) -> bool:
        """把 key 对应的控件滚到视口顶部；找不到时保持当前滚动值。"""
        if not key:
            return False
        grid = self._view_mode == "grid"
        index = self._cards_by_key if grid else self._rows_by_key
        view = index.get(key)
        if view is None:
            return False
        scroll = self._active_scroll()
        # 内容高度要立刻落定：QScrollArea 的 widgetResizable 是延后一轮布局
        # 才更新滚动范围的，否则这里的 setValue 会被旧上限截断（实测切回
        # 列表时会少滚 56px，锚点掉到上一条）。
        try:
            layout = scroll.widget().layout()
            if layout is not None:
                layout.activate()
                scroll.widget().adjustSize()
        except RuntimeError:
            pass
        top = view.geometry().top()
        views = self._cards if grid else self._rows
        if top <= 0 and views and view is not views[0]:
            # 布局还没生效（首帧）时所有 geometry 都是 0；宁可不滚，也不要
            # 在用户在列表中间时把他弹回顶部。
            return False
        scroll.verticalScrollBar().setValue(max(0, top))
        return True

    def _on_view_mode_changed(self, mode: str) -> None:
        if self._shutting_down or mode == self._view_mode:
            return
        anchor = self._visible_anchor_key()
        self._flush_wheel_scrollers()
        self._grow_settle_timer.stop()
        self._cancel_autoscroll()
        self._view_mode = "grid" if mode == "grid" else "list"
        self.view_switch.set_mode(self._view_mode)
        self._release_covers()
        # 未登录/加载失败等状态不因切换视图而被覆盖。
        if self._loaded_once:
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
        if self._shutting_down or not self.isWindow():
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
        if self._shutting_down or self._minimize_guard:
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
        self.fab_bar.setVisible(self._visible and not self._shutting_down)

    def eventFilter(self, watched, event) -> bool:
        if (event.type() == QEvent.Resize
                and watched is self.grid_scroll.viewport()
                and not self._shutting_down):
            self._grid_resize_timer.start()
        return super().eventFilter(watched, event)

    def _fit_grid(self) -> None:
        """v1.0 需求 4：按 viewport 宽度重算列数 + 单元格尺寸（去抖后调用）。

        列数变了只**重排**已有的卡片（不复用重建：卡片本身、封面缓存、悬停
        状态都保留），并用「首个可见条目 key」把滚动位置钉回去；列数没变且
        单元格尺寸也没变时原地返回，滚动/拖动窗口不会做无谓的布局工作。
        """
        if self._shutting_down or self._building_rows or not self._cards or self._view_mode != "grid":
            return
        viewport = self.grid_scroll.viewport()
        gap = self.grid_layout.spacing()
        columns = self.preferences['grid_columns'] or grid_columns_for_width(
            viewport.width(), target=CARD_WIDTHS[self.preferences['card_size']])
        columns_changed = columns != self._grid_columns
        # 先记锚点再重排：重排会改所有卡片的行列，滚动值就没意义了。
        anchor = self._visible_anchor_key() if columns_changed else ""
        if columns_changed:
            self._apply_grid_columns(columns)
        width = max(
            80, (viewport.width() - 4 - (columns - 1) * gap) // columns
        )
        # Card height follows its 16:9 cover, independent of viewport height.
        height = round(width * 9 / 16) + 140
        size = (width, height)
        if size != self._grid_cell_size or columns_changed:
            self._grid_cell_size = size
            for card in self._cards:
                card.fit_cell(width, height)
            self.grid_layout.activate()
        if columns_changed:
            # 卡片新尺寸生效后再把锚点那条视频钉回视口顶部。
            self._restore_anchor(anchor)
        self._update_fabs()
        self._schedule_visible_covers()

    def _apply_grid_columns(self, columns: int) -> None:
        """把 ``self._cards`` 按新列数重新落位（只 removeWidget + addWidget）。

        全程不 destroy 任何卡片，也不重设样式表；QGridLayout 对已经 parent 在
        同一容器里的控件重新 addWidget 只是改位置，代价远低于重建。
        """
        columns = max(1, int(columns))
        self._grid_columns = columns
        for col in range(max(columns, GRID_MAX_COLUMNS)):
            self.grid_layout.setColumnStretch(col, 1 if col < columns else 0)
        for index, card in enumerate(self._cards):
            row, col = divmod(index, columns)
            slot = self.grid_layout.itemAtPosition(row, col)
            if slot is not None and slot.widget() is card:
                continue
            self.grid_layout.addWidget(card, row, col)

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
        if not self._loaded_once:
            self.count_label.setText("")
            return
        if total == 0:
            text = "共 0 个"
        elif filtered:
            text = f"显示 {shown} / 共 {total} 个"
        else:
            text = f"共 {total} 个"
        if self._last_update_epoch:
            text += " · " + time.strftime(
                "%H:%M", time.localtime(self._last_update_epoch)
            ) + " 更新"
        self.count_label.setText(text)

    def _set_refreshing(self, active: bool) -> None:
        ready = (not active and not self._busy_operation and self._has_session
                 and not self._shutting_down)
        self.refresh_button.setText("刷新中…" if active else "刷新")
        self.refresh_button.setEnabled(ready)
        self.fab_refresh.set_busy(active)
        self.fab_refresh.setEnabled(ready)
        self._refresh_shortcut.setEnabled(ready)
        self._update_fabs()

    def _update_destructive_controls(self) -> None:
        ready = (
            self._has_session
            and self._has_csrf
            and bool(self._items)
            and not self._busy_operation
            and not self._shutting_down
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
        if self._shutting_down:
            return "插件正在关闭"
        if not self._has_session:
            return "请先登录 B 站"
        if item.aid <= 0:
            return "该条目缺少 aid，请到网页版处理"
        if not self._has_csrf:
            return "登录信息不完整，请重新登录后再试"
        if self._busy_operation:
            return "正在执行其他操作，请稍候"
        return ""

    # -------------------------------------------------------------- states
    def _show_loading(self) -> None:
        # v1.0 需求 3：骨架形态跟随当前视图（默认网格），不再固定列表样式。
        self.loading_view.set_mode(self._view_mode)
        self.stack.setCurrentWidget(self.loading_view)
        self.loading_view.set_state("正在加载稍后再看…", loading=True)
        self._update_header(0, len(self._items))

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
        if self._items:
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
        """重排当前视图。

        ``keep_position`` 用于「删掉一条」这类原地小改：数据版本变了但
        用户的滚动位置、已展开的分页量、隐藏视图里的封面都应保留，否则
        几百个控件会被销毁重建、滚动条范围骤降，整页跳一下并逐行闪。
        """
        if self._shutting_down:
            return
        if self._building_rows:
            # 分批让出期间被定时器触发的重入：延后到构建完成后统一重排，
            # 避免两个 _rebuild_rows 同时改写 _rows / _rows_by_key。
            self._render_pending = True
            return
        self.browse_state.validate_account()
        total = len(self._items)
        if not total:
            self._clear_rows()
            if self._loaded_once:
                self.empty_view.set_state(
                    "稍后再看是空的",
                    "在 B 站点击“稍后再看”后，视频会出现在这里。",
                    primary="打开 B 站",
                )
                self.stack.setCurrentWidget(self.empty_view)
                self._update_header(0, 0)
            else:
                self.stack.setCurrentWidget(self.loading_view)
            self._update_destructive_controls()
            return

        query = self.search.text()
        filter_mode = self.filter_combo.currentData() or FILTER_ALL
        sort_mode = self.sort_combo.currentData() or SORT_RECENT
        # Cache the filtered/sorted view: repeated renders with unchanged inputs
        # (cover deliveries, status flashes, destructive-control refreshes) used
        # to re-run filter+sort over the whole list every time.
        signature = (query, filter_mode, sort_mode, self._items_version)
        cached = self._filter_cache
        prev_signature = cached[0] if cached is not None else None
        if cached is not None and cached[0] == signature:
            selected = cached[1]
        else:
            selected = filtered_items(
                self._items, query, filter_mode, sort_mode
            )
            self._filter_cache = (signature, selected)
        # v1.0：新查询（搜索/筛选/排序/数据变化）回到首屏；滚动追加时
        # signature 不变，_render_limit 保持已展开的行数，不重复回退。
        # 删除一条只让筛选结果少一项，不该把用户已经展开的分页打回首屏。
        if reset_scroll or (prev_signature != signature and not keep_position):
            self._render_limit = self._initial_limit()
        shown = len(selected)
        self._render_total = shown
        filtered = bool(query.strip()) or filter_mode != FILTER_ALL
        self._update_header(shown, total, filtered)
        if not shown:
            self._clear_rows()
            self.noresult_view.set_state(
                "没有匹配的视频",
                "试试更换关键词，或清除当前筛选条件。",
                primary="清除搜索/筛选",
            )
            self.stack.setCurrentWidget(self.noresult_view)
            self._update_destructive_controls()
            return

        # 隐藏视图的清理会清掉里面所有卡片的封面并 deleteLater 首批之外的
        # 控件；删除一条时跳过它，避免下次切换视图看到一片重新加载。
        if not keep_position:
            self._prune_inactive_view(selected)
        self.browse_state.materialize()
        if self._view_mode == "grid":
            self._rebuild_cards(selected)
        else:
            self._rebuild_rows(selected)
        scroll = self._active_scroll()
        self.stack.setCurrentWidget(scroll)
        scroll.widget().layout().activate()
        if self._view_mode == "grid":
            self._fit_grid()
        self._update_destructive_controls()
        if reset_scroll:
            scroll.verticalScrollBar().setValue(0)
        self.browse_state.restore()
        self._update_fabs()
        # 分批构建期间被跳过的可见区补取，在整页落定后再排一次。
        if self._visible:
            self._schedule_visible_covers()

    def _release_covers(self) -> None:
        self.card_preview.stop()
        for view in self._cover_views:
            view.cover.set_pixmap(None)
        self._cover_views.clear()

    def _drop_view(self, view, layout) -> None:
        if self.card_preview.target is view.cover:
            self.card_preview.stop()
        self._cover_views.discard(view)
        layout.removeWidget(view)
        view.hide()
        view.cover.set_pixmap(None)
        view.deleteLater()

    def _prune_inactive_view(self, items: list[WatchLaterItem]) -> None:
        """隐藏视图只保留首批可复用控件；不创建，也不持有历史封面。"""
        grid = self._view_mode != "grid"
        views = self._cards if grid else self._rows
        index = self._cards_by_key if grid else self._rows_by_key
        layout = self.grid_layout if grid else self.list_layout
        limit = GRID_INITIAL_CARDS if grid else PAGE_INITIAL_ROWS
        wanted = {item.key: item for item in items[:limit]}
        for key in list(index):
            view = index[key]
            if key not in wanted:
                self._drop_view(index.pop(key), layout)
            else:
                view.update_item(wanted[key])
                view.cover.set_pixmap(None)
                self._cover_views.discard(view)
        views[:] = [view for view in views if view.item.key in index]
        covers = self._cards_by_cover if grid else self._rows_by_cover
        covers.clear()
        for view in views:
            if view.item.cover_url:
                covers.setdefault(view.item.cover_url, []).append(view)

    def _clear_rows(self) -> None:
        self._release_covers()
        while self.grid_layout.count():
            entry = self.grid_layout.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._cards.clear()
        self._cards_by_key.clear()
        self._cards_by_cover.clear()
        self._grid_cell_size = None
        self._grid_resize_timer.stop()
        self._cover_scroll_timer.stop()
        self._grow_settle_timer.stop()
        self._filter_cache = None
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._rows.clear()
        self._rows_by_key.clear()
        self._rows_by_cover.clear()
        self._destructive_ready = None
        # v1.0：清空列表时回到首屏分页，避免下一批数据沿用旧的展开量。
        self._render_limit = self._initial_limit()
        self._render_total = 0
        self._grow_pending = False
        self.list_layout.addStretch(1)

    def _make_row(self, item: WatchLaterItem) -> RowCard:
        row = RowCard(
            item,
            self.covers.cached,
            self._remove_reason,
            self.list_widget,
            lazy_cover=True,
        )
        row.open_requested.connect(self._open_item)
        row.copy_requested.connect(self._copy_item)
        row.remove_requested.connect(self._confirm_remove)
        row.play_requested.connect(self._play_item)
        self.card_preview.watch(row.cover)
        row.selection_requested.connect(self._select_item)
        self._sync_card_action(row)
        row.double_play_requested.connect(self._on_double_play)
        row.selection_toggled.connect(self._on_multi_toggle)
        row.set_multi_mode(
            multiselect_is_confirming(self._multiselect_state),
            item.key in self._multi_selected_keys,
        )
        return row

    def _rebuild_cards(self, items: list[WatchLaterItem]) -> None:
        wanted = {item.key for item in items[:self._render_limit] if item.key}
        existing = self._cards_by_key
        for key in list(existing):
            if key not in wanted:
                self._drop_view(existing.pop(key), self.grid_layout)
        ordered = []
        visible_items = items[:self._render_limit]
        allow_yield = (
            self._visible
            and not self._shutting_down
            and len(visible_items) >= GRID_BUILD_YIELD_MIN
            and QApplication.instance() is not None
        )
        created = 0
        for item in visible_items:
            if not item.key:
                continue
            card = existing.get(item.key)
            if card is None:
                card = GridCard(item, self.covers.cached, self._remove_reason,
                                self.grid_widget, lazy_cover=True)
                card.open_requested.connect(self._open_item)
                card.copy_requested.connect(self._copy_item)
                card.remove_requested.connect(self._confirm_remove)
                card.play_requested.connect(self._play_item)
                self.card_preview.watch(card.cover)
                card.selection_requested.connect(self._select_item)
                self._sync_card_action(card)
                card.double_play_requested.connect(self._on_double_play)
                card.selection_toggled.connect(self._on_multi_toggle)
                existing[item.key] = card
                if self._grid_cell_size is not None:
                    card.fit_cell(*self._grid_cell_size)
                created += 1
                if allow_yield and created % GRID_BUILD_BATCH == 0:
                    self._yield_during_build()
            else:
                card.update_item(item)
            position = len(ordered)
            row, col = divmod(position, self._grid_columns)
            slot = self.grid_layout.itemAtPosition(row, col)
            if slot is None or slot.widget() is not card:
                self.grid_layout.addWidget(card, row, col)
            card.show()
            ordered.append(card)
        self._cards = ordered
        self._cards_by_cover = {}
        multi_active = multiselect_is_confirming(self._multiselect_state)
        for card in ordered:
            card.set_multi_mode(
                multi_active, card.item.key in self._multi_selected_keys
            )
            if card.item.cover_url:
                self._cards_by_cover.setdefault(card.item.cover_url, []).append(card)
            if self._danmaku is not None:
                if card.item.danmaku_count is None:
                    count = self._danmaku.cached(card.item.bvid)
                    if count is not None:
                        card.set_danmaku_count(count)
                if card.item.view_count is None:
                    views = self._danmaku.cached_view(card.item.bvid)
                    if views is not None:
                        card.set_view_count(views)
        self._destructive_ready = None

    def _rebuild_rows(self, items: list[WatchLaterItem]) -> None:
        """Reuse row widgets keyed by item key; only build what is new.

        v1 destroyed and rebuilt every RowCard on each search keystroke.  That
        cost ~0.5 ms per card, so a 673-item list paid ~350 ms per keystroke.
        Reusing by key keeps the cost proportional to the change, not the list.

        v1.0: a cold first paint still creates every RowCard in one go
        (~0.8 s with 641 rows).  While the window is visible we yield to the
        event loop every ROW_BUILD_BATCH new rows, so Qt can paint and respond;
        the call stays synchronous, so _rows is complete on return.

        v1.0: yielding only spreads the freeze out, it does not remove the
        work.  We now materialize just the first _render_limit entries and
        append more batches as the user approaches the bottom of the list.
        _rows therefore contains only the rows that actually exist as widgets.
        """
        # 数据层 items 仍是完整筛选结果；只有这段窗口会被做成 QWidget。
        visible_items = items[: min(len(items), self._render_limit)]
        wanted = {item.key: item for item in visible_items if item.key}
        existing = self._rows_by_key

        # Reuse rows whose item is still present; drop the rest.
        stale = [key for key in existing if key not in wanted]
        for key in stale:
            self._drop_view(existing.pop(key), self.list_layout)

        allow_yield = (
            self._visible
            and not self._shutting_down
            and len(visible_items) >= ROW_BUILD_YIELD_MIN
            and QApplication.instance() is not None
        )
        ordered: list[RowCard] = []
        created = 0
        for item in visible_items:
            key = item.key
            row = existing.get(key) if key else None
            if row is None:
                if not key:
                    continue
                row = self._make_row(item)
                existing[key] = row
                created += 1
                if allow_yield and created % ROW_BUILD_BATCH == 0:
                    self._yield_during_build()
            else:
                row.update_item(item)
            ordered.append(row)

        # Re-seat in display order; Qt keeps already-parented widgets cheap.
        for position, row in enumerate(ordered):
            current = self.list_layout.itemAt(position)
            if current is not None and current.widget() is row:
                continue
            self.list_layout.insertWidget(position, row)

        # Drop the trailing stretch that _clear_rows left behind, then re-add it.
        last = self.list_layout.count() - 1
        if last >= 0:
            tail = self.list_layout.itemAt(last)
            if tail is not None and tail.widget() is None:
                self.list_layout.takeAt(last)
        self.list_layout.addStretch(1)

        self._rows = ordered
        self._rows_by_cover = {}
        for row in ordered:
            cover = row.item.cover_url
            if cover:
                self._rows_by_cover.setdefault(cover, []).append(row)
        # v1.0 需求：：重建/复用行时，已缓存的弹幕数直接显示，不再等待信号。
        provider = self._danmaku
        if provider is not None:
            for row in ordered:
                if row.item.bvid and row.item.danmaku_count is None:
                    count = provider.cached(row.item.bvid)
                    if count is not None:
                        row.set_danmaku_count(count)
                # v1.0 需求：：播放量同样走缓存补齐。
                if row.item.bvid and row.item.view_count is None:
                    views = provider.cached_view(row.item.bvid)
                    if views is not None:
                        row.set_view_count(views)
        self._destructive_ready = None
        if self._render_pending:
            self._render_pending = False
            QTimer.singleShot(0, self._render)

    def _yield_during_build(self) -> None:
        """Let Qt paint/respond between row batches.

        The _render() call stays synchronous: the complete row list is
        published when it returns.  Re-entrant renders are deferred via
        _render_pending instead of mutating half-built state.
        """
        self._building_rows = True
        try:
            try:
                QCoreApplication.processEvents(
                    QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 1
                )
            except (TypeError, RuntimeError):
                QCoreApplication.processEvents()
        finally:
            self._building_rows = False

    def _cover_ready(self, url: str) -> None:
        if self._shutting_down or self._building_rows:
            return
        # v1 scanned every row per delivered cover (O(n) each).  Index by cover
        # URL instead, so a delivery touches only the rows that show it.
        index = self._cards_by_cover if self._view_mode == "grid" else self._rows_by_cover
        for row in index.get(url, ()):
            if row in self._cover_views and self._visible:
                row.refresh_cover()
        # A delivery drains the pending queue by one.  Re-arm the debounced
        # visible-row check so rows that were dropped while the queue was full
        # get their get() retried once slots free up (no extra scroll needed).
        self._schedule_visible_covers()

    # -------------------------------------------------- visible-cover refill
    def _danmaku_provider(self) -> DanmakuCountProvider | None:
        """v1.0 需求：：弹幕数提供器懒创建；shutdown 后不再重建。"""
        if self._shutting_down:
            return None
        if self._danmaku is None:
            try:
                self._danmaku = DanmakuCountProvider(self)
                self._danmaku.count_ready.connect(self._danmaku_ready)
                # v1.0 需求：：播放量与弹幕数同一次请求回来。
                self._danmaku.view_ready.connect(self._view_count_ready)
                if self._visible:
                    self._danmaku.resume()
            except Exception:
                self._danmaku = None
        return self._danmaku

    def _danmaku_ready(self, bvid: str, count: int) -> None:
        if self._shutting_down:
            return
        # 两种视图可能共享同一 item；每个 meta 都要更新，不能首个命中就返回。
        for index in (self._rows_by_key, self._cards_by_key):
            view = index.get(bvid)
            if view is not None:
                view.set_danmaku_count(count)
        # 行已滚出/被筛选掉：写回模型供下次物化。
        for item in self._items:
            if item.bvid == bvid:
                item.danmaku_count = count
                break

    def _view_count_ready(self, bvid: str, count: object) -> None:
        """v1.0 需求：：播放量到位，只改 meta 行，不重建行/卡。"""
        if self._shutting_down or count is None:
            return
        try:
            value = max(0, int(count))
        except (TypeError, ValueError):
            return
        for index in (self._rows_by_key, self._cards_by_key):
            view = index.get(bvid)
            if view is not None:
                view.set_view_count(value)
        for item in self._items:
            if item.bvid == bvid:
                item.view_count = value
                break

    def _connect_scroll_refetch(self) -> None:
        if self._scroll_refetch_connected:
            return
        for scroll in (self.list_scroll, self.grid_scroll):
            scroll.verticalScrollBar().valueChanged.connect(self._schedule_visible_covers)
            scroll.verticalScrollBar().valueChanged.connect(self._update_fabs)
            scroll.verticalScrollBar().rangeChanged.connect(self._schedule_visible_covers)
        self._scroll_refetch_connected = True

    def _disconnect_scroll_refetch(self) -> None:
        if not self._scroll_refetch_connected:
            return
        for scroll in (self.list_scroll, self.grid_scroll):
            bar = scroll.verticalScrollBar()
            for signal, slot in ((bar.valueChanged, self._schedule_visible_covers),
                                 (bar.valueChanged, self._update_fabs),
                                 (bar.rangeChanged, self._schedule_visible_covers)):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._scroll_refetch_connected = False

    def _schedule_visible_covers(self, *_args) -> None:
        if self._shutting_down or not self._visible or self._building_rows or self._inline_panel is not None:
            return
        # Deliveries and scrollbar signals must not indefinitely reset refill.
        if not self._cover_scroll_timer.isActive():
            self._cover_scroll_timer.start(COVER_REFETCH_DEBOUNCE_MS)
        self._maybe_grow_page()

    # ------------------------------------------------------ incremental paging
    def _maybe_grow_page(self) -> None:
        """接近底部时排一次追加；**不重启**排队中的定时器（节流，不是去抖）。

        旧实现在每个 bar.valueChanged 里 ``timer.start()``：`start()` 会重置
        计时，于是只要还在滚，settle 就永远不会到期 —— 表现就是「滚到底就没了，
        必须停一下才长出下一批」，一边滚一边等永远等不到。现在改成节流：
        已经在排队就直接返回，让它到期后追加一批；持续滚动时每
        GROW_SETTLE_MS 追加一批，用户不必停手。

        成本可控的前提是 `_rebuild_cards` 已经按 GRID_BUILD_BATCH（4 张）分批
        让出事件循环，一批 16 张是 4 段 ~5ms，不会出现 20ms 的整块冻结。
        """
        if (
            self._shutting_down
            or not self._visible
            or self._building_rows
            or self._grow_busy
            or self._render_limit >= self._render_total
            or self.stack.currentWidget() is not self._active_scroll()
        ):
            return
        if self._grow_settle_timer.isActive():
            # 已有一批在排队：不要 restart（去抖会让"一直滚"永远等不到追加）。
            return
        bar = self._active_scroll().verticalScrollBar()
        threshold = GRID_PREFETCH_PX if self._view_mode == "grid" else PAGE_PREFETCH_PX
        if bar.maximum() - bar.value() > threshold:
            return
        self._grow_pending = True
        self._grow_settle_timer.start(GROW_SETTLE_MS)

    def _grow_page(self) -> None:
        """追加一批 RowCard；只有当前分页窗口内的条目会被物化。"""
        self._grow_pending = False
        if (
            self._shutting_down
            or not self._visible
            or self._inline_panel is not None
            or self._building_rows
            or self.stack.currentWidget() is not self._active_scroll()
            or self._render_limit >= self._render_total
        ):
            return
        bar = self._active_scroll().verticalScrollBar()
        threshold = GRID_PREFETCH_PX if self._view_mode == "grid" else PAGE_PREFETCH_PX
        if bar.maximum() - bar.value() > threshold:
            return
        self._grow_busy = True
        try:
            grow = GRID_GROW_CARDS if self._view_mode == "grid" else PAGE_GROW_ROWS
            self._render_limit = min(self._render_total, self._render_limit + grow)
            # keep_position=True：追加是"往下接一段"，不该顺手把隐藏视图里的
            # 卡片全部 prune 一遍（那是给删除一条准备的路径）。滚动中每 60ms
            # 追加一批，prune 会把隐藏视图的封面反复扔掉又装回来。
            self._render(keep_position=True)
        finally:
            self._grow_busy = False
        # 内容仍不足以产生滚动条时（超长窗口）继续补，避免用户永远无法
        # 触发追加；_render_limit 到达总数后自然停止，不会无限排队。
        # 需求 3：此刻并没有滚动发生，不必等 settle，直接 0ms 续摊
        # （改成直调 _grow_page，绕开 _maybe_grow_page 的 120ms 停稳闸门）。
        if (
            self._render_limit < self._render_total
            and bar.maximum() == 0
        ):
            QTimer.singleShot(0, self._grow_page)

    def _refetch_visible_covers(self) -> None:
        """Re-run get() for the rows currently in (or just outside) the viewport.

        CoverLoader.get() drops a URL when its pending queue is full, so a
        quiet jump to a far region otherwise leaves permanent placeholders.
        Only a handful of visible rows is touched - never the whole list - so
        the MAX_PENDING memory guard is never re-filled in one pass.

        v1.0 滚动性能：「拉取带」与「保留带」分开。拉取带只有视口 ±1 行（真的
        要显示的），保留带是 ±GRID_KEEP_ROWS 行。旧实现用同一套 ±1 行同时
        `set_pixmap(None)`，于是滚过半屏就把刚加载好的封面全部释放，滚回来再
        逐张重建预合成位图（每张 ~0.35ms，一屏 24 张 ≈ 8ms），肉眼就是
        「之前加载好的封面又要重新加载一次，很卡」。现在只有离视口超过保留带
        的卡片才释放，正常来回滚不会再触发重建。
        """
        if self._shutting_down or not self._visible or self._building_rows or self._inline_panel is not None:
            return
        grid = self._view_mode == "grid"
        rows = self._cards if grid else self._rows
        scroll = self._active_scroll()
        if not rows or self.stack.currentWidget() is not scroll:
            self._release_covers()
            return
        viewport_rect = scroll.viewport().rect()
        if viewport_rect.isEmpty():
            return
        spacing = self.grid_layout.spacing() if grid else self.list_layout.spacing()
        stride = max(60, rows[0].geometry().height() + spacing)
        margin_rows = GRID_VISIBLE_MARGIN if grid else _VISIBLE_ROW_MARGIN
        keep_rows = GRID_KEEP_ROWS if grid else _VISIBLE_ROW_MARGIN + 2
        margin = stride * margin_rows
        offset = scroll.verticalScrollBar().value()
        band_top = offset - margin
        band_bottom = offset + viewport_rect.height() + margin

        def _first_row_at(top: int) -> int:
            """Rows are stacked top-to-bottom, so their bottoms are monotonic."""
            lo, hi = 0, len(rows)
            while lo < hi:
                mid = (lo + hi) // 2
                if rows[mid].geometry().bottom() < top:
                    lo = mid + 1
                else:
                    hi = mid
            return lo

        index = _first_row_at(band_top)
        # Hard cap also bounds the degenerate "layout not finished" case.
        budget = viewport_rect.height() // stride + 2 * margin_rows + 2
        if grid:
            budget = min(GRID_COVER_BUDGET, budget * self._grid_columns)
        visible = []
        while index < len(rows) and len(visible) < budget:
            row = rows[index]
            if row.geometry().top() > band_bottom:
                break
            visible.append(row)
            index += 1

        # 保留带：视口上下各 keep_rows 行。只有落在带外的才释放封面。
        keep_margin = stride * keep_rows
        retain_top = offset - keep_margin
        retain_bottom = offset + viewport_rect.height() + keep_margin
        start = _first_row_at(retain_top)
        retain = set()
        walk = start
        while walk < len(rows):
            row = rows[walk]
            if row.geometry().top() > retain_bottom:
                break
            retain.add(row)
            walk += 1
        for row in self._cover_views - retain:
            row.cover.set_pixmap(None)
        self._cover_views = retain
        visible.sort(key=lambda row: not (row.geometry().bottom() >= offset
            and row.geometry().top() <= offset + viewport_rect.height()))
        self.covers.prioritize([row.item.cover_url for row in visible])
        provider = self._danmaku_provider()
        for row in visible:
            row.refresh_cover(self.covers.get)
            if provider is not None and row.item.bvid:
                count = provider.get(row.item.bvid)
                if count is not None:
                    row.set_danmaku_count(count)
                # 播放量与弹幕数同批到达，缓存命中时一并刷新。
                views = provider.cached_view(row.item.bvid)
                if views is not None and row.item.view_count != views:
                    row.set_view_count(views)

    # -------------------------------------------------------------- account
    def _read_account(self) -> _Account:
        try:
            snapshot = self.context.credentials("bilibili")
        except Exception as error:
            return _Account({}, self._generation, False, False, error)
        if snapshot is None:
            return _Account({}, self._generation, False, False, None)
        values = getattr(snapshot, "values", None)
        if values is None and isinstance(snapshot, dict):
            values = snapshot
        cookies: dict[str, str] = {}
        try:
            if values is not None:
                for key, value in dict(values).items():
                    if value is not None:
                        cookies[str(key)] = str(value)
        except (TypeError, ValueError) as error:
            return _Account({}, self._generation, False, False, error)
        generation = safe_int(getattr(snapshot, "generation", 0))
        return _Account(
            cookies=cookies,
            generation=generation,
            has_session=bool(cookies.get("SESSDATA", "").strip()),
            has_csrf=bool(cookies.get("bili_jct", "").strip()),
            error=None,
        )

    def _clear_account_data(self, *, clear_credentials: bool = True) -> None:
        self._items.clear()
        self._items_version += 1
        self._clear_rows()
        self.covers.clear()
        # v1.0 需求：：账号数据作废时弹幕数缓存一并清空。
        if self._danmaku is not None:
            self._danmaku.clear()
        self._loaded_once = False
        self._loaded_at = 0.0
        self._last_update_epoch = 0.0
        self._pending_remove = None
        self.error_bar.clear_error()
        if clear_credentials:
            self._cookies = {}
            self._has_session = False
            self._has_csrf = False
        self._clear_status()
        self._set_refreshing(False)
        self._update_header(0, 0)
        self._update_destructive_controls()

    def on_account_changed(self, generation: Any = None) -> None:
        if self._shutting_down:
            return
        self.browse_state.clear()
        self._request_seq += 1
        self._operation_seq += 1
        self._cancel_request(self._active_list)
        self._cancel_request(self._active_operation)
        self._active_list = None
        self._active_operation = None
        self._busy_operation = ""
        self._items.clear()
        self._items_version += 1
        self._clear_rows()
        self.covers.clear()
        # v1.0 需求：：账号数据作废时弹幕数缓存一并清空。
        if self._danmaku is not None:
            self._danmaku.clear()
        self._loaded_once = False
        self._loaded_at = 0.0
        self._last_update_epoch = 0.0
        self._pending_remove = None
        self.error_bar.clear_error()
        self._clear_status()
        self._set_refreshing(False)
        if self.wall is not None:
            try:
                self.wall.clear_bindings()
            except Exception:
                pass

        account = self._read_account()
        if account.generation:
            self._generation = account.generation
        else:
            self._generation = safe_int(generation)
        self._cookies = account.cookies
        self._has_session = account.has_session
        self._has_csrf = account.has_csrf
        self._login_expired = False
        self._set_refreshing(False)
        self._update_destructive_controls()

        if not self._visible:
            return
        if account.error is not None:
            self._show_list_error(
                "无法读取登录信息：" + describe_error(account.error),
                login_expired=False,
            )
            return
        if not account.has_session:
            self._show_login_state(expired=False)
            return
        self._load(manual=False)

    # ---------------------------------------------------------------- load
    def _cache_fresh(self) -> bool:
        return (
            self._loaded_once
            and time.monotonic() - self._loaded_at < CACHE_TTL_SECONDS
        )

    def _load(self, *, manual: bool) -> None:
        # 按钮、菜单、F5、画面墙共用单飞守卫，不取消后重开同一请求。
        if self._shutting_down or self._busy_operation or self._active_list is not None:
            return
        self._clear_status()
        account = self._read_account()
        if account.error is not None:
            self._show_list_error(
                "无法读取登录信息：" + describe_error(account.error),
                login_expired=False,
            )
            return
        if not account.has_session:
            self._clear_account_data()
            self._show_login_state(expired=False)
            return
        if account.generation and account.generation != self._generation:
            self._generation = account.generation
            self._clear_account_data(clear_credentials=False)
        self._cookies = account.cookies
        self._has_session = True
        self._has_csrf = account.has_csrf
        self._update_destructive_controls()

        self._request_seq += 1
        request_id = self._request_seq
        self._cancel_request(self._active_list)
        self._active_list = None

        self.error_bar.clear_error()
        self._set_refreshing(True)
        if not self._loaded_once:
            self._show_loading()

        worker = ListRequest(
            self._cookies, self._generation, request_id
        )
        self._active_list = worker
        worker.completed.connect(self._list_completed)
        worker.failed.connect(self._list_failed)
        self._track(worker)

    def _list_completed(
        self, request_id: int, generation: int, snapshot: Any
    ) -> None:
        if self._shutting_down:
            return
        if request_id != self._request_seq or generation != self._generation:
            return
        self._active_list = None
        self._set_refreshing(False)
        if not isinstance(snapshot, WatchLaterSnapshot):
            self._list_failed(
                request_id,
                generation,
                BiliAPIError("数据格式异常", format_error=True),
            )
            return
        self._items = list(snapshot.items)
        self._items_version += 1
        self._loaded_once = True
        self._loaded_at = time.monotonic()
        self._last_update_epoch = time.time()
        self.error_bar.clear_error()
        self._log(
            "list endpoint=%s status=%s code=0 count=%s total=%s"
            % (
                snapshot.endpoint,
                snapshot.http_status,
                len(snapshot.items),
                snapshot.total,
            )
        )
        self._render()
        self._publish_to_wall()

    def _list_failed(
        self, request_id: int, generation: int, error: Any
    ) -> None:
        if self._shutting_down:
            return
        if request_id != self._request_seq or generation != self._generation:
            return
        self._active_list = None
        self._set_refreshing(False)
        if not isinstance(error, BaseException):
            error = BiliAPIError("请求失败，请重试")
        self._log(
            "list failed endpoint=%s status=%s code=%s"
            % (
                getattr(error, "endpoint", ""),
                getattr(error, "http_status", ""),
                getattr(error, "code", ""),
            )
        )
        login_expired = bool(
            isinstance(error, BiliAPIError) and error.login_required
        )
        self._show_list_error(
            describe_error(error), login_expired=login_expired
        )

    # ---------------------------------------------------------- destructive
    def _confirm_remove(self, item: WatchLaterItem) -> None:
        if self._shutting_down or self._busy_operation:
            return
        reason = self._remove_reason(item)
        if reason:
            self._flash_status(reason)
            return
        self._confirm_action('移除稍后再看', f'从稍后再看移除《{item.title}》？\n此操作会修改当前 B 站账号，无法撤销。',
                             '移除', lambda: self._begin_remove(item))

    def _confirm_action(self, title, message, action, callback):
        self.card_preview.stop()
        self._back_to_browser()
        generation = self._generation
        panel = InlineConfirmation(title, message, action, self)
        self._show_inline_panel(panel, title)
        panel.finished.connect(lambda result: callback() if result == 1 and not self._shutting_down
                               and generation == self._generation else None)

    def _begin_remove(self, item: WatchLaterItem) -> None:
        if self._busy_operation or not self._has_csrf:
            return
        self._cancel_refresh_for_operation()
        self._operation_seq += 1
        request_id = self._operation_seq
        self._pending_remove = item
        self._busy_operation = "remove"
        self.error_bar.clear_error()
        self._set_refreshing(False)
        self._update_destructive_controls()
        worker = RemoveRequest(
            self._cookies,
            self._generation,
            request_id,
            item.aid,
        )
        self._active_operation = worker
        worker.completed.connect(self._remove_completed)
        worker.failed.connect(self._operation_failed)
        self._track(worker)

    def _remove_completed(
        self, request_id: int, generation: int, _payload: Any
    ) -> None:
        if self._shutting_down:
            return
        if (
            request_id != self._operation_seq
            or generation != self._generation
            or self._busy_operation != "remove"
        ):
            return
        item = self._pending_remove
        if item is not None:
            self._items = [
                current
                for current in self._items
                if current.key != item.key
            ]
        self._pending_remove = None
        self._items_version += 1
        self._loaded_at = time.monotonic()
        self._last_update_epoch = time.time()
        self._end_operation()
        self._render_after_remove()
        self._publish_to_wall()
        if not self._multi_remove_queue:
            self._flash_status("已移除")
        self._log("remove endpoint=del status=200 code=0 count=1")
        # v1.0 需求 10：多选删除串行推进下一个（队列空时立即返回）。
        QTimer.singleShot(0, self._pump_multi_remove)

    def _confirm_clear(self) -> None:
        if self._shutting_down or self._busy_operation:
            return
        if not self._has_csrf:
            self._flash_status("登录信息不完整，请重新登录后再试")
            return
        if not self._items:
            self._flash_status("稍后再看已经是空的")
            return
        self._confirm_action('清空稍后再看', '此操作会删除当前 B 站账号的全部稍后再看，无法撤销。',
                             '清空', self._begin_clear)

    def _begin_clear(self) -> None:
        if self._busy_operation or not self._has_csrf:
            return
        self._cancel_refresh_for_operation()
        self._operation_seq += 1
        request_id = self._operation_seq
        self._busy_operation = "clear"
        self.error_bar.clear_error()
        self._set_refreshing(False)
        self._update_destructive_controls()
        worker = ClearRequest(
            self._cookies, self._generation, request_id
        )
        self._active_operation = worker
        worker.completed.connect(self._clear_completed)
        worker.failed.connect(self._operation_failed)
        self._track(worker)

    def _clear_completed(
        self, request_id: int, generation: int, _payload: Any
    ) -> None:
        if self._shutting_down:
            return
        if (
            request_id != self._operation_seq
            or generation != self._generation
            or self._busy_operation != "clear"
        ):
            return
        self._items.clear()
        self._items_version += 1
        self.covers.clear()
        self._loaded_once = True
        self._loaded_at = time.monotonic()
        self._last_update_epoch = time.time()
        self._end_operation()
        self._render()
        self._publish_to_wall()
        self._flash_status("已清空")
        self._log("clear endpoint=clear status=200 code=0 count=0")

    def _operation_failed(
        self, request_id: int, generation: int, error: Any
    ) -> None:
        if self._shutting_down:
            return
        if (
            request_id != self._operation_seq
            or generation != self._generation
            or not self._busy_operation
        ):
            return
        if not isinstance(error, BaseException):
            error = BiliAPIError("操作失败，请重试")
        self._log(
            "operation failed endpoint=%s status=%s code=%s"
            % (
                getattr(error, "endpoint", ""),
                getattr(error, "http_status", ""),
                getattr(error, "code", ""),
            )
        )
        login_expired = bool(
            isinstance(error, BiliAPIError) and error.login_required
        )
        self._end_operation()
        self._multi_remove_queue.clear()  # v1.0：失败即停止后续多选删除
        if login_expired:
            self._clear_account_data()
            self._show_login_state(expired=True)
            return
        self.error_bar.show_error("操作失败：" + describe_error(error))

    def _end_operation(self) -> None:
        self._busy_operation = ""
        self._pending_remove = None
        self._set_refreshing(False)
        self._update_destructive_controls()

    def _cancel_refresh_for_operation(self) -> None:
        self._request_seq += 1
        self._cancel_request(self._active_list)
        self._active_list = None

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
        if self._shutting_down:
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
        if self._shutting_down:
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
        self._multi_remove_queue.clear()
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
        items = [it for it in self._items if it.key in keys]
        reason = self._remove_reason(items[0]) if items else ""
        if reason:
            self._flash_status(reason)
            return
        self._multiselect_state = MULTISELECT_IDLE
        self._multi_selected_keys = set()
        self._apply_multiselect_to_views()
        self._refresh_multi_button()
        self._multi_remove_queue = [it.key for it in items]
        self._pump_multi_remove()

    def _pump_multi_remove(self) -> None:
        if (self._shutting_down or self._busy_operation
                or not self._multi_remove_queue):
            return
        key = self._multi_remove_queue.pop(0)
        item = next((it for it in self._items if it.key == key), None)
        if item is None:
            QTimer.singleShot(0, self._pump_multi_remove)
            return
        self._begin_remove(item)

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
        if not was_pick and not self._shutting_down:
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
        if worker is None:
            return
        try:
            worker.cancel()
        except Exception:
            pass

    def _track(self, worker: Any) -> None:
        self._requests.add(worker)
        worker.finished.connect(
            self._request_finished,
            Qt.QueuedConnection,
        )
        try:
            worker.start()
        except Exception as error:
            self._requests.discard(worker)
            list_failed = self._active_list is worker
            if self._active_list is worker:
                self._active_list = None
            if self._active_operation is worker:
                self._active_operation = None
            dispose_finished_request(worker)
            self._busy_operation = ""
            self._set_refreshing(False)
            self._update_destructive_controls()
            if list_failed:
                self._show_list_error(
                    "请求失败：" + describe_error(error),
                    login_expired=False,
                )
            else:
                self.error_bar.show_error(
                    "操作失败：" + describe_error(error)
                )

    def _request_finished(self) -> None:
        worker = self.sender()
        if worker is not None:
            self._retire(worker)

    def _retire(self, worker: Any) -> None:
        self._requests.discard(worker)
        if self._active_list is worker:
            self._active_list = None
        if self._active_operation is worker:
            self._active_operation = None
        dispose_finished_request(worker)

    def _log(self, message: str) -> None:
        try:
            self.context.log(message)
        except Exception:
            pass

    # ------------------------------------------------------------------ wall
    def _publish_to_wall(self) -> None:
        """Push the current list and session state to the tile controller."""
        wall = self.wall
        if wall is None or self._shutting_down:
            return
        try:
            wall.set_items(self._items)
            wall.set_session_state(
                has_session=self._has_session and self._has_csrf,
                busy=bool(self._busy_operation),
            )
        except Exception:
            pass

    def items_snapshot(self) -> list[WatchLaterItem]:
        """Read-only copy of the current list for the tile picker."""
        return list(self._items)

    def has_session(self) -> bool:
        return bool(self._has_session)

    def pick_item_for_tile(self, parent=None) -> WatchLaterItem | None:
        """Show the picker over the in-memory list; never touches the network."""
        from .picker import WatchLaterPicker

        if self._shutting_down:
            return None
        if not self._items:
            self._flash_status("稍后再看列表为空，请先刷新")
            return None
        return WatchLaterPicker.pick(self._items, parent or self)

    def refresh_from_wall(self) -> None:
        """Refresh requested from a tile menu; keeps the current tile state."""
        if self._shutting_down or self._busy_operation:
            return
        self._load(manual=True)

    def remove_from_wall(self, item: WatchLaterItem) -> None:
        """Remove one item requested from a tile menu, with the same guards."""
        if self._shutting_down or self._busy_operation:
            return
        reason = self._remove_reason(item)
        if reason:
            self._flash_status(reason)
            return
        self._confirm_remove(item)

    # ------------------------------------------------------------ lifecycle
    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._shutting_down:
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
        if self._shutting_down:
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
        self.covers.pause()
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
        self.covers.pause()
        if self._danmaku is not None:
            self._danmaku.pause()
        self._restore_entrance()
        self._save_geometry_now()
        self.closed.emit()
        event.accept()

    def activate(self) -> None:
        if self._shutting_down:
            return
        if self._inline_panel is None:
            self.setFocus(Qt.OtherFocusReason)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
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
        self._request_seq += 1
        self._operation_seq += 1
        self._cancel_request(self._active_list)
        self._cancel_request(self._active_operation)
        for worker in tuple(self._requests):
            self._cancel_request(worker)
        for worker in tuple(self._requests):
            worker.finished.disconnect(self._request_finished)
            for name in ('completed', 'failed'):
                signal = getattr(worker, name, None)
                if signal is not None:
                    signal.disconnect()
            retire_page_request(worker)
            self._requests.discard(worker)
        self._active_list = None
        self._active_operation = None
        self._busy_operation = ""
        self._pending_remove = None
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
        self._items.clear()
        self._items_version += 1
        self._clear_rows()
        self._cookies.clear()
        self._has_session = False
        self._has_csrf = False
        self._update_destructive_controls()
