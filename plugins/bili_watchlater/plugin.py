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

import bisect
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
#: 跨屏事件（Qt6 的 ScreenChangeInternal）；缺失时退回 DPR 变化事件。
_SCREEN_CHANGE = getattr(QEvent.Type, "ScreenChangeInternal",
                         QEvent.Type.DevicePixelRatioChange)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
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


from .motion_trace import measure

def _as_int(value) -> int:
    from .models import safe_int

    return safe_int(value)


def _video_page_url(item) -> str:
    # Public https webpage URL, used ONLY for the explicit browser fallback.
    # It is never handed to the player (host EMBEDDED_OPTIONS has ytdl=False).
    from .models import video_url

    return video_url(item)


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


class _PipClickFilter(QObject):
    """v1.0 需求：：多路小窗（PiP）里恢复单击暂停/播放（仅点播格）。

    宿主 ``PictureInPictureWindow`` 在 QApplication 上装了应用级过滤器，
    对 tile.video 的左键 Press grabMouse、Release/DblClick 全部消费，导致
    插件装在 tile.video 上的对象级过滤器收不到点击。应用级过滤器按「后装
    先执行」排序：本过滤器在 PiP 激活时才安装（晚于 PiP window），因此先
    于 PiP 看到事件。Press 不消费（保留 PiP 拖拽重排）；Release 时若未发生
    拖拽位移则排队切换暂停，但仍放行 Release，让宿主完整清理 gesture
    与鼠标抓取；插件不替宿主结束拖拽。
    """

    def __init__(self, plugin) -> None:
        super().__init__()
        self._plugin = plugin
        self._press = None  # (tile, QPoint)

    def eventFilter(self, watched, event):  # noqa: N802
        plugin = self._plugin
        if plugin._shutting_down:
            return False
        kind = event.type()
        # v1.0 需求：：组合画中画选择框出现时补齐点播格的 UP 头像。
        if kind == QEvent.Type.Show and \
                hasattr(watched, "checks") and hasattr(watched, "rooms"):
            try:
                plugin._fill_pip_choice_avatars(watched)
            except Exception:
                pass
            return False
        # 跨 DPI 白屏 → 纯黑遮罩盖住重载过程，不引入加载等待。
        # 事件可能落在浮窗顶层 / tile / video 任意一层，统一交给插件解析。
        if kind in (QEvent.Type.DevicePixelRatioChange, _SCREEN_CHANGE):
            try:
                plugin._on_dpi_event(watched)
            except Exception:
                pass
            return False
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            tile = self._vod_tile_of(watched)
            self._press = ((tile, event.globalPosition().toPoint())
                           if tile is not None else None)
            return False
        if kind == QEvent.Type.MouseMove and (event.buttons() & Qt.LeftButton):
            if self._press is not None:
                tile, start = self._press
                if (event.globalPosition().toPoint() - start).manhattanLength() \
                        >= QApplication.startDragDistance():
                    self._press = None  # 拖拽重排，交还 PiP
            return False
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
            press = self._press
            self._press = None
            if press is None:
                return False
            tile, start = press
            if (event.globalPosition().toPoint() - start).manhattanLength() \
                    >= QApplication.startDragDistance():
                return False  # 拖拽结束，交还 PiP 完成重排
            binding = plugin._binding_for_tile(tile)
            if binding is None or binding.state not in ("playing", "paused"):
                return False
            QTimer.singleShot(0, lambda t=tile, b=binding:
                              plugin._single_vod_click(t, b))
            return False  # 宿主必须收到 Release 来清理 gesture/reorder/grab
        return False

    def _vod_tile_of(self, watched):
        if getattr(watched, "room", None) is not None:
            return None
        try:
            parent = watched.parentWidget()
        except Exception:
            return None
        if parent is None or watched not in (
            getattr(parent, "video", None), getattr(parent, "pause_overlay", None),
            getattr(parent, "cover", None), getattr(parent, "spinner", None),
        ):
            return None
        room = getattr(parent, "room", None)
        if not isinstance(room, dict) or not room.get("watchlater_bvid"):
            return None
        return parent


class _VodKeyFilter(QObject):
    """应用级按键/跨屏过滤器：只接管点播格，不 patch 任何宿主类。

    宿主的全局快捷键是挂在主窗口上的 ``QShortcut(Qt.WindowShortcut)``：一旦
    全屏格窗口或画中画浮窗成为活动窗口，这些快捷键就不再派发（全屏左右键
    seek 失效、PiP 里 B 失效）；而空格落在全屏底栏里获得焦点的按钮上时会被
    当成「点一下」（表现为调画质）。这一层过滤器：

    * ``ShortcutOverride``：只对我们接管的键 accept，压掉宿主/插件自己的
      QShortcut，保证紧随其后的 KeyPress 一定先送到这一层；
    * ``KeyPress``：直接执行暂停/播放、±5 秒进度、弹幕开关，并吞掉事件，
      底栏按钮（尤其画质按钮）不会再被空格触发；
    * ``KeyRelease``：把本轮按住的键一并吞掉，避免 QAbstractButton 松开时
      补一次 click；
    * DPR / 换屏事件：交给插件的黑屏遮罩逻辑统一解析受影响格子。
    """

    def __init__(self, plugin) -> None:
        super().__init__()
        self._plugin = plugin

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        plugin = self._plugin
        if plugin._shutting_down:
            return False
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride:
            if plugin._vod_key_action(event, watched) is None:
                return False
            try:
                event.accept()
            except Exception:
                pass
            return True
        if kind == QEvent.Type.KeyPress:
            try:
                return plugin._handle_vod_key(event, watched)
            except Exception:
                return False
        if kind == QEvent.Type.KeyRelease:
            try:
                key = event.key()
                if key in plugin._key_down:
                    plugin._key_down.discard(key)
                    event.accept()
                    return True
            except Exception:
                pass
            return False
        if kind in (QEvent.Type.DevicePixelRatioChange, _SCREEN_CHANGE):
            try:
                plugin._on_dpi_event(watched)
            except Exception:
                pass
        return False


class _VodBinding:
    """One VOD playback bound to a synthetic room (keyed by bvid)."""

    def __init__(self, item, cell, tile) -> None:
        self.item = item
        self.bvid = str(getattr(item, "bvid", "") or "")
        self.cell = cell
        self.tile = tile
        self.prev_room = None
        self.prev_chrome = {
            name: getattr(tile, name) for name in (
                "_recording_available", "quality_options", "quality",
                "actual_quality", "quality_locked"
            ) if hasattr(tile, name)
        }
        self.resume_paused = False
        self.cid = _as_int(getattr(item, "cid", 0))
        self.state = "resolving"  # resolving/playing/paused/error/ended
        self.stream = None
        self.warm_lease = None
        self.pending_warm_lease = None
        self.progress = 0.0
        self.duration = 0.0
        self.pending_seek = 0.0
        self.pending_audio = ""
        self.audio_attempts = 0
        self.strip = None
        self.card = None
        self.danmaku = None       # [(time, mode, color, text)] sorted
        self.dm_cursor = 0
        self.dm_last_pos = 0.0
        self.dm_pending = False
        self.request_id = 0
        # --- v1.0：清晰度 / 全屏进度 / 预览 / 状态槽 ---
        self.quality = 0          # 当前档位 qn（0=接口默认最高）
        self.qualities = []       # [(qn, label)] 来自 accept_quality/description
        self.line = None          # 全屏视频底边 3px 蓝色进度线
        self.preview = None       # VodPreviewProvider（雪碧图预览）
        # --- v1.0 需求：：B 站字幕（SRT 临时文件）与开关 ---
        self.subtitles: list = []   # [(label, srt_path)]
        self.subtitle_on = False
        self.subtitle_loaded = False
        self._sub_pending = False
        #: v1.0b：复用宿主药丸时的状态（原文案/绘制钩子的存档）。
        self.order_pill = None
        #: v1.0 需求：：多 P 视频当前播放的分 P（0 基）。
        self.part = 0
        # --- v1.0 任务 3/8：左上角「N/总数」位置徽章 ---
        self.badge = None
        #: v1.0 任务 6：EOF 死锁存（一旦 ended 就不再接受续播/重连路径）。
        self.ended = False
        self._state_slot = None   # stateChanged 连接句柄（重注入先断旧连接）
        self._state_player = None  # 连接时的播放器实例（重建后不对旧对象断连）


class BiliWatchLaterPlugin(api.Plugin):
    def __init__(self) -> None:
        super().__init__()
        self.context = None
        self.page = None
        self.action: QAction | None = None
        self.button: QToolButton | None = None
        self.wall = None
        self._registered = False
        self._tile_registered = False
        self._shutting_down = False
        # --- v1.0 点播绑定：bvid -> _VodBinding（合成房间随格子拖拽移动，
        # 节拍器按房间标记扫描当前所在格子，绝不按房间号字符串反查绑定）。
        self._bindings: dict[str, _VodBinding] = {}
        self._cell_tiles: dict[int, object] = {}
        self._vod_requests: set = set()
        self._vod_context: dict[int, dict] = {}
        self._cell_vod: dict[int, int] = {}
        self._vod_seq = 0
        self._next_prefetch = None
        from .playback_session import PlaybackSession
        self.playback_session = PlaybackSession(self)
        self._account_generation = 0
        self._dm_requests: set = set()
        self._dm_context: dict[int, tuple] = {}
        self._dm_seq = 0
        self._sub_requests: set = set()
        self._sub_context: dict[int, tuple] = {}
        self._sub_seq = 0
        self._tick_timer: QTimer | None = None
        # --- 格子事件过滤器（右键/拖放/几何），同一格子只装一次 ---
        self._tile_filter: _TileEventFilter | None = None
        self._watched_tiles: set[int] = set()
        # --- 弹窗选片目标格（右键「选择稍后再看视频…」与侧栏同一窗口） ---
        self._pick_target_tile = None
        # --- v1.0：全屏底栏进度条（官方 tile control 通道，按 tile 持有） ---
        self._bar_progress: dict[int, dict] = {}
        self._progress_registered = False
        # --- v1.0：B 站语义单击/双击（250ms 判别）与空格暂停 ---
        self._click_timers: dict[int, QTimer] = {}
        self._space_shortcut: QShortcut | None = None
        self._danmaku_shortcut: QShortcut | None = None
        self._seek_left: QShortcut | None = None
        self._seek_right: QShortcut | None = None
        self._seek_host = None
        self._danmaku_host = None
        #: 应用级按键过滤器（全屏 / 画中画窗口的按键与跨屏事件）。
        self._key_filter: _VodKeyFilter | None = None
        self._key_down: set[int] = set()
        self._key_dedup: dict[str, float] = {}
        self._fullscreen_active = False
        #: v1.0 需求：：PiP 跨 DPI 时的黑色遮罩（按 tile 复用，避免反复建窗）。
        self._dpi_covers = {}
        self._dpi_cover_timers: dict[int, QTimer] = {}
        self._dpi_cover_deadline: dict[int, float] = {}
        self._dpi_pending = False
        self._dpi_handling = False
        self._dpi_ignore_until = 0.0
        self._dpi_flush_timer: QTimer | None = None
        # Avatars share the async image loader; no independently owned QThreads.
        self._face_loader = None
        self._face_pixmaps = {}
        self._space_host = None
        # --- v1.0：单击/拖拽判别自记 press（不依赖宿主 _press_pos） ---
        self._vod_press: dict[int, QPoint] = {}
        # --- v1.0 需求：：PiP 应用级点击过滤器（激活时才装，先于 PiP） ---
        self._pip_filter: _PipClickFilter | None = None
        self._pip_active = False
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
        if self.context is None:
            return None
        try:
            return getattr(self.context, "window", None)
        except Exception:
            return None

    def _host_tiles(self):
        window = self._host_window()
        wall = getattr(window, "wall", None)
        tiles = getattr(wall, "tiles", None)
        try:
            return list(tiles) if tiles is not None else []
        except TypeError:
            return []

    def _playback_tiles(self):
        """Chat cells keep backing Tiles, but are not video destinations."""
        tiles = self._host_tiles()
        wall = getattr(self._host_window(), 'wall', None)
        panes = getattr(wall, 'pane_danmaku', None)
        if panes is None:
            return tiles
        chat_cells = panes.bindings()
        return [tile for cell, tile in panes.cell_tiles().items()
                if cell not in chat_cells and tile in tiles]

    def _host_player(self, tile):
        window = self._host_window()
        players = getattr(window, "players", None)
        if players is None:
            return None
        try:
            return players.get(tile)
        except Exception:
            return None

    def _stop_host_tile(self, tile) -> None:
        """能力探测：停掉这一格的宿主播放器（释放解码/音频/网络/重连）。"""
        window = self._host_window()
        stop = getattr(window, "_stop_tile", None)
        if not callable(stop):
            return
        try:
            stop(tile)
        except Exception as error:
            self._log("host tile stop failed type=" + type(error).__name__)

    def _start_host_tile(self, tile) -> None:
        """能力探测：恢复这一格的直播。宿主自己检查 room_id/live/关窗。"""
        room = getattr(tile, "room", None)
        if not isinstance(room, dict) or not str(room.get("room_id") or ""):
            return
        window = self._host_window()
        start = getattr(window, "start_tile", None)
        if not callable(start):
            return
        try:
            start(tile)
        except Exception as error:
            self._log("host tile start failed type=" + type(error).__name__)

    def _play_on_host(self, tile, url: str, headers: dict, *, start_paused=False) -> bool:
        """v1.0 核心：把点播直链注入宿主自己的格子播放器。

        ``_play_on``（app.py）会创建/复用 ``window.players[tile]``、
        接好宿主状态槽、写 tile.stream_*、发 stream.resolved —— 底栏按钮、
        占位符、暂停帧、快捷键、拖拽秒切因此全部原生生效。
        """
        window = self._host_window()
        play_on = getattr(window, "_play_on", None)
        if not callable(play_on):
            return False
        muted = bool(getattr(tile, 'muted', False))
        if start_paused:
            tile.muted = True  # Suppress audio until the host player has accepted pause.
        try:
            play_on(tile, url, profile="web", headers=headers)
            if start_paused:
                tile.set_paused(True)
                player = self._host_player(tile)
                if player is not None:
                    player.set_paused(True)
        except Exception as error:
            self._log("host play_on failed type=" + type(error).__name__)
            return False
        finally:
            if start_paused:
                tile.muted = muted
                player = self._host_player(tile)
                if player is not None:
                    player.set_muted(muted or bool(getattr(window, '_match_sync_active', False)))
        return True

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
            (b for b in self._bindings.values() if b.tile is tile), None
        )
        if binding is not None:
            return binding
        # 全屏 / 画中画可能用替身格对象：按合成房间的 bvid 反查绑定。
        try:
            room = getattr(tile, "room", None)
            if isinstance(room, dict):
                return self._bindings.get(str(room.get("watchlater_bvid") or ""))
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
        if binding.subtitles or binding._sub_pending or self._shutting_down:
            return
        from .workers import SubtitleRequest

        self._sub_seq += 1
        request_id = self._sub_seq
        binding._sub_pending = True
        self._sub_context[request_id] = (binding, self._account_generation)
        worker = SubtitleRequest(cookies, request_id, binding.bvid, binding.cid)
        self._sub_requests.add(worker)
        worker.completed.connect(self._on_subtitle_completed)
        worker.failed.connect(self._on_subtitle_failed)
        worker.finished.connect(
            lambda rid=request_id: self._retire_subtitle(rid), Qt.QueuedConnection
        )
        worker.finished.connect(worker.deleteLater)
        self._sync_subtitle_control(binding.tile)
        try:
            worker.start()
        except Exception as error:
            self._sub_requests.discard(worker)
            self._on_subtitle_failed(request_id, error)
            worker.deleteLater()

    def _retire_subtitle(self, request_id: int) -> None:
        self._sub_context.pop(request_id, None)
        for worker in tuple(self._sub_requests):
            if getattr(worker, "request_id", None) == request_id:
                self._sub_requests.discard(worker)
                break

    def _current_subtitle_binding(self, request_id):
        ctx = self._sub_context.pop(request_id, None)
        if ctx is None:
            return None
        binding, generation = ctx
        if (self._shutting_down or generation != self._account_generation
                or self._bindings.get(binding.bvid) is not binding):
            return None
        binding._sub_pending = False
        return binding

    def _on_subtitle_completed(self, request_id, results) -> None:
        binding = self._current_subtitle_binding(request_id)
        if binding is None:
            self._unlink_paths([path for _label, path in (results or [])])
            return
        binding.subtitles = list(results or [])
        if not binding.subtitles:
            binding.subtitle_on = False
            self._notify_warning("该视频没有可用字幕，或当前账号暂不能获取字幕。")
        self._sync_subtitle_control(binding.tile)
        # 文件加载后由 ticker 挂到对应播放器；多路返回不会串到其它绑定。

    def _on_subtitle_failed(self, request_id, error) -> None:
        binding = self._current_subtitle_binding(request_id)
        if binding is None:
            return
        binding.subtitle_on = False
        self._sync_subtitle_control(binding.tile)
        self._notify_warning("字幕加载失败，播放不受影响。")
        self._log("subtitle fetch failed type=" + type(error).__name__)

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
        if not binding.subtitle_on or not binding.subtitles or binding.subtitle_loaded:
            return
        label, path = binding.subtitles[0]
        if self._mpv_command(player, "sub-add", path, "select", str(label)):
            binding.subtitle_loaded = True
            try:
                player._set("sub-visibility", True)
            except Exception:
                pass
        else:
            binding.subtitle_on = False
            self._sync_subtitle_control(binding.tile)
            self._notify_warning("当前播放器无法加载字幕。")

    @staticmethod
    def _unlink_paths(paths) -> None:
        import os

        for path in paths:
            try:
                os.unlink(path)
            except Exception:
                pass

    def _toggle_subtitle(self, tile) -> None:
        binding = self._binding_for_tile(tile)
        if (binding is None or binding.state not in ("playing", "paused")
                or binding._sub_pending):
            return
        binding.subtitle_on = not binding.subtitle_on
        if binding.subtitle_on and not binding.subtitles:
            self._start_subtitle_fetch(binding, self._cookie_snapshot())
        player = self._host_player(tile)
        if player is not None and binding.subtitle_loaded:
            try:
                player._set("sub-visibility", binding.subtitle_on)
            except Exception:
                pass
        self._sync_subtitle_control(tile)

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
        self._vod_press.pop(key, None)
        timer = self._click_timers.pop(key, None)
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
                self._vod_press.clear()
                for t in (source, target):
                    timer = self._click_timers.pop(id(t), None)
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
        for binding in self._bindings.values():
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
        for binding in self._bindings.values():
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
        """v1.0 需求：：PiP 激活时安装应用级点击过滤器（晚于 PiP window
        安装 → 先于它执行），退出时移除。在节拍器里按状态变化收敛。"""
        window = self._host_window()
        pip = getattr(window, "picture_in_picture", None)
        active_fn = getattr(pip, "active", None)
        active = False
        if callable(active_fn):
            try:
                active = bool(active_fn())
            except Exception:
                active = False
        active = active and bool(self._bindings) and not self._shutting_down
        floating = getattr(pip, "floating", None) if active else None
        identity = id(floating) if floating is not None else active
        if active == self._pip_active and identity == getattr(self, "_pip_identity", None):
            return
        self._pip_identity = identity
        self._pip_active = active
        # 画中画窗口此时才装上自己的应用级过滤器；我们的按键过滤器要重装
        # 一次，保持「后装先执行」，否则按键会先被浮窗过滤器吃掉。
        self._bump_key_filter()
        self._vod_press.clear()
        if self._pip_filter is not None:
            self._pip_filter._press = None
        app = QApplication.instance()
        if app is None:
            return
        if active:
            if self._pip_filter is None:
                self._pip_filter = _PipClickFilter(self)
            try:
                app.installEventFilter(self._pip_filter)
            except Exception:
                pass
        else:
            if self._pip_filter is not None:
                try:
                    app.removeEventFilter(self._pip_filter)
                except Exception:
                    pass
            self._pip_filter = None

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
        if fs_active != self._fullscreen_active:
            self._fullscreen_active = fs_active
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
        """四个兜底 QShortcut 跟随活动窗口（全屏窗口 / 画中画浮窗 / 主窗）。"""
        if host is None:
            return
        for attr, host_attr in (
            ("_space_shortcut", "_space_host"),
            ("_seek_left", "_seek_host"),
            ("_seek_right", "_seek_host"),
            ("_danmaku_shortcut", "_danmaku_host"),
        ):
            shortcut = getattr(self, attr, None)
            if shortcut is None or getattr(self, host_attr, None) is host:
                continue
            try:
                shortcut.setParent(host)
                setattr(self, host_attr, host)
            except Exception:
                pass

    def _on_vod_click(self, tile, binding) -> None:
        """B 站语义（需求：）：单击=暂停/播放，双击=全屏。

        第一次左键释放起 250ms 判别窗；窗内第二次释放=双击，取消单击。
        拖拽结束（宿主已清 _press_pos）不会走到这里（过滤器已挡）。
        """
        key = id(tile)
        timer = self._click_timers.get(key)
        if timer is not None and timer.isActive():
            timer.stop()
            self._click_timers.pop(key, None)
            signal = getattr(tile, "fullscreenRequested", None)
            if signal is not None:
                try:
                    signal.emit(tile)
                except Exception:
                    pass
            return
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(250)
        timer.timeout.connect(
            lambda t=tile, b=binding: self._single_vod_click(t, b)
        )
        self._click_timers[key] = timer
        timer.start()

    def _single_vod_click(self, tile, binding) -> None:
        self._click_timers.pop(id(tile), None)
        if (self._shutting_down or binding.tile is not tile
                or self._bindings.get(binding.bvid) is not binding):
            return
        if binding.state not in ("playing", "paused"):
            return
        signal = getattr(tile, "pauseToggled", None)
        if signal is not None:
            try:
                signal.emit(tile.room)
            except Exception:
                pass

    def _ensure_space_shortcut(self) -> None:
        """空格暂停（需求：）：宿主从未绑定空格，插件补一个。

        WindowShortcut 上下文：只在宿主窗口（或全屏格子窗口）激活时生效；
        弹窗搜索框聚焦时天然不触发。处理器再检查输入控件焦点让路。
        """
        if self._space_shortcut is not None or self._shutting_down:
            return
        window = self._host_window()
        if window is None:
            return
        try:
            shortcut = QShortcut(QKeySequence(Qt.Key_Space), window)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(self._on_space)
            self._space_shortcut = shortcut
            self._space_host = window
        except Exception as error:
            self._log("space shortcut failed type=" + type(error).__name__)

    def _tile_under_cursor(self):
        """鼠标下的点播格：墙面 + 全屏窗口 + 画中画浮窗一起找。"""
        try:
            pos = QCursor.pos()
        except Exception:
            return None
        for tile in self._vod_tiles_anywhere():
            try:
                if tile.isVisible() and tile.rect().contains(tile.mapFromGlobal(pos)):
                    return tile
            except Exception:
                continue
        return None

    # --------------------------------------- 应用级按键：全屏 / 画中画统一入口
    def _bump_key_filter(self) -> None:
        """（重新）安装应用级过滤器，保证它比宿主 PiP 过滤器更晚装、更早执行。

        QApplication 的事件过滤器按「后装先执行」调用。画中画窗口是在插件
        加载后才创建并安装自己的应用级过滤器，所以每次形态切换都把本过滤器
        摘下再装一次，确保按键先到我们手里。
        """
        if self._shutting_down:
            return
        app = QApplication.instance()
        if app is None:
            return
        if self._key_filter is None:
            self._key_filter = _VodKeyFilter(self)
        try:
            app.removeEventFilter(self._key_filter)
        except Exception:
            pass
        try:
            app.installEventFilter(self._key_filter)
        except Exception as error:
            self._log("key filter install failed type=" + type(error).__name__)

    def _vod_tiles_anywhere(self):
        """当前所有点播格：主墙 + 全屏窗口 + 画中画浮窗（按 id 去重）。"""
        found: dict[int, object] = {}
        for tile in self._host_tiles():
            try:
                room = getattr(tile, "room", None)
                if isinstance(room, dict) and room.get("watchlater_bvid"):
                    found[id(tile)] = tile
            except Exception:
                continue
        app = QApplication.instance()
        main = self._host_window()
        if app is not None:
            for top in app.topLevelWidgets():
                if top is main:
                    continue
                try:
                    if not top.isVisible():
                        continue
                    children = top.findChildren(QWidget)
                except Exception:
                    continue
                for child in children:
                    try:
                        room = getattr(child, "room", None)
                        if (isinstance(room, dict) and room.get("watchlater_bvid")
                                and hasattr(child, "video") and hasattr(child, "bottom")):
                            found[id(child)] = child
                    except Exception:
                        continue
        return list(found.values())

    def _any_tile_under_cursor(self):
        """鼠标下的任意格子（含直播格），用于画中画里的弹幕快捷键。"""
        try:
            pos = QCursor.pos()
        except Exception:
            return None
        candidates = list(self._host_tiles())
        app = QApplication.instance()
        main = self._host_window()
        if app is not None:
            for top in app.topLevelWidgets():
                if top is main:
                    continue
                try:
                    if not top.isVisible():
                        continue
                    for child in top.findChildren(QWidget):
                        if (hasattr(child, "room") and hasattr(child, "video")
                                and hasattr(child, "bottom")):
                            candidates.append(child)
                except Exception:
                    continue
        seen: set[int] = set()
        for tile in candidates:
            if tile is None or id(tile) in seen:
                continue
            seen.add(id(tile))
            try:
                if tile.isVisible() and tile.rect().contains(tile.mapFromGlobal(pos)):
                    return tile
            except Exception:
                continue
        return None

    def _tile_in_pip_window(self, tile) -> bool:
        """这个格子是否真的被搬进了画中画浮窗（而不是主墙里的格子）。"""
        if tile is None:
            return False
        try:
            if bool(tile.property("pictureInPicture")):
                return True
        except Exception:
            pass
        try:
            pip = getattr(self._host_window(), "picture_in_picture", None)
            floating = getattr(pip, "floating", None)
            if floating is not None and (
                tile.window() is floating or floating.isAncestorOf(tile)
            ):
                return True
        except Exception:
            pass
        return False

    def _active_vod_tile(self):
        """当前应响应按键的点播格：全屏 > 鼠标下 > 活动浮窗里的格子。"""
        tiles = self._vod_tiles_anywhere()
        for tile in tiles:
            try:
                if bool(tile.property("fullscreen")) or (
                    tile.window() is not None and tile.window().isFullScreen()
                ):
                    return tile
            except Exception:
                continue
        tile = self._tile_under_cursor()
        if tile is not None:
            return tile
        try:
            active = QApplication.activeWindow()
        except Exception:
            active = None
        if active is not None and active is not self._host_window():
            for candidate in tiles:
                try:
                    if candidate.window() is active:
                        return candidate
                except Exception:
                    continue
        return None

    def _key_window_ok(self, watched=None) -> bool:
        """按键所在窗口必须是主窗口 / 全屏窗口 / 画中画浮窗之一。

        稍后再看弹窗（搜索框、按钮）或其它对话框里的同名按键一律放行，
        不能误把弹窗里的空格当成播放暂停。
        """
        try:
            active = QApplication.activeWindow()
        except Exception:
            active = None
        if active is None and isinstance(watched, QWidget):
            try:
                active = watched.window()
            except Exception:
                active = None
        if active is None:
            return False
        main = self._host_window()
        if active is main:
            return True
        try:
            pip = getattr(main, "picture_in_picture", None)
            floating = getattr(pip, "floating", None)
            if floating is not None and (
                active is floating or floating.isAncestorOf(active)
            ):
                return True
        except Exception:
            pass
        for tile in self._vod_tiles_anywhere():
            try:
                if tile.window() is active or active is tile:
                    return True
            except Exception:
                continue
        return False

    def _danmaku_key_text(self) -> str:
        window = self._host_window()
        try:
            return str((getattr(window, "shortcuts", None) or {}).get(
                "video_danmaku") or "B")
        except Exception:
            return "B"

    def _danmaku_combo(self):
        """宿主的弹幕快捷键（默认 B）拆成 (键码, 修饰键)。"""
        text = self._danmaku_key_text()
        try:
            seq = QKeySequence(text)
            if not seq.isEmpty():
                combo = seq[0]
                if hasattr(combo, "key"):
                    return int(combo.key()), combo.keyboardModifiers().value
                return int(combo) & 0x01FFFFFF, Qt.NoModifier.value
        except Exception:
            pass
        return int(Qt.Key_B), Qt.NoModifier.value

    def _vod_key_action(self, event, watched=None):
        """本次按键是否该由插件接管；返回 (动作, 格子, 绑定) 或 None。"""
        try:
            key = int(event.key())
            modifiers = event.modifiers().value
        except Exception:
            return None
        danmaku_key, danmaku_mod = self._danmaku_combo()
        action = None
        if key in (int(Qt.Key_Space), int(Qt.Key_Left), int(Qt.Key_Right)):
            if modifiers & ~Qt.KeypadModifier.value:
                return None
            action = {
                int(Qt.Key_Space): "space",
                int(Qt.Key_Left): "left",
                int(Qt.Key_Right): "right",
            }[key]
        elif key == danmaku_key and modifiers == danmaku_mod:
            action = "danmaku"
        if action is None or not self._key_window_ok(watched):
            return None
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return None  # 输入框里的空格/方向键是打字和移动光标
        if action == 'danmaku' and self._host_danmaku_shortcut_reachable(watched):
            return None
        tile = self._active_vod_tile()
        binding = self._binding_for_tile(tile)
        if action == "danmaku":
            # 画中画里直播格也允许用宿主同款弹幕键（宿主快捷键挂在主窗口，
            # 浮窗成为活动窗口后收不到），只要它确实在浮窗内且有弹幕按钮。
            if tile is None or binding is None:
                tile = self._any_tile_under_cursor()
                binding = self._binding_for_tile(tile)
                if (tile is None or not self._tile_in_pip_window(tile)
                        or getattr(tile, "danmaku_button", None) is None):
                    return None
        elif binding is None or binding.state not in ("playing", "paused"):
            return None
        return action, tile, binding

    def _claim_key(self, name: str) -> bool:
        """同一次按键只允许一条链路（应用级过滤器 / QShortcut）处理一次。"""
        now = time.monotonic()
        last = self._key_dedup.get(name, 0.0)
        if now - last < 0.012:
            return False
        self._key_dedup[name] = now
        if len(self._key_dedup) > 16:
            for stale in [k for k, v in self._key_dedup.items()
                          if now - v > 1.0]:
                self._key_dedup.pop(stale, None)
        return True

    def _handle_vod_key(self, event, watched=None) -> bool:
        info = self._vod_key_action(event, watched)
        if info is None:
            return False
        action, tile, binding = info
        try:
            self._key_down.add(int(event.key()))
        except Exception:
            pass
        if not self._claim_key(action):
            # 同一按键已被本插件另一条链路处理，仍然吞掉，别让按钮再响应。
            try:
                event.accept()
            except Exception:
                pass
            return True
        if action == "space":
            self._toggle_pause(tile, binding)
        elif action in ("left", "right"):
            step = -5.0 if action == "left" else 5.0
            self._seek_binding_relative(binding, step)
        elif action == "danmaku":
            self._toggle_danmaku(tile, binding)
        try:
            event.accept()
        except Exception:
            pass
        return True

    def _toggle_pause(self, tile, binding) -> None:
        if binding is None or binding.state not in ("playing", "paused"):
            return
        signal = getattr(tile, "pauseToggled", None)
        if signal is None and binding.tile is not None:
            tile = binding.tile
            signal = getattr(tile, "pauseToggled", None)
        if signal is None:
            return
        try:
            signal.emit(getattr(tile, "room", None))
        except Exception:
            pass

    def _toggle_danmaku(self, tile, binding) -> None:
        button = getattr(tile, "danmaku_button", None)
        if button is None and binding is not None and binding.tile is not None:
            button = getattr(binding.tile, "danmaku_button", None)
        if button is None:
            return
        try:
            before = bool(button.isChecked())
        except Exception:
            return

        def finish() -> None:
            if self._shutting_down:
                return
            try:
                if bool(button.isChecked()) == before:
                    button.toggle()   # 宿主这一路没处理 → 插件补上
            except RuntimeError:
                pass

        QTimer.singleShot(0, finish)

    # ------------------------------------------------------ v1.0 方向键 seek
    def _ensure_seek_shortcuts(self) -> None:
        """需求：：左右方向键控制视频进度（B 站网页语义：±5 秒，可按住连发）。

        只占左右方向键，不动上下（那是宿主的音量）。全屏时把快捷键挂到全屏
        窗口（和空格同一套 _sync 逻辑），输入框聚焦时不生效。
        """
        if self._seek_left is not None or self._shutting_down:
            return
        window = self._host_window()
        if window is None:
            return
        try:
            left = QShortcut(QKeySequence(Qt.Key_Left), window)
            left.setContext(Qt.WindowShortcut)
            left.activated.connect(lambda: self._seek_by_keys(-5.0))
            right = QShortcut(QKeySequence(Qt.Key_Right), window)
            right.setContext(Qt.WindowShortcut)
            right.activated.connect(lambda: self._seek_by_keys(5.0))
            self._seek_left, self._seek_right = left, right
            self._seek_host = window
        except Exception as error:
            self._log("seek shortcut failed type=" + type(error).__name__)

    def _seek_by_keys(self, delta: float, tile=None) -> None:
        """QShortcut 兜底链路（主窗口）；全屏/画中画由应用级过滤器直接调用。"""
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return
        if not self._claim_key("left" if delta < 0 else "right"):
            return
        if tile is None:
            tile = self._active_vod_tile()
        binding = self._binding_for_tile(tile)
        if binding is None or binding.state not in ("playing", "paused"):
            return
        self._seek_binding_relative(binding, float(delta))

    # ------------------------------------------------ PiP 弹幕键 / 跨屏黑遮罩
    def _ensure_danmaku_shortcut(self) -> None:
        """The host owns this QShortcut; floating VOD windows use our key filter."""
        return

    def _host_danmaku_shortcut_reachable(self, watched=None) -> bool:
        window = self._host_window()
        target = watched.window() if isinstance(watched, QWidget) else QApplication.activeWindow()
        sequence = QKeySequence(self._danmaku_key_text())
        for shortcut in getattr(window, '_window_shortcuts', ()):
            try:
                if (shortcut.isEnabled() and shortcut.key() == sequence
                        and shortcut.parent().window() is target):
                    return True
            except RuntimeError:
                continue
        return False

    def _on_danmaku_shortcut(self) -> None:
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return
        if not self._claim_key("danmaku"):
            return
        tile = self._active_vod_tile()
        binding = self._binding_for_tile(tile)
        if binding is None:
            return
        self._toggle_danmaku(tile, binding)

    def _on_dpi_event(self, watched) -> None:
        """跨 DPI / 换屏事件入口：只登记，绝不在事件派发栈里做重活。

        换屏时 Qt 会给窗口树里几乎每个 QObject 投递 ScreenChangeInternal /
        DevicePixelRatioChange，插件的三个事件过滤器会收到成百上千次。旧实现
        在每次事件里 ``win.findChildren(QWidget)`` 全树扫描并同步创建 / show
        原生黑遮罩，遮罩自身又产生同类事件 → 事件风暴与重入；GUI 线程长时间
        停在扫描循环里（日志 faulthandler 反复抓到 plugin.py 的 getattr 行），
        宿主看门狗判为卡死，Windows 侧还可能 native access violation。

        现在无论事件落在浮窗顶层、QWindow、tile 还是 video，都只做一次合并：
        120ms 后的单次定时器里，仅从插件自己维护的 ``_bindings`` 取点播格
        （不再扫控件树），统一盖遮罩 + 排队几何同步。处理期间与之后 350ms
        的重入/冷却事件一律忽略，从根上掐断自激循环。
        """
        if (self._shutting_down or self._dpi_handling
                or not self._bindings):
            return
        if self._pip_drag_active():
            # 拖动浮窗时 Move/DPR 事件风暴会让 900ms 遮罩反复续期 → 黑屏。
            # 拖动期间不盖、已盖的立即撤掉，并给一小段冷却吞掉松手后的
            # 残余事件；真正的跨 DPI 变化（非拖动）仍走原来的保护逻辑。
            self._hide_all_dpi_covers()
            self._dpi_pending = False
            try:
                timer = self._dpi_flush_timer
                if timer is not None and not sip_deleted(timer):
                    timer.stop()
            except Exception:
                pass
            self._dpi_ignore_until = time.monotonic() + 0.25
            return
        # 自己创建的原生黑遮罩（及其 windowHandle）引发的同类事件不再排队。
        try:
            if getattr(watched, "objectName", lambda: "")() == "watchlaterDpiCover":
                return
        except Exception:
            pass
        now = time.monotonic()
        if now < self._dpi_ignore_until or self._dpi_pending:
            return
        self._dpi_pending = True
        try:
            timer = self._dpi_flush_timer
            if timer is None or sip_deleted(timer):
                timer = QTimer()          # Plugin 不是 QObject，不能用 self 作 parent
                timer.setSingleShot(True)
                timer.timeout.connect(self._flush_dpi_event)
                self._dpi_flush_timer = timer
            timer.stop()
            timer.start(120)
        except Exception:
            self._dpi_pending = False

    def _flush_dpi_event(self) -> None:
        """合并后的跨屏处理：盖遮罩 + 对真实点播格排队一次几何同步。"""
        self._dpi_pending = False
        if self._shutting_down or not self._bindings:
            return
        if self._pip_drag_active():
            self._hide_all_dpi_covers()
            return
        self._dpi_handling = True
        try:
            # 绑定表就是插件自己的「点播格清单」：主墙 / 全屏 / 画中画的
            # tile 都在里面，不需要（也不允许）遍历整个宿主控件树。
            vod_tiles = []
            for binding in list(self._bindings.values()):
                tile = binding.tile
                if tile is None:
                    continue
                try:
                    if not sip_deleted(tile):
                        vod_tiles.append(tile)
                except Exception:
                    continue
            # 黑遮罩只盖点播格；直播格绝不盖。
            for tile in vod_tiles:
                try:
                    self._cover_pip_dpi(tile)
                except Exception:
                    continue
            # 几何/浮层同步保留原来的覆盖面（所有格子），但合并成对真实
            # tile 各排一次，不给 video / badge / overlay 等子控件各排一次。
            try:
                all_tiles = self._host_tiles()
            except Exception:
                all_tiles = []
            seen: set[int] = set()
            for tile in vod_tiles + all_tiles:
                if tile is None or id(tile) in seen:
                    continue
                seen.add(id(tile))
                try:
                    if not sip_deleted(tile):
                        self._queue_tile_sync(tile)
                except Exception:
                    continue
        finally:
            self._dpi_handling = False
            # show()/raise_()/重排触发的后续事件（含异步投递的）全部落进冷却。
            self._dpi_ignore_until = time.monotonic() + 0.35


    def _pip_drag_active(self) -> bool:
        """浮窗是否正在被鼠标拖动（左键按住移动）。"""
        try:
            if QApplication.mouseButtons() & Qt.LeftButton:
                return True
        except Exception:
            pass
        return False


    def _hide_all_dpi_covers(self) -> None:
        """拖动开始 / 进行中：立刻撤掉所有黑遮罩并停掉续期定时器。

        旧逻辑每来一次 DPI / Move 事件就 ``timer.start(900)`` 续期，拖动时
        事件连续不断，遮罩因此一直不 hide，纯黑 QWidget 盖在视频上就是
        「拖动浮窗时视频黑屏」。
        """
        for cover in list(self._dpi_covers.values()):
            try:
                if not sip_deleted(cover):
                    cover.hide()
            except Exception:
                pass
        for timer in list(self._dpi_cover_timers.values()):
            try:
                if not sip_deleted(timer):
                    timer.stop()
            except Exception:
                pass
        self._dpi_cover_deadline.clear()


    def _hide_dpi_cover(self, tile) -> None:
        cover = self._dpi_covers.get(id(tile))
        if cover is None:
            return
        try:
            if sip_deleted(cover):
                return
            cover.hide()
        except Exception:
            pass

    def _raise_dpi_cover(self, tile, cover) -> None:
        try:
            if self._shutting_down or sip_deleted(cover) or cover.isHidden():
                return
            if self._dpi_covers.get(id(tile)) is not cover:
                return
            cover.raise_()
        except Exception:
            pass

    def _cover_pip_dpi(self, tile) -> None:
        """跨分辨率屏的白屏重载期间盖一层纯黑（只盖画中画里的点播格）。"""
        if tile is None or self._shutting_down:
            return
        if self._pip_drag_active():
            self._hide_dpi_cover(tile)
            return
        try:
            in_pip = self._tile_in_pip_window(tile) or (
                self._tile_in_pip(tile)
                and tile.window() is not self._host_window())
        except Exception:
            return
        if not in_pip:
            return
        try:
            cover = self._dpi_covers.get(id(tile))
            if cover is None or sip_deleted(cover):
                cover = QWidget(tile)
                cover.setObjectName("watchlaterDpiCover")
                cover.setAttribute(Qt.WA_NativeWindow, True)
                cover.setStyleSheet(
                    "QWidget#watchlaterDpiCover { background: #000000; }")
                cover.setFocusPolicy(Qt.NoFocus)
                cover.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                cover.hide()
                self._dpi_covers[id(tile)] = cover
            video = getattr(tile, "video", None)
            rect = tile.rect()
            if video is not None and not sip_deleted(video):
                try:
                    box = video.geometry()
                    if video.parentWidget() is tile:
                        rect = QRect(box)
                    else:
                        rect = QRect(video.mapTo(tile, QPoint(0, 0)), box.size())
                except Exception:
                    rect = tile.rect()
            cover.setGeometry(rect)
            cover.show()
            cover.raise_()
            # 原生视频窗口重绘时可能重新盖到遮罩之上，前 600ms 分几次抬起。
            now = time.monotonic()
            if now >= self._dpi_cover_deadline.get(id(tile), 0.0) - 0.3:
                self._dpi_cover_deadline[id(tile)] = now + 0.9
                for delay in (60, 180, 360, 600):
                    QTimer.singleShot(
                        delay,
                        lambda c=cover, t=tile: self._raise_dpi_cover(t, c),
                    )
            timer = self._dpi_cover_timers.get(id(tile))
            if timer is None or sip_deleted(timer):
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(lambda t=tile: self._hide_dpi_cover(t))
                self._dpi_cover_timers[id(tile)] = timer
            timer.start(900)   # 重复事件自动续期，直到这一轮重载结束
        except Exception:
            return

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
            binding = self._bindings.get(bvid)
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
        """空格兜底链路：无论鼠标在哪，全屏 / 画中画 / 墙面都只做暂停播放。"""
        if self._shutting_down:
            return
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return  # 输入框里的空格是打字，不是暂停
        if not self._claim_key("space"):
            return
        tile = self._active_vod_tile()
        binding = self._binding_for_tile(tile)
        if binding is None or binding.state not in ("playing", "paused"):
            return
        self._toggle_pause(tile, binding)

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
            existing = self._bindings.get(str(getattr(item, "bvid", "") or ""))
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
        if self._shutting_down or tile is None or item is None:
            return
        bvid = str(getattr(item, "bvid", "") or "")
        if not bvid:
            return
        window = self._host_window()
        if not callable(getattr(window, "_play_on", None)):
            self._notify_warning(
                "当前宿主版本不支持内嵌播放，请用浏览器打开。"
            )
            return
        cell = self._resolve_cell_from_tile(tile)
        if cell is None:
            self._notify_warning("这个格子没有注册稍后再看控件，无法内嵌播放。")
            return
        # 同一视频只在一个格子播：换格起播 = 把旧格子还原。
        old = self._bindings.get(bvid)
        if old is not None:
            if old.tile is tile:
                # 同格重复拖入同一视频 = 重播：直接重新取流，不先把
                # 旧直播起起来再停（避免无谓的直播取流与画面闪烁）。
                old.item = item
                self._replay_binding(old)
                return
            # 同一个正在播放的视频再次拖到另一个格子 = 移动画面，保留解码器。
            if old.state in ("playing", "paused"):
                from .tile_swap import swap_tiles_preserving_players
                if swap_tiles_preserving_players(window, old.tile, tile):
                    self._sync_tile_widgets(old.tile)
                    self._sync_tile_widgets(tile)
                    return
                self._notify_warning("当前布局暂不能无中断换格，视频保持原位。")
                return
            self._release_binding(old, restart=True)
        # 目标格上已有的别的点播：先静默替换（不中间起直播）。
        for existing in list(self._bindings.values()):
            if existing.tile is tile:
                self._release_binding(existing, restart=False)
        room = getattr(tile, "room", None) or {}
        prev_room = None
        if str(room.get("room_id") or "") and not room.get("watchlater_bvid"):
            prev_room = dict(room)
        binding = _VodBinding(item, cell, tile)
        from .preferences import read_preferences
        binding.resume_paused = not read_preferences(self.context)['auto_play']
        binding.part = 0          # v1.0 需求：：新片从第一 P 开始
        start = self._resume_seconds(item)
        if resume is not None:
            binding.part = resume['part']
            binding.quality = resume['quality']
            binding.resume_paused = binding.resume_paused or resume['paused']
            start = resume['progress']
            binding.progress = start
        binding.prev_room = prev_room
        self._bindings[bvid] = binding
        if self.wall is not None:
            try:
                self.wall.bind(cell, item)
            except Exception:
                pass
        try:
            tile.set_status("正在取流…")
        except Exception:
            pass
        if prepared is not None:
            self._start_resolution(binding, start=start, prepared=prepared)
        else:
            self._start_resolution(binding, start=start)
        self._ensure_tick_timer()

    @staticmethod
    def _room_title(binding) -> str:
        """合成房间标题；多 P 时追加「P{n}」，画中画/标题浮标才分得清第几 P。"""
        title = str(getattr(binding.item, "title", "") or "")
        parts = int(getattr(binding.item, "parts", 1) or 1)
        if parts > 1:
            return "%s  P%d" % (title, int(binding.part) + 1)
        return title

    @staticmethod
    def _resume_seconds(item) -> float:
        # 单 P 才续播；多 P 不猜分P（与 v1.0 同一规则）。
        if bool(getattr(item, "is_multi_part", False)):
            return 0.0
        try:
            return max(0.0, float(getattr(item, "progress", 0) or 0))
        except (TypeError, ValueError):
            return 0.0

    def _cookie_snapshot(self) -> dict[str, str]:
        snapshot = None
        try:
            snapshot = self.context.credentials("bilibili")
        except Exception:
            snapshot = None
        values = getattr(snapshot, "values", None)
        if values is None and isinstance(snapshot, dict):
            values = snapshot
        cookies: dict[str, str] = {}
        try:
            if values is not None:
                cookies = {
                    str(key): str(value)
                    for key, value in dict(values).items()
                    if value is not None
                }
        except (TypeError, ValueError):
            cookies = {}
        if self._account_generation <= 0:
            cred_generation = _as_int(getattr(snapshot, "generation", 0))
            self._account_generation = cred_generation if cred_generation > 0 else 1
        return cookies

    # ------------------------------------------------------------ VOD workers
    def _start_resolution(self, binding, *, start: float,
                          qn: int | None = None, prepared=None) -> None:
        """起一次取流（首播/重试/重播/秒切回退续播/清晰度切换共用）。"""
        cookies = self._cookie_snapshot()
        generation = self._account_generation
        cell = binding.cell
        self._cancel_cell_requests(cell)
        from .workers import VodResolveRequest

        if qn is None:
            qn = int(binding.quality or 0)
        self._vod_seq += 1
        request_id = self._vod_seq
        binding.request_id = request_id
        previous_state = binding.state
        binding.resume_paused = binding.resume_paused or previous_state == "paused"
        # v1.0 任务 6：重播/重试/换片都是显式重启，解除 ended 锁存。
        binding.ended = False
        binding.state = "resolving"
        room = getattr(binding.tile, "room", None) or {}
        self._vod_context[request_id] = {
            "cell": cell,
            "tile": binding.tile,
            "item": binding.item,
            "bvid": binding.bvid,
            "generation": generation,
            "start": float(start),
            "qn": int(qn),
            "previous_state": previous_state,
            "expected_room": str(room.get("room_id") or ""),
        }
        self._cell_vod[id(cell)] = request_id
        if prepared is not None:
            try:
                stream, lease = prepared.activate()
                binding.pending_warm_lease = lease
                self._on_vod_completed(request_id, generation, stream)
                self._retire_vod(request_id)
                return
            except Exception as error:
                lease = getattr(binding, 'pending_warm_lease', None)
                if lease is not None:
                    lease.close()
                    binding.pending_warm_lease = None
                self._log('prefetch handoff fallback type=' + type(error).__name__)
        worker = VodResolveRequest(
            cookies, generation, request_id, binding.bvid,
            # 多 P：第 2 页起不再用列表里的 cid，交给 resolve 按 page 取。
            _as_int(getattr(binding.item, "cid", 0)) if binding.part <= 0 else 0,
            int(qn), hq=True, page=binding.part,
        )
        self._vod_requests.add(worker)
        worker.completed.connect(self._on_vod_completed)
        worker.failed.connect(self._on_vod_failed)
        try:
            worker.finished.connect(worker.deleteLater)
        except Exception:
            pass
        worker.finished.connect(
            lambda rid=request_id: self._retire_vod(rid), Qt.QueuedConnection
        )
        try:
            worker.start()
        except Exception as error:
            self._vod_requests.discard(worker)
            self._vod_context.pop(request_id, None)
            self._cell_vod.pop(id(cell), None)
            self._log("vod worker start failed type=" + type(error).__name__)
            self._show_card_error(binding, "无法启动取流，请用浏览器打开")
        # Danmaku starts after resolution supplies the actual part's cid.
        # Subtitles remain lazy and require an explicit click.

    def _start_danmaku_fetch(self, binding, cookies: dict) -> None:
        from .workers import DanmakuRequest

        self._dm_seq += 1
        request_id = self._dm_seq
        binding.dm_pending = True
        self._dm_context[request_id] = (binding, self._account_generation, binding.part)
        worker = DanmakuRequest(
            cookies, request_id, binding.bvid,
            binding.cid,
        )
        self._dm_requests.add(worker)
        worker.completed.connect(self._on_danmaku_completed)
        worker.failed.connect(self._on_danmaku_failed)
        try:
            worker.finished.connect(worker.deleteLater)
        except Exception:
            pass
        worker.finished.connect(
            lambda rid=request_id: self._retire_danmaku(rid), Qt.QueuedConnection
        )
        try:
            worker.start()
        except Exception:
            self._dm_requests.discard(worker)
            self._dm_context.pop(request_id, None)
            binding.dm_pending = False

    def _on_danmaku_completed(self, request_id, comments) -> None:
        ctx = self._dm_context.get(request_id)
        if ctx is None or self._shutting_down:
            return
        binding, generation, part = ctx
        if (self._bindings.get(binding.bvid) is not binding
                or generation != self._account_generation or part != binding.part):
            return
        binding.danmaku = list(comments or [])
        binding.dm_cursor = bisect.bisect_left(
            [entry[0] for entry in binding.danmaku], binding.progress
        )
        binding.dm_last_pos = binding.progress
        binding.dm_pending = False

    def _on_danmaku_failed(self, request_id, error) -> None:
        ctx = self._dm_context.get(request_id)
        if ctx is None or self._shutting_down:
            return
        binding, generation, part = ctx
        if (self._bindings.get(binding.bvid) is not binding
                or generation != self._account_generation or part != binding.part):
            return
        binding.dm_pending = False
        message = getattr(error, "message", None)
        self._log("danmaku fetch failed: " + str(message or type(error).__name__)[:160])

    def _retire_danmaku(self, request_id) -> None:
        self._dm_context.pop(request_id, None)
        for worker in tuple(self._dm_requests):
            if getattr(worker, "request_id", None) == request_id:
                self._dm_requests.discard(worker)
                break

    def _is_current_vod(self, request_id, generation, ctx) -> bool:
        if generation != self._account_generation:
            return False
        if ctx.get("generation") != generation:
            return False
        return self._cell_vod.get(id(ctx["cell"])) == request_id

    def _on_vod_completed(self, request_id, generation, stream) -> None:
        if self._shutting_down:
            return
        ctx = self._vod_context.get(request_id)
        if ctx is None or not self._is_current_vod(request_id, generation, ctx):
            return
        tile = ctx["tile"]
        if tile not in self._host_tiles():
            return  # 格子已被布局拆掉
        room = getattr(tile, "room", None) or {}
        if str(room.get("room_id") or "") != ctx.get("expected_room", ""):
            return  # 取流期间用户换了台/拖了直播进来：结果作废
        binding = self._bindings.get(ctx["bvid"])
        if binding is None or binding.request_id != request_id:
            return
        self._inject_playback(binding, stream, float(ctx.get("start", 0.0)))

    def _on_vod_failed(self, request_id, generation, error) -> None:
        if self._shutting_down:
            return
        ctx = self._vod_context.get(request_id)
        if ctx is None or not self._is_current_vod(request_id, generation, ctx):
            return
        binding = self._bindings.get(ctx["bvid"])
        if binding is None or binding.request_id != request_id:
            return
        message = getattr(error, "message", None)
        if not isinstance(message, str) or not message.strip():
            message = "取流失败，请用浏览器打开该视频"
        if ctx.get("previous_state") in ("playing", "paused") and self._host_player(binding.tile):
            binding.state = ctx["previous_state"]
            self._apply_tile_chrome(binding)
            self._sync_fullscreen_state(binding.tile)
            self._notify_warning("画质切换失败，保留原画质继续播放。")
        else:
            self._show_card_error(binding, message)

    def _retire_vod(self, request_id) -> None:
        self._vod_context.pop(request_id, None)
        for worker in tuple(self._vod_requests):
            if getattr(worker, "request_id", None) == request_id:
                self._vod_requests.discard(worker)
                break

    def _cancel_cell_requests(self, cell) -> None:
        request_id = self._cell_vod.pop(id(cell), None)
        if request_id is None or request_id not in self._vod_context:
            return
        for worker in tuple(self._vod_requests):
            if getattr(worker, "request_id", None) == request_id:
                try:
                    worker.cancel()
                except Exception:
                    pass

    def _cancel_all_requests(self) -> None:
        for worker in tuple(self._vod_requests) + tuple(self._dm_requests) \
                + tuple(self._sub_requests):
            try:
                worker.cancel()
            except Exception:
                pass

    def _drain_requests(self) -> None:
        self._cancel_all_requests()
        for worker in tuple(self._vod_requests) + tuple(self._dm_requests) \
                + tuple(self._sub_requests):
            try:
                if worker.isRunning():
                    worker.wait()
            except Exception:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass
        # 字幕 worker 交接后的临时文件：completed 信号可能在宿主退出前未被
        # 处理（_sub_context 已清），这里按 worker 记录的 srt_path 兜底删除。
        # 与 binding 释放时的清理幂等（重复 unlink 异常被吞）。
        sub_paths = [getattr(w, "srt_path", None) for w in tuple(self._sub_requests)]
        self._unlink_paths([p for p in sub_paths if p])
        self._vod_requests.clear()
        self._vod_context.clear()
        self._cell_vod.clear()
        self._dm_requests.clear()
        self._dm_context.clear()
        self._sub_requests.clear()
        self._sub_context.clear()

    # ----------------------------------------------------- playback injection
    def _inject_playback(self, binding, stream, start: float) -> None:
        from .tile_vod import is_direct_url, merge_headers

        tile = binding.tile
        url = getattr(stream, "url", "")
        if not is_direct_url(url):
            # 红线：网页 URL 绝不喂宿主播放器（ytdl=False 必错）。
            self._show_card_error(binding, "取流结果异常，请用浏览器打开")
            return
        old_lease = getattr(binding, 'warm_lease', None)
        if old_lease is not None:
            old_lease.close()
        binding.warm_lease = getattr(binding, 'pending_warm_lease', None)
        binding.pending_warm_lease = None
        self._stop_host_tile(tile)
        current = getattr(tile, "room", None) or {}
        danmaku_enabled = bool(current.get("video_danmaku_enabled", True)) \
            if current.get("watchlater_bvid") == binding.bvid else True
        synthetic = {
            "room_id": binding.bvid,      # 无冒号：start_tile 不会当平台房
            "live": False,                # 离线分支：宿主绝不联网取直播
            "live_known": True,
            "platform": "bilibili",
            "uname": str(getattr(binding.item, "owner_name", "") or ""),
            # v1.0 需求：/17：画中画/头像位读 room["face"]，缺了就只剩空白。
            "face": str(getattr(binding.item, "owner_face", "") or ""),
            "title": self._room_title(binding),
            "video_danmaku_enabled": danmaku_enabled,
            "watchlater_bvid": binding.bvid,  # 扫描标记（dict 拷贝会保留）
            "watchlater_part": int(binding.part),  # 多 P：当前第几 P（0 基）
        }
        try:
            tile.set_room(synthetic)
        except Exception as error:
            self._log("synthetic room failed type=" + type(error).__name__)
            self._release_binding(binding, restart=True)
            return
        # 需求：：左上角「未开播」药丸由 room["live"] 驱动（playback_badges
        # .py:81-84），点播期间先藏一次；悬停再想显示时由格子过滤器拦
        # Show 事件（实例级，不 patch 宿主类）。
        badge = getattr(tile, "stream_badge", None)
        if badge is not None:
            try:
                badge.hide()
            except RuntimeError:
                pass
        if not self._play_on_host(tile, url.strip(), merge_headers(
                getattr(stream, "headers", None)), start_paused=binding.resume_paused):
            self._release_binding(binding, restart=True)
            self._notify_warning("播放器启动失败，请用浏览器打开该视频。")
            return
        player = self._host_player(tile)
        if player is None:
            self._release_binding(binding, restart=True)
            return
        # VOD：EOF 不是冻屏（宿主 _check 会把 eof 当 stall 计分）。
        try:
            player.freeze_watch = False
        except Exception:
            pass
        try:
            player._set("keep-open", "always")  # 结束停在最后一帧
        except Exception:
            pass
        binding.pending_seek = 0.0
        if start > 0:
            text = ("%d" % int(start)) if start == int(start) else ("%g" % start)
            try:
                player._set("start", text)  # 复播时内核已建，选项生效
            except Exception:
                pass
            # 首播时宿主内核在 _play_on 内部才 bind，上面的写入是空操作；
            # 节拍器在文件加载后校验位置，丢了才补 seek（v1.0 实际运行环境证据）。
            binding.pending_seek = start
        binding.pending_audio = ""
        binding.audio_attempts = 0
        if getattr(stream, "is_dash", False):
            audio_url = getattr(stream, "audio_url", "") or ""
            if isinstance(audio_url, str) and audio_url.strip():
                binding.pending_audio = audio_url.strip()
        binding.stream = stream
        binding.cid = _as_int(getattr(stream, "cid", 0)) or binding.cid
        if binding.danmaku is None and not binding.dm_pending:
            self._start_danmaku_fetch(binding, self._cookie_snapshot())
        binding.subtitle_loaded = False
        # 需求：：接口给的可选档位进绑定，右键「画质」菜单用。
        try:
            from .vod_stream import qualities_from_stream

            binding.qualities = qualities_from_stream(stream)
            actual = _as_int(getattr(stream, "qn", 0))
            if actual > 0:
                binding.quality = actual
        except Exception:
            binding.qualities = []
        self._apply_tile_chrome(binding)
        binding.state = "paused" if binding.resume_paused else "playing"
        binding.progress = start
        # v1.0 任务 3：左上角立刻换成「N/总数」，不留一个空洞。
        self._sync_position_badge(binding)
        self._sync_subtitle_control(tile)
        # stateChanged 连接卫生：同一播放器重注入（清晰度切换/续播）先断
        # 旧 lambda，避免重复回调累积。播放器实例被重建（拖格回退 release
        # 后新建）时不对旧对象断连——libpyside 会刷 Failed to disconnect
        # 警告；旧实例销毁时连接自然消失。
        slot = lambda state, b=binding: self._on_host_player_state(b, state)
        try:
            if (binding._state_slot is not None
                    and binding._state_player is player):
                try:
                    player.stateChanged.disconnect(binding._state_slot)
                except Exception:
                    pass
            player.stateChanged.connect(slot)
            binding._state_slot = slot
            binding._state_player = player
        except Exception:
            pass
        # 需求：：预览雪碧图 provider（懒加载，首次悬停才抓取）。
        from .preferences import read_preferences
        if binding.preview is None and read_preferences(self.context)['video_preview']:
            try:
                from .vod_preview import VodPreviewProvider

                binding.preview = VodPreviewProvider(
                    binding.bvid, binding.cid,
                )
            except Exception as error:
                binding.preview = None
                self._log("preview create failed type=" + type(error).__name__)
        self._ensure_tile_widgets(binding)
        if binding.card is not None:
            binding.card.hide()
        if binding.strip is not None:
            binding.strip.reset()
        entry = self._bar_progress.get(id(tile))
        if entry is not None and binding.preview is not None:
            try:
                entry["widget"].set_preview_provider(binding.preview)
            except Exception:
                pass
        if entry is not None:
            try:
                entry["widget"].reset()
            except Exception:
                pass
        self._ensure_space_shortcut()
        self._sync_fullscreen_state(tile)
        self._ensure_tick_timer()

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
        """只观察宿主播放器状态，绝不回写宿主（宿主自己的槽已接好）。"""
        if self._shutting_down or binding.state not in ("playing", "paused"):
            return
        if state != "error":
            return
        player = self._host_player(binding.tile)
        try:
            if player is not None and player._prop("eof-reached", False):
                return  # EOF 由节拍器走 ended，不算错误
        except Exception:
            pass
        binding.state = "error"
        self._show_card_error(binding, "播放中断或失败，可重试")

    def _show_card_error(self, binding, message: str) -> None:
        binding.state = "error"
        self._ensure_tile_widgets(binding)
        if binding.card is not None:
            try:
                binding.card.show_error(message)
            except RuntimeError:
                pass
        if binding.strip is not None:
            try:
                binding.strip.hide()
            except RuntimeError:
                pass

    @staticmethod
    def _mpv_command(player, *args) -> bool:
        """v1.0 头号修复：宿主 TilePlayer **没有 command()**（player.py 只有
        _prop/_set/play/stop/release/set_paused），裸 mpv 在 ``player.player``。

        v1.0 直接 ``player.command(...)`` 抛 AttributeError 被 except 吞掉，
        导致进度条拖不动（需求：）、拖格续播定位丢失（需求：）、DASH 挂
        音轨静默失败。顺序：真宿主裸 mpv → 测试替身的 command → seek 的
        属性写兜底（time-pos）。
        """
        raw = getattr(player, "player", None)
        command = getattr(raw, "command", None)
        if callable(command):
            try:
                command(*args)
                return True
            except Exception:
                return False
        command = getattr(player, "command", None)
        if callable(command):
            try:
                command(*args)
                return True
            except Exception:
                return False
        if args and args[0] == "seek" and len(args) >= 2:
            try:
                player._set("time-pos", float(args[1]))
                return True
            except Exception:
                return False
        return False

    def _seek_binding_relative(self, binding, delta: float) -> None:
        """需求 2：B 站原版左右键 —— 以播放器真实 time-pos 为基准 ±5 秒。

        旧实现算 ``binding.progress + 5`` 再发绝对 seek：全屏重排会释放/重建
        播放器，节拍器也可能滞后，``progress`` 停在旧值时连按几次都跳到同一
        个位置，肉眼就是「全屏下左右键没反应」。mpv 的相对 seek 以真实播放
        位置为准，按住连发每次都实打实走 5 秒；节拍器下一拍会从 time-pos
        把 ``binding.progress`` 修正回真实值。
        """
        if binding is None or self._shutting_down:
            return
        delta = float(delta)
        if not delta:
            return
        player = self._player_for_binding(binding)
        if player is None:
            return
        current = float(getattr(binding, "progress", 0.0) or 0.0)
        duration = float(getattr(binding, "duration", 0.0) or 0.0)
        local_target = max(0.0, current + delta)
        if duration > 0:
            local_target = min(local_target, duration)
        raw = getattr(player, "player", None)
        command = getattr(raw, "command", None)
        if not callable(command):
            command = getattr(player, "command", None)
        if callable(command):
            # relative+exact = 精确 ±5 秒（不吸附关键帧），与 B 站网页一致；
            # 少数 mpv 构建不认组合 flag 时退回普通 relative。
            if (self._mpv_command(player, "seek", delta, "relative+exact")
                    or self._mpv_command(player, "seek", delta, "relative")):
                binding.progress = local_target
                return
        # 没有裸 mpv command 的替身播放器：_mpv_command 的 _set 兜底只认
        # 绝对秒数，所以按本地进度做一次绝对跳。
        self._seek_binding(binding, local_target)


    def _seek_binding(self, binding, seconds: float) -> None:
        player = self._player_for_binding(binding)
        if player is None:
            return
        target = max(0.0, float(seconds))
        if self._mpv_command(player, "seek", target, "absolute"):
            # 立即回写进度：节拍器下一拍前滑杆/时间显示不闪回。
            binding.progress = target

    def _retry_binding(self, binding) -> None:
        if self._shutting_down or binding.state == "resolving":
            return
        if binding.card is not None:
            binding.card.hide()
        try:
            binding.tile.set_status("正在取流…")
        except Exception:
            pass
        self._start_resolution(binding, start=binding.progress)

    def _replay_binding(self, binding) -> None:
        if self._shutting_down or binding.state == "resolving":
            return
        if binding.card is not None:
            binding.card.hide()
        binding.dm_cursor = 0
        binding.dm_last_pos = 0.0
        self._start_resolution(binding, start=0.0)

    def _open_binding_browser(self, binding) -> None:
        self._open_url(_video_page_url(binding.item))

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
        if self._next_prefetch is not None:
            self._next_prefetch.clear()
        for binding in self._bindings.values():
            self._sync_next_button(binding)
            self._sync_position_badge(binding)

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
        bound = [binding.tile for binding in self._bindings.values() if binding.tile in tiles]
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
        parts = int(getattr(binding.item, "parts", 1) or 1)
        return binding.part + 1 < parts or self._next_item_for(binding.bvid) is not None

    def _sync_next_button(self, binding) -> None:
        """结束卡上的「下一个视频」可用性跟随列表变化（删除/刷新后仍准）。"""
        card = binding.card
        if card is None:
            return
        try:
            card.set_next_available(self._has_next(binding))
        except (AttributeError, RuntimeError):
            pass

    def _next_binding(self, binding) -> None:
        """播放列表里的下一条；到末尾就明确提示，绝不悄悄重播当前这条。

        复用 :meth:`_start_vod_for_tile` 的既有换片路径（同格不同 bvid 会
        先 ``_release_binding(restart=False)`` 静默替换），不新建播放器路径。
        """
        if self._shutting_down or binding is None:
            return
        parts = int(getattr(binding.item, "parts", 1) or 1)
        if binding.part + 1 < parts:
            # v1.0 需求：：同一个多 P 视频先播下一 P，播完所有 P 再进下一条。
            prepared = self._take_prefetched(binding, binding.item, binding.part + 1, int(binding.quality or 0))
            self._cancel_binding_media(binding)
            if binding.preview is not None:
                binding.preview.release()
                binding.preview = None
            self._unlink_paths([path for _label, path in binding.subtitles])
            binding.subtitles = []
            binding.subtitle_loaded = binding.subtitle_on = False
            binding.danmaku = None
            binding.cid = 0
            binding.part += 1
            from .preferences import read_preferences
            binding.resume_paused = not read_preferences(self.context)['auto_play']
            binding.dm_cursor = 0
            binding.dm_last_pos = 0.0
            if prepared is not None:
                self._start_resolution(binding, start=0.0, prepared=prepared)
            else:
                self._start_resolution(binding, start=0.0)
            return
        item = self._next_item_for(binding.bvid)
        if item is None:
            self._notify_warning("已经是稍后再看列表里的最后一个视频了。")
            self._sync_next_button(binding)
            return
        tile = binding.tile
        if tile is None or tile not in self._host_tiles():
            self._notify_warning("这个格子已经不在了，无法播放下一个视频。")
            return
        prepared = self._take_prefetched(binding, item, 0, 0)
        if prepared is not None:
            self._start_vod_for_tile(tile, item, prepared=prepared)
        else:
            self._start_vod_for_tile(tile, item)

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
        for binding in list(self._bindings.values()):
            if binding.tile is tile or binding.cell is cell:
                self._release_binding(binding, restart=True)

    def _release_binding(self, binding, *, restart: bool) -> None:
        """结束一个点播绑定：停播放器、还原房间、清理控件。

        只用于合成房间**还在格子上**的路径（还原按钮/账号切换/卸载/
        起播替换）。格子已被宿主换房的清理走 :meth:`_detach_binding`。
        """
        tile = binding.tile
        alive = tile in self._host_tiles()
        if alive:
            room = getattr(tile, "room", None) or {}
            owns_room = room.get("watchlater_bvid") == binding.bvid
            if not owns_room:
                self._detach_binding(binding)
                return
            self._stop_host_tile(tile)
            self._restore_tile_chrome(binding, quality=True)
            prev = binding.prev_room
            try:
                tile.set_room(prev if prev and prev.get("room_id") else None)
            except Exception:
                pass
            if (restart and not self._shutting_down and prev
                    and prev.get("room_id") and prev.get("live")):
                self._start_host_tile(tile)
        self._detach_binding(binding)

    def _cancel_binding_media(self, binding) -> None:
        """Invalidate a binding's subtitle/danmaku deliveries before replacing it."""
        if self._next_prefetch is not None:
            self._next_prefetch.drop(id(binding))
        for name in ('warm_lease', 'pending_warm_lease'):
            lease = getattr(binding, name, None)
            if lease is not None:
                lease.close()
                setattr(binding, name, None)
        for contexts, workers in ((self._dm_context, self._dm_requests),
                                  (self._sub_context, self._sub_requests)):
            for request_id, ctx in tuple(contexts.items()):
                if ctx[0] is binding:
                    contexts.pop(request_id, None)
                    for worker in tuple(workers):
                        if worker.request_id == request_id:
                            worker.cancel()
        binding.dm_pending = binding._sub_pending = False

    def _detach_binding(self, binding) -> None:
        """只收走插件自己的控件与状态，绝不碰格子当前的房间/播放器。

        合成房间从格子上消失（用户关闭这一路、或把别的直播拖进来替换）
        时，格子现在归宿主：停掉新播放器或把旧房间 set_room 回去，都会
        吃掉用户刚拖入的直播。这里只做插件侧清理。
        """
        if self._bindings.get(binding.bvid) is binding:
            self._bindings.pop(binding.bvid, None)
        self._restore_tile_chrome(binding, quality=False)
        self._vod_press.pop(id(binding.tile), None)
        self._cancel_binding_media(binding)
        if binding.request_id:
            self._vod_context.pop(binding.request_id, None)
        if binding.strip is not None and binding.preview is not None:
            try:
                binding.strip.set_preview_provider(None)
            except Exception:
                pass
        preview = binding.preview
        binding.preview = None
        if preview is not None:
            try:
                preview.release()
            except RuntimeError:
                pass
            except Exception as error:
                self._log("preview release failed type=" + type(error).__name__)
        if binding._state_slot is not None:
            player = self._host_player(binding.tile)
            if player is not None and player is binding._state_player:
                try:
                    player.stateChanged.disconnect(binding._state_slot)
                except Exception:
                    pass
            binding._state_slot = None
            binding._state_player = None
        for widget in (binding.strip, binding.card, binding.line, binding.badge):
            if widget is not None:
                try:
                    widget.hide()
                    widget.deleteLater()
                except RuntimeError:
                    pass
        self._restore_order_pill(binding, getattr(binding.tile, "stream_badge", None))
        binding.strip = None
        binding.card = None
        binding.line = None
        binding.badge = None
        # v1.0 需求：：清理字幕临时文件 + 藏字幕按钮。
        self._unlink_paths([path for _label, path in binding.subtitles])
        binding.subtitles = []
        binding.subtitle_loaded = False
        binding.subtitle_on = False
        self._sync_subtitle_control(binding.tile)
        timer = self._click_timers.pop(id(binding.tile), None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        entry = self._bar_progress.get(id(binding.tile))
        if entry is not None:
            try:
                entry["widget"].set_preview_provider(None)
                if entry.get("visible"):
                    entry["visible"] = False
                    entry["widget"].hide()
                    entry["ctx"].invalidate_layout()
            except Exception:
                pass
        if self.wall is not None and binding.cell is not None:
            try:
                self.wall.bind(binding.cell, None)
            except Exception:
                pass
        if not self._bindings:
            if self._tick_timer is not None:
                self._tick_timer.stop()
            self._sync_pip_filter()

    # ------------------------------------------------------------------ ticker
    def _ensure_tick_timer(self) -> None:
        if self._shutting_down:
            return
        if self._tick_timer is None:
            # 宿主 api.Plugin 是普通 Python 类、不是 QObject，不能当 parent
            # （实际运行环境 QTimer(self) 会 TypeError）。无 parent，卸载时显式释放。
            self._tick_timer = QTimer()
            self._tick_timer.setInterval(300)
            self._tick_timer.timeout.connect(self._tick)
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    @measure('playback_tick')
    def _tick(self) -> None:
        if self._shutting_down:
            return
        if not self._bindings:
            if self._tick_timer is not None:
                self._tick_timer.stop()
            return
        self._sync_pip_filter()
        tiles = self._host_tiles()
        for binding in list(self._bindings.values()):
            try:
                self._tick_binding(binding, tiles)
            except Exception as error:
                self._log("tick failed type=" + type(error).__name__)

    def _hide_pause_overlay(self, tile) -> None:
        """v1.0 任务 5：结束态不留「已暂停」浮层。

        宿主 ``Tile.set_paused(True)``（widgets.py）会
        ``pause_overlay.start()``，结束卡底下就叠着「已暂停」。这里只停
        浮层，保留 ``tile.paused`` 状态本身 —— 宿主 watchdog
        （player.py ``if self.paused: return``）与重连判定都还靠它安静。
        """
        overlay = getattr(tile, "pause_overlay", None)
        if overlay is None:
            return
        try:
            overlay.stop()
        except Exception:
            pass
        try:
            overlay.hide()
        except RuntimeError:
            pass

    def _cancel_host_retries(self, tile) -> None:
        """v1.0 任务 6：取消宿主已排队的「画面静止/断流」重连。

        宿主的冻结恢复（``playback_recovery.picture_frozen``，宿主
        playback_recovery.py）会直接 ``start_tile`` 重新起播 —— 那一步
        就是状态被打回 ``connecting``（用户看到的「连接中」）。它在
        ``tile.paused`` 时本会早退，但全屏进出重排可能把 paused 复位，所以
        进入 ended 时主动把已排队的刷新清掉，避免结束后又被拉起来连一次。
        """
        window = self._host_window()
        recovery = getattr(window, "playback_recovery", None)
        if recovery is None:
            return
        for name in ("clear_freeze", "cancel_retry"):
            method = getattr(recovery, name, None)
            if not callable(method):
                continue
            try:
                method(tile)
            except Exception:
                pass

    def _enter_ended(self, binding, tile, player) -> None:
        """v1.0 任务 5/6：进入结束态只发生一次，之后这条绑定彻底停住。

        * 任务 5：不上「已暂停」浮层，结束卡是唯一可见 UI。
        * 任务 6：全屏 EOF 之后反复「连接中」的两条入口都要堵住 ——
         ① 插件自己的续播分支（全屏重排释放播放器 → ``player is None``
          → ``_start_resolution``）；② 宿主的冻结恢复
          （``start_tile`` → 状态打回 ``connecting``）。前者靠
          :meth:`_tick_binding` 的 ended 早退，后者靠
          :meth:`_cancel_host_retries`。这里先清掉所有待续播状态。
        """
        if binding.state == 'ended':
            return
        binding.state = "ended"
        binding.ended = True
        binding.pending_seek = 0.0
        binding.pending_audio = ""
        binding.audio_attempts = 0
        binding.resume_paused = False
        try:
            tile.set_paused(True)
            player.set_paused(True)
        except Exception:
            pass
        self._cancel_host_retries(tile)
        self._hide_pause_overlay(tile)
        if binding.strip is not None:
            try:
                binding.strip.hide()
            except RuntimeError:
                binding.strip = None
        if binding.card is not None:
            binding.card.sync_geometry(tile)
            binding.card.show_ended(self._has_next(binding))
        self._sync_position_badge(binding)
        self._sync_subtitle_control(tile)
        from .preferences import read_preferences
        if read_preferences(self.context)['autoplay'] and self._has_next(binding):
            QTimer.singleShot(0, lambda: self._auto_advance(binding))

    def _auto_advance(self, binding):
        from .preferences import read_preferences
        if (not self._shutting_down and self._bindings.get(binding.bvid) is binding
                and binding.state == 'ended' and read_preferences(self.context)['autoplay']
                and self._has_next(binding)):
            self._next_binding(binding)

    def _maybe_prefetch(self, binding):
        from .preferences import read_preferences
        enabled = read_preferences(self.context)['prefetch_next']
        if not enabled:
            if self._next_prefetch is not None:
                self._next_prefetch.drop(id(binding))
            return
        if binding.state != 'playing' or binding.duration <= 0 or binding.duration - binding.progress > 30:
            return
        parts = int(getattr(binding.item, 'parts', 1) or 1)
        if binding.part + 1 < parts:
            item, part, quality = binding.item, binding.part + 1, int(binding.quality or 0)
        else:
            item, part, quality = self._next_item_for(binding.bvid), 0, 0
            if item is None or item.bvid in self._bindings:
                return
        cookies = self._cookie_snapshot()
        if self._next_prefetch is None:
            from .next_prefetch import NextPrefetch
            self._next_prefetch = NextPrefetch(QApplication.instance())
        key = (self._account_generation, item.bvid, part, quality)
        self._next_prefetch.prepare(id(binding), key, cookies, int(getattr(item, 'cid', 0)) if part == 0 else 0)

    def _take_prefetched(self, binding, item, part, quality):
        from .preferences import read_preferences
        if self._next_prefetch is None or not read_preferences(self.context)['prefetch_next']:
            return None
        return self._next_prefetch.take(id(binding), (self._account_generation, item.bvid, part, quality))

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
        if self._next_prefetch is not None and not values['prefetch_next']:
            self._next_prefetch.clear()
        for binding in self._bindings.values():
            self._configure_preview(binding, values['video_preview'])

    def _configure_preview(self, binding, enabled):
        if not enabled and binding.preview is not None:
            from .card_preview import retire_preview
            retire_preview(binding.preview)
            binding.preview = None
        elif enabled and binding.preview is None:
            from .vod_preview import VodPreviewProvider
            binding.preview = VodPreviewProvider(binding.bvid, binding.cid)
        if binding.strip is not None:
            binding.strip.set_preview_provider(binding.preview)
        entry = self._bar_progress.get(id(binding.tile))
        if entry is not None:
            entry['widget'].set_preview_provider(binding.preview)

    def _tick_binding(self, binding, tiles) -> None:
        bvid = binding.bvid
        tile = next(
            (t for t in tiles
             if isinstance(getattr(t, "room", None), dict)
             and t.room.get("watchlater_bvid") == bvid),
            None,
        )
        if tile is None:
            if binding.state == "resolving":
                if binding.request_id not in self._vod_context:
                    self._detach_binding(binding)  # 请求已死，绑定成孤儿
                return
            # 格子被关/被直播拖入替换：合成房间已不在任何格子上，
            # 格子当前的房间/播放器归宿主，只 detach 插件控件。
            self._detach_binding(binding)
            return
        if tile is not binding.tile:
            # 秒切回退路径：合成房间搬到了别的格子 → 控件跟着搬。
            old_cell = binding.cell
            binding.tile = tile
            for widget in (binding.strip, binding.card, binding.line,
                           binding.badge):
                if widget is not None:
                    try:
                        widget.setParent(tile)
                    except RuntimeError:
                        pass
            new_cell = self._resolve_cell_from_tile(tile)
            if new_cell is None and self.wall is not None:
                # 需求：加固：宿主链暂时查不到（布局过渡期）就按
                # _cell_tiles 反查，绝不让 binding.cell 留在旧格子上——
                # 否则旧格子下一次起播的 _cancel_cell_requests 会误杀
                # 本格子的续播请求。
                try:
                    for candidate in list(self.wall._cells.values()):
                        if self._cell_tiles.get(id(candidate)) is tile:
                            new_cell = candidate
                            break
                except Exception:
                    new_cell = None
            if new_cell is not None:
                binding.cell = new_cell
                if self.wall is not None:
                    try:
                        if old_cell is not None and old_cell is not new_cell:
                            self.wall.bind(old_cell, None)
                        self.wall.bind(new_cell, binding.item)
                    except Exception:
                        pass
            self._sync_tile_widgets(tile)
        # 需求：：宿主 rewire/relayout 可能重新显示录制按钮，发现可见就再藏。
        try:
            rec = getattr(tile, "recording_button", None)
            if rec is not None and rec.isVisible():
                tile.set_recording_available(False)
        except Exception:
            pass
        if binding.state == "ended":
            # v1.0 任务 6：结束态是终止态，只做最小几何同步。
            # 这里**绝不**再走 _start_resolution / _play_on / 续播分支：
            # 全屏重排释放播放器后，``player is None`` 那条恢复分支正是
            # 「反复连接中」的入口（宿主 _play_on 会把状态打回 connecting）。
            self._hide_pause_overlay(tile)
            self._sync_fullscreen_state(tile)
            self._sync_next_button(binding)
            card = binding.card
            if card is not None:
                try:
                    if card.isVisible():
                        card.sync_geometry(tile)
                except RuntimeError:
                    binding.card = None
            return
        player = self._host_player(tile)
        if player is None:
            # 布局隐藏确实会释放播放器，可见后才按进度重建。正常拖格
            # 已在 Drop 阶段交换 Tile 对象，不会进入这个恢复分支。
            if binding.state in ("playing", "paused"):
                try:
                    visible = tile.isVisible()
                except Exception:
                    visible = False
                if visible:
                    binding.resume_paused = binding.state == "paused"
                    try:
                        tile.set_status("正在续播…")
                    except Exception:
                        pass
                    # 隐藏很久的签名URL可能过期；真正被释放后重新解析。
                    self._start_resolution(binding, start=binding.progress)
            return
        prop = getattr(player, "_prop", None)
        if not callable(prop):
            return
        duration = float(prop("duration", 0) or 0)
        position = prop("time-pos", None)
        position = float(position) if position is not None else None
        loaded = duration > 0 or position is not None
        if loaded:
            self._apply_subtitle(binding, player)
        if loaded and binding.resume_paused:
            binding.resume_paused = False
            try:
                tile.set_paused(True)
                player.set_paused(True)
                binding.state = "paused"
            except Exception:
                pass
        # DASH 音轨只能在文件加载后挂（v1.0 实际运行环境证据）。
        if binding.pending_audio and loaded:
            if self._mpv_command(
                player, "audio-add", binding.pending_audio, "select"
            ):
                binding.pending_audio = ""
            else:
                binding.audio_attempts += 1
                if binding.audio_attempts >= 3:
                    binding.pending_audio = ""  # 有画无声胜过没画
        # start 选项丢失校验（首播内核未 bind 时是空写）。
        if binding.pending_seek > 0 and loaded:
            target = binding.pending_seek
            binding.pending_seek = 0.0
            if position is None or position < target - 1.0:
                self._mpv_command(player, "seek", target, "absolute")
        # EOF：走宿主暂停 API 让 watchdog 安静，再上结束卡。
        if binding.state == "playing" and bool(prop("eof-reached", False)):
            self._enter_ended(binding, tile, player)
        # 宿主暂停按钮/快捷键改了 tile.paused → 同步绑定状态。
        try:
            paused = bool(tile.paused)
        except Exception:
            paused = False
        if binding.state == "playing" and paused:
            binding.state = "paused"
        elif binding.state == "paused" and not paused:
            binding.state = "playing"
        if position is not None:
            binding.progress = position
        if duration > 0:
            binding.duration = duration
        self._maybe_prefetch(binding)
        self._sync_fullscreen_state(tile)
        self._sync_subtitle_control(tile)
        # v1.0 任务 3/8：列表刷新/删除后序号与总数要跟着变。
        self._sync_position_badge(binding)
        entry = self._bar_progress.get(id(tile))
        if entry is not None and entry.get("visible"):
            widget = entry.get("widget")
            try:
                if widget is not None:
                    widget.set_duration(duration)
                    widget.set_position(position or 0.0)
            except RuntimeError:
                self._bar_progress.pop(id(tile), None)
        line = binding.line
        if line is not None:
            try:
                if line.isVisible():
                    if duration > 0:
                        line.set_ratio(max(0.0, min(
                            1.0, (position or 0.0) / duration)))
                    line.sync_geometry(tile)
            except RuntimeError:
                binding.line = None
        card = binding.card
        if card is not None:
            try:
                if card.isVisible():
                    card.sync_geometry(tile)
            except RuntimeError:
                binding.card = None
        self._feed_danmaku(binding, tile, position, paused)

    def _feed_danmaku(self, binding, tile, position, paused: bool) -> None:
        """按播放进度把点播弹幕喂给宿主原生引擎（需求 5）。

        渲染/轨道/速度/开关全用宿主 ``tile.video_danmaku``；引擎满轨道或
        弹幕开关关闭时 add_event 自己拒绝，这里像直播一样丢弃不积压。
        """
        if not binding.danmaku or paused or binding.state != "playing":
            return
        if position is None:
            return
        engine = getattr(tile, "video_danmaku", None)
        add_event = getattr(engine, "add_event", None)
        if not callable(add_event):
            return
        if position < binding.dm_last_pos - 2.0:
            # 后退 seek：清屏并从新位置重新对表。
            try:
                engine.clear_comments()
            except Exception:
                pass
            binding.dm_cursor = bisect.bisect_left(
                [entry[0] for entry in binding.danmaku], position
            )
        horizon = position + 0.25
        comments = binding.danmaku
        cursor = binding.dm_cursor
        while cursor < len(comments) and comments[cursor][0] <= horizon:
            moment, mode, color, text = comments[cursor]
            cursor += 1
            if moment < binding.dm_last_pos - 0.5:
                continue  # 前进 seek 跳过的历史弹幕不补喷
            try:
                add_event({"kind": "danmaku", "text": text, "color": color})
            except Exception:
                pass
        binding.dm_cursor = cursor
        binding.dm_last_pos = position

    # ------------------------------------------------------------- lifecycle
    def on_event(self, event, payload) -> None:
        if event == api.EVENT_STARTED:
            QTimer.singleShot(0, self.playback_session.restore)
        elif event == api.EVENT_ACCOUNT_CHANGED:
            if (
                isinstance(payload, dict)
                and payload.get("platform") == "bilibili"
            ):
                self._account_generation += 1
                self.playback_session.clear()
                if self._next_prefetch is not None:
                    self._next_prefetch.clear()
                self._cancel_all_requests()
                for binding in list(self._bindings.values()):
                    self._release_binding(binding, restart=True)
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
        if self._next_prefetch is not None:
            self._next_prefetch.clear()
            self._next_prefetch.deleteLater()
            self._next_prefetch = None
        if self._dpi_flush_timer is not None:
            self._dpi_flush_timer.stop()
            self._dpi_flush_timer.deleteLater()
            self._dpi_flush_timer = None
        self._dpi_pending = False
        if self._tick_timer is not None:
            self._tick_timer.stop()
            try:
                self._tick_timer.deleteLater()
            except RuntimeError:
                pass
            self._tick_timer = None
        self._drain_requests()
        # 关窗顺序（app.py）：EVENT_CLOSING → plugins.unload →
        # config save。这里把合成房间还原成原直播房/空格，配置永不留点播房。
        for binding in list(self._bindings.values()):
            self._release_binding(binding, restart=False)
        self._bindings.clear()
        self._sync_pip_filter()
        self._vod_press.clear()
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
        for timer in list(self._click_timers.values()):
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self._click_timers.clear()
        if self._space_shortcut is not None:
            try:
                self._space_shortcut.setEnabled(False)
                self._space_shortcut.deleteLater()
            except RuntimeError:
                pass
            self._space_shortcut = None
        if self._danmaku_shortcut is not None:
            try:
                self._danmaku_shortcut.setEnabled(False)
                self._danmaku_shortcut.deleteLater()
            except RuntimeError:
                pass
            self._danmaku_shortcut = None
        for attr in ("_seek_left", "_seek_right"):
            sc = getattr(self, attr, None)
            if sc is not None:
                try:
                    sc.setEnabled(False)
                    sc.deleteLater()
                except RuntimeError:
                    pass
            setattr(self, attr, None)
        self._seek_host = None
        self._danmaku_host = None
        if self._key_filter is not None:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeEventFilter(self._key_filter)
                except Exception:
                    pass
            self._key_filter = None
        self._key_down.clear()
        self._key_dedup.clear()
        self._fullscreen_active = False
        for timer in self._dpi_cover_timers.values():
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self._dpi_cover_timers.clear()
        self._dpi_cover_deadline.clear()
        for cover in self._dpi_covers.values():
            try:
                cover.hide()
                cover.deleteLater()
            except RuntimeError:
                pass
        self._dpi_covers.clear()
        if self._face_loader is not None:
            self._face_loader.shutdown()
            self._face_loader.deleteLater()
            self._face_loader = None
        self._face_pixmaps.clear()
        self._space_host = None
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
