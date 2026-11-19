"""Wall (tile) menu integration for the watch-later plugin.

Host capability note (see 内部说明 section 3.1): ``Plugin.tile_actions`` returns
flat ``(label, callback)`` pairs, and ``ddm/widgets.py:build_menu`` renders each
one with ``menu.addAction``.  There is no public submenu extension point, and
patching the host menu is forbidden.

So the two-level menu is built on the plugin's *own* widget: ``WatchLaterCell``
sets ``Qt.CustomContextMenu`` and pops its own ``QMenu`` containing a
「稍后再看」 submenu.  The flat ``tile_actions`` entry stays as a fallback for
narrow tiles where the host hides the control entirely.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from .models import WatchLaterItem, video_url

MENU_LABEL = "稍后再看…"


class WallController(QObject):
    """Owns the per-tile bindings and the shared menu actions.

    One instance per plugin load.  It never touches host private objects; the
    only host-facing surface is ``Plugin.tile_actions`` and the tile control
    factory.
    """

    #: emitted when the user asks for the picker for a given tile room
    pick_requested = Signal(object)
    #: emitted when the user asks to open the full list page
    open_list_requested = Signal()
    #: emitted when the user asks for a list refresh
    refresh_requested = Signal()
    #: emitted when the user asks to remove one item
    remove_requested = Signal(object)
    #: emitted when a tile should go back to the live state; carries the cell
    restore_requested = Signal(object)
    #: emitted when a bound item should play in its own tile; (cell, item)
    play_requested = Signal(object, object)
    #: like ``pick_requested`` but keeps the originating cell; (cell, room)
    pick_from_cell = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cells: dict[int, Any] = {}
        self._bindings: dict[int, WatchLaterItem] = {}
        self._items: list[WatchLaterItem] = []
        self._has_session = True
        self._busy = False
        self._shutting_down = False
        self._cover_provider: Callable[[str], Any] | None = None
        # Tile passed to the flat host menu entry; the plugin probes it for the
        # matching cell because a flat callback has no sender widget.
        self._flat_tile: Any = None

    # ------------------------------------------------------------- registry
    def register_cell(self, cell) -> None:
        if self._shutting_down:
            return
        self._cells[id(cell)] = cell
        try:
            cell.play_requested.connect(self._on_play)
            cell.copy_requested.connect(self._on_copy)
            cell.remove_requested.connect(self.remove_requested)
            cell.restore_requested.connect(self._on_restore)
            cell.pick_requested.connect(self._on_pick)
            # v3: the cell now emits a real refresh signal; forward it so the
            # plugin can refresh the list.  ``getattr`` keeps older cells
            # (without the signal) working instead of raising.
            refresh = getattr(cell, "refresh_requested", None)
            if refresh is not None:
                try:
                    refresh.connect(
                        lambda c=cell: self.refresh_requested.emit()
                    )
                except Exception:
                    pass
            if self._cover_provider is not None:
                cell.set_cover_provider(self._cover_provider)
            cell.set_remove_reason(self._remove_reason)
            cell.set_destructive_ready(self._can_destroy())
        except (RuntimeError, TypeError):
            pass

    def forget_cell(self, cell) -> None:
        self._cells.pop(id(cell), None)
        self._bindings.pop(id(cell), None)

    def set_cover_provider(self, provider) -> None:
        self._cover_provider = provider
        for cell in list(self._cells.values()):
            try:
                cell.set_cover_provider(provider)
            except RuntimeError:
                pass

    def set_items(self, items: list[WatchLaterItem]) -> None:
        """Refresh the shared list; re-sync any tile whose item disappeared."""
        self._items = list(items)
        by_key = {item.key: item for item in self._items if item.key}
        for cell_id, bound in list(self._bindings.items()):
            cell = self._cells.get(cell_id)
            if cell is None:
                continue
            fresh = by_key.get(bound.key)
            if fresh is None:
                self._bindings.pop(cell_id, None)
                try:
                    cell.bind_item(None)
                except RuntimeError:
                    pass
            elif fresh is not bound:
                self._bindings[cell_id] = fresh
                try:
                    cell.bind_item(fresh)
                except RuntimeError:
                    pass

    def set_session_state(self, *, has_session: bool, busy: bool = False) -> None:
        self._has_session = bool(has_session)
        self._busy = bool(busy)
        for cell in list(self._cells.values()):
            try:
                cell.set_destructive_ready(self._can_destroy())
            except RuntimeError:
                pass

    def clear_bindings(self) -> None:
        self._bindings.clear()
        for cell in list(self._cells.values()):
            try:
                cell.bind_item(None)
            except RuntimeError:
                pass

    # ---------------------------------------------------------------- state
    def _can_destroy(self) -> bool:
        return (
            self._has_session
            and not self._busy
            and not self._shutting_down
        )

    def _remove_reason(self, item: WatchLaterItem) -> str:
        if self._shutting_down:
            return "插件正在关闭"
        if not self._has_session:
            return "请先登录 B 站"
        if self._busy:
            return "正在执行其他操作，请稍候"
        if item.aid <= 0:
            return "该条目缺少 aid，请到网页版处理"
        return ""

    def bound_item(self, cell) -> WatchLaterItem | None:
        return self._bindings.get(id(cell))

    def bind(self, cell, item: WatchLaterItem | None) -> None:
        if item is None:
            self._bindings.pop(id(cell), None)
        else:
            self._bindings[id(cell)] = item
        try:
            cell.bind_item(item)
            cell.set_expanded(item is not None)
        except RuntimeError:
            pass

    # --------------------------------------------------------------- actions
    def _on_play(self, item) -> None:
        # v3: playback resolves a direct stream in the GUI plugin; a webpage
        # URL is no longer opened in the browser.  The signal carries the exact
        # cell so the plugin never has to look a cell up by room-id string.
        cell = self.sender()
        for candidate in list(self._cells.values()):
            if candidate is cell:
                self.play_requested.emit(candidate, item)
                return

    def _on_copy(self, item) -> None:
        # Keep the cell module's ``item_url`` / ``copy_to_clipboard`` helpers:
        # they remain the v3 contract for copy and for the browser fallback
        # URL.  ``open_in_browser`` is deliberately NOT imported any more.
        # A local equivalent covers hosts where those helpers were removed.
        try:
            from .watchlater_cell import copy_to_clipboard, item_url

            url = item_url(item)
            if url:
                copy_to_clipboard(url)
                return
        except Exception:
            pass
        url = video_url(item)
        if not url:
            return
        try:
            clipboard = QGuiApplication.clipboard()
        except Exception:
            return
        if clipboard is not None:
            clipboard.setText(url)

    def _on_restore(self) -> None:
        cell = self.sender()
        target = None
        for candidate in list(self._cells.values()):
            if candidate is cell:
                target = candidate
                break
        if target is None:
            return
        self._bindings.pop(id(target), None)
        try:
            target.bind_item(None)
            target.set_expanded(False)
        except RuntimeError:
            pass
        self.restore_requested.emit(target)

    def _on_pick(self, room) -> None:
        if room is None:
            self.open_list_requested.emit()
            return
        cell = self.sender()
        for candidate in list(self._cells.values()):
            if candidate is cell:
                # Keep the cell reference all the way to the plugin: no
                # room-id string reverse lookup, so an empty/unknown id can
                # never bind the wrong tile.
                self.pick_from_cell.emit(candidate, room)
                return
        # Flat host menu callback has no sender cell; use the legacy signal and
        # let the plugin probe the tile, with an explicit notify on failure.
        self.pick_requested.emit(room)

    # ------------------------------------------------------------ menu entry
    def tile_actions(self, tile) -> list[tuple[str, Callable[[], None]]]:
        """Flat fallback entry for the host's own tile menu.

        The host renders these as plain items, so this stays a single entry that
        opens the picker.  It is the only host-facing menu surface used.
        """
        if self._shutting_down:
            return []
        room = getattr(tile, "room", None)
        # Remember the tile object: the flat callback has no sender cell, so
        # the plugin probes this object for the matching registered cell.
        self._flat_tile = tile
        label = MENU_LABEL
        return [(label, lambda: self.pick_requested.emit(dict(room or {})))]

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        for cell in list(self._cells.values()):
            try:
                cell.shutdown()
            except RuntimeError:
                pass
        self._cells.clear()
        self._bindings.clear()
        self._items = []
