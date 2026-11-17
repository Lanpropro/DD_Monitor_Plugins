"""DD监控室CE plugin entry for Bilibili watch-later (v1.0).

v1.0 架构（用户实测 11 条返工 +「直接用本体播放器」）：解析出的点播直链
注入**宿主自己的格子播放器**（``window._play_on``），不再自建播放层。
占位符、暂停帧、底栏按钮、全局快捷键、拖拽秒切、弹幕引擎全部回归本体。

宿主证据（只读，不 patch 任何宿主类）：
* app.py ``_play_on(tile, url, profile, headers=...)`` 创建/复用
  ``window.players[tile]`` 并接好宿主自己的状态槽；
* app.py ``_on_player_state`` 只认非空 room_id → 点播格写一个
  **合成房间**（room_id=bvid，live=False，无冒号）：状态机/全屏/拖拽都
  生效，而 ``start_tile`` 对 live=False 只走离线路径，绝不联网取直播；
* app.py 关窗时 EVENT_CLOSING 先于配置保存 → 卸载时把合成房间
  还原成原直播房间/空格，配置里永远不会留下点播房；
* room_placement.py ``hot_swap`` 换的是格子对象位置，播放器不动 →
  点播格拖到播放中的格子无缝秒切；拖到空格走停换回退，房间扫描发现
  合成房间换了格子后自动续播；
* widgets.py 每格自带 ``video_danmaku`` 引擎与底栏「弹」开关 →
  点播弹幕按 time-pos 喂 ``add_event`` 即可，渲染/设置全用本体的。
"""
from __future__ import annotations

import os
import re
import threading
import time

# Defensive logging guards: identical messages are collapsed, credential
# material is scrubbed before it can reach the host log (or the de-dup cache
# keys), and the cache is capped so it cannot grow without bound.
_LOG_DEDUP_WINDOW = 5.0
#: v1.0 需求：：底栏进度条低于这个可用宽度就不显示，改由视频底边蓝线代劳。
BAR_PROGRESS_MIN_WIDTH = 180
_LOG_MAX_KEYS = 64
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"\b(?:SESSDATA|bili_jct|DedeUserID|DedeUserID__ckMd5|sid|access_key|"
    r"refresh_token|buvid3|csrf|csrf_token|Cookie)\b\s*[=:]\s*[^\s,;&]+",
    re.IGNORECASE,
)
_CREDENTIAL_WORD = re.compile(r"\b(?:SESSDATA|Cookie)\b", re.IGNORECASE)

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl

def sip_deleted(widget) -> bool:
    """C++ 对象已析构的 QWidget：定时器回调里必须判一次。"""
    try:
        import shiboken6
        return not shiboken6.isValid(widget)
    except Exception:
        try:
            widget.objectName()
            return False
        except RuntimeError:
            return True


#: v1.0 需求：/3：是否在视频左上角画「N/总数」。用户要求去掉画面上的黑色
#: 斑块（浮窗与墙面都是它），默认关闭；改回 True 即可恢复（实现都还在）。
POSITION_BADGE_ENABLED = False
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QDesktopServices,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QToolButton,
    QWidget,
)

from ddm import plugins as api

# The host loads plugin.py as a single module.  Make sibling modules part of
# this plugin's package, matching the official bili_live plugin pattern.
__path__ = [os.path.dirname(__file__)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__


from .host_playback import HostPlaybackAdapter
from .playback_coordinator import PlaybackCoordinator
from .pip_dpi import PipDpiController
from .playback_input import PlaybackInput, _PipClickFilter, _VodKeyFilter, _SCREEN_CHANGE
from .playback_binding import VodBinding as _VodBinding  # Legacy binding import.





def _resolve_tile(tile_context, window=None):
    """Probe the host for the live ``Tile`` that owns this tile control.

    Walks up from ``tile_context.parent`` through ``parentWidget()`` looking
    for an object that exposes ``video`` / ``bottom`` / ``room`` at once.
    Returns ``None`` on an unknown/older host: callers must degrade
    gracefully instead of raising.  No host class is patched.
    """
    widget = None
    if tile_context is not None:
        try:
            widget = getattr(tile_context, "parent", None)
        except Exception:
            widget = None
    if widget is None:
        widget = window
    seen: set[int] = set()
    for _ in range(64):
        if widget is None or id(widget) in seen:
            break
        seen.add(id(widget))
        try:
            if all(
                hasattr(widget, name)
                for name in ("video", "bottom", "room")
            ):
                return widget
        except Exception:
            pass
        try:
            widget = widget.parentWidget()
        except Exception:
            widget = None
    return None


class _TileEventFilter(QObject):
    """格子事件过滤：右键菜单 + 点播拖放 + 几何同步 + v1.0 播放交互。

    * ContextMenu：空房间格子弹插件选片菜单；**点播格**（watchlater_bvid）
      弹插件自建的 4 项菜单（画质/视频流信息/声道/全屏，用户需求：）；
      直播格一律放行，宿主菜单一个字节不动。
    * DragEnter/DragMove/Drop：只认插件自己的 ``VOD_MIME``（弹窗视频卡
      拖入），宿主 ROOM_MIME/TILE_MIME/DANMAKU_MIME 全部放行。
    * Resize/Move/Show/WinIdChange：排队同步进度条/状态卡几何与全屏态
      （全屏进出会 setWindowFlags → WinIdChange）。
    * 徽章 Show：点播播放期间拦住 stream_badge 的显示（需求：「未开播」）。
    * video 左键 Release/DblClick：点播格按 B 站语义单击暂停/双击全屏
      （需求：）；Press/Move 不拦，宿主 TILE_MIME 拖拽不受影响。
    """

    def __init__(self, plugin) -> None:
        super().__init__()
        self._plugin = plugin

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        plugin = self._plugin
        if plugin._shutting_down:
            return False
        kind = event.type()
        if kind == QEvent.Type.ContextMenu:
            room = getattr(watched, "room", None)
            if isinstance(room, dict) and room.get("watchlater_bvid"):
                try:
                    if plugin._show_vod_tile_menu(watched, event.globalPos()):
                        return True
                except Exception:
                    pass  # 菜单失败就放行给宿主，绝不吞掉事件
            if isinstance(room, dict) and not str(room.get("room_id") or ""):
                try:
                    if plugin._show_empty_tile_menu(watched, event.globalPos()):
                        return True
                except Exception:
                    pass  # 菜单失败就放行给宿主，绝不吞掉事件
            return False
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            from .models import VOD_MIME

            mime = event.mimeData()
            if mime is not None and mime.hasFormat("application/x-ddm-tile"):
                if plugin._handle_tile_drag(watched, event):
                    return True
            if mime is None or not mime.hasFormat(VOD_MIME):
                return False  # 不涉及点播的宿主拖拽，放行
            if kind == QEvent.Type.Drop:
                bvid = bytes(mime.data(VOD_MIME)).decode("utf-8", "ignore").strip()
                event.acceptProposedAction()
                try:
                    plugin._on_video_dropped(watched, bvid)
                except Exception:
                    pass
            else:
                event.acceptProposedAction()
            return True
        # --- v1.0：以下分支只针对 tile 的子控件（video / stream_badge） ---
        if getattr(watched, "room", None) is None:
            parent = None
            try:
                parent = watched.parentWidget()
            except Exception:
                parent = None
            if parent is not None and getattr(parent, "room", None) is not None:
                if kind == QEvent.Type.Show and \
                        getattr(parent, "stream_badge", None) is watched:
                    binding = plugin._binding_for_tile(parent)
                    if binding is not None and binding.state in (
                        "resolving", "playing", "paused"
                    ):
                        # v1.0b：宿主药丸就是我们的「视频排序药丸」。
                        # 播放态让它显示（显隐完全由宿主 _sync_stream_badge 决定，
                        # 因此天然与标题同显同隐）；PiP/取流态仍退回 v1.0 的隐藏。
                        plugin._sync_order_pill(binding)
                        if watched.isHidden():
                            return False
                        if plugin._binding_for_tile(parent) is not None \
                                and binding.state in ("playing", "paused") \
                                and not plugin._tile_in_pip(parent):
                            return False
                        try:
                            watched.hide()
                        except RuntimeError:
                            pass
                        return True
                if watched in (getattr(parent, "video", None),
                               getattr(parent, "pause_overlay", None)):
                    # 需求：：video 的 Resize/Move 在宿主 _layout_areas 写完
                    # video.setGeometry 之后才触发，几何已是终值 → 立即同步
                    # 进度条/蓝线，不排队、不闪（Tile 级 Resize 会早一帧）。
                    if kind in (QEvent.Type.Resize, QEvent.Type.Move,
                                QEvent.Type.WinIdChange, QEvent.Type.DevicePixelRatioChange,
                                _SCREEN_CHANGE):
                        if kind in (QEvent.Type.DevicePixelRatioChange, _SCREEN_CHANGE):
                            # 跨分辨率屏时先盖住白屏重载，再重排浮层。
                            try:
                                plugin._on_dpi_event(parent)
                            except Exception:
                                pass
                            return False
                        plugin._sync_tile_widgets(parent)
                        return False
                    if plugin._tile_in_pip(parent):
                        return False  # PiP 由应用级观察器处理，避免重复暂停
                    if kind == QEvent.Type.MouseButtonDblClick:
                        binding = plugin._binding_for_tile(parent)
                        if binding is not None and binding.state in ("playing", "paused"):
                            plugin._vod_press[id(parent)] = event.globalPosition().toPoint()
                            event.accept()
                            return True
                    # 需求：：不再依赖宿主 _press_pos（宿主仅全屏才写它，
                    # 导致墙面/小窗返回后单击暂停失效）。插件自记 press 全局
                    # 坐标 + 移动阈值判别点击/拖拽。
                    if kind == QEvent.Type.MouseButtonPress and \
                            event.button() == Qt.LeftButton:
                        plugin._vod_press[id(parent)] = event.globalPosition().toPoint()
                        return False
                    if kind == QEvent.Type.MouseMove and \
                            (event.buttons() & Qt.LeftButton):
                        start = plugin._vod_press.get(id(parent))
                        if start is not None and \
                                (event.globalPosition().toPoint() - start).manhattanLength() \
                                >= QApplication.startDragDistance():
                            plugin._vod_press.pop(id(parent), None)
                        return False
                    if kind == QEvent.Type.MouseButtonRelease and \
                            event.button() == Qt.LeftButton:
                        start = plugin._vod_press.pop(id(parent), None)
                        binding = plugin._binding_for_tile(parent)
                        if binding is not None and binding.state in ("playing", "paused"):
                            moved = (
                                start is not None
                                and (event.globalPosition().toPoint() - start).manhattanLength()
                                >= QApplication.startDragDistance()
                            )
                            if start is not None and not moved:
                                event.accept()
                                plugin._on_vod_click(parent, binding)
                                return True
            return False
        if kind == QEvent.Type.DynamicPropertyChange:
            if bytes(event.propertyName()) in (b"pictureInPicture", b"fullscreen"):
                plugin._vod_press.clear()
                plugin._queue_tile_sync(watched)
                QTimer.singleShot(0, plugin._sync_pip_filter)
        elif kind in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show,
                      QEvent.Type.WinIdChange, QEvent.Type.DevicePixelRatioChange,
                      _SCREEN_CHANGE):
            # v1.0 需求：/14：跨屏（ScreenChangeInternal）也要重排浮层，
            # 否则原生子窗口还按旧 DPI 绘制，表现为小窗白屏 / UI 错位。
            if kind in (QEvent.Type.DevicePixelRatioChange, _SCREEN_CHANGE):
                try:
                    plugin._on_dpi_event(watched)
                except Exception:
                    pass
                return False
            plugin._queue_tile_sync(watched)
        return False


def _input_field(name):
    return property(lambda self: getattr(self.input, name),
                    lambda self, value: setattr(self.input, name, value))


def _dpi_field(name):
    return property(lambda self: getattr(self.pip_dpi, name),
                    lambda self, value: setattr(self.pip_dpi, name, value))


def _playback_field(name):
    """Legacy plugin state forwards to the single playback coordinator."""
    return property(lambda plugin:getattr(plugin.playback,name),
                    lambda plugin,value:setattr(plugin.playback,name,value))


class BiliWatchLaterPlugin(api.Plugin):
    _click_timers = _input_field('click_timers')
    _space_shortcut = _input_field('space_shortcut')
    _danmaku_shortcut = _input_field('danmaku_shortcut')
    _seek_left = _input_field('seek_left')
    _seek_right = _input_field('seek_right')
    _seek_host = _input_field('seek_host')
    _danmaku_host = _input_field('danmaku_host')
    _key_filter = _input_field('key_filter')
    _key_down = _input_field('key_down')
    _key_dedup = _input_field('key_dedup')
    _fullscreen_active = _input_field('fullscreen_active')
    _space_host = _input_field('space_host')
    _vod_press = _input_field('vod_press')
    _pip_filter = _input_field('pip_filter')
    _pip_active = _input_field('pip_active')
    _pip_identity = _input_field('pip_identity')

    _dpi_covers = _dpi_field('covers')
    _dpi_cover_timers = _dpi_field('cover_timers')
    _dpi_cover_deadline = _dpi_field('cover_deadline')
    _dpi_pending = _dpi_field('pending')
    _dpi_handling = _dpi_field('handling')
    _dpi_ignore_until = _dpi_field('ignore_until')
    _dpi_flush_timer = _dpi_field('flush_timer')

    _bindings = _playback_field('bindings')
    _vod_requests = _playback_field('vod_requests')
    _vod_context = _playback_field('vod_context')
    _cell_vod = _playback_field('cell_vod')
    _vod_seq = _playback_field('vod_seq')
    _next_prefetch = _playback_field('next_prefetch')
    _account_generation = _playback_field('account_generation')
    _dm_requests = _playback_field('dm_requests')
    _dm_context = _playback_field('dm_context')
    _dm_seq = _playback_field('dm_seq')
    _sub_requests = _playback_field('sub_requests')
    _sub_context = _playback_field('sub_context')
    _sub_seq = _playback_field('sub_seq')
    _tick_timer = _playback_field('tick_timer')

    def __init__(self) -> None:
        super().__init__()
        self.context = None
        self.host = HostPlaybackAdapter(lambda:self.context,self._log)
        self.playback = PlaybackCoordinator(self)
        self.input = PlaybackInput(self)
        self.page = None
        self.action: QAction | None = None
        self.button: QToolButton | None = None
        self.wall = None
        self._registered = False
        self._tile_registered = False
        self._shutting_down = False
        # --- v1.0 点播绑定：bvid -> _VodBinding（合成房间随格子拖拽移动，
        # 节拍器按房间标记扫描当前所在格子，绝不按房间号字符串反查绑定）。
        self._cell_tiles: dict[int, object] = {}
        from .playback_session import PlaybackSession
        self.playback_session = PlaybackSession(self)
        # --- 格子事件过滤器（右键/拖放/几何），同一格子只装一次 ---
        self._tile_filter: _TileEventFilter | None = None
        self._watched_tiles: set[int] = set()
        # --- 弹窗选片目标格（右键「选择稍后再看视频…」与侧栏同一窗口） ---
        self._pick_target_tile = None
        # --- v1.0：全屏底栏进度条（官方 tile control 通道，按 tile 持有） ---
        self._bar_progress: dict[int, dict] = {}
        self._progress_registered = False
        self.pip_dpi = PipDpiController(self, sip_deleted)
        # Avatars share the async image loader; no independently owned QThreads.
        self._face_loader = None
        self._face_pixmaps = {}
        # --- v1.0 需求：：字幕按钮 TileControl 载体（按 tile 持有） ---
        self._subtitle_controls: dict[int, dict] = {}
        #: v1.0 性能：每格最近一次重绘浮层时的几何/形态 key。
        self._overlay_keys: dict[int, tuple] = {}
        self._subtitle_registered = False
        # --- v1.0 需求：：宿主画质按钮 qualityChanged 连接去重 ---
        self._quality_connected: dict[int, tuple] = {}
        self._geometry_pending: set[int] = set()
        # --- logging throttle state (thread-safe; GUI thread normally) ---
        self._log_lock = threading.Lock()
        self._log_last_seen: dict[str, float] = {}

    # ------------------------------------------------------------- lifecycle
    def on_load(self, context) -> None:
        required = (
            "register_workspace_control",
            "unregister_workspace_control",
            "show_workspace",
            "credentials",
            "request_login",
            "notify",
        )
        missing = [
            name
            for name in required
            if not callable(getattr(context, name, None))
        ]
        if missing:
            raise RuntimeError(
                "bili_watchlater 需要 DD监控室CE 0.7.2 或更新版本；"
                "当前宿主缺少接口：" + "、".join(missing)
            )
        if not hasattr(api, "EVENT_ACCOUNT_CHANGED") or not hasattr(
            api, "EVENT_CLOSING"
        ):
            raise RuntimeError(
                "bili_watchlater 需要 DD监控室CE 0.7.2 或更新版本；"
                "当前宿主缺少账号或退出事件"
            )

        self.context = context
        from .watch_order import WatchOrder
        from .preferences import read_preferences
        self.watch_order = WatchOrder(context)
        self.order_sidebar = None
        self._order_container = None
        self._shutting_down = False
        if context.window is None:
            return  # Package validation hosts have no Qt widgets or sidebar.

        from .entry_icon import WatchLaterButton
        self.button = WatchLaterButton()
        self.action = QAction("稍后再看", self.button)
        self.action.setCheckable(True)
        self.action.setToolTip("B站稍后再看")
        self.action.toggled.connect(self._toggle_page)
        self.button.setDefaultAction(self.action)
        self.button.setToolTip("B站稍后再看")
        self.button.setAccessibleName("稍后再看")
        self.button.setIconSize(QSize(18, 18))
        self.button.setIcon(self._make_icon())

        self._registered = bool(
            context.register_workspace_control(
                "watchlater", self.button, priority=40
            )
        )
        if not self._registered:
            self.button.deleteLater()
            self.button = None
            self.action = None
            raise RuntimeError(
                "当前宿主没有可用的功能中心侧栏，"
                "无法注册稍后再看入口"
            )

        self._sync_order_sidebar(read_preferences(context)['show_watch_order'])
        self._register_tile_control(context)
        # 应用级按键过滤器：全屏窗口/画中画浮窗成为活动窗口时，挂在
        # 主窗口上的 QShortcut(Qt.WindowShortcut) 不再派发，必须由这一层补上。
        self._bump_key_filter()
        try:
            self._ensure_danmaku_shortcut()
        except Exception:
            pass
        try:
            self._ensure_seek_shortcuts()
        except Exception:
            pass
        # v1.0 需求：：宿主把每格房间写进配置。上次退出时若停在点播房（合成
        # 房间），重启后那一格还挂着 bvid 却没有播放器 —— 看起来就是「原有
        # 格子没有正确播放历史视频」。延迟一拍等墙体建好，统一清干净。
        try:
            QTimer.singleShot(0, self._sanitize_stale_rooms)
        except Exception:
            pass

    def _sanitize_stale_rooms(self) -> None:
        """清掉重启后残留的合成点播房（只认插件自己的标记，不碰真实直播房）。"""
        if self._shutting_down:
            return
        tiles = self._host_tiles()
        if not tiles:
            # 墙体还没建好（重启后 tiles 可能晚到）：退避重试，最多 10 次。
            retries = getattr(self, "_sanitize_retries", 0)
            if retries < 10:
                self._sanitize_retries = retries + 1
                QTimer.singleShot(400, self._sanitize_stale_rooms)
            return
        self._sanitize_retries = 0
        cleared = 0
        for tile in self._host_tiles():
            # v1.0b：旧版本建的原生子窗口热重载后可能还挂着（透明洞）。
            try:
                for stale in tile.findChildren(QWidget, "watchlaterPosition"):
                    try:
                        stale.clearMask()
                        stale.hide()
                        stale.setParent(None)
                        stale.deleteLater()
                    except RuntimeError:
                        pass
            except Exception:
                pass
            room = getattr(tile, "room", None)
            if not isinstance(room, dict) or not room.get("watchlater_bvid"):
                continue
            try:
                self._stop_host_tile(tile)
            except Exception:
                pass
            try:
                tile.set_room(None)
                tile.set_status("")
            except Exception:
                try:
                    tile.set_room({})
                except Exception:
                    continue
            cleared += 1
        if cleared:
            self._log("cleared stale vod rooms count=%d" % cleared)

    def _register_tile_control(self, context) -> None:
        register = getattr(context, "register_tile_control", None)
        if not callable(register) or not hasattr(api, "TileControl"):
            self._log("tile control unavailable; wall menu disabled")
            return
        try:
            from .wall_menu import WallController

            self.wall = WallController()
            for signal_name, slot in (
                ("play_requested", self._on_play_requested),
                ("restore_requested", self._on_restore_requested),
                ("pick_from_cell", self._on_pick_requested),
                ("pick_requested", self._on_pick_flat),
                ("open_list_requested", self._on_open_list),
                ("refresh_requested", self._on_refresh_requested),
                ("remove_requested", self._on_remove_requested),
            ):
                signal = getattr(self.wall, signal_name, None)
                if signal is not None:
                    try:
                        signal.connect(slot)
                    except Exception:
                        pass
            register("watchlater", self._make_tile_control, priority=60)
            self._tile_registered = True
            # v1.0（需求：）：全屏时交互式进度条进底栏布局，随底栏同隐同显。
            # priority 61 > 零宽载体 60 → TileControls._allocate 把剩余宽度
            # 全给首项（plugin_controls.py），进度条吃满底栏余量。
            try:
                register("watchlater_progress", self._make_progress_control,
                         priority=61)
                self._progress_registered = True
            except Exception as error:
                self._progress_registered = False
                self._log(
                    "progress control registration failed type="
                    + type(error).__name__
                )
            # v1.0 需求：/2：字幕开关回到官方 TileControls（priority 59）。
            # 容器本身紧挨「弹」左侧 → 天然满足「字幕与弹幕互换位置」；
            # 宽度由宿主自己预算，不会再出现「右侧按钮被挤出可视区」。
            try:
                register("watchlater_subtitle", self._make_subtitle_control,
                         priority=59)
                self._subtitle_registered = True
            except Exception as error:
                self._subtitle_registered = False
                self._log(
                    "subtitle control registration failed type="
                    + type(error).__name__
                )
        except Exception as error:
            self.wall = None
            self._tile_registered = False
            self._log(
                "tile control registration failed type="
                + type(error).__name__
            )

    def _make_tile_control(self, tile_context):
        from .watchlater_cell import WatchLaterCell

        plugin = self
        window = None
        if self.context is not None:
            try:
                window = getattr(self.context, "window", None)
            except Exception:
                window = None
        tile = _resolve_tile(tile_context, window)
        if tile is None:
            plugin._log("tile video area probe failed; inline playback disabled")
        else:
            plugin._watch_tile(tile)

        class WatchLaterTileControl(api.TileControl):
            def __init__(self) -> None:
                widget = WatchLaterCell()
                # 底栏零宽状态载体：不占像素，但保持「可见」，宿主的
                # applicable()/set_room 通知链才不断（拖拽换格靠它感知）。
                super().__init__(
                    widget,
                    minimum_width=0,
                    preferred_width=0,
                )
                self.widget = widget
                if plugin.wall is not None:
                    plugin.wall.register_cell(widget)
                plugin._register_tile(widget, tile)

            def set_room(self, room):
                self.widget.set_room(room)
                if plugin.wall is not None:
                    plugin.wall.set_session_state(
                        has_session=plugin._has_session()
                    )
                try:
                    tile_context.invalidate_layout()
                except Exception:
                    pass

            def interaction_active(self):
                # v1.0（需求 11）：播放完全走宿主播放器，格子内没有任何
                # 插件键盘交互。宿主 _handle_shortcut（app.py）只要
                # 看到 True 就全局禁用本体快捷键 —— 必须永远 False。
                return False

            def shutdown(self):
                plugin._forget_cell(self.widget)
                if plugin.wall is not None:
                    plugin.wall.forget_cell(self.widget)
                self.widget.shutdown()

        return WatchLaterTileControl()

    # ------------------------------------------------------------------ wall
    def _has_session(self) -> bool:
        page = self.page
        if page is not None:
            try:
                return bool(page.has_session())
            except Exception:
                return False
        account = self._read_account()
        return bool(account)

    def _read_account(self) -> bool:
        try:
            snapshot = self.context.credentials("bilibili")
        except Exception:
            return False
        if snapshot is None:
            return False
        values = getattr(snapshot, "values", None)
        if values is None:
            return False
        try:
            return bool(str(dict(values).get("SESSDATA") or "").strip())
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------ pick entry
    def _on_pick_requested(self, cell, room=None) -> None:
        """右键「选择稍后再看视频…」：与侧栏按钮打开同一个弹窗（需求 7）。"""
        if self._shutting_down or self.wall is None:
            return
        if room is None and isinstance(cell, dict):
            self._on_pick_flat(cell)
            return
        tile = self._cell_tiles.get(id(cell))
        self._show_page(pick_tile=tile)

    def _on_pick_flat(self, room) -> None:
        """宿主右键菜单的扁平项：无 sender cell，探测格子对象。"""
        if self._shutting_down or self.wall is None:
            return
        tile = getattr(self.wall, "_flat_tile", None)
        if tile is None or self._resolve_cell_from_tile(tile) is None:
            self._notify_warning(
                "没有找到对应的格子，请右键格子画面选择「稍后再看」重试。"
            )
            return
        self._show_page(pick_tile=tile)

    def _resolve_cell_from_tile(self, tile):
        if tile is None or self.wall is None:
            return None
        try:
            cells = list(self.wall._cells.values())
        except Exception:
            return None
        for cell in cells:
            widget = cell
            seen: set[int] = set()
            while widget is not None and id(widget) not in seen:
                if widget is tile:
                    return cell
                seen.add(id(widget))
                try:
                    widget = widget.parentWidget()
                except Exception:
                    break
        return None

    def _on_open_list(self) -> None:
        if self._shutting_down:
            return
        self._set_action_checked(True)
        if not self._show_page():
            self._set_action_checked(False)

    def _on_refresh_requested(self) -> None:
        page = self.page
        if page is None:
            return
        try:
            page.refresh_from_wall()
        except Exception:
            pass

    def _on_remove_requested(self, item) -> None:
        page = self.page
        if page is None:
            return
        try:
            page.remove_from_wall(item)
        except Exception:
            pass

    # ------------------------------------------------------- host capability
    def _host_window(self):
        return self.host.window()

    def _host_tiles(self):
        return self.host.tiles()

    def _playback_tiles(self):
        return self.host.playback_tiles()

    def _host_player(self, tile):
        return self.host.player(tile)

    def _stop_host_tile(self, tile) -> None:
        return self.host.stop(tile)

    def _start_host_tile(self, tile) -> None:
        return self.host.start(tile)

    def _play_on_host(self, tile, url: str, headers: dict, *, start_paused=False) -> bool:
        return self.host.play(tile, url, headers, start_paused=start_paused)

    # --------------------------------------------------------- tile watching
    def _watch_tile(self, tile) -> None:
        if self._shutting_down or id(tile) in self._watched_tiles:
            return
        if self._tile_filter is None:
            self._tile_filter = _TileEventFilter(self)
        try:
            tile.installEventFilter(self._tile_filter)
            self._watched_tiles.add(id(tile))
        except Exception:
            return
        # v1.0：video（单击/双击，需求：）与 stream_badge（拦「未开播」
        # 药丸的 Show，需求：）也装上同一个过滤器。后装的先执行，插件在
        # 宿主自己的 video 过滤器之前拿到事件；只拦点播格的特定事件。
        for name in ("video", "stream_badge", "pause_overlay"):
            child = getattr(tile, name, None)
            if child is None:
                continue
            try:
                child.installEventFilter(self._tile_filter)
            except Exception:
                pass
        # v1.0 需求：：宿主画质按钮 set_quality → qualityChanged(room, qn)。
        # 宿主对 live=False 只写值不重播，插件接住信号自行按 qn 续播。
        quality_signal = getattr(tile, "qualityChanged", None)
        if quality_signal is not None and id(tile) not in self._quality_connected:
            try:
                slot = lambda room, qn, t=tile: self._on_tile_quality_changed(t, room, qn)
                quality_signal.connect(slot)
                self._quality_connected[id(tile)] = (tile, slot)
            except Exception:
                pass

    def _binding_for_tile(self, tile):
        if tile is None:
            return None
        binding = next(
            (b for b in self.playback.bindings.values() if b.tile is tile), None
        )
        if binding is not None:
            return binding
        # 全屏 / 画中画可能用替身格对象：按合成房间的 bvid 反查绑定。
        try:
            room = getattr(tile, "room", None)
            if isinstance(room, dict):
                return self.playback.bindings.get(str(room.get("watchlater_bvid") or ""))
        except Exception:
            pass
        return None

    def _player_for_binding(self, binding):
        """按绑定的格子取宿主播放器；替身格/换格后按 bvid 兜底。"""
        player = self._host_player(binding.tile)
        if player is not None:
            return player
        window = self._host_window()
        players = getattr(window, "players", None)
        try:
            for tile, candidate in dict(players or {}).items():
                room = getattr(tile, "room", None)
                if isinstance(room, dict) and str(
                    room.get("watchlater_bvid") or ""
                ) == str(binding.bvid):
                    return candidate
        except Exception:
            pass
        for tile in self._vod_tiles_anywhere():
            if tile is binding.tile:
                continue
            try:
                room = getattr(tile, "room", None)
                if not (isinstance(room, dict) and str(
                        room.get("watchlater_bvid") or "") == str(binding.bvid)):
                    continue
            except Exception:
                continue
            player = self._host_player(tile)
            if player is not None:
                return player
        return None

    def _make_progress_control(self, tile_context):
        """全屏底栏进度条的官方 TileControl 载体（需求：/8）。

        控件挂在 tile.bottom 布局里：底栏 3 秒 idle 自动隐藏（下滑+淡出）
        时它跟着一起隐；鼠标唤醒底栏时一起显。非全屏/无点播时隐藏，
        applicable() 把它排除，不占底栏宽度。
        """
        from .tile_vod import VodBarProgress

        plugin = self
        tile = _resolve_tile(tile_context, self._host_window())

        class ProgressControl(api.TileControl):
            def __init__(self) -> None:
                widget = VodBarProgress()
                widget.ensurePolished()
                super().__init__(
                    widget, minimum_width=max(BAR_PROGRESS_MIN_WIDTH,
                                              widget.minimumSizeHint().width()),
                    preferred_width=100000
                )
                self.widget = widget
                widget.hide()
                widget.seek_requested.connect(
                    lambda sec, t=tile: plugin._seek_tile_vod(t, sec)
                )
                if tile is not None:
                    plugin._bar_progress[id(tile)] = {
                        "widget": widget,
                        "tile": tile,
                        "ctx": tile_context,
                        "visible": False,
                    }

            def set_room(self, room):
                # 可见性由节拍器按「全屏 + 点播状态」驱动（300ms 内收敛），
                # 显隐翻转时自会 invalidate_layout。宿主 TileControls.set_room
                # 每次房间更新已统一重排，这里不再重复触发（直播格房间
                # 轮询频繁，避免无谓的双倍布局）。
                pass

            def interaction_active(self):
                return False

            def shutdown(self):
                entry = plugin._bar_progress.pop(id(tile), None)
                if entry is not None:
                    try:
                        entry["widget"].hide()
                    except RuntimeError:
                        pass

        return ProgressControl()

    def _seek_tile_vod(self, tile, seconds: float) -> None:
        binding = self._binding_for_tile(tile)
        if binding is not None:
            self._seek_binding(binding, seconds)

    # ------------------------------------------------ v1.0 subtitle (需求：)
    def _make_subtitle_control(self, tile_context):
        """字幕开关的官方 TileControl（v1.0 需求：/2：回到容器里）。

        v1.0 曾把它直接插进 ``tile.bottom`` 布局去「贴到弹幕右边」，代价是
        宿主 ``_layout_bottom_controls`` 的宽度预算不认这个新控件 —— 实测
        1920 宽底栏整行 1978px，容器把「弹幕」压住 46px，右侧四个按钮看着
        就像消失了。v1.0 用户要求「字幕与弹幕互换位置」，正好把按钮收回
        ``TileControls``：容器本身紧挨在「弹」左侧、宽度由宿主自己算，
        溢出问题从根上不存在。

        priority 59 让它在容器里排最后（progress 61 → cell 60 → 字幕 59），
        因此紧贴「弹」的左边；``_allocate`` 把余量给首项，52px 也不会被拉长。
        """
        from .tile_vod import VodSubtitleButton

        plugin = self
        tile = _resolve_tile(tile_context, self._host_window())

        class SubtitleControl(api.TileControl):
            def __init__(self) -> None:
                widget = VodSubtitleButton()
                super().__init__(widget, minimum_width=52, preferred_width=52)
                self.widget = widget
                widget.hide()
                widget.clicked.connect(
                    lambda _checked=False, t=tile: plugin._toggle_subtitle(t)
                )
                if tile is not None:
                    plugin._subtitle_controls[id(tile)] = {
                        "widget": widget, "tile": tile, "ctx": tile_context,
                        "visible": False,
                    }

            def set_room(self, room):
                pass

            def interaction_active(self):
                return False

            def shutdown(self):
                entry = plugin._subtitle_controls.pop(id(tile), None)
                if entry is not None:
                    try:
                        entry["widget"].hide()
                    except RuntimeError:
                        pass

        return SubtitleControl()

    def _start_subtitle_fetch(self, binding, cookies: dict) -> None:
        return self.playback.start_subtitle_fetch(binding, cookies)

    def _retire_subtitle(self, request_id: int) -> None:
        return self.playback.retire_subtitle(request_id)

    def _current_subtitle_binding(self, request_id):
        return self.playback.current_subtitle_binding(request_id)

    def _on_subtitle_completed(self, request_id, results) -> None:
        return self.playback.on_subtitle_completed(request_id, results)
        # 文件加载后由 ticker 挂到对应播放器；多路返回不会串到其它绑定。

    def _on_subtitle_failed(self, request_id, error) -> None:
        return self.playback.on_subtitle_failed(request_id, error)

    def _sync_subtitle_control(self, tile) -> None:
        entry = self._subtitle_controls.get(id(tile))
        if entry is None:
            return
        binding = self._binding_for_tile(tile)
        visible = binding is not None and binding.state in ("playing", "paused")
        try:
            widget = entry["widget"]
            if visible != entry.get("visible"):
                entry["visible"] = visible
                widget.setVisible(visible)
                # 官方通道：显隐翻转后让宿主重算一次底栏宽度预算。
                entry["ctx"].invalidate_layout()
            widget.setEnabled(visible and not binding._sub_pending if binding else False)
            widget.set_checked(bool(binding and binding.subtitle_on))
            widget.setToolTip("正在加载字幕…" if binding and binding._sub_pending
                              else "B站字幕：优先中文（点击开关）")
        except (RuntimeError, AttributeError):
            pass

    def _apply_subtitle(self, binding, player) -> None:
        return self.playback.apply_subtitle(binding, player)

    @staticmethod
    def _unlink_paths(paths) -> None:
        return PlaybackCoordinator.unlink_paths(paths)

    def _toggle_subtitle(self, tile) -> None:
        return self.playback.toggle_subtitle(tile)

    def _build_empty_tile_menu(self, tile):
        """空格子右键菜单（需求 8：只留选片 + 宿主原有的切换为弹幕）。"""
        if self._shutting_down or self.wall is None:
            return None
        cell = self._resolve_cell_from_tile(tile)
        menu = QMenu(tile)
        pick = menu.addAction("选择稍后再看视频…")
        pick.setEnabled(cell is not None)
        if cell is not None:
            pick.triggered.connect(lambda: self._on_pick_requested(cell))
        menu.addSeparator()
        pane_edit = getattr(tile, "paneEditRequested", None)
        if pane_edit is not None:
            switch = menu.addAction("切换为弹幕")
            switch.triggered.connect(lambda: pane_edit.emit())
        return menu

    def _show_empty_tile_menu(self, tile, global_pos) -> bool:
        menu = self._build_empty_tile_menu(tile)
        if menu is None:
            return False
        menu.exec(global_pos)
        menu.deleteLater()
        return True

    def _register_tile(self, cell, tile) -> None:
        self._cell_tiles[id(cell)] = tile

    def _forget_cell(self, cell) -> None:
        self._cancel_cell_requests(cell)
        tile = self._cell_tiles.pop(id(cell), None)
        if tile is not None:
            self._unwatch_tile(tile)

    def _unwatch_tile(self, tile) -> None:
        """格子被宿主销毁（布局变化）时按格注销监视。

        断开 qualityChanged、移除事件过滤器、清掉对 tile 的强引用与待决
        点击定时器，避免连接/引用滞留到 on_unload 才清造成跨布局泄漏。
        与 on_unload 的全量清理幂等（重复 disconnect/remove 异常被吞）。
        """
        if tile is None:
            return
        key = id(tile)
        entry = self._quality_connected.pop(key, None)
        if entry is not None:
            owner, slot = entry
            try:
                owner.qualityChanged.disconnect(slot)
            except (RuntimeError, TypeError, AttributeError):
                pass
        if self._tile_filter is not None:
            for target in (tile, getattr(tile, "video", None),
                           getattr(tile, "stream_badge", None),
                           getattr(tile, "pause_overlay", None)):
                if target is None:
                    continue
                try:
                    target.removeEventFilter(self._tile_filter)
                except Exception:
                    pass
        self._watched_tiles.discard(key)
        self._subtitle_controls.pop(key, None)
        self.input.vod_press.pop(key, None)
        timer = self.input.click_timers.pop(key, None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass

    def _queue_tile_sync(self, tile) -> None:
        key = id(tile)
        if key in self._geometry_pending or self._shutting_down:
            return
        self._geometry_pending.add(key)

        def sync():
            self._geometry_pending.discard(key)
            self._sync_tile_widgets(tile)

        QTimer.singleShot(0, sync)

    def _drag_source_tile(self, event):
        """解析格子拖拽的源格（唯一标识）。

        宿主 ``Tile.mouseMoveEvent`` 用 ``QDrag(self)``，故 ``event.source()``
        就是被拖的源 Tile（或其子控件）——沿父链反查到 wall.tiles 中的 Tile，
        这是唯一可靠的身份。仅当 source() 不可用（跨应用拖拽/测试假事件）时
        才回退到 mime 里的 room_id 字符串，且要求非空并唯一匹配：空格("")与
        重复直播房的 room_id 不唯一，按字符串 ``next()`` 会命中错误源格导致
        换错位置，故宁可拒绝（交还宿主原路径）也不猜。
        """
        tiles = self._host_tiles()
        node = None
        try:
            node = event.source()
        except Exception:
            node = None
        while node is not None:
            if node in tiles:
                return node
            try:
                node = node.parentWidget()
            except Exception:
                node = None
        try:
            source_id = bytes(
                event.mimeData().data("application/x-ddm-tile")
            ).decode("utf-8")
        except (ValueError, UnicodeError, AttributeError):
            return None
        if not source_id:
            return None
        matches = [t for t in tiles
                   if str((getattr(t, "room", None) or {}).get("room_id", ""))
                   == source_id]
        return matches[0] if len(matches) == 1 else None

    def _handle_tile_drag(self, target, event) -> bool:
        """只接管涉及点播的格子换位，避免宿主 offline fallback 销毁播放器。"""
        if target not in self._host_tiles():
            return False
        source = self._drag_source_tile(event)
        if source is None or not (self._binding_for_tile(source)
                                  or self._binding_for_tile(target)):
            return False
        if event.type() == QEvent.Type.Drop:
            try:
                target.setProperty("dropActive", False)
                target.style().unpolish(target)
                target.style().polish(target)
                if source is not target:
                    from .tile_swap import swap_tiles_preserving_players
                    if not swap_tiles_preserving_players(self._host_window(), source, target):
                        self._notify_warning("当前布局暂不能无中断换格，请退出全屏后再试。")
                self.input.vod_press.clear()
                for t in (source, target):
                    timer = self.input.click_timers.pop(id(t), None)
                    if timer is not None:
                        timer.stop()
                        timer.deleteLater()
                    self._sync_tile_widgets(t)
            except Exception as error:
                self._log("VOD tile swap failed type=" + type(error).__name__)
                # 不再转交宿主的销毁/重建回退，避免异常后发生二次交换。
                self._notify_warning("换格未完成，播放保持原位。")
        event.acceptProposedAction()
        return True

    def _sync_tile_widgets(self, tile) -> None:
        """格子 Resize/Move/Show/WinIdChange 后同步几何与全屏态（排队调用）。"""
        if self._shutting_down:
            return
        for binding in self.playback.bindings.values():
            if binding.tile is not tile:
                continue
            try:
                compact = bool(self._tile_in_pip(tile)) and (
                    tile.width() < 340 or tile.height() > tile.width() * 1.15)
                if binding.card is not None and hasattr(binding.card, "set_compact"):
                    binding.card.set_compact(compact)
                if binding.card is not None and binding.card.isVisible():
                    binding.card.sync_geometry(tile)
                if binding.line is not None and binding.line.isVisible():
                    binding.line.sync_geometry(tile)
            except RuntimeError:
                pass  # widget already deleted by Qt
        self._sync_fullscreen_state(tile)
        # v1.0 需求：：宿主刚按宽度重算过 compact_popup，这里再钉回 mini。
        for binding in self.playback.bindings.values():
            if binding.tile is tile:
                self._force_volume_mini(tile)
                break
        # v1.0 需求：/14：重排/跨屏之后把原生浮层抬起来重画。
        self._repaint_overlays(tile)

    @staticmethod
    def _force_volume_mini(tile) -> None:
        """把这一格的音量按钮钉成 mini（图标）形态（v1.0 需求：）。"""
        volume = getattr(tile, "volume_button", None)
        if volume is None:
            return
        try:
            if getattr(volume, "compact_popup", False):
                return
            volume.compact_popup = True
            update = getattr(volume, "_update_tooltip", None)
            if callable(update):
                update()
        except Exception:
            pass

    def _repaint_overlays(self, tile) -> None:
        """v1.0 需求：/14：几何/DPI 变化后把浮层抬起来并重画。

        宿主收起/展开房间列表、窗口跨屏、侧栏改宽之后，格子会重排；视频是
        原生 HWND，插件这些原生子控件不会自动跟着重绘（表现为「部分 UI 显示
        错误」，多路小窗跨屏时甚至整窗白屏）。这里统一调宿主的
        ``raise_overlays()`` 并显式 ``update()`` 自己的控件。
        """
        # v1.0b：画中画板块退回 v1.0 —— 不做任何插件侧跨屏重绘。
        if self._tile_in_pip(tile):
            return
        # v1.0 性能：宿主 raise_overlays() 会重算浮层遮罩，非常贵。只有几何/
        # DPR/形态真的变了才做一次（原来每次几何同步都调，是整墙卡顿的元凶）。
        try:
            key = (tile.width(), tile.height(),
                   round(tile.devicePixelRatioF(), 2),
                   bool(tile.property("fullscreen")),
                   bool(tile.property("pictureInPicture")))
        except Exception:
            key = None
        cache = self._overlay_keys
        if key is not None and cache.get(id(tile)) == key:
            return
        if key is not None:
            cache[id(tile)] = key
        raise_overlays = getattr(tile, "raise_overlays", None)
        if callable(raise_overlays):
            try:
                raise_overlays()
            except Exception:
                pass

    # ------------------------------------------- v1.0 fullscreen / interaction
    def _tile_in_pip(self, tile) -> bool:
        """v1.0 需求：：格子是否处于多路小窗（PiP）形态。"""
        try:
            if bool(tile.property("pictureInPicture")):
                return True
        except Exception:
            pass
        window = self._host_window()
        pip = getattr(window, "picture_in_picture", None)
        active = getattr(pip, "active", None)
        if callable(active):
            try:
                return bool(active())
            except Exception:
                return False
        return False

    def _sync_pip_filter(self) -> None:
        return self.input.sync_pip_filter()

    def _fit_vod_bottom_bar(self, tile) -> int:
        """Share spare bar width through the host allocator, including subtitles."""
        entry = self._bar_progress.get(id(tile))
        if entry is None:
            return 0
        controls = getattr(tile, "_plugin_controls", None)
        bar = getattr(tile, "bottom", None)
        layout = bar.layout() if bar is not None else None
        widget = entry.get("widget")
        wrapper = widget.parentWidget() if widget is not None else None
        container = getattr(controls, "widget", None)
        if (layout is None or container is None or wrapper is None
                or wrapper.parentWidget() is not container):
            # 宿主结构不完整（老版本 / 离线替身）：不做宽度接管，按最小宽度
            # 报告「放得下」，让宿主自己的 _allocate 决定。
            return BAR_PROGRESS_MIN_WIDTH
        spacer = getattr(controls, "spacer", None)
        status = getattr(tile, "status_label", None)
        if container.width() <= 0 or not container.isVisible():
            # 宿主这一轮没选中进度条（太窄 / 小窗）：不要用空白space硬撑出宽度。
            return 0
        extra = 0
        for box in (status, spacer):
            if box is not None:
                extra += max(0, box.width())
        if extra > 0:
            try:
                if status is not None:
                    status.setFixedWidth(0)
                if spacer is not None:
                    spacer.setFixedWidth(0)
                width = container.width() + extra
                # v1.0 需求：：宿主在动画帧里可能反复重设空白占位，这里按底栏
                # 宽度硬钳一次，绝不让容器无限长大（退出/进入全屏时的「鬼畜生长」）。
                width = max(0, min(width, bar.width() - 4))
                controls.allocate(width)
                layout.activate()
            except RuntimeError:
                return 0
        return max(0, wrapper.width()) if not wrapper.isHidden() else 0

    def _sync_fullscreen_state(self, tile) -> None:
        """按 tile.property('fullscreen') / PiP 切换进度控件形态（需求：/8/15）。

        v1.0：**画面墙也把进度条放进底栏**（需求：），不再浮在画面上挡字幕；
        底栏放不下（窄格）或处于多路小窗时退回视频底边 3px 蓝线。全屏/墙面
        都由 :meth:`_fit_vod_bottom_bar` 把中间区让给进度条（需求：/2/3）。
        空格快捷键跟随全屏窗口。
        """
        if self._shutting_down or tile is None:
            return
        try:
            fullscreen = bool(tile.property("fullscreen"))
        except Exception:
            fullscreen = False
        pip = self._tile_in_pip(tile)
        compact = fullscreen or pip
        binding = self._binding_for_tile(tile)
        active = binding is not None and binding.state in ("playing", "paused")
        entry = self._bar_progress.get(id(tile))
        if entry is not None:
            # Keep the widget eligible while the host hides only its wrapper
            # for lack of space; widening the tile must restore it naturally.
            requested = bool(active and not pip)
            if entry["widget"].isHidden() == requested:
                entry["widget"].setVisible(requested)
                entry["ctx"].invalidate_layout()
        free = 0
        if entry is not None and active and not pip:
            free = self._fit_vod_bottom_bar(tile)
        bar_visible = bool(entry is not None and active and free > 0)
        if entry is not None:
            entry["visible"] = bar_visible
        if binding is not None:
            # v1.0 需求：：交互式进度条在底栏时，蓝线只在「没有底栏条」的
            # 形态（小窗 / 窄格）保留，避免画面底部重复两套进度。
            line = binding.line
            if line is not None:
                try:
                    show_line = bool(active and not bar_visible)
                    line.setVisible(show_line)
                    if show_line:
                        line.sync_geometry(tile)
                except RuntimeError:
                    binding.line = None
            if binding.strip is not None:
                # v1.0：画面墙不再用浮在画面上的 seek 条（会压住字幕）。
                try:
                    binding.strip.hide()
                except RuntimeError:
                    binding.strip = None
        # 全屏 / 画中画形态切换：把兜底快捷键一起挂到活动窗口，重装应用级
        # 按键过滤器，并清掉底栏按钮的键盘焦点（空格不再「点」到画质按钮）。
        fs_active = bool(fullscreen and active)
        if fs_active != self.input.fullscreen_active:
            self.input.fullscreen_active = fs_active
            self._bump_key_filter()
            if fs_active:
                try:
                    focus = QApplication.focusWidget()
                    if isinstance(focus, QToolButton) and tile.isAncestorOf(focus):
                        focus.clearFocus()
                except Exception:
                    pass
        host = self._host_window()
        if (fullscreen and active) or (pip and active):
            try:
                host = tile.window() or host
            except Exception:
                pass
        self._sync_shortcut_hosts(host)
        if binding is not None:
            # v1.0 任务 8：全屏进出立刻重排左上角位置徽章，不等下一拍。
            self._sync_position_badge(binding)

    def _sync_shortcut_hosts(self, host) -> None:
        return self.input.sync_shortcut_hosts(host)

    def _on_vod_click(self, tile, binding) -> None:
        return self.input.on_vod_click(tile, binding)

    def _single_vod_click(self, tile, binding) -> None:
        return self.input.single_vod_click(tile, binding)

    def _ensure_space_shortcut(self) -> None:
        return self.input.ensure_space_shortcut()

    def _tile_under_cursor(self):
        return self.input.tile_under_cursor()

    # --------------------------------------- 应用级按键：全屏 / 画中画统一入口
    def _bump_key_filter(self) -> None:
        return self.input.bump_key_filter()

    def _vod_tiles_anywhere(self):
        return self.input.vod_tiles_anywhere()

    def _any_tile_under_cursor(self):
        return self.input.any_tile_under_cursor()

    def _tile_in_pip_window(self, tile) -> bool:
        return self.input.tile_in_pip_window(tile)

    def _active_vod_tile(self):
        return self.input.active_vod_tile()

    def _key_window_ok(self, watched=None) -> bool:
        return self.input.key_window_ok(watched)

    def _danmaku_key_text(self) -> str:
        return self.input.danmaku_key_text()

    def _danmaku_combo(self):
        return self.input.danmaku_combo()

    def _vod_key_action(self, event, watched=None):
        return self.input.vod_key_action(event, watched)

    def _claim_key(self, name: str) -> bool:
        return self.input.claim_key(name)

    def _handle_vod_key(self, event, watched=None) -> bool:
        return self.input.handle_vod_key(event, watched)

    def _toggle_pause(self, tile, binding) -> None:
        return self.input.toggle_pause(tile, binding)

    def _toggle_danmaku(self, tile, binding) -> None:
        return self.input.toggle_danmaku(tile, binding)

    # ------------------------------------------------------ v1.0 方向键 seek
    def _ensure_seek_shortcuts(self) -> None:
        return self.input.ensure_seek_shortcuts()

    def _seek_by_keys(self, delta: float, tile=None) -> None:
        return self.input.seek_by_keys(delta, tile)

    # ------------------------------------------------ PiP 弹幕键 / 跨屏黑遮罩
    def _ensure_danmaku_shortcut(self) -> None:
        return self.input.ensure_danmaku_shortcut()

    def _host_danmaku_shortcut_reachable(self, watched=None) -> bool:
        return self.input.host_danmaku_shortcut_reachable(watched)

    def _on_danmaku_shortcut(self) -> None:
        return self.input.on_danmaku_shortcut()

    def _on_dpi_event(self, watched) -> None:
        return self.pip_dpi.on_dpi_event(watched)

    def _flush_dpi_event(self) -> None:
        return self.pip_dpi.flush_dpi_event()


    def _pip_drag_active(self) -> bool:
        return self.pip_dpi.pip_drag_active()


    def _hide_all_dpi_covers(self) -> None:
        return self.pip_dpi.hide_all_dpi_covers()


    def _hide_dpi_cover(self, tile) -> None:
        return self.pip_dpi.hide_dpi_cover(tile)

    def _raise_dpi_cover(self, tile, cover) -> None:
        return self.pip_dpi.raise_dpi_cover(tile, cover)

    def _cover_pip_dpi(self, tile) -> None:
        return self.pip_dpi.cover_pip_dpi(tile)

    def _fill_pip_choice_avatars(self, dialog) -> None:
        """需求：：组合画中画选择框里，点播格的 UP 头像补上。

        宿主的 `PictureInPictureDialog` 只从**侧栏条目**（`sidebar.items()`）取
        头像，合成点播房不在侧栏 → `faces.get(room_id)` 是 None → 只剩字母占位。
        这里在对话框 Show 时按 `watchlater_bvid` 找到绑定，异步取 owner_face
        后回填到对应 RoomChoice 的 Avatar（只发 bytes，QPixmap 在 GUI 线程建）。
        """
        if dialog is None or self._shutting_down:
            return
        rooms = getattr(dialog, "rooms", None)
        checks = getattr(dialog, "checks", None)
        if not isinstance(rooms, dict) or not isinstance(checks, dict):
            return
        for key, room in rooms.items():
            if not isinstance(room, dict):
                continue
            bvid = str(room.get("watchlater_bvid") or "")
            if not bvid:
                continue
            check = checks.get(key)
            avatar = getattr(check, "avatar", None) if check is not None else None
            if avatar is None or not hasattr(avatar, "set_pixmap_image"):
                continue
            pixmap = self._face_pixmaps.get(bvid)
            if pixmap is not None:
                try:
                    avatar.set_pixmap_image(pixmap)
                except Exception:
                    pass
                continue
            binding = self.playback.bindings.get(bvid)
            url = str(getattr(getattr(binding, "item", None), "owner_face", "") or "")
            if self._face_loader is None:
                from .avatars import AvatarLoader

                self._face_loader = AvatarLoader()
                self._face_loader.ready.connect(self._on_face_ready)
            self._face_loader.get(bvid, url)

    def _on_face_ready(self, bvid: str, data) -> None:
        """Decoded avatar delivered on the GUI thread."""
        if self._shutting_down or data is None or data.isNull():
            return
        pixmap = data
        self._face_pixmaps[str(bvid)] = pixmap
        for dialog in QApplication.topLevelWidgets():
            rooms = getattr(dialog, "rooms", None)
            checks = getattr(dialog, "checks", None)
            if not isinstance(rooms, dict) or not isinstance(checks, dict):
                continue
            for key, room in rooms.items():
                if not isinstance(room, dict) or \
                        str(room.get("watchlater_bvid") or "") != str(bvid):
                    continue
                avatar = getattr(checks.get(key), "avatar", None)
                if avatar is not None:
                    try:
                        avatar.set_pixmap_image(pixmap)
                    except Exception:
                        pass

    def _fullscreen_tile(self):
        """当前处于全屏的格子（全屏只会有一个）。"""
        for tile in self._host_tiles():
            try:
                if bool(tile.property("fullscreen")):
                    return tile
            except Exception:
                continue
        return None

    def _on_space(self) -> None:
        return self.input.on_space()

    def _show_vod_tile_menu(self, tile, global_pos) -> bool:
        """点播格右键（需求：，已确认 4 项）：画质/视频流信息/声道/全屏。

        宿主 build_menu 无过滤钩子，插件在 ContextMenu 事件级拦截自建菜单
        （实例级 eventFilter，不 patch 宿主类）。动作全部复用宿主信号/方法。
        """
        binding = self._binding_for_tile(tile)
        if binding is None:
            return False
        from .vod_stream import quality_label

        menu = QMenu(tile)
        quality_menu = menu.addMenu("画质")
        qualities = list(binding.qualities or [])
        if not qualities and binding.quality > 0:
            qualities = [(binding.quality, quality_label(binding.quality))]
        if not qualities:
            action = quality_menu.addAction("取流完成后可用")
            action.setEnabled(False)
        else:
            for qn, label in qualities:
                action = quality_menu.addAction(str(label))
                action.setCheckable(True)
                action.setChecked(int(qn) == int(binding.quality))
                action.triggered.connect(
                    lambda _checked=False, b=binding, q=int(qn):
                    self._switch_quality(b, q)
                )
        info_signal = getattr(tile, "streamInfoRequested", None)
        if info_signal is not None:
            menu.addAction(
                "视频流信息…",
                lambda: QTimer.singleShot(0, tile, info_signal.emit),
            )
        set_channel = getattr(tile, "set_audio_channel", None)
        if callable(set_channel):
            audio_menu = menu.addMenu("声道")
            current = int(getattr(tile, "audio_channel", 0) or 0)
            for name, value in (
                ("默认（跟随片源）", 0), ("只播左声道", 3),
                ("只播右声道", 4), ("反向立体声", 2),
                ("杜比矩阵下混（多声道源）", 5),
            ):
                action = audio_menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(value == current)
                action.triggered.connect(
                    lambda _checked=False, v=value: set_channel(v)
                )
        fullscreen_signal = getattr(tile, "fullscreenRequested", None)
        if fullscreen_signal is not None:
            menu.addAction("全屏查看", lambda: fullscreen_signal.emit(tile))
        try:
            menu.exec(global_pos)
        finally:
            menu.deleteLater()
        return True

    def _switch_quality(self, binding, qn: int) -> None:
        """清晰度切换（需求：）：按当前秒重新取流，注入后续播。"""
        if self._shutting_down or binding.state == "resolving":
            return
        qn = int(qn)
        if qn == int(binding.quality) and binding.state in ("playing", "paused"):
            return
        binding.resume_paused = bool(getattr(binding.tile, "paused", False))
        position = float(binding.progress)
        if binding.card is not None:
            try:
                binding.card.hide()
            except RuntimeError:
                pass
        try:
            binding.tile.set_status("正在切换清晰度…")
        except Exception:
            pass
        self._start_resolution(binding, start=position, qn=qn)

    def _on_tile_quality_changed(self, tile, room, qn) -> None:
        """v1.0 需求：：格子内画质按钮 → 按选中 qn 重新取流续播。"""
        if self._shutting_down:
            return
        binding = self._binding_for_tile(tile)
        if binding is None:
            return
        try:
            qn = int(qn)
        except (TypeError, ValueError):
            return
        if qn <= 0 or qn == int(binding.quality):
            return
        self._switch_quality(binding, qn)

    def _apply_tile_chrome(self, binding) -> None:
        """v1.0 需求：/4：点播格隐藏录制按钮 + 灌入画质按钮档位。"""
        tile = binding.tile
        if binding.prev_chrome is None:
            binding.prev_chrome = {
                name: getattr(tile, name) for name in (
                    "_recording_available", "quality_options", "quality",
                    "actual_quality", "quality_locked"
                ) if hasattr(tile, name)
            }
        try:
            tile.set_recording_available(False)
        except Exception:
            try:
                btn = getattr(tile, "recording_button", None)
                if btn is not None:
                    btn.hide()
            except Exception:
                pass
        # v1.0 需求：：点播格的音量按钮钉在 mini 形态；宿主每次重排都会按宽度
        # 重算 compact_popup，所以记下原值并在 _sync_tile_widgets 里重申。
        volume = getattr(tile, "volume_button", None)
        if volume is not None and "volume_compact" not in binding.prev_chrome:
            binding.prev_chrome["volume_compact"] = bool(
                getattr(volume, "compact_popup", False))
        self._force_volume_mini(tile)
        set_options = getattr(tile, "set_quality_options", None)
        if callable(set_options):
            options = [
                {"qn": int(qn), "label": str(label), "desc": str(label)}
                for qn, label in (binding.qualities or [])
            ]
            if options:
                try:
                    set_options(options)
                except Exception:
                    pass
        try:
            tile.quality_locked = False
            tile.quality = binding.quality
            tile.actual_quality = binding.quality
            if isinstance(tile.room, dict):
                tile.room["quality"] = binding.quality
            button = getattr(tile, "quality_button", None)
            if button is not None:
                label = next((label for qn, label in binding.qualities
                              if int(qn) == binding.quality), str(binding.quality))
                button.setText(str(label))
                button.setToolTip("选择这一路的视频画质（以账号可用档位为准）")
                layout = getattr(tile, "_layout_controls", None)
                if callable(layout):
                    layout()
        except (AttributeError, RuntimeError):
            pass

    def _restore_tile_chrome(self, binding, *, quality: bool) -> None:
        saved = binding.prev_chrome
        if saved is None:
            return
        binding.prev_chrome = None
        tile = binding.tile
        try:
            if "_recording_available" in saved:
                tile.set_recording_available(saved["_recording_available"])
            if "volume_compact" in saved:
                volume = getattr(tile, "volume_button", None)
                if volume is not None:
                    volume.compact_popup = bool(saved["volume_compact"])
                    update = getattr(volume, "_update_tooltip", None)
                    if callable(update):
                        try:
                            update()
                        except Exception:
                            pass
            if quality:
                for name in ("quality", "actual_quality", "quality_locked"):
                    if name in saved:
                        setattr(tile, name, saved[name])
                if "quality_options" in saved:
                    tile.quality_options = saved["quality_options"]
                button = getattr(tile, "quality_button", None)
                label = getattr(tile, "_quality_text", None)
                if button is not None and callable(label):
                    button.setText(label())
                layout = getattr(tile, "_layout_controls", None)
                if callable(layout):
                    layout()
        except (AttributeError, RuntimeError):
            pass

    # -------------------------------------------------------------- VOD start
    def _on_play_requested(self, cell, item) -> None:
        """wall.play_requested（兼容旧信号）：按 cell 找格子起播。"""
        if self._shutting_down or item is None:
            return
        tile = self._cell_tiles.get(id(cell))
        if tile is None:
            return
        self._start_vod_for_tile(tile, item)

    def _on_video_dropped(self, tile, bvid: str) -> None:
        """弹窗视频卡拖进格子（需求 6）：空格子/直播格都直接起播。"""
        if self._shutting_down or not bvid:
            return
        page = self._ensure_page()
        item = None
        if page is not None:
            try:
                item = next(
                    (it for it in page.items_snapshot()
                     if str(getattr(it, "bvid", "")) == bvid),
                    None,
                )
            except Exception:
                item = None
        if item is None:
            self._notify_warning("列表里没有找到这个视频，请先刷新稍后再看列表。")
            self._on_open_list()
            return
        self._start_vod_for_tile(tile, item)

    def _placement_targets(self):
        wall = getattr(self._host_window(), 'wall', None)
        panes = getattr(wall, 'pane_danmaku', None)
        options = []
        for index, tile in enumerate(self._playback_tiles()):
            if sip_deleted(tile):
                continue
            cell = panes.cell_for_tile(tile) if panes is not None else index
            if cell is None:
                continue
            room = getattr(tile, 'room', None) or {}
            occupied = bool(room.get('room_id') or self._binding_for_tile(tile))
            name = str(room.get('uname') or room.get('title') or '已有视频') if occupied else '空格'
            options.append((tile, f'第 {cell + 1} 格 · {name}', occupied))
        return options

    def _placement_snapshot(self):
        from .placement_layout import snapshot_wall
        page = self.page
        cached = page.covers.cached if page is not None else lambda _url: None
        return snapshot_wall(getattr(self._host_window(), 'wall', None), self._playback_tiles(),
                             self._binding_for_tile, cached)

    def _place_item_from_page(self, item, target):
        if self._shutting_down or item is None:
            return
        if target not in self._playback_tiles():
            self._notify_warning('目标格已变化，请重新选择目标格。')
            if self.page is not None:
                self.page.refresh_targets()
            return
        picked = self._pick_target_tile is not None
        self._pick_target_tile = None
        self._start_vod_for_tile(target, item)
        if picked and self.page is not None:
            self.page.set_pick_target(None)
            self.page.close()

    def _play_item_from_page(self, item) -> None:
        """弹窗「播放」按钮：选片模式进目标格，浏览模式进第一个空格。"""
        if self._shutting_down or item is None:
            return
        candidates = self._playback_tiles()
        tile = self._pick_target_tile
        if tile is not None and tile not in candidates:
            tile = None
        if tile is None:
            existing = self.playback.bindings.get(str(getattr(item, "bvid", "") or ""))
            if existing is not None and existing.tile in candidates:
                tile = existing.tile
        if tile is None:
            tile = next(
                (t for t in candidates
                 if not str((getattr(t, "room", None) or {}).get("room_id") or "")),
                None,
            )
        if tile is None:
            self._notify_warning(
                "没有空格子了。把视频直接拖到想替换的格子上即可播放。"
            )
            return
        picked = self._pick_target_tile is not None
        self._pick_target_tile = None
        self._log(f"play selection tile_index={self._host_tiles().index(tile) + 1} "
                  f"video_candidates={len(candidates)}")
        self._start_vod_for_tile(tile, item)
        if picked and self.page is not None:
            try:
                self.page.set_pick_target(None)
                self.page.close()
            except Exception:
                pass

    def _start_vod_for_tile(self, tile, item, *, prepared=None, resume=None) -> None:
        return self.playback.start_vod_for_tile(tile, item, prepared=prepared, resume=resume)

    @staticmethod
    def _room_title(binding) -> str:
        return PlaybackCoordinator.room_title(binding)

    @staticmethod
    def _resume_seconds(item) -> float:
        return PlaybackCoordinator.resume_seconds(item)

    def _cookie_snapshot(self) -> dict[str, str]:
        return self.playback.cookie_snapshot()

    # ------------------------------------------------------------ VOD workers
    def _start_resolution(self, binding, *, start: float, qn: int | None=None, prepared=None) -> None:
        return self.playback.start_resolution(binding, start=start, qn=qn, prepared=prepared)
        # Danmaku starts after resolution supplies the actual part's cid.
        # Subtitles remain lazy and require an explicit click.

    def _start_danmaku_fetch(self, binding, cookies: dict) -> None:
        return self.playback.start_danmaku_fetch(binding, cookies)

    def _on_danmaku_completed(self, request_id, comments) -> None:
        return self.playback.on_danmaku_completed(request_id, comments)

    def _on_danmaku_failed(self, request_id, error) -> None:
        return self.playback.on_danmaku_failed(request_id, error)

    def _retire_danmaku(self, request_id) -> None:
        return self.playback.retire_danmaku(request_id)

    def _is_current_vod(self, request_id, generation, ctx) -> bool:
        return self.playback.is_current_vod(request_id, generation, ctx)

    def _on_vod_completed(self, request_id, generation, stream) -> None:
        return self.playback.on_vod_completed(request_id, generation, stream)

    def _on_vod_failed(self, request_id, generation, error) -> None:
        return self.playback.on_vod_failed(request_id, generation, error)

    def _retire_vod(self, request_id) -> None:
        return self.playback.retire_vod(request_id)

    def _cancel_cell_requests(self, cell) -> None:
        return self.playback.cancel_cell_requests(cell)

    def _cancel_all_requests(self) -> None:
        return self.playback.cancel_all_requests()

    def _drain_requests(self) -> None:
        return self.playback.drain_requests()

    # ----------------------------------------------------- playback injection
    def _inject_playback(self, binding, stream, start: float) -> None:
        return self.playback.inject_playback(binding, stream, start)

    def _ensure_tile_widgets(self, binding) -> None:
        from .tile_vod import VodStatusCard

        tile = binding.tile
        # v1.0 需求：：画面墙不再建浮在视频上的 seek 条 —— 进度条统一进
        # 底栏（见 _fit_vod_bottom_bar），避免压住画面底部的字幕。
        binding.strip = None
        if binding.card is None:
            try:
                card = VodStatusCard(tile)
                card.retry_requested.connect(lambda b=binding: self._retry_binding(b))
                card.replay_requested.connect(lambda b=binding: self._replay_binding(b))
                card.next_requested.connect(lambda b=binding: self._next_binding(b))
                card.browser_requested.connect(lambda b=binding: self._open_binding_browser(b))
                card.restore_requested.connect(lambda b=binding: self._release_binding(b, restart=True))
                binding.card = card
            except Exception as error:
                self._log("card create failed type=" + type(error).__name__)
        if binding.line is None:
            # 分级导入：进度线是 v1.0 新增控件，模块缺失时降级为无进度线，
            # 绝不连累 strip/card。
            try:
                from .tile_vod import VodProgressLine

                line = VodProgressLine(tile)
                line.hide()
                binding.line = line
            except Exception as error:
                self._log("line create failed type=" + type(error).__name__)
        if binding.badge is None and POSITION_BADGE_ENABLED:
            # v1.0 任务 3/8：只补左上角的位置徽章，失败就当没这功能。
            try:
                from .tile_vod import VodPositionBadge

                binding.badge = VodPositionBadge(tile)
            except Exception as error:
                self._log("position badge create failed type=" + type(error).__name__)

    # --------------------------------------------------- v1.0 position badge
    def _list_position_text(self, bvid: str) -> str:
        """当前视频在稍后再看列表里的 ``N/总数``；无页面/未命中返回空串。"""
        page = self.page
        if page is None:
            return ""
        try:
            from .models import format_position, list_position

            return format_position(list_position(self._ordered_items(), bvid))
        except Exception:
            return ""

    def _sync_position_badge(self, binding) -> None:
        """左上角「N/总数」：v1.0b 起复用宿主自己的 BadgeSurface。

        用户反馈的那个「透出主窗口 / DD监控室CE 标题」的黑洞，本质是插件
        在原生视频 HWND 之上又建了一个原生子窗口（位置徽章），它的遮罩/
        分层区域是全透明的，于是把后面的窗口透了出来。修法不是调层级，
        而是**不再建独立窗口**：直接把宿主的 stream_badge 重画成排序药丸。
        BadgeSurface 自带逐像素 alpha 合成，显隐又由 _sync_stream_badge
        统一控制 —— 正好满足「和视频标题一起出现一起消失」。
        """
        if self._shutting_down or binding is None:
            return
        tile = binding.tile
        if tile is None:
            return
        if not POSITION_BADGE_ENABLED:
            self._sync_order_pill(binding)
            return
        badge = binding.badge
        if badge is None:
            return
        try:
            if badge.isVisible():
                badge.hide()
        except RuntimeError:
            binding.badge = None

    # ------------------------------------------------- v1.0b 视频排序药丸
    def _sync_order_pill(self, binding) -> None:
        """把宿主左上角药丸改成排序药丸；PiP 与取流态退回 v1.0（不显示）。"""
        if self._shutting_down or binding is None:
            return
        tile = binding.tile
        badge = getattr(tile, "stream_badge", None)
        if badge is None or not hasattr(badge, "_rebuild"):
            return
        active = binding.state in ("playing", "paused")
        # v1.0 性能：N/总数 是 O(列表长度) 的线性扫描，每拍都算很贵。
        # 位置极少变，这里最多 2 秒刷新一次；列表变动后由节拍自然收敛。
        text = ""
        if active:
            now = time.monotonic()
            if now - getattr(binding, "_pos_refreshed", 0.0) > 2.0 \
                    or not hasattr(binding, "_pos_text"):
                binding._pos_text = self._list_position_text(binding.bvid)
                binding._pos_refreshed = now
            text = getattr(binding, "_pos_text", "")
        if self._tile_in_pip(tile) or not text:
            self._restore_order_pill(binding, badge, hide=True)
            return
        state = binding.order_pill
        if state is None:
            saved = {}
            for name in ("_text", "paint_badge", "live", "viewers", "offline"):
                if name in getattr(badge, "__dict__", {}):
                    saved[name] = badge.__dict__[name]
            state = {"saved": saved, "label": [""], "installed": False}
            binding.order_pill = state
        if not state["installed"]:
            holder = state["label"]

            def badge_text() -> str:
                return holder[0]

            def draw_pill(painter) -> None:
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(21, 23, 28, 238))
                try:
                    painter.drawPath(badge.badge_path)
                except Exception:
                    painter.drawRoundedRect(
                        QRectF(0, 0, badge.width(), badge.height()), 12, 12)
                # 左侧蓝点，区分于宿主的 LIVE 粉色圆环
                painter.setBrush(QColor(74, 168, 255))
                painter.drawEllipse(QPointF(12.0, badge.height() / 2.0), 3.4, 3.4)
                font = QFont()
                font.setPixelSize(13)
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(QColor(232, 236, 242, 242))
                painter.drawText(
                    QRectF(22, 0, max(20, badge.width() - 28), badge.height()),
                    int(Qt.AlignVCenter | Qt.AlignLeft), holder[0])
                painter.setBrush(Qt.NoBrush)

            badge._text = badge_text
            badge.paint_badge = draw_pill
            badge.live = False
            badge.viewers = ""
            badge.offline = False
            state["installed"] = True
        if state["label"][0] != text:
            state["label"][0] = text
            try:
                badge._rebuild()      # 按新文字重算宽度
                badge.update()
            except Exception:
                pass

    @staticmethod
    def _restore_order_pill(binding, badge, hide: bool = False) -> None:
        """还原宿主药丸（卸载/切房/PiP 时用），绝不留下插件的绘制钩子。"""
        if badge is None:
            try:
                binding.order_pill = None
            except Exception:
                pass
            return
        state = getattr(binding, "order_pill", None)
        if state is not None and state.get("installed"):
            for name in ("_text", "paint_badge"):
                badge.__dict__.pop(name, None)
            for name, value in state.get("saved", {}).items():
                badge.__dict__[name] = value
            try:
                badge._rebuild()
            except Exception:
                pass
            binding.order_pill = None
        if hide:
            try:
                badge.hide()
            except RuntimeError:
                pass

    def _on_host_player_state(self, binding, state: str) -> None:
        return self.playback.on_host_player_state(binding, state)

    def _show_card_error(self, binding, message: str) -> None:
        return self.playback.show_card_error(binding, message)

    @staticmethod
    def _mpv_command(player, *args) -> bool:
        return PlaybackCoordinator.mpv_command(player, *args)

    def _seek_binding_relative(self, binding, delta: float) -> None:
        return self.playback.seek_binding_relative(binding, delta)


    def _seek_binding(self, binding, seconds: float) -> None:
        return self.playback.seek_binding(binding, seconds)

    def _retry_binding(self, binding) -> None:
        return self.playback.retry_binding(binding)

    def _replay_binding(self, binding) -> None:
        return self.playback.replay_binding(binding)

    def _open_binding_browser(self, binding) -> None:
        return self.playback.open_binding_browser(binding)

    # ------------------------------------------------- v1.0 任务 7：下一个视频
    def _ordered_items(self):
        order = getattr(self, 'watch_order', None)
        if order is not None and order.account is not None:
            return order.snapshot()
        return self.page.items_snapshot() if self.page is not None else []

    def _watch_items_changed(self, items):
        self.watch_order.update(items, self.page._cookies.get('DedeUserID', ''))
        if self.order_sidebar is not None:
            self.order_sidebar.refresh()
        self._watch_order_changed()

    def _watch_order_changed(self):
        self.playback.clear_prefetch()
        for binding in self.playback.bindings.values():
            self._sync_next_button(binding)
            self._sync_position_badge(binding)

    def _restore_removed_order(self, keys, restored):
        if self.watch_order.restore(keys,restored):
            if self.order_sidebar is not None:
                self.order_sidebar.refresh()
            self._watch_order_changed()

    def _sync_order_sidebar(self, enabled):
        register = getattr(self.context, 'register_sidebar_page', None)
        if not callable(register):
            return
        container = getattr(self, '_order_container', None)
        if enabled and container is None:
            from .order_sidebar import DeferredOrderSidebar
            container = DeferredOrderSidebar(self._create_order_sidebar)
            if register('watch_order', container, title='观看顺序', icon=self._make_icon(), on_filter=container.set_filter):
                self._order_container = container
            else:
                container.deleteLater()
        elif not enabled and container is not None:
            self.context.unregister_sidebar_page('watch_order')
            container.hide()
            container.deleteLater()
            self._order_container = None
            self.order_sidebar = None

    def _create_order_sidebar(self):
        page = self._ensure_page()
        if page is None:
            return None
        from .order_sidebar import OrderSidebar
        self._watch_items_changed(page.items_snapshot())
        self.order_sidebar = OrderSidebar(page, self.watch_order, self._play_from_order)
        self.order_sidebar.order_changed.connect(self._watch_order_changed)
        return self.order_sidebar

    def _play_from_order(self, item):
        tiles = self._playback_tiles()
        bound = [binding.tile for binding in self.playback.bindings.values() if binding.tile in tiles]
        target = bound[0] if len(bound) == 1 else None
        if not bound:
            target = next((tile for tile in tiles if not (getattr(tile, 'room', None) or {}).get('room_id')), None)
        if target is not None:
            self._start_vod_for_tile(target, item)
        elif self._show_page():
            self.page._open_placement_picker(item)

    def _next_item_for(self, bvid: str):
        """稍后再看列表里 ``bvid`` 的下一条；已是最后一条/查不到返回 None。"""
        page = self.page
        if page is None:
            return None
        try:
            order = getattr(self, 'watch_order', None)
            if order is not None and order.account is not None:
                return order.next_after(bvid)
            from .models import list_position

            items = self._ordered_items()
            position = list_position(items, bvid)
            if position is None:
                return None
            index, total = position
            if index >= total:
                return None
            return items[index]
        except Exception:
            return None

    def _has_next(self, binding) -> bool:
        return self.playback.has_next(binding)

    def _sync_next_button(self, binding) -> None:
        return self.playback.sync_next_button(binding)

    def _next_binding(self, binding) -> None:
        return self.playback.next_binding(binding)

    def _open_url(self, url: str) -> None:
        if isinstance(url, str) and url.startswith("https://"):
            try:
                QDesktopServices.openUrl(QUrl(url))
            except Exception:
                pass

    def _on_restore_requested(self, cell) -> None:
        if self.wall is not None:
            try:
                self.wall.bind(cell, None)
            except Exception:
                pass
        tile = self._cell_tiles.get(id(cell))
        for binding in list(self.playback.bindings.values()):
            if binding.tile is tile or binding.cell is cell:
                self._release_binding(binding, restart=True)

    def _release_binding(self, binding, *, restart: bool) -> None:
        return self.playback.release_binding(binding, restart=restart)

    def _cancel_binding_media(self, binding) -> None:
        return self.playback.cancel_binding_media(binding)

    def _detach_binding(self, binding) -> None:
        return self.playback.detach_binding(binding)

    # ------------------------------------------------------------------ ticker
    def _ensure_tick_timer(self) -> None:
        return self.playback.ensure_tick_timer()

    def _tick(self) -> None:
        return self.playback.tick()

    def _hide_pause_overlay(self, tile) -> None:
        return self.playback.hide_pause_overlay(tile)

    def _cancel_host_retries(self, tile) -> None:
        return self.playback.cancel_host_retries(tile)

    def _enter_ended(self, binding, tile, player) -> None:
        return self.playback.enter_ended(binding, tile, player)

    def _auto_advance(self, binding):
        return self.playback.auto_advance(binding)

    def _maybe_prefetch(self, binding):
        return self.playback.maybe_prefetch(binding)

    def _take_prefetched(self, binding, item, part, quality):
        return self.playback.take_prefetched(binding, item, part, quality)

    def _preferences_changed(self, values):
        order = getattr(self, 'watch_order', None)
        if order is not None and order.include_finished != values.get('order_include_finished', True):
            order.include_finished = values.get('order_include_finished', True)
            if self.order_sidebar is not None:
                self.order_sidebar.refresh()
            self._watch_order_changed()
        self._sync_order_sidebar(values.get("show_watch_order", True))
        if not values.get('resume_session', False):
            self.playback_session.clear()
        if not values['prefetch_next']:
            self.playback.clear_prefetch()
        for binding in self.playback.bindings.values():
            self._configure_preview(binding, values['video_preview'])

    def _configure_preview(self, binding, enabled):
        return self.playback.configure_preview(binding, enabled)

    def _tick_binding(self, binding, tiles) -> None:
        return self.playback.tick_binding(binding, tiles)

    def _feed_danmaku(self, binding, tile, position, paused: bool) -> None:
        return self.playback.feed_danmaku(binding, tile, position, paused)

    # ------------------------------------------------------------- lifecycle
    def on_event(self, event, payload) -> None:
        if event == api.EVENT_STARTED:
            QTimer.singleShot(0, self.playback_session.restore)
        elif event == api.EVENT_ACCOUNT_CHANGED:
            if (
                isinstance(payload, dict)
                and payload.get("platform") == "bilibili"
            ):
                self.playback.advance_generation()
                self.playback_session.clear()
                self.playback.clear_prefetch()
                self._cancel_all_requests()
                self.playback.release_all(restart=True)
                if self.wall is not None:
                    try:
                        self.wall.clear_bindings()
                    except Exception:
                        pass
                    try:
                        self.wall.set_session_state(
                            has_session=self._read_account()
                        )
                    except Exception:
                        pass
                if self.page is not None:
                    try:
                        self.page.on_account_changed(
                            payload.get("generation")
                        )
                        if self.order_sidebar is not None and self.order_sidebar.isVisible():
                            self.page.refresh_from_wall()
                    except RuntimeError:
                        self.page = None
        elif event == api.EVENT_CLOSING:
            if not self._shutting_down:
                self.playback_session.capture()
            self.on_unload()

    def on_unload(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        container = getattr(self, '_order_container', None)
        if container is not None:
            self.context.unregister_sidebar_page('watch_order')
            container.hide()
            container.deleteLater()
            self._order_container = None
            self.order_sidebar = None
        self.playback.dispose_prefetch()
        self.pip_dpi.stop_flush()
        self.playback.stop_tick_timer()
        self._drain_requests()
        # 关窗顺序（app.py）：EVENT_CLOSING → plugins.unload →
        # config save。这里把合成房间还原成原直播房/空格，配置永不留点播房。
        self.playback.release_all(restart=False)
        self._sync_pip_filter()
        self.input.vod_press.clear()
        self._geometry_pending.clear()
        for tile, slot in self._quality_connected.values():
            try:
                tile.qualityChanged.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._quality_connected.clear()
        self._subtitle_controls.clear()
        if self._tile_filter is not None:
            for tile in self._cell_tiles.values():
                for target in (tile, getattr(tile, "video", None),
                               getattr(tile, "stream_badge", None),
                               getattr(tile, "pause_overlay", None)):
                    if target is None:
                        continue
                    try:
                        target.removeEventFilter(self._tile_filter)
                    except Exception:
                        pass
        self._watched_tiles.clear()
        self._cell_tiles.clear()
        self._pick_target_tile = None
        self.input.stop_click_timers()
        self.input.dispose_shortcuts()
        self.input.remove_key_filter()
        self.input.key_down.clear()
        self.input.key_dedup.clear()
        self.input.fullscreen_active = False
        self.pip_dpi.dispose_covers()
        if self._face_loader is not None:
            self._face_loader.shutdown()
            self._face_loader.deleteLater()
            self._face_loader = None
        self._face_pixmaps.clear()
        self.input.space_host = None
        self._bar_progress.clear()

        page = self.page
        self.page = None
        if page is not None:
            try:
                page.shutdown()
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass
            try:
                page.deleteLater()
            except RuntimeError:
                pass

        wall = self.wall
        self.wall = None
        if wall is not None:
            try:
                wall.shutdown()
            except Exception:
                pass
            try:
                wall.deleteLater()
            except RuntimeError:
                pass
        self._tile_registered = False

        if self._registered and self.context is not None:
            try:
                self.context.unregister_workspace_control("watchlater")
            except Exception:
                pass
            self.button = None
            self.action = None
        else:
            button = self.button
            self.button = None
            self.action = None
            if button is not None:
                button.deleteLater()
        self._registered = False

    # ---------------------------------------------------------------- entry
    def _toggle_page(self, checked: bool) -> None:
        if self._shutting_down:
            return
        if checked:
            if not self._show_page():
                self._set_action_checked(False)
        elif self.page is not None and self.page.isVisible():
            self.page.close()

    def _ensure_page(self):
        if self._shutting_down:
            return None
        if self.page is not None:
            return self.page
        try:
            from .page import WatchLaterPage

            self.page = WatchLaterPage(self.context, wall=self.wall)
            self.page.removal_undo.snapshot_order = lambda: list(self.watch_order.keys)
            self.page.removal_undo.restore_order = self._restore_removed_order
            self.page.closed.connect(self._page_closed)
            self.page.items_changed.connect(self._watch_items_changed)
            self.page.preferences_changed.connect(self._preferences_changed)
            # v1.0 需求：：横幅「退出选片」→ 清目标格，「播放」回浏览语义。
            cancelled = getattr(self.page, "pick_cancelled", None)
            if cancelled is not None:
                try:
                    cancelled.connect(self._on_pick_cancelled)
                except Exception:
                    pass
            self.page.set_play_handler(self._play_item_from_page)
            self.page.set_placement_handlers(self._placement_targets, self._place_item_from_page, self._placement_snapshot)
            if self.wall is not None:
                self.wall.set_cover_provider(self.page.covers.get)
        except Exception as error:
            self.page = None
            self._log("page create failed type=" + type(error).__name__)
            return None
        return self.page

    def _on_pick_cancelled(self) -> None:
        self._pick_target_tile = None

    def _show_page(self, pick_tile=None) -> bool:
        """独立弹出窗口（v1.0 bug 6）；右键选片与侧栏共用同一个（需求 7）。

        pick_tile 非空时进入选片模式：页面顶部显示目标格横幅，点「播放」
        绑定到该格后自动关窗；拖拽任何视频卡到任意格子始终可用（需求 6）。
        """
        if self._shutting_down:
            return False
        page = self._ensure_page()
        if page is None:
            self._notify_warning(
                "无法创建稍后再看页面，请更新插件或查看日志。"
            )
            return False
        self._pick_target_tile = pick_tile
        try:
            label = None
            if pick_tile is not None:
                tiles = self._host_tiles()
                try:
                    label = "第 %d 格" % (tiles.index(pick_tile) + 1)
                except ValueError:
                    label = "目标格子"
            page.set_pick_target(label)
            page.refresh_targets(pick_tile)
        except Exception:
            pass
        try:
            if not page.isVisible():
                # v1.0 性能：setWindowFlags 会销毁并重建原生窗口（连同全部
                # 子控件句柄），每次重开都做一次等于把整页重排一遍。改为
                # 只在首次（parentWidget 为空）初始化窗口标志与几何；之后
                # 直接 show，保留用户手动调整过的位置和尺寸。
                if page.parentWidget() is None:
                    window = self._host_window()
                    if window is not None:
                        page.setParent(window, Qt.Window | Qt.FramelessWindowHint)
                    else:
                        page.setWindowFlags(page.windowFlags() | Qt.Window)
                    page.setWindowTitle("B站稍后再看")
                    page.resize(980, 660)
                page.show()
            page.raise_()
            page.activateWindow()
        except Exception as error:
            self._log("popup show failed type=" + type(error).__name__)
            self._notify_warning(
                "无法打开稍后再看窗口，请更新插件或查看日志。"
            )
            return False
        try:
            page.activate()
        except Exception:
            pass
        return True

    def _notify_warning(self, message: str) -> None:
        try:
            self.context.notify(message, kind="warning")
        except Exception:
            pass

    def _log(self, message: str) -> None:
        """防御式日志：任何一步失败都绝不让异常往上报（需求：的异常崩溃）。

        - 脱敏输入先截断，避免超长串让正则跑太久；
        - `__init__` 早期（_log_lock 还没建）就调用也安全；
        - 宿主 `context.log` 可能未挂 / 非线程安全，全部兜住。
        """
        try:
            raw = str(message)[:4096]
            text = _CREDENTIAL_ASSIGNMENT.sub("credential=<redacted>", raw)
            text = _CREDENTIAL_WORD.sub("credential", text)
        except Exception:
            return
        try:
            if not hasattr(self, "_log_lock") or not hasattr(self, "_log_last_seen"):
                return
            now = time.monotonic()
            with self._log_lock:
                last = self._log_last_seen.get(text)
                if last is not None and now - last < _LOG_DEDUP_WINDOW:
                    return
                if (
                    text not in self._log_last_seen
                    and len(self._log_last_seen) >= _LOG_MAX_KEYS
                ):
                    oldest = min(self._log_last_seen, key=self._log_last_seen.get)
                    self._log_last_seen.pop(oldest, None)
                self._log_last_seen[text] = now
        except Exception:
            pass
        try:
            context = getattr(self, "context", None)
            log = getattr(context, "log", None) if context is not None else None
            if callable(log):
                log(text)
        except Exception:
            pass

    def _page_closed(self) -> None:
        if self._shutting_down:
            return
        self._pick_target_tile = None
        if self.page is not None:
            try:
                self.page.set_pick_target(None)
            except Exception:
                pass
        self._set_action_checked(False)

    def _set_action_checked(self, checked: bool) -> None:
        action = self.action
        if action is None:
            return
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)

    # ------------------------------------------------------------ tile menu
    def tile_actions(self, tile):
        if self._shutting_down or self.wall is None:
            return []
        return self.wall.tile_actions(tile)

    # ----------------------------------------------------------------- icon
    @staticmethod
    def _make_icon() -> QIcon:
        from .entry_icon import WatchLaterIcon
        return QIcon(WatchLaterIcon())


plugin = BiliWatchLaterPlugin()
