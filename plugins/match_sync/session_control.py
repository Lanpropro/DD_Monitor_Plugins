"""Session control. State and Qt callback ownership stay on Viewer."""
from __future__ import annotations

from .engine import LateBuffer, SafetyBuffer
from .replay import ReplayTimeline


def toggle_running(viewer):
    if viewer.running:
        viewer.stop()
        return
    if not viewer.rows:
        viewer.notice.setText("请先添加直播间")
        return
    viewer.running = True
    viewer.independent_playback.clear()
    viewer.independent_shifts.clear()
    viewer.rematch_rooms = set(viewer.rows) - {viewer.alignment.reference} if viewer.alignment.automatic else set()
    viewer.rematch_admit.clear()
    viewer.safety_buffer = SafetyBuffer()
    viewer.late_buffer = LateBuffer(viewer.reserve_seconds)
    viewer.late_notice.hide()
    if not viewer.alignment.manual_locked:
        viewer.stable_reference_shift = None
    viewer.last_health_update = None
    viewer.playback_rate = 1.0
    viewer.admitted_rooms.clear()
    viewer.rendered_shifts.clear()
    viewer.shift_reference = None
    viewer.shift_updated = None
    viewer.relative_rates.clear()
    viewer.generation += 1
    if not viewer.alignment.manual_locked:
        viewer.alignment.lags = {viewer.alignment.reference: 0}
    viewer.locked_clock = None
    viewer.sync_waiting = False
    viewer.alignment.candidates.clear()
    viewer._refresh_chat_status()
    for row in viewer.rows.values():
        row.user_confirmed = False
        viewer._start_row(row)
    viewer.sync_picture()
    viewer._sync_sidebar_marks()
    viewer.audio.start()
    viewer.playback_probe.start()
    viewer.render_timer.start()
    viewer.match_timer.start()


def stop(viewer):
    viewer.clock_recovery.cancel()
    clock_dialog = getattr(viewer, 'clock_dialog', None)
    if clock_dialog is not None:
        clock_dialog.reject()
    viewer.dismiss_buffer_notice()
    viewer.replay_bar.preview.stop()
    viewer.running = False
    if viewer.stream_info_dialog is not None:
        viewer.stream_info_dialog.close()
    with viewer._switch_stage('stop_playback_probe'):
        viewer.playback_probe.stop()
    viewer.independent_playback.clear()
    for floating in list(viewer.floating_crops.values()):
        floating.close()
    viewer.analysis_serial += 1
    viewer.late_buffer = LateBuffer(viewer.reserve_seconds)
    viewer.late_notice.hide()
    viewer.matching = False
    if viewer.fullscreen_dialog is not None:
        viewer.fullscreen_dialog.close()
    viewer._stop_recording()
    viewer.replay = ReplayTimeline()
    if viewer.multi_recorder is not None:
        viewer.multi_recorder.stop(wait=True)
    viewer.replay_bar.refresh(force=True)
    viewer.sync_waiting = False
    viewer.locked_clock = None
    viewer.offset_repositioning = False
    viewer.generation += 1
    viewer.render_timer.stop()
    viewer.match_timer.stop()
    viewer.compare.setChecked(False)
    with viewer._switch_stage('stop_audio'):
        viewer.audio.stop()
    for _card, _name, canvas, state in viewer.comparison_panel.cards.values():
        canvas.set_frame(None)
        canvas.waiting = True
        state.setText("已停止")
    with viewer._switch_stage('stop_sources'):
        for row in viewer.rows.values():
            viewer._stop_row(row)
            seed = viewer.sources.get(row.room_id) or {}
            if str(seed.get("url") or "").startswith(("http://", "https://")):
                viewer.sources.pop(row.room_id, None)
    if not viewer.host_audio_timer.isActive():
        viewer._release_host_audio()
    viewer.panel.set_status("已停止")
    viewer.panel.count.set_connections(0)
    viewer.overlay_pending.clear()
    viewer.overlay_last = ""
    if viewer.video_danmaku is not None:
        viewer.video_danmaku.clear()
    viewer.canvas.set_frame(None)
    viewer._show_buffering(False)
    viewer._sync_sidebar_marks()


def closeEvent(viewer, event, _base):
    if viewer.embedded:
        viewer.reject()
        event.accept()
        return
    viewer.stop()
    if viewer.save_timer.isActive():
        viewer.save_timer.stop()
        viewer.save()
    _base(event)
    with viewer._switch_stage('restore_host_widgets'):
        viewer._restore_host_widgets()


def reject(viewer, _base):
    if viewer.embedded:
        viewer.stop()
        if viewer.save_timer.isActive():
            viewer.save_timer.stop()
            viewer.save()
        viewer.hide()
        with viewer._switch_stage('restore_host_widgets'):
            viewer._restore_host_widgets()
        viewer.finished.emit(0)
    else:
        _base()
