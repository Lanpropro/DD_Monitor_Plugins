"""Source control. Session state remains on the Viewer facade."""
from __future__ import annotations

import time
from .room_widgets import COLORS, RoomRow
from .media import Decoder
from .room_input import parse_room

def dismiss_removal_notice(viewer):
    viewer.removal_timer.stop()
    viewer.removal_notice.hide()
    viewer.removed_room = None


def remove_room_with_undo(viewer, room_id):
    row = viewer.rows[room_id]
    saved = (room_id, row.preferences(), viewer.sources.get(room_id),
             list(viewer.rows).index(room_id), viewer.main.currentData() == room_id, viewer.running)
    label = row.label()
    viewer.remove_room(room_id)
    viewer.removed_room = saved
    viewer.removal_label.setText('已移除 ' + label)
    viewer.removal_notice.adjustSize()
    viewer.position_removal_notice()
    viewer.removal_notice.show()
    viewer.removal_notice.raise_()
    viewer.removal_timer.start()


def undo_remove_room(viewer):
    saved = viewer.removed_room
    viewer.dismiss_removal_notice()
    if saved is None:
        return
    room_id, preferences, source, index, main, running = saved
    if room_id in viewer.rows:
        return
    if source is not None:
        viewer.sources[room_id] = source
    viewer.add_room(room_id, preferences)
    keys = list(viewer.rows)
    if index < len(keys) - 1:
        viewer.reorder_room(room_id, keys[index], False)
    if main:
        viewer.select_main(room_id)
    if running and not viewer.running:
        viewer.toggle_running()


def _add_input(viewer):
    if viewer.add_room(viewer.input.text()):
        viewer.input.clear()


def add_room(viewer, text, preferences=None):
    try:
        room_id = parse_room(text, viewer.context.manager)
    except ValueError as error:
        viewer.notice.setText(str(error))
        return False
    if room_id in viewer.rows:
        viewer.notice.setText("这位主播已经加入")
        return False
    viewer._stop_recording()
    options = dict(viewer.saved_rooms.get(room_id, {}))
    options.update(preferences or {})
    used = {number for row in viewer.rows.values() for number in row.record_tracks}
    options.setdefault('record_tracks', next(([number] for number in range(1, 7) if number not in used), []))
    row = RoomRow(viewer, room_id, options, COLORS[len(viewer.rows) % len(COLORS)])
    viewer.rows[room_id] = row
    viewer.rows_layout.addWidget(row)
    viewer.update_main_choices()
    if viewer.running:
        viewer._start_row(row)
    viewer.changed()
    return True


def import_current(viewer):
    wall = getattr(viewer.context.window, "wall", None)
    if wall is not None:
        for tile in wall.visible_tiles():
            room = tile.room or {}
            room_id = str(room.get("room_id") or "")
            if room_id and room_id not in viewer.rows:
                viewer.add_room(room_id, {"alias": room.get("uname") or ""})


def update_main_choices(viewer):
    viewer.send_console.sync_rooms()
    selected = viewer.main.currentData()
    viewer.main.blockSignals(True)
    viewer.main.clear()
    for room_id, row in viewer.rows.items():
        viewer.main.addItem(row.label(), room_id)
    index = viewer.main.findData(selected)
    viewer.main.setCurrentIndex(max(0, index) if viewer.rows else -1)
    viewer.main.blockSignals(False)
    viewer._main_changed()
    viewer._refresh_chat_status()


def remove_room(viewer, room_id):
    viewer._stop_recording()
    if room_id in viewer.floating_crops:
        viewer.floating_crops[room_id].close()
    viewer.independent_playback.pop(room_id, None)
    row = viewer.rows.pop(room_id)
    viewer.rematch_rooms.discard(room_id)
    viewer.rematch_admit.discard(room_id)
    viewer.independent_shifts.pop(room_id, None)
    viewer.admitted_rooms.discard(room_id)
    viewer.rendered_shifts.pop(room_id, None)
    viewer.relative_rates.pop(room_id, None)
    viewer.safety_buffer.arrivals.pop(room_id, None)
    viewer.overlay_pending.pop(room_id, None)
    viewer._stop_row(row)
    viewer.sources.pop(room_id, None)
    viewer.rows_layout.removeWidget(row)
    row.deleteLater()
    viewer.alignment.lags.pop(room_id, None)
    viewer.alignment.candidates.pop(room_id, None)
    viewer.update_main_choices()
    viewer.generation += 1
    viewer._sync_sidebar_marks()
    if not viewer.rows:
        viewer.stop()
        viewer.panel.set_placeholder('')
        viewer.notice.setText('')
    viewer.changed()


def _start_row(viewer, row):
    row.alignment_hint, row.hint_pending, row.hint_searching = None, False, False
    row.large_candidate = None
    row.paused = False
    row.sync_state = "准备缓存"
    row.mix_gain = 0.0
    row.recovery_since = None
    row.was_isolated = False
    row.actual_position = None
    row.picture_wait_since = None
    row.match_confirmed = False
    row.awaiting_confirmation = False
    row.review_alignment = False
    row.reset_pending = False
    row.needs_epoch_samples = row.user_confirmed
    row.match_failures = 0
    row.alignment_started = time.monotonic()
    row.recheck_alignment = False
    row.search_failures = 0
    row.next_analysis = 0.0
    if viewer.alignment.manual_locked:
        row.match_text = "手动时间已锁定；勾选自动对齐可解除"
    row.pending.clear()
    row.actual_quality = 0
    seed = dict(viewer.sources.get(row.room_id) or {})
    seed["title"] = seed.get("title") or row.title
    if seed.get("quality", row.quality) != row.quality:
        seed.pop("url", None)
    seed["quality"] = row.quality
    seed['highest_quality'] = row.quality_auto and row.room_id == viewer.alignment.reference
    policy = viewer._crop_source_policy(row)
    seed['crop_quality_policy'] = policy
    seed['balanced_quality'] = policy == 'balanced'
    if policy != 'source':
        seed.pop('url', None)
    row.decoder = Decoder(row.room_id, seed, row.platform)
    viewer.clock_recovery.source_reset(row.room_id)
    row.decoder.set_crop((0, 0, 1, 1))
    decoder = row.decoder
    decoder.events.information.connect(lambda info, r=row, d=decoder: viewer._information(r, d, info))
    decoder.events.state.connect(lambda text, r=row, d=decoder: viewer._state(r, d, text, False))
    decoder.events.reset.connect(lambda r=row, d=decoder: viewer._reset(r, d))
    decoder.start()
    viewer._start_chat(row)
    viewer._sync_sidebar_marks()


def _valid(viewer, row, worker, chat=False):
    return (viewer.running and viewer.rows.get(row.room_id) is row and
            (row.chat if chat else row.decoder) is worker)


def _information(viewer, row, worker, info):
    if not viewer._valid(row, worker):
        return
    if info.get('quality_options'):
        row.quality_options = [dict(item) for item in info['quality_options']]
    if info.get("requested_quality") and viewer._crop_source_policy(row) == 'source':
        row.quality = int(info["requested_quality"])
    if "title" in info:
        row.title = info.get("title") or ""
    if not row.alias.text().strip() and info.get("uname"):
        row.alias.setText(info.get("uname") or "未命名主播")
        viewer.update_main_choices()
    if info.get("actual_quality"):
        row.actual_quality = int(info["actual_quality"])
        if viewer._crop_source_policy(row) == 'source' and row.platform is not None and row.platform.kind != "douyu":
            row.quality = row.actual_quality
        viewer.changed()
    if row.room_id == viewer.main.currentData():
        viewer.sync_picture()


def _state(viewer, row, worker, text, chat):
    if viewer._valid(row, worker, chat):
        if chat:
            row.chat_text = text
            viewer._refresh_chat_status()
        else:
            row.video_text = text
        row.refresh_status()


def _reset(viewer, row, worker):
    if viewer._valid(row, worker):
        viewer.clock_recovery.source_reset(row.room_id)
        row.large_candidate = None
        previous = getattr(row.decoder.history, 'previous', None)
        position = viewer.live_clock() + viewer.shifts().get(row.room_id, 0)
        if previous is not None and previous.frame_at(position) is not None:
            row.reset_pending = True
            return
        row.reset_pending = False
        row.needs_epoch_samples = row.user_confirmed
        row.was_isolated = True
        row.match_confirmed = False
        row.mix_gain = 0
        row.recovery_since = None
        row.picture_wait_since = None
        viewer.alignment.candidates.pop(row.room_id, None)
        row.match_failures = 0
        row.alignment_started = time.monotonic()
        if viewer.alignment.automatic and row.room_id != viewer.alignment.reference:
            viewer._queue_rematch(row)
        if row.room_id == viewer.alignment.reference:
            for peer in viewer.rows.values():
                peer.needs_epoch_samples = peer.user_confirmed
                peer.match_confirmed = False
                peer.match_failures = 0
                peer.alignment_started = time.monotonic()
                if peer.room_id != row.room_id and viewer.alignment.automatic:
                    viewer._queue_rematch(peer)
            viewer.alignment.candidates.clear()
        viewer.generation += 1


def _stop_row(row):
    row.clear_buffer_warning()
    row.alignment_hint, row.hint_pending, row.hint_searching = None, False, False
    if row.decoder is not None:
        row.decoder.stop()
    if row.chat is not None:
        row.chat.stop()
    row.decoder = None
    row.chat = None
    row.mix_gain = 0
    row.match_confirmed = False
    row.sync_state = "已停止"
    row.actual_position = None
    row.join.hide()
    row.pending.clear()
    row.video_text = "已停止"
    row.chat_text = "弹幕已停止"
    row.match_text = "等待画面"
    row.buffer_text = ""
    row.refresh_status()


def _add_dragged(viewer, room_id):
    room = next((item for item in getattr(viewer.context.window, "rooms", [])
                 if str(item.get("room_id") or "") == room_id), {})
    if viewer.add_room(room_id, {"alias": room.get("uname") or ""}):
        if viewer.embedded and not viewer.running:
            viewer.toggle_running()
        return True
    return False
