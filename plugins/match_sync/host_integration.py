"""Host integration. State and Qt callback ownership stay on Viewer."""
from __future__ import annotations

import sys
import time
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QFrame, QLabel, QLineEdit, QPlainTextEdit, QTextEdit, QWidget
from PySide6.QtGui import QCursor, QKeySequence
from PySide6.QtCore import Qt
from ddm.widgets import ROOM_MIME
from ddm.dialogs import SHORTCUT_ACTIONS
from shiboken6 import isValid
from contextlib import nullcontext
from ddm import theme


def embed(viewer, content):
    viewer.embedded = True
    viewer.setParent(content, Qt.Widget)
    viewer.setGeometry(content.rect())
    content.installEventFilter(viewer)
    for widget in viewer.add_controls:
        widget.hide()
    viewer.notice.setText('')


def _shortcut_action(viewer, watched, event):
    if not viewer.isVisible() or not isinstance(watched, QWidget):
        return None
    if watched.window() not in (viewer.window(), viewer.fullscreen_dialog):
        return None
    if QApplication.activeModalWidget() not in (None, viewer, viewer.fullscreen_dialog):
        return None
    if isinstance(watched, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
        return None
    if isinstance(watched, QComboBox) and watched.isEditable():
        return None
    pressed = QKeySequence(event.keyCombination()).toString()
    shortcuts = getattr(viewer.context.window, "shortcuts", {})
    return next((action for action, _label, default in SHORTCUT_ACTIONS
                 if pressed and pressed == shortcuts.get(action, default)), None)


def _handle_shortcut(viewer, action):
    if action == "restore":
        if viewer.fullscreen_dialog is not None:
            viewer.fullscreen_dialog.close()
        return
    if action == "focus" and viewer.fullscreen_dialog is not None:
        viewer.fullscreen_dialog.close()
        return
    point = QCursor.pos()
    target = None
    if viewer.picture.isVisible() and viewer.picture.rect().contains(viewer.picture.mapFromGlobal(point)):
        target = viewer.rows.get(viewer.main.currentData())
    else:
        for key, (card, _name, _canvas, _state) in viewer.comparison_panel.cards.items():
            if card.isVisible() and card.rect().contains(card.mapFromGlobal(point)):
                target = viewer.rows.get(key)
                break
    if action == "solo":
        for row in viewer.rows.values():
            row.audible.setChecked(row is target)
    elif target is not None:
        if action == "focus":
            viewer._fullscreen_picture()
        elif action == "mute":
            target.audible.toggle()
        elif action == "video_danmaku":
            viewer.picture.danmaku_button.toggle()
        elif action == "audio_cycle":
            if not target.audible.isChecked():
                target.channel.setCurrentIndex(0)
                target.audible.setChecked(True)
            elif target.channel.currentIndex() == target.channel.count() - 1:
                target.audible.setChecked(False)
            else:
                target.channel.setCurrentIndex(target.channel.currentIndex() + 1)




def _switch_stage(viewer, stage):
    probe = getattr(viewer, 'switch_probe', None)
    return probe.span(stage) if probe is not None else nullcontext()


def showEvent(viewer, event, _base):
    QApplication.instance().installEventFilter(viewer)
    if viewer.embedded:
        suspend = getattr(viewer.context.window, "set_match_sync_playback", None)
        if callable(suspend):
            with viewer._switch_stage('suspend_host_playback'):
                suspend(True)
        viewer.host_audio_timer.start()
        viewer._suppress_host_audio()
    if viewer.embedded and not viewer.hidden_host_widgets:
        host = viewer.context.window
        for widget in (getattr(host, "wall", None), getattr(host, "empty_hint", None)):
            if widget is not None:
                viewer.hidden_host_widgets[widget] = not widget.isHidden()
                widget.installEventFilter(viewer)
                widget.hide()
    with viewer._switch_stage('show_controls'):
        viewer._sync_sidebar_marks()
        viewer.send_console.sync_height()
    viewer.console_content_height = None
    viewer.align_room_controls()
    _base(event)


def hideEvent(viewer, event, _base):
    QApplication.instance().removeEventFilter(viewer)
    _base(event)


def _sync_sidebar_marks(viewer):
    sidebar = getattr(viewer.context.window, "sidebar", None)
    if not viewer.embedded or sidebar is None or viewer.isHidden():
        return
    now = time.monotonic()
    state = (viewer.running, tuple((key, id(row.decoder)) for key, row in viewer.rows.items()),
             getattr(sidebar, "side", "left"), getattr(sidebar, "collapsed", False),
             len(getattr(sidebar, "_items", [])), theme.is_light())
    if state == getattr(viewer, "sidebar_marks_state", None) and now < viewer.sidebar_marks_due:
        return
    viewer.sidebar_marks_state, viewer.sidebar_marks_due = state, now + .25
    for row in viewer.rows.values():
        row.refresh_avatar()
        viewer.send_console.refresh_avatar(row.room_id)
    for item in getattr(sidebar, "_items", []):
        row = viewer.rows.get(str(item.room.get("room_id") or ""))
        active = viewer.running and row is not None and row.decoder is not None
        viewer._style_sidebar_item(item, active, bool(item.property('onWall')))
    viewer._sync_strip_marks(sidebar, True)


def _style_sidebar_item(viewer, item, active, hold):
    # Newer hosts convert QSS for continuous corners. Compare their source
    # recipe, otherwise each poll reapplies QSS that the host converts again.
    source_style = getattr(sys.modules.get('ddm.continuous_corners'), 'source_stylesheet', str)
    current = source_style(item.styleSheet())
    changed = bool(item.property('matchSync')) != active
    if changed:
        item.setProperty('matchSync', active)
    if active or hold:
        original = viewer.sidebar_item_styles.setdefault(item, current)
        style = original + """
                #NavItem[onWall="true"] { border: none; }
                #NavItem[matchSync="true"], #NavItem[matchSync="true"][onWall="true"] {
                    border: 1px solid #fb7299;
                }
            """
    elif item in viewer.sidebar_item_styles:
        style = viewer.sidebar_item_styles.pop(item)
    else:
        return
    if current != style:
        item.setStyleSheet(style)
    elif changed:
        item.style().unpolish(item)
        item.style().polish(item)
        item.update()


def _sync_strip_marks(viewer, sidebar, enabled):
    strip = getattr(sidebar, "_head_strip", None)
    for room_id, avatar in getattr(strip, "_avatars", {}).items():
        for mark in avatar.findChildren(QLabel, options=Qt.FindDirectChildrenOnly):
            if f"background: {theme.ACCENT};" in mark.styleSheet():
                if enabled:
                    mark.setProperty("matchSyncHidden", True)
                    mark.hide()
                elif mark.property("matchSyncHidden"):
                    mark.setProperty("matchSyncHidden", False)
                    mark.show()
        frame = avatar.findChild(QFrame, "MatchSyncStripMark")
        row = viewer.rows.get(room_id)
        active = enabled and viewer.running and row is not None and row.decoder is not None
        if active and frame is None:
            frame = QFrame(avatar)
            frame.setObjectName("MatchSyncStripMark")
            frame.setAttribute(Qt.WA_TransparentForMouseEvents)
            frame.setGeometry(avatar.rect())
            frame.setStyleSheet(f"background: transparent; border: 1px solid {theme.PINK};"
                               f" border-radius: {avatar.width() // 2}px;")
        if frame is not None:
            frame.setVisible(bool(active))
            frame.raise_()


def _restore_host_widgets(viewer):
    viewer.host_audio_timer.stop()
    viewer._release_host_audio()
    sidebar = getattr(viewer.context.window, "sidebar", None)
    if sidebar is not None:
        for item in tuple(viewer.sidebar_item_styles):
            if isValid(item):
                viewer._style_sidebar_item(item, False, False)
            else:
                viewer.sidebar_item_styles.pop(item)
        viewer._sync_strip_marks(sidebar, False)
    previous, viewer.hidden_host_widgets = viewer.hidden_host_widgets, {}
    for widget, visible in previous.items():
        widget.removeEventFilter(viewer)
        widget.setVisible(visible)
    resume = getattr(viewer.context.window, "set_match_sync_playback", None)
    if viewer.embedded and callable(resume):
        with viewer._switch_stage('resume_host_playback'):
            resume(False)


def _suppress_host_audio(viewer):
    players = getattr(viewer.context.window, "players", {})
    current = {}
    active = viewer.host_audio_timer.isActive() if viewer.embedded else viewer.audio.sink is not None
    if active:
        for tile, player in list(players.items()):
            if (viewer.embedded or str((tile.room or {}).get("room_id") or "") in viewer.rows) and not player._released:
                player.set_muted(True)
                current[player] = tile
    for player, tile in viewer.suppressed.items():
        if player not in current and not player._released:
            viewer._restore_host_audio(player, tile)
    viewer.suppressed = current


def _restore_host_audio(viewer, player, old_tile):
    players = getattr(viewer.context.window, "players", {})
    owner = next((tile for tile, value in players.items() if value is player), old_tile)
    player.set_muted(bool(owner.muted))


def _release_host_audio(viewer):
    for player, tile in viewer.suppressed.items():
        if not player._released:
            viewer._restore_host_audio(player, tile)
    viewer.suppressed.clear()


def dragEnterEvent(viewer, event):
    if event.mimeData().hasFormat(ROOM_MIME):
        event.acceptProposedAction()


def dropEvent(viewer, event):
    if not event.mimeData().hasFormat(ROOM_MIME):
        return
    room_id = bytes(event.mimeData().data(ROOM_MIME)).decode("utf-8", "ignore")
    if viewer._add_dragged(room_id):
        event.acceptProposedAction()
