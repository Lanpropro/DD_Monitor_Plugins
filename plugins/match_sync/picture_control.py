"""Picture control. Session state remains on the Viewer facade."""
from __future__ import annotations

import sys
from ddm.fullscreen_cursor import FullscreenCursor
from .engine import LateBuffer
from .panels import MatchSettingsDialog
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout
from PySide6.QtCore import Qt
from .fullscreen import mark_fullscreen
from urllib.parse import urlsplit

def _main_changed(viewer, *_args):
    selected = viewer.main.currentData() or ""
    viewer.independent_playback.pop(selected, None)
    for room_id, row in viewer.rows.items():
        row.main_button.setChecked(room_id == selected)
        row.main_button.setText('主画面' if room_id == selected else '设为主画面')
        row.realign.setEnabled(viewer.automatic.isChecked() and room_id != selected)
    if selected != viewer.alignment.reference:
        viewer.dismiss_buffer_notice()
        for current in viewer.rows.values():
            current.clear_buffer_warning()
        viewer.late_buffer = LateBuffer(viewer.reserve_seconds)
        viewer.late_notice.hide()
        for current in viewer.rows.values():
            current.large_candidate = None
            current.alignment_hint, current.hint_pending, current.hint_searching = None, False, False
        saved = viewer.shifts() if viewer.alignment.manual_locked else None
        viewer.stable_reference_shift = viewer.independent_shifts.pop(selected, None)
        viewer.alignment.select_reference(selected)
        if saved is not None:
            viewer._save_delay_offsets(saved)
        viewer.generation += 1
        viewer.canvas.frame_key = None
        for key, row in viewer.rows.items():
            row.match_text = ("手动时间已锁定；勾选自动对齐可解除" if viewer.alignment.manual_locked else
                              "主画面基准" if key == selected else "等待重新确认")
            row.refresh_status()
    viewer._apply_crop_quality()
    main = viewer.rows.get(selected)
    if (viewer.running and main is not None and main.quality_auto and main.decoder is not None
            and not main.decoder.seed.get('highest_quality')):
        viewer._reload_picture()
    viewer.sync_picture()
    viewer.changed()


def select_main(viewer, room_id):
    viewer.main.setCurrentIndex(viewer.main.findData(room_id))
    viewer._main_changed()


def _show_buffering(viewer, active):
    if viewer.canvas.waiting != bool(active):
        viewer.canvas.waiting = bool(active)
        viewer.canvas.update()
    if getattr(viewer.picture, "_buffering", False) != bool(active):
        viewer.picture.set_buffering(bool(active))


def _stream_info_snapshot(viewer):
    row = viewer.rows.get(viewer.main.currentData())
    if row is None:
        return None
    info = {'room': f'{row.label()} · {row.room_id}', 'state': 'idle',
            'core': 'FFmpeg / PyAV（比赛二路）'}
    decoder = row.decoder
    if not viewer.running or decoder is None:
        return info
    width, height = decoder.video_size
    info.update(state=('paused' if row.paused else 'connecting' if not width else
                       'buffering' if viewer.canvas.waiting else 'playing'),
                host=urlsplit(decoder.source_url).hostname or '',
                video={'w': width, 'h': height}, fps=decoder.fps, decoder='no')
    history = decoder.history
    # Read retained-cache metadata only; never wait for decoding or inspect
    # the FFmpeg pipe on the GUI thread. Unknown source properties stay unknown.
    if history.lock.acquire(blocking=False):
        try:
            info['video_codec'] = history.codec[0] if history.codec else None
            bounds = history.bounds()
            info['buffer_seconds'] = bounds[1] - bounds[0] if bounds else None
            info['buffer_bytes'] = history.byte_size
        finally:
            history.lock.release()
    return info


def _open_stream_info(viewer):
    if viewer._stream_info_snapshot() is None:
        return
    if viewer.stream_info_dialog is None:
        from ddm.stream_info import StreamInfoDialog
        dialog = StreamInfoDialog(viewer._stream_info_snapshot, viewer)
        dialog.finished.connect(viewer._stream_info_closed)
        viewer.stream_info_dialog = dialog
    viewer.stream_info_dialog.refresh()
    viewer.stream_info_dialog.show()
    viewer.stream_info_dialog.raise_()
    viewer.stream_info_dialog.activateWindow()


def _stream_info_closed(viewer, _result):
    viewer.stream_info_dialog = None


def sync_picture(viewer, *_args, refresh_metadata=True):
    viewer.replay_bar.preview.sync_source()
    row = viewer.rows.get(viewer.main.currentData())
    # Poll twice per source frame: Qt paint/chat work can delay a timer by
    # several milliseconds. The audio clock still owns playback speed.
    interval = max(4, int(500 / getattr(row.decoder, "fps", 30))) if row is not None else 33
    viewer._picture_poll_base = interval
    state = (row.room_id, id(row.decoder), getattr(row.decoder, 'fps', 30)) if row is not None else None
    if state != getattr(viewer, '_picture_poll_source', None):
        viewer._picture_poll_source = state
        viewer.render_timer.setInterval(interval)
    tile = viewer.picture
    if row is None:
        viewer._stop_recording()
        tile.set_room(None)
        viewer.canvas.set_frame(None)
        viewer._show_buffering(False)
        return
    tile.blockSignals(True)
    try:
        if str(tile.room.get("room_id") or "") != row.room_id:
            refresh_metadata = True
            controls_visible = tile._overlay_visible
            viewer._stop_recording()
            viewer.canvas.set_frame(None)
            tile.set_room({"room_id": row.room_id, "uname": row.label(), "live": True,
                           "quality": row.quality})
            tile.set_controls_visible(controls_visible)
        if refresh_metadata:
            tile.room["title"] = row.title
            tile.title_badge.set_text(row.title or row.label(), "")
            tile.title_badge.setToolTip(row.title or row.label())
            tile.stream_badge.set_state(True, "")
            # Capture workers already deliver the available tiers. GUI
            # refreshes must not call a platform provider for metadata.
            if row.quality_options and tile.quality_options != row.quality_options:
                tile.set_quality_options(row.quality_options)
            if tile.quality != row.quality:
                tile.set_quality(row.quality)
            if tile.actual_quality != row.actual_quality:
                tile.set_actual_quality(row.actual_quality)
        if tile.volume != row.volume.value():
            tile.set_volume(row.volume.value())
        if tile.audio_channel != row.channel.currentData():
            tile.set_audio_channel(row.channel.currentData())
        if tile.muted != (not row.audible.isChecked()):
            tile.set_muted(not row.audible.isChecked())
        if tile.paused != row.paused:
            tile.set_paused(row.paused)
        if not tile._player_active:
            tile.set_video_active(True)
        tile.cover.hide()
        tile.stream_url = getattr(row.decoder, "source_url", "") if row.decoder else ""
        tile.stream_headers = getattr(row.decoder, "source_headers", {}) if row.decoder else {}
    finally:
        tile.blockSignals(False)
    viewer._show_buffering(viewer.running and not row.paused and
                         (viewer.canvas.image.isNull() or viewer.canvas.waiting))


def open_match_settings(viewer):
    if viewer.match_settings is None:
        viewer.match_settings = MatchSettingsDialog(viewer)
    viewer.match_settings.show()
    viewer.match_settings.raise_()
    viewer.match_settings.activateWindow()


def _picture_setting(viewer, key, value):
    row = viewer.rows.get(viewer.main.currentData())
    if row is None:
        return
    widget = getattr(row, key)
    if key == "channel":
        widget.setCurrentIndex(max(0, widget.findData(value)))
    elif key == "audible":
        widget.setChecked(value)
    else:
        widget.setValue(value)


def _quality_changed(viewer, _room, quality):
    row = viewer.rows.get(viewer.main.currentData())
    if row is not None:
        row.quality = quality
        row.quality_auto = False
        viewer._reload_picture()
        viewer.changed()


def _reload_picture(viewer):
    row = viewer.rows.get(viewer.main.currentData())
    if row is None or not viewer.running:
        return
    viewer._stop_row(row)
    viewer.sources.pop(row.room_id, None)
    viewer._start_row(row)
    if not viewer.alignment.manual_locked:
        viewer.alignment.lags = {viewer.alignment.reference: 0}
        if viewer.alignment.automatic:
            viewer.stable_reference_shift = None
            viewer.locked_clock = None
            viewer.sync_waiting = False
    viewer.alignment.candidates.clear()
    viewer.generation += 1
    viewer.canvas.set_frame(None)
    viewer._show_buffering(True)


def _pause_picture(viewer):
    row = viewer.rows.get(viewer.main.currentData())
    if row is not None:
        row.paused = not row.paused
        viewer.sync_picture()


def _fullscreen_picture(viewer):
    if viewer.fullscreen_dialog is not None:
        viewer.fullscreen_dialog.close()
        return
    if not viewer.rows:
        return
    dialog = QDialog(viewer.window(), Qt.Window | Qt.FramelessWindowHint)
    dialog.setScreen(viewer.picture.screen())
    dialog.setWindowTitle("比赛二路主画面")
    dialog.settings = viewer.context.window.settings
    dialog._fullscreen_tile = viewer.picture
    dialog._fullscreen_cursor = FullscreenCursor(dialog)
    viewer.console_size_timer.stop()
    sizes = viewer.body_split.sizes()
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(viewer.picture)
    fullscreen_hwnd = None
    def restore(_result):
        if fullscreen_hwnd is not None:
            mark_fullscreen(fullscreen_hwnd, False)
        dialog._fullscreen_cursor.stop()
        viewer.body_split.insertWidget(0, viewer.picture)
        viewer.body_split.setSizes(sizes)
        viewer.fullscreen_dialog = None
        viewer.queue_console_height()
        viewer.reparent_floating_crops(viewer.floating_surface())
        dialog.deleteLater()
    dialog.finished.connect(restore)
    viewer.fullscreen_dialog = dialog
    dialog.showFullScreen()
    viewer.reparent_floating_crops(dialog)
    if sys.platform == 'win32' and QApplication.platformName() == 'windows':
        fullscreen_hwnd = int(dialog.winId())
        mark_fullscreen(fullscreen_hwnd, True)
    dialog.raise_()
    dialog.activateWindow()
    dialog._fullscreen_cursor.start()
