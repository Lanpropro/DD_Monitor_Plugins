"""Compact bottom-bar control for the Bilibili watch-later plugin (v1.0).

Playback is owned by the host's own tile player (direct VOD URL injected via
``window._play_on``); the plugin only adds a progress strip / status card in
the video area (see :mod:`tile_vod`).  The cell itself is a ZERO-WIDTH,
never-painted state carrier in the bottom bar: it occupies no pixels, but
stays technically ``visible`` to Qt so the host's ``set_room`` notification
chain keeps working (drag-swap detection relies on it).

A few widgets survive as zero-sized, never-shown compatibility stubs because
``WallController`` and the offline tests read their *state* (title text,
``remove_action.isEnabled()``, ``expanded`` ...).  They are deliberately kept
out of the visible layout, so from the host's point of view the control
occupies no space at all.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .models import WatchLaterItem, format_duration, video_url

STATE_LIVE = "live"
STATE_WATCHLATER = "watchlater"
STATE_EMPTY = "empty"
STATE_LOGIN = "login"
STATE_ERROR = "error"

# v1.0: the cell occupies zero pixels in the host bottom bar.  It must NOT be
# hide()n: TileControls.applicable()/set_room() skip hidden widgets, and the
# set_room notification chain is how drag-swap detection keeps working.
COMPACT_WIDTH = 0
# Legacy constant kept so external importers do not break.  v2 used it to lock
# the cell to 320px (P0-2); v3 never uses it to size the cell.
EXPANDED_WIDTH = 320


class _CoverStub(QWidget):
    """Zero-sized replacement for the v2 ``_CoverStrip``.

    No paintEvent, no QPixmap scaling: covers are shown by the overlay now, so
    the v2 cover-scaling/cache problem disappears with the removed panel.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(0, 0)
        self.hide()

    def set_item(self, item, pixmap) -> None:  # call-shape parity only
        return None


class WatchLaterCell(QWidget):
    """Bottom-bar tile control: one compact button + a context menu.

    ``expanded`` still tracks whether an item is bound because the host's
    ``interaction_active()`` reads it, but expanding no longer grows the bar.
    """

    play_requested = Signal(object)
    copy_requested = Signal(object)
    remove_requested = Signal(object)
    restore_requested = Signal()
    pick_requested = Signal(object)
    #: v3: a real signal; WallController forwards it to the list refresh.
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item: WatchLaterItem | None = None
        self._room: dict[str, Any] = {}
        self._expanded = False
        self._shutting_down = False
        self._cover_provider = None
        self._remove_reason = None
        self._destructive_ready = True
        self._status_kind = ""

        self.setObjectName("watchlaterCell")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # v1.0: no visible widget in the bottom bar at all.  The button object
        # survives (hidden) only so older 离线测试/slots referencing it still work.
        self.toggle_button = QToolButton(self)
        self.toggle_button.setText("稍后")
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.toggle_button.setToolTip("稍后再看：展开")
        self.toggle_button.setAccessibleName("稍后再看")
        self.toggle_button.setCursor(Qt.PointingHandCursor)
        self.toggle_button.clicked.connect(self._on_toggle_clicked)
        self.toggle_button.hide()
        root.addWidget(self.toggle_button, 0)

        # --- zero-sized compatibility stubs, never part of the visible bar ---
        self.panel = QWidget(self)
        self.panel.setFixedSize(0, 0)
        self.panel.hide()
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self.stack = QStackedWidget(self.panel)
        panel_layout.addWidget(self.stack)

        self.item_page = QWidget(self.stack)
        item_layout = QVBoxLayout(self.item_page)
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(0)
        self.cover = _CoverStub(self.item_page)
        item_layout.addWidget(self.cover)
        self.title_label = QLabel(self.item_page)
        item_layout.addWidget(self.title_label)
        self.meta_label = QLabel(self.item_page)
        item_layout.addWidget(self.meta_label)
        self.progress_label = QLabel(self.item_page)
        item_layout.addWidget(self.progress_label)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(0)
        self.play_button = QPushButton("播放", self.item_page)
        self.play_button.hide()  # v3: never shown; kept as a hidden stub
        buttons.addWidget(self.play_button)
        self.more_button = QToolButton(self.item_page)
        self.more_button.hide()
        self.menu = QMenu(self.more_button)
        self.copy_action = self.menu.addAction("复制链接")
        self.remove_action = self.menu.addAction("从稍后再看移除…")
        self.restore_action = self.menu.addAction("还原为直播态")
        self.more_button.setMenu(self.menu)
        buttons.addWidget(self.more_button)
        item_layout.addLayout(buttons)
        self.copy_action.triggered.connect(self._on_copy)
        self.remove_action.triggered.connect(self._on_remove)
        self.restore_action.triggered.connect(self._on_restore)
        self.play_button.clicked.connect(self._on_play)
        self.stack.addWidget(self.item_page)

        self.empty_page = QWidget(self.stack)
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(0)
        self.empty_label = QLabel(self.empty_page)
        empty_layout.addWidget(self.empty_label)
        self.pick_button = QPushButton("选择视频…", self.empty_page)
        self.pick_button.hide()
        self.pick_button.clicked.connect(self._on_pick)
        empty_layout.addWidget(self.pick_button)
        self.stack.addWidget(self.empty_page)

        self.status_page = QWidget(self.stack)
        status_layout = QVBoxLayout(self.status_page)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        self.status_label = QLabel(self.status_page)
        status_layout.addWidget(self.status_label)
        self.stack.addWidget(self.status_page)

        self._apply_state()

    # ------------------------------------------------------------------ state
    @property
    def item(self) -> WatchLaterItem | None:
        return self._item

    @property
    def expanded(self) -> bool:
        return self._expanded

    @property
    def state(self) -> str:
        if not self._expanded:
            return STATE_LIVE
        if self._item is not None:
            return STATE_WATCHLATER
        if self._status_kind == "login":
            return STATE_LOGIN
        if self._status_kind == "error":
            return STATE_ERROR
        return STATE_EMPTY

    def bind_item(self, item: WatchLaterItem | None) -> None:
        self._item = item
        self._status_kind = ""
        if item is not None:
            self.title_label.setText(item.title)
            self.title_label.setToolTip(item.title)
            self.setAccessibleName(item.title)
            meta = [item.owner_name]
            if item.duration > 0:
                meta.append(format_duration(item.duration))
            if item.is_multi_part:
                meta.append(f"共 {item.parts} P")
            self.meta_label.setText(" · ".join(meta))
            progress = item.progress_text
            self.progress_label.setText(progress)
            self.progress_label.setVisible(bool(progress))
            self._refresh_cover()
        else:
            self.title_label.clear()
            self.meta_label.clear()
            self.progress_label.clear()
            self.progress_label.hide()
            self.cover.set_item(None, None)
        self._apply_state()

    def show_status(self, kind: str, message: str) -> None:
        self._status_kind = kind
        self.status_label.setText(message)
        self._item = None
        self._apply_state()

    def set_expanded(self, expanded: bool) -> None:
        if self._shutting_down:
            return
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._apply_state()

    def set_cover_provider(self, provider) -> None:
        self._cover_provider = provider
        self._refresh_cover()

    def set_remove_reason(self, reason) -> None:
        self._remove_reason = reason
        self._update_actions()

    def set_destructive_ready(self, ready: bool) -> None:
        self._destructive_ready = bool(ready)
        self._update_actions()

    def set_room(self, room: dict[str, Any] | None) -> None:
        # v2 P1 regression: every set_room() call cleared the binding, so a
        # room switch / tile swap / fullscreen toggle silently unbound the
        # cell.  WallController now owns bindings keyed by id(cell) and clears
        # them only on an account change.  Here we refresh the context only
        # when the room id actually changes, and never touch the binding.
        incoming = dict(room or {})
        if incoming.get("room_id") == self._room.get("room_id"):
            return
        self._room = incoming

    def _apply_state(self) -> None:
        expanded = self._expanded
        # v1.0: zero width in every state; the cell never paints and never
        # grows the bar.  Visibility flag stays True so the host's
        # applicable()/set_room() chain keeps working (see COMPACT_WIDTH).
        self.setFixedWidth(COMPACT_WIDTH)
        # panel/stack are zero-sized stubs.  Toggling their visibility keeps
        # the legacy isHidden() contract without painting or resizing.
        self.panel.setVisible(expanded)
        self.stack.setVisible(expanded)
        if self._item is not None:
            self.stack.setCurrentWidget(self.item_page)
        elif self._status_kind in ("login", "error"):
            self.stack.setCurrentWidget(self.status_page)
        else:
            self.stack.setCurrentWidget(self.empty_page)
        self._update_actions()

    def _update_actions(self) -> None:
        has_item = self._item is not None
        self.play_button.setEnabled(has_item)
        self.copy_action.setEnabled(has_item)
        self.restore_action.setEnabled(has_item)
        reason = ""
        if self._item is not None and self._remove_reason is not None:
            try:
                reason = self._remove_reason(self._item) or ""
            except Exception:
                reason = ""
        self.remove_action.setEnabled(
            has_item and self._destructive_ready and not reason
        )
        self.remove_action.setToolTip(reason or "从稍后再看移除…")

    def _refresh_cover(self) -> None:
        # v3: covers live in the overlay; the cell keeps no pixmap and never
        # scales one.  The v2 cover-cache problem is gone with the panel.
        return None

    # ----------------------------------------------------------------- events
    def _on_toggle_clicked(self) -> None:
        self.set_expanded(not self._expanded)

    def _on_play(self) -> None:
        if self._item is not None:
            self.play_requested.emit(self._item)

    def _on_copy(self) -> None:
        if self._item is not None:
            self.copy_requested.emit(self._item)

    def _on_remove(self) -> None:
        if self._item is not None:
            self.remove_requested.emit(self._item)

    def _on_restore(self) -> None:
        self.restore_requested.emit()

    def _on_pick(self) -> None:
        self.pick_requested.emit(self._room)

    def _on_refresh(self) -> None:
        # v3: the refresh menu item now has its own signal instead of wrongly
        # opening the picker (v2 emitted pick_requested here).
        self.refresh_requested.emit()

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        sub = menu.addMenu("稍后再看")
        sub.setToolTipsVisible(True)

        pick = sub.addAction("选择稍后再看视频…")
        pick.setEnabled(not self._shutting_down)
        pick.triggered.connect(self._on_pick)

        # v1.0（需求 8）：「打开稍后再看列表」「刷新稍后再看」从右键菜单
        # 删除 —— 列表/刷新都在弹窗窗口里，右键只负责选片。

        sub.addSeparator()
        remove = sub.addAction("从稍后再看移除本条")
        remove.setEnabled(self._item is not None and self._destructive_ready)
        remove.triggered.connect(self._on_remove)

        menu.addSeparator()
        restore = menu.addAction("还原为直播态")
        restore.setEnabled(self._expanded and self._item is not None)
        restore.triggered.connect(self._on_restore)
        return menu

    def _show_menu(self, position) -> None:
        menu = self._build_menu()
        menu.exec(self.mapToGlobal(position))
        menu.deleteLater()

    # ------------------------------------------------------------- lifecycle
    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._item = None
        self._cover_provider = None
        self._remove_reason = None
        self._room = {}
        try:
            self.panel.hide()
        except RuntimeError:
            pass


def open_in_browser(url: str) -> bool:
    """Open an https URL in the system browser; never anything else."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    return bool(QDesktopServices.openUrl(QUrl(url)))


def copy_to_clipboard(text: str) -> bool:
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        return False
    clipboard.setText(text)
    return True


def item_url(item: WatchLaterItem) -> str:
    return video_url(item)
