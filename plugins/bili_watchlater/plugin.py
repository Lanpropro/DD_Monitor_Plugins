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

from PySide6.QtCore import QSize, Qt, QTimer, QUrl

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
    QFontMetrics,
    QDesktopServices,
    QIcon,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
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
from .tile_presentation import TilePresentation, _TileEventFilter, _resolve_tile
from .playback_input import PlaybackInput, _PipClickFilter, _VodKeyFilter, _SCREEN_CHANGE
from .playback_binding import VodBinding as _VodBinding  # Legacy binding import.









def _presentation_field(name):
    return property(lambda self: getattr(self.presentation, name),
                    lambda self, value: setattr(self.presentation, name, value))


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
    @property
    def BAR_PROGRESS_MIN_WIDTH(self):
        return BAR_PROGRESS_MIN_WIDTH

    @property
    def POSITION_BADGE_ENABLED(self):
        return POSITION_BADGE_ENABLED

    _cell_tiles = _presentation_field('_cell_tiles')
    _tile_filter = _presentation_field('_tile_filter')
    _watched_tiles = _presentation_field('_watched_tiles')
    _bar_progress = _presentation_field('_bar_progress')
    _subtitle_controls = _presentation_field('_subtitle_controls')
    _overlay_keys = _presentation_field('_overlay_keys')
    _quality_connected = _presentation_field('_quality_connected')
    _geometry_pending = _presentation_field('_geometry_pending')
    _face_loader = _presentation_field('_face_loader')
    _face_pixmaps = _presentation_field('_face_pixmaps')

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
        self.presentation = TilePresentation(self)
        self.page = None
        self.action: QAction | None = None
        self.button: QToolButton | None = None
        self.wall = None
        self._registered = False
        self._tile_registered = False
        self._shutting_down = False
        # --- v1.0 点播绑定：bvid -> _VodBinding（合成房间随格子拖拽移动，
        # 节拍器按房间标记扫描当前所在格子，绝不按房间号字符串反查绑定）。
        from .playback_session import PlaybackSession
        self.playback_session = PlaybackSession(self)
        # --- 弹窗选片目标格（右键「选择稍后再看视频…」与侧栏同一窗口） ---
        self._pick_target_tile = None
        # --- v1.0：全屏底栏进度条（官方 tile control 通道，按 tile 持有） ---
        self._progress_registered = False
        self.pip_dpi = PipDpiController(self, sip_deleted)
        self._subtitle_registered = False
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
            "register_sidebar_page",
            "unregister_sidebar_page",
            "start_sidebar_drag",
            "cancel_sidebar_drag",
        )
        missing = [
            name
            for name in required
            if not callable(getattr(context, name, None))
        ]
        if missing:
            raise RuntimeError(
                "bili_watchlater 需要 DD监控室CE 0.8 热修复1或更新版本；"
                "当前宿主缺少接口：" + "、".join(missing)
            )
        if not hasattr(api, "EVENT_ACCOUNT_CHANGED") or not hasattr(
            api, "EVENT_CLOSING"
        ):
            raise RuntimeError(
                "bili_watchlater 需要支持账号和退出事件的本体；"
                "当前宿主缺少账号或退出事件"
            )

        if getattr(api, "ROOM_SUPPORTS_LIVE_DANMAKU", None) != "supports_live_danmaku":
            raise RuntimeError(
                "bili_watchlater 需要支持实时弹幕来源能力筛选的配套本体；"
                "请升级到0.8 热修复1或更新版本（缺少 ROOM_SUPPORTS_LIVE_DANMAKU）"
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
            binding = self._bindings.get(room.get("watchlater_bvid"))
            if binding is not None and binding.tile is tile:
                continue  # A first-show restore may already have injected media.
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
        return self.presentation.make_tile_control(tile_context)

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
        return self.presentation.resolve_cell_from_tile(tile)

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
        return self.presentation.watch_tile(tile)

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
        return self.presentation.make_progress_control(tile_context)

    def _seek_tile_vod(self, tile, seconds: float) -> None:
        return self.presentation.seek_tile_vod(tile, seconds)

    # ------------------------------------------------ v1.0 subtitle (需求：)
    def _make_subtitle_control(self, tile_context):
        return self.presentation.make_subtitle_control(tile_context)

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
        return self.presentation.sync_subtitle_control(tile)

    def _apply_subtitle(self, binding, player) -> None:
        return self.playback.apply_subtitle(binding, player)

    @staticmethod
    def _unlink_paths(paths) -> None:
        return PlaybackCoordinator.unlink_paths(paths)

    def _toggle_subtitle(self, tile) -> None:
        return self.playback.toggle_subtitle(tile)

    def _build_empty_tile_menu(self, tile):
        return self.presentation.build_empty_tile_menu(tile)

    def _show_empty_tile_menu(self, tile, global_pos) -> bool:
        return self.presentation.show_empty_tile_menu(tile, global_pos)

    def _register_tile(self, cell, tile) -> None:
        return self.presentation.register_tile(cell, tile)

    def _forget_cell(self, cell) -> None:
        return self.presentation.forget_cell(cell)

    def _unwatch_tile(self, tile) -> None:
        return self.presentation.unwatch_tile(tile)

    def _queue_tile_sync(self, tile) -> None:
        return self.presentation.queue_tile_sync(tile)

    def _drag_source_tile(self, event):
        return self.presentation.drag_source_tile(event)

    def _handle_tile_drag(self, target, event) -> bool:
        return self.presentation.handle_tile_drag(target, event)

    def _sync_tile_widgets(self, tile) -> None:
        return self.presentation.sync_tile_widgets(tile)

    @staticmethod
    def _force_volume_mini(tile) -> None:
        return TilePresentation.force_volume_mini(tile)

    def _repaint_overlays(self, tile) -> None:
        return self.presentation.repaint_overlays(tile)

    # ------------------------------------------- v1.0 fullscreen / interaction
    def _tile_in_pip(self, tile) -> bool:
        return self.presentation.tile_in_pip(tile)

    def _sync_pip_filter(self) -> None:
        return self.input.sync_pip_filter()

    def _fit_vod_bottom_bar(self, tile) -> int:
        return self.presentation.fit_vod_bottom_bar(tile)

    def _sync_fullscreen_state(self, tile) -> None:
        return self.presentation.sync_fullscreen_state(tile)

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
        return self.presentation.fill_pip_choice_avatars(dialog)

    def _on_face_ready(self, bvid: str, data) -> None:
        return self.presentation.on_face_ready(bvid, data)

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
        return self.presentation.show_vod_tile_menu(tile, global_pos)

    def _switch_quality(self, binding, qn: int) -> None:
        return self.presentation.switch_quality(binding, qn)

    def _on_tile_quality_changed(self, tile, room, qn) -> None:
        return self.presentation.on_tile_quality_changed(tile, room, qn)

    def _apply_tile_chrome(self, binding) -> None:
        return self.presentation.apply_tile_chrome(binding)

    def _restore_tile_chrome(self, binding, *, quality: bool) -> None:
        return self.presentation.restore_tile_chrome(binding, quality=quality)

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
        return self.presentation.ensure_tile_widgets(binding)

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
        return self.presentation.sync_position_badge(binding)

    # ------------------------------------------------- v1.0b 视频排序药丸
    def _sync_order_pill(self, binding) -> None:
        return self.presentation.sync_order_pill(binding)

    @staticmethod
    def _restore_order_pill(binding, badge, hide: bool = False) -> None:
        return TilePresentation.restore_order_pill(binding, badge, hide)

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
        self.playback.refresh_items(items)
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
        self._sync_order_wall_marks()
        self.order_sidebar.order_changed.connect(self._watch_order_changed)
        return self.order_sidebar

    def _sync_order_wall_marks(self):
        sidebar = getattr(self, 'order_sidebar', None)
        if sidebar is None:
            return
        keys = {(getattr(tile, 'room', None) or {}).get('watchlater_bvid')
                for tile in self._playback_tiles()}
        sidebar.list.set_on_wall(keys & self.playback.bindings.keys())

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
            self.playback_session.restore()
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
        self.presentation.disconnect_tiles()
        self._pick_target_tile = None
        self.input.stop_click_timers()
        self.input.dispose_shortcuts()
        self.input.remove_key_filter()
        self.input.key_down.clear()
        self.input.key_dedup.clear()
        self.input.fullscreen_active = False
        self.pip_dpi.dispose_covers()
        self.presentation.dispose_faces()
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
