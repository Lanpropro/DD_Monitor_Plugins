"""Host tile controls, geometry, drag/drop and chrome; media binding state stays external."""
from __future__ import annotations
import time
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QMenu, QToolButton, QWidget
from ddm import plugins as api
from .playback_input import _SCREEN_CHANGE

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



class TilePresentation:
    def __init__(self, owner):
        self.owner = owner
        self._cell_tiles: dict[int, object] = {}
        self._tile_filter: _TileEventFilter | None = None
        self._watched_tiles: set[int] = set()
        self._bar_progress: dict[int, dict] = {}
        self._face_loader = None
        self._face_pixmaps = {}
        self._subtitle_controls: dict[int, dict] = {}
        self._overlay_keys: dict[int, tuple] = {}
        self._quality_connected: dict[int, tuple] = {}
        self._geometry_pending: set[int] = set()

    def disconnect_tiles(self):
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

    def dispose_faces(self):
        if self._face_loader is not None:
            self._face_loader.shutdown()
            self._face_loader.deleteLater()
            self._face_loader = None
        self._face_pixmaps.clear()

    def make_tile_control(self, tile_context):
        from .watchlater_cell import WatchLaterCell

        plugin = self.owner
        window = None
        if self.owner.context is not None:
            try:
                window = getattr(self.owner.context, "window", None)
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

    def resolve_cell_from_tile(self, tile):
        if tile is None or self.owner.wall is None:
            return None
        try:
            cells = list(self.owner.wall._cells.values())
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

    def watch_tile(self, tile) -> None:
        if self.owner._shutting_down or id(tile) in self._watched_tiles:
            return
        if self._tile_filter is None:
            self._tile_filter = _TileEventFilter(self.owner)
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
                slot = lambda room, qn, t=tile: self.on_tile_quality_changed(t, room, qn)
                quality_signal.connect(slot)
                self._quality_connected[id(tile)] = (tile, slot)
            except Exception:
                pass

    def make_progress_control(self, tile_context):
        """全屏底栏进度条的官方 TileControl 载体（需求：/8）。

        控件挂在 tile.bottom 布局里：底栏 3 秒 idle 自动隐藏（下滑+淡出）
        时它跟着一起隐；鼠标唤醒底栏时一起显。非全屏/无点播时隐藏，
        applicable() 把它排除，不占底栏宽度。
        """
        from .tile_vod import VodBarProgress

        plugin = self.owner
        tile = _resolve_tile(tile_context, self.owner._host_window())

        class ProgressControl(api.TileControl):
            def __init__(self) -> None:
                widget = VodBarProgress()
                widget.ensurePolished()
                super().__init__(
                    widget, minimum_width=max(plugin.BAR_PROGRESS_MIN_WIDTH,
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

    def seek_tile_vod(self, tile, seconds: float) -> None:
        binding = self.owner._binding_for_tile(tile)
        if binding is not None:
            self.owner._seek_binding(binding, seconds)

    def make_subtitle_control(self, tile_context):
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

        plugin = self.owner
        tile = _resolve_tile(tile_context, self.owner._host_window())

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

    def sync_subtitle_control(self, tile) -> None:
        entry = self._subtitle_controls.get(id(tile))
        if entry is None:
            return
        binding = self.owner._binding_for_tile(tile)
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

    def build_empty_tile_menu(self, tile):
        """空格子右键菜单（需求 8：只留选片 + 宿主原有的切换为弹幕）。"""
        if self.owner._shutting_down or self.owner.wall is None:
            return None
        cell = self.resolve_cell_from_tile(tile)
        menu = QMenu(tile)
        pick = menu.addAction("选择稍后再看视频…")
        pick.setEnabled(cell is not None)
        if cell is not None:
            pick.triggered.connect(lambda: self.owner._on_pick_requested(cell))
        menu.addSeparator()
        pane_edit = getattr(tile, "paneEditRequested", None)
        if pane_edit is not None:
            switch = menu.addAction("切换为弹幕")
            switch.triggered.connect(lambda: pane_edit.emit())
        return menu

    def show_empty_tile_menu(self, tile, global_pos) -> bool:
        menu = self.build_empty_tile_menu(tile)
        if menu is None:
            return False
        menu.exec(global_pos)
        menu.deleteLater()
        return True

    def register_tile(self, cell, tile) -> None:
        self._cell_tiles[id(cell)] = tile

    def forget_cell(self, cell) -> None:
        self.owner._cancel_cell_requests(cell)
        tile = self._cell_tiles.pop(id(cell), None)
        if tile is not None:
            self.unwatch_tile(tile)

    def unwatch_tile(self, tile) -> None:
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
        self.owner.input.vod_press.pop(key, None)
        timer = self.owner.input.click_timers.pop(key, None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass

    def queue_tile_sync(self, tile) -> None:
        key = id(tile)
        if key in self._geometry_pending or self.owner._shutting_down:
            return
        self._geometry_pending.add(key)

        def sync():
            self._geometry_pending.discard(key)
            self.sync_tile_widgets(tile)

        QTimer.singleShot(0, sync)

    def drag_source_tile(self, event):
        """解析格子拖拽的源格（唯一标识）。

        宿主 ``Tile.mouseMoveEvent`` 用 ``QDrag(self)``，故 ``event.source()``
        就是被拖的源 Tile（或其子控件）——沿父链反查到 wall.tiles 中的 Tile，
        这是唯一可靠的身份。仅当 source() 不可用（跨应用拖拽/测试假事件）时
        才回退到 mime 里的 room_id 字符串，且要求非空并唯一匹配：空格("")与
        重复直播房的 room_id 不唯一，按字符串 ``next()`` 会命中错误源格导致
        换错位置，故宁可拒绝（交还宿主原路径）也不猜。
        """
        tiles = self.owner._host_tiles()
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

    def handle_tile_drag(self, target, event) -> bool:
        """只接管涉及点播的格子换位，避免宿主 offline fallback 销毁播放器。"""
        if target not in self.owner._host_tiles():
            return False
        source = self.drag_source_tile(event)
        if source is None or not (self.owner._binding_for_tile(source)
                                  or self.owner._binding_for_tile(target)):
            return False
        if event.type() == QEvent.Type.Drop:
            try:
                target.setProperty("dropActive", False)
                target.style().unpolish(target)
                target.style().polish(target)
                if source is not target:
                    from .tile_swap import swap_tiles_preserving_players
                    if not swap_tiles_preserving_players(self.owner._host_window(), source, target):
                        self.owner._notify_warning("当前布局暂不能无中断换格，请退出全屏后再试。")
                self.owner.input.vod_press.clear()
                for t in (source, target):
                    timer = self.owner.input.click_timers.pop(id(t), None)
                    if timer is not None:
                        timer.stop()
                        timer.deleteLater()
                    self.sync_tile_widgets(t)
            except Exception as error:
                self.owner._log("VOD tile swap failed type=" + type(error).__name__)
                # 不再转交宿主的销毁/重建回退，避免异常后发生二次交换。
                self.owner._notify_warning("换格未完成，播放保持原位。")
        event.acceptProposedAction()
        return True

    def sync_tile_widgets(self, tile) -> None:
        """格子 Resize/Move/Show/WinIdChange 后同步几何与全屏态（排队调用）。"""
        if self.owner._shutting_down:
            return
        for binding in self.owner.playback.bindings.values():
            if binding.tile is not tile:
                continue
            try:
                compact = bool(self.tile_in_pip(tile)) and (
                    tile.width() < 340 or tile.height() > tile.width() * 1.15)
                if binding.card is not None and hasattr(binding.card, "set_compact"):
                    binding.card.set_compact(compact)
                if binding.card is not None and binding.card.isVisible():
                    binding.card.sync_geometry(tile)
                if binding.line is not None and binding.line.isVisible():
                    binding.line.sync_geometry(tile)
            except RuntimeError:
                pass  # widget already deleted by Qt
        self.sync_fullscreen_state(tile)
        # v1.0 需求：：宿主刚按宽度重算过 compact_popup，这里再钉回 mini。
        for binding in self.owner.playback.bindings.values():
            if binding.tile is tile:
                self.force_volume_mini(tile)
                break
        # v1.0 需求：/14：重排/跨屏之后把原生浮层抬起来重画。
        self.repaint_overlays(tile)

    @staticmethod
    def force_volume_mini(tile) -> None:
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

    def repaint_overlays(self, tile) -> None:
        """v1.0 需求：/14：几何/DPI 变化后把浮层抬起来并重画。

        宿主收起/展开房间列表、窗口跨屏、侧栏改宽之后，格子会重排；视频是
        原生 HWND，插件这些原生子控件不会自动跟着重绘（表现为「部分 UI 显示
        错误」，多路小窗跨屏时甚至整窗白屏）。这里统一调宿主的
        ``raise_overlays()`` 并显式 ``update()`` 自己的控件。
        """
        # v1.0b：画中画板块退回 v1.0 —— 不做任何插件侧跨屏重绘。
        if self.tile_in_pip(tile):
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

    def tile_in_pip(self, tile) -> bool:
        """v1.0 需求：：格子是否处于多路小窗（PiP）形态。"""
        try:
            if bool(tile.property("pictureInPicture")):
                return True
        except Exception:
            pass
        window = self.owner._host_window()
        pip = getattr(window, "picture_in_picture", None)
        active = getattr(pip, "active", None)
        if callable(active):
            try:
                return bool(active())
            except Exception:
                return False
        return False

    def fit_vod_bottom_bar(self, tile) -> int:
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
            return self.owner.BAR_PROGRESS_MIN_WIDTH
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

    def sync_fullscreen_state(self, tile) -> None:
        """按 tile.property('fullscreen') / PiP 切换进度控件形态（需求：/8/15）。

        v1.0：**画面墙也把进度条放进底栏**（需求：），不再浮在画面上挡字幕；
        底栏放不下（窄格）或处于多路小窗时退回视频底边 3px 蓝线。全屏/墙面
        都由 :meth:`_fit_vod_bottom_bar` 把中间区让给进度条（需求：/2/3）。
        空格快捷键跟随全屏窗口。
        """
        if self.owner._shutting_down or tile is None:
            return
        try:
            fullscreen = bool(tile.property("fullscreen"))
        except Exception:
            fullscreen = False
        pip = self.tile_in_pip(tile)
        compact = fullscreen or pip
        binding = self.owner._binding_for_tile(tile)
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
            free = self.fit_vod_bottom_bar(tile)
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
        if fs_active != self.owner.input.fullscreen_active:
            self.owner.input.fullscreen_active = fs_active
            self.owner._bump_key_filter()
            if fs_active:
                try:
                    focus = QApplication.focusWidget()
                    if isinstance(focus, QToolButton) and tile.isAncestorOf(focus):
                        focus.clearFocus()
                except Exception:
                    pass
        host = self.owner._host_window()
        if (fullscreen and active) or (pip and active):
            try:
                host = tile.window() or host
            except Exception:
                pass
        self.owner._sync_shortcut_hosts(host)
        if binding is not None:
            # v1.0 任务 8：全屏进出立刻重排左上角位置徽章，不等下一拍。
            self.sync_position_badge(binding)

    def fill_pip_choice_avatars(self, dialog) -> None:
        """需求：：组合画中画选择框里，点播格的 UP 头像补上。

        宿主的 `PictureInPictureDialog` 只从**侧栏条目**（`sidebar.items()`）取
        头像，合成点播房不在侧栏 → `faces.get(room_id)` 是 None → 只剩字母占位。
        这里在对话框 Show 时按 `watchlater_bvid` 找到绑定，异步取 owner_face
        后回填到对应 RoomChoice 的 Avatar（只发 bytes，QPixmap 在 GUI 线程建）。
        """
        if dialog is None or self.owner._shutting_down:
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
            binding = self.owner.playback.bindings.get(bvid)
            url = str(getattr(getattr(binding, "item", None), "owner_face", "") or "")
            if self._face_loader is None:
                from .avatars import AvatarLoader

                self._face_loader = AvatarLoader()
                self._face_loader.ready.connect(self.on_face_ready)
            self._face_loader.get(bvid, url)

    def on_face_ready(self, bvid: str, data) -> None:
        """Decoded avatar delivered on the GUI thread."""
        if self.owner._shutting_down or data is None or data.isNull():
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

    def show_vod_tile_menu(self, tile, global_pos) -> bool:
        """点播格右键（需求：，已确认 4 项）：画质/视频流信息/声道/全屏。

        宿主 build_menu 无过滤钩子，插件在 ContextMenu 事件级拦截自建菜单
        （实例级 eventFilter，不 patch 宿主类）。动作全部复用宿主信号/方法。
        """
        binding = self.owner._binding_for_tile(tile)
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
                    self.switch_quality(b, q)
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

    def switch_quality(self, binding, qn: int) -> None:
        """清晰度切换（需求：）：按当前秒重新取流，注入后续播。"""
        if self.owner._shutting_down or binding.state == "resolving":
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
        self.owner._start_resolution(binding, start=position, qn=qn)

    def on_tile_quality_changed(self, tile, room, qn) -> None:
        """v1.0 需求：：格子内画质按钮 → 按选中 qn 重新取流续播。"""
        if self.owner._shutting_down:
            return
        binding = self.owner._binding_for_tile(tile)
        if binding is None:
            return
        try:
            qn = int(qn)
        except (TypeError, ValueError):
            return
        if qn <= 0 or qn == int(binding.quality):
            return
        self.switch_quality(binding, qn)

    def apply_tile_chrome(self, binding) -> None:
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
        self.force_volume_mini(tile)
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

    def restore_tile_chrome(self, binding, *, quality: bool) -> None:
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

    def ensure_tile_widgets(self, binding) -> None:
        from .tile_vod import VodStatusCard

        tile = binding.tile
        # v1.0 需求：：画面墙不再建浮在视频上的 seek 条 —— 进度条统一进
        # 底栏（见 _fit_vod_bottom_bar），避免压住画面底部的字幕。
        binding.strip = None
        if binding.card is None:
            try:
                card = VodStatusCard(tile)
                card.retry_requested.connect(lambda b=binding: self.owner._retry_binding(b))
                card.replay_requested.connect(lambda b=binding: self.owner._replay_binding(b))
                card.next_requested.connect(lambda b=binding: self.owner._next_binding(b))
                card.browser_requested.connect(lambda b=binding: self.owner._open_binding_browser(b))
                card.restore_requested.connect(lambda b=binding: self.owner._release_binding(b, restart=True))
                binding.card = card
            except Exception as error:
                self.owner._log("card create failed type=" + type(error).__name__)
        if binding.line is None:
            # 分级导入：进度线是 v1.0 新增控件，模块缺失时降级为无进度线，
            # 绝不连累 strip/card。
            try:
                from .tile_vod import VodProgressLine

                line = VodProgressLine(tile)
                line.hide()
                binding.line = line
            except Exception as error:
                self.owner._log("line create failed type=" + type(error).__name__)
        if binding.badge is None and self.owner.POSITION_BADGE_ENABLED:
            # v1.0 任务 3/8：只补左上角的位置徽章，失败就当没这功能。
            try:
                from .tile_vod import VodPositionBadge

                binding.badge = VodPositionBadge(tile)
            except Exception as error:
                self.owner._log("position badge create failed type=" + type(error).__name__)

    def sync_position_badge(self, binding) -> None:
        """左上角「N/总数」：v1.0b 起复用宿主自己的 BadgeSurface。

        用户反馈的那个「透出主窗口 / DD监控室CE 标题」的黑洞，本质是插件
        在原生视频 HWND 之上又建了一个原生子窗口（位置徽章），它的遮罩/
        分层区域是全透明的，于是把后面的窗口透了出来。修法不是调层级，
        而是**不再建独立窗口**：直接把宿主的 stream_badge 重画成排序药丸。
        BadgeSurface 自带逐像素 alpha 合成，显隐又由 _sync_stream_badge
        统一控制 —— 正好满足「和视频标题一起出现一起消失」。
        """
        if self.owner._shutting_down or binding is None:
            return
        tile = binding.tile
        if tile is None:
            return
        if not self.owner.POSITION_BADGE_ENABLED:
            self.sync_order_pill(binding)
            return
        badge = binding.badge
        if badge is None:
            return
        try:
            if badge.isVisible():
                badge.hide()
        except RuntimeError:
            binding.badge = None

    def sync_order_pill(self, binding) -> None:
        """把宿主左上角药丸改成排序药丸；PiP 与取流态退回 v1.0（不显示）。"""
        if self.owner._shutting_down or binding is None:
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
                binding._pos_text = self.owner._list_position_text(binding.bvid)
                binding._pos_refreshed = now
            text = getattr(binding, "_pos_text", "")
        if self.tile_in_pip(tile) or not text:
            self.restore_order_pill(binding, badge, hide=True)
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
    def restore_order_pill(binding, badge, hide: bool = False) -> None:
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
