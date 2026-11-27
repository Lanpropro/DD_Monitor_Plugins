"""Presentation. Session state remains on the Viewer facade."""
from __future__ import annotations

import time
from contextlib import nullcontext
from .chat_control import live_chat_events, replay_chat_events
from .playback_timing import cache_gap, picture_poll_interval


def start_rendering(viewer):
    if viewer.split_presentation:
        viewer._configure_picture_budget()
        _arm_picture_timer(viewer, getattr(viewer, '_picture_poll_base', 33))
        viewer.auxiliary_timer.start()
    else:
        viewer.render_timer.start()


def stop_rendering(viewer):
    viewer.render_timer.stop()
    viewer.auxiliary_timer.stop()
    viewer._picture_due = viewer._presentation_update = None
    gpu = getattr(viewer, 'd3d11_picture', None)
    if gpu is not None:
        gpu.stop()


def _arm_picture_timer(viewer, interval):
    viewer._picture_due = time.perf_counter() + interval / 1000
    viewer.render_timer.start(interval)


def _next_picture_interval(viewer):
    if viewer._picture_poll is not None:
        target, frame_stamp, fps, rate, requested = viewer._picture_poll
        return picture_poll_interval(target, frame_stamp, fps, rate, time.perf_counter() - requested)
    return getattr(viewer, '_picture_poll_base', 33)


def render_picture_tick(viewer):
    entered = time.perf_counter()
    if not viewer.running:
        return
    probe = viewer.playback_probe if viewer.playback_probe.thread is not None else None
    deadline = viewer._picture_due
    period = viewer.render_timer.interval() / 1000
    if deadline is not None:
        lateness = max(0., entered - deadline)
        viewer._picture_due = deadline + (1 + int(lateness / period)) * period
    if probe is not None:
        probe.begin()
        probe.timings.record('picture_poll_gap', probe.render_gap / 1000, at=entered)
        if deadline is not None:
            probe.timings.record('picture_timer_late', lateness, at=entered)
    timeline = probe.gui_timeline if probe is not None else None
    if timeline is not None:
        if deadline is not None:
            timeline.point('picture.timer_due', deadline)
        timeline.point('picture.poll', entered)
    viewer._picture_poll = None
    try:
        position = _playback_position(viewer, probe)
        waiting = _render_picture(viewer, probe, position)
        viewer._presentation_update = (viewer.generation, position, waiting)
    finally:
        if probe is not None:
            probe.end()
        interval = getattr(viewer, '_picture_poll_base', 33)
        if viewer.running and interval != viewer.render_timer.interval():
            _arm_picture_timer(viewer, interval)


def render_auxiliary_tick(viewer):
    update = viewer._presentation_update
    if not viewer.running or update is None or update[0] != viewer.generation:
        return
    _, position, waiting = update
    probe = viewer.playback_probe if viewer.playback_probe.thread is not None else None
    timeline = probe.gui_timeline if probe is not None else None
    if probe is not None:
        probe.begin(auxiliary=True)
    try:
        dragging_crop, refresh_status_ui = _prepare_auxiliary(viewer, probe)
        with timeline.span('controls.delay') if timeline is not None else nullcontext():
            _sync_delay_controls(viewer, position[1])
        with timeline.span('controls.reader_budget') if timeline is not None else nullcontext():
            viewer._configure_picture_budget()
        _render_previews(viewer, probe, position)
        _render_auxiliary(viewer, probe, position, waiting, dragging_crop, refresh_status_ui)
    finally:
        if probe is not None:
            probe.end(auxiliary=True)


def render(viewer):
    if not viewer.running:
        return
    viewer._picture_poll = None
    probe = viewer.playback_probe if viewer.playback_probe.thread is not None else None
    if probe is not None:
        probe.begin()
    try:
        viewer._render(probe)
    finally:
        if probe is not None:
            probe.end()
        interval = _next_picture_interval(viewer)
        if viewer.running and viewer.render_timer.interval() != interval:
            viewer.render_timer.setInterval(interval)


def _render(viewer, probe):
    dragging_crop, refresh_status_ui = _prepare_auxiliary(viewer, probe)
    position = _playback_position(viewer, probe, combined=True)
    _render_previews(viewer, probe, position)
    waiting = _render_picture(viewer, probe, position)
    _render_auxiliary(viewer, probe, position, waiting, dragging_crop, refresh_status_ui)


def _prepare_auxiliary(viewer, probe):
    timeline = probe.gui_timeline if probe is not None else None
    if not viewer.embedded:
        viewer._suppress_host_audio()
    dragging_crop = any(crop.drag is not None for crop in viewer.all_floating_crops())
    status_now = time.monotonic()
    refresh_status_ui = not dragging_crop and status_now >= viewer.status_ui_due
    if refresh_status_ui:
        viewer.status_ui_due = status_now + .25
        if probe is not None:
            probe.pipeline_sources = tuple((index, row.decoder) for index, row in enumerate(viewer.rows.values())
                                           if row.decoder is not None and hasattr(row.decoder, 'timings'))
    if probe is not None:
        probe.mark('metadata_sidebar')
    if not dragging_crop:
        with timeline.span('controls.sidebar') if timeline is not None else nullcontext():
            viewer._sync_sidebar_marks()
    if probe is not None:
        probe.mark('metadata_picture')
    with timeline.span('controls.picture') if timeline is not None else nullcontext():
        viewer.sync_picture(refresh_metadata=refresh_status_ui)
    return dragging_crop, refresh_status_ui


def _sync_delay_controls(viewer, shifts):
    if viewer.alignment.automatic:
        origin = shifts.get(viewer.alignment.reference, 0)
        for key, row in viewer.rows.items():
            delay = row.delay
            if delay.lineEdit().isModified():
                continue
            target = max(delay.minimum(), min(delay.maximum(), origin - shifts[key]))
            current = delay.value()
            if abs(current - target) >= .5 * 10 ** -delay.decimals():
                blocked = delay.blockSignals(True)
                delay.setValue(target)
                delay.blockSignals(blocked)
                current = delay.value()
            row.previous_delay = current


def _playback_position(viewer, probe, *, combined=False):
    if probe is not None:
        probe.mark('clock')
    clock = viewer.live_clock()
    wanted = viewer.target_shifts()
    shifts = viewer._smooth_shifts(wanted)
    if combined:
        _sync_delay_controls(viewer, shifts)
        viewer._configure_picture_budget()
    for key, current in viewer.rows.items():
        if current.reset_pending and current.decoder is not None:
            previous = getattr(current.decoder.history, 'previous', None)
            retained = previous.bounds() if previous is not None else None
            if retained is None or clock + shifts[key] >= retained[1] - .06 or previous.frame_at(clock + shifts[key]) is None:
                current.decoder.history.previous = None
                viewer._reset(current, current.decoder)
    clock = viewer._recover_clock(clock, shifts)
    now = time.monotonic()
    viewer.replay.observe(now, clock, {key: shift for key, shift in shifts.items()
                                   if key == viewer.alignment.reference or viewer.rows[key].sync_state == '已同步'})
    shown_clock, shown_shifts = viewer.replay.presentation(clock, shifts, now)
    if viewer.replay.active:
        histories = viewer.replay_histories()
        if not viewer.replay.valid(shown_clock, shown_shifts, histories):
            following = viewer.replay.advance_gap(shown_clock, now, histories)
            if following is None:
                viewer.return_live()
                shown_clock, shown_shifts = clock, shifts
            else:
                shown_clock, shown_shifts = following, viewer.replay.shifts_at(following)
                viewer.overlay_pending.clear()
                viewer.audio.flush()
    return clock, shifts, wanted, shown_clock, shown_shifts


def _render_previews(viewer, probe, position):
    _, _, _, shown_clock, shown_shifts = position
    timeline = probe.gui_timeline if probe is not None else None
    with timeline.span('controls.replay_bar') if timeline is not None else nullcontext():
        viewer.replay_bar.refresh()
    if probe is not None:
        probe.mark('floating')
    viewer.render_floating_crops(shown_clock, shown_shifts)


def _render_picture(viewer, probe, position):
    _, _, _, shown_clock, shown_shifts = position
    if probe is not None:
        probe.mark('picture')
    row = viewer.rows.get(viewer.alignment.reference)
    gpu = getattr(viewer, 'd3d11_picture', None)
    if row is None or row.decoder is None or row.paused or row.room_id not in shown_shifts:
        if gpu is not None:
            gpu.suspend()
        if probe is not None:
            probe.frame = None
        return None
    target = shown_clock + shown_shifts[row.room_id]
    requested = time.perf_counter()
    frame = (gpu.render(row.decoder, target, viewer.playback_rate) if gpu is not None
             else viewer.canvas.show_at(row.decoder, target))
    viewer._picture_poll = (target, viewer.canvas.requested_frame_key if frame is not None else None,
                            getattr(row.decoder, 'fps', 30),
                            1. if viewer.replay.active else viewer.playback_rate, requested)
    row.actual_position = viewer.canvas.frame_key
    waiting = viewer.canvas.playback_waiting(row.decoder, target, frame,
                                             viewer.sync_waiting and not viewer.replay.active)
    if probe is not None:
        probe.observe(row.decoder, target, viewer.canvas.frame_key, waiting)
    return waiting


def _render_auxiliary(viewer, probe, position, waiting, dragging_crop, refresh_status_ui):
    clock, shifts, wanted, shown_clock, shown_shifts = position
    now = time.monotonic()
    timeline = probe.gui_timeline if probe is not None else None
    row = viewer.rows.get(viewer.alignment.reference)
    if waiting is not None and row is not None and row.decoder is not None and not row.paused:
        if probe is not None:
            probe.mark('picture_status')
        if probe is not None and refresh_status_ui:
            window = viewer.window()
            handle = window.windowHandle()
            screen = window.screen()
            sidebar_depth, sidebar_age = viewer.panel.playback_backlog(now)
            probe.surface = dict(visible=viewer.canvas.isVisible(), window_visible=window.isVisible(),
                                 minimized=window.isMinimized(), exposed=handle.isExposed() if handle else False,
                                 active=window.isActiveWindow(), canvas_size=(viewer.canvas.width(), viewer.canvas.height()),
                                 source_size=getattr(row.decoder, 'video_size', (0, 0)),
                                 main_slot=tuple(viewer.rows).index(row.room_id),
                                 source_kind=row.platform.kind if row.platform is not None else 'bilibili',
                                 sync_waiting=viewer.sync_waiting,
                                 main_started=viewer.stable_reference_shift is not None,
                                 screen_hz=screen.refreshRate() if screen else 0,
                                 playback_rate=viewer.playback_rate, replay=viewer.replay.active,
                                 late_buffer_building=viewer.late_buffer.building,
                                 audio_clock_interpolated=viewer.audio.clock_observation is not None,
                                 native_clip_retry_blocks=viewer.native_clip_retry_blocks,
                                 sidebar_backlog=sidebar_depth, sidebar_oldest_ms=round(sidebar_age, 1),
                                 floating=[crop.diagnostics() for crop in viewer.all_floating_crops()],
                                 render_interval_ms=viewer.render_timer.interval())
            gpu = getattr(viewer, 'd3d11_picture', None)
            if gpu is not None:
                probe.surface['main_presenter'] = gpu.diagnostics()
        viewer._show_buffering(waiting)
        if viewer.sync_waiting:
            viewer.notice.setText("主画面等待缓存恢复；异常二路已静音，已确认偏移保持不变")
        elif waiting:
            bounds = row.decoder.history.bounds_at(clock + shifts[row.room_id])
            target = clock + shifts[row.room_id]
            if bounds and target < bounds[0]:
                viewer.notice.setText(f"主画面当前缓存约 {bounds[1] - bounds[0]:.1f} 秒；"
                                    "偏移超出可用范围，请等待缓存或减小延后量")
            elif bounds and target > bounds[1] + 1:
                viewer.notice.setText("主画面所需内容尚未收到；请减小提前量，或检查该路连接")
            elif bounds and bounds[0] <= target <= bounds[1]:
                viewer.notice.setText("主画面显示帧准备较慢；正在追赶播放进度，声音继续播放")
            else:
                viewer.notice.setText("主画面等待缓存或连接；较大延后量需要先积累足够内容")
        else:
            delay = time.monotonic() - clock - shifts[row.room_id]
            mode = ("自动对齐已开启" if viewer.alignment.automatic else
                    "自动对齐已关闭；手动偏移")
            viewer.notice.setText(f"主画面：{row.label()} · 本地播放延后约 {delay:.1f} 秒 · {mode}")
    if probe is not None:
        probe.mark('comparison')
    if viewer.compare.isChecked():
        viewer.comparison_panel.render(shown_clock, shown_shifts)
    if viewer.replay.active:
        viewer.notice.setText('整组回看中；后台继续接收直播并自动对齐')
    viewer._update_sync_health(clock, shifts)
    if clock_status := viewer.clock_recovery.status_text():
        viewer.notice.setText(clock_status)
    if probe is not None:
        probe.mark('chat')
    chat_deadline = time.perf_counter() + .004
    eligible = []
    chat_budget = min(2 if dragging_crop else 80, max(1, 200 // max(1, len(viewer.rows))))
    for room_id, row in viewer.rows.items():
        if refresh_status_ui:
            bounds = row.decoder.history.bounds_at(clock + shifts[room_id]) if row.decoder is not None else None
            text = ""
            if bounds:
                text = f"历史缓存约 {int(bounds[1] - bounds[0])} 秒"
                position = clock + shifts[room_id]
                ahead = max(0, bounds[1] - position) if bounds[0] <= position <= bounds[1] else 0
                text += f"，{'同步目标' if room_id != viewer.alignment.reference else ''}前方可播放约 {ahead:.1f} 秒"
                if room_id != viewer.alignment.reference and viewer.alignment.automatic and not row.match_confirmed:
                    text += "（候选偏移，尚未验证）"
                if position < bounds[0] or position > bounds[1] + 1:
                    text += "，当前播放偏移超出范围"
                    text += "（" + cache_gap(row.decoder.history, position) + "）"
            if row.sync_state != "已同步":
                text += " · 本路暂时静音"
            elif viewer.alignment.automatic and row.correction_remaining > .1001:
                reference = viewer.alignment.reference
                text += (f" · 播放偏移：目标 {wanted[reference] - wanted[room_id]:+.2f} 秒，"
                         f"当前 {shifts[reference] - shifts[room_id]:+.2f} 秒")
            if row.buffer_text != text:
                row.buffer_text = text
                row.refresh_status()
        shift = shifts[room_id]
        target = clock + shift
        due = float('-inf')
        for index, (received, event) in enumerate(row.pending):
            if index >= chat_budget or received > target:
                break
            # A corrected source timestamp can go backward; keep its queue in FIFO order.
            due = max(due, received - shift)
            eligible.append((due, room_id, row, received, event))
    with timeline.span('chat.sidebar_batch') if timeline is not None else nullcontext():
        if viewer.split_presentation and not viewer.replay.active:
            viewer.panel.queue_playback_events(
                live_chat_events(viewer, eligible, clock, now, chat_deadline, limit=viewer.panel.playback_batch_limit), now)
        else:
            viewer.panel.add_events(live_chat_events(viewer, eligible, clock, now, chat_deadline))
        if viewer.replay.active:
            viewer.panel.add_events(replay_chat_events(viewer, shown_clock, shown_shifts))
    with timeline.span('chat.overlay_feed') if timeline is not None else nullcontext():
        viewer._render_overlay(shown_clock, shown_shifts)
    if viewer.multi_recorder is not None and not viewer.multi_recorder.cancelled.is_set():
        main = viewer.rows.get(viewer.alignment.reference)
        if main is None or main.decoder is None or main.decoder.history is not viewer.multi_recorder.history:
            viewer._stop_recording()
        else:
            viewer.multi_recorder.snapshot = viewer._recording_snapshot()
            elapsed = max(0, int(time.monotonic() - viewer.record_started))
            text = f'{elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}'
            if text != viewer.record_elapsed:
                viewer.record_elapsed = text
                viewer.picture.set_recording_elapsed(text)


def _configure_picture_budget(viewer):
    reference = viewer.alignment.reference
    shown = set(viewer.comparison_panel.rooms_to_show() if viewer.compare.isChecked() else (reference,))
    shown.update(viewer.floating_crops)
    comparisons = len(shown - {reference})
    inactive = max(1, len(viewer.rows) - len(shown))
    for key, row in viewer.rows.items():
        visible = key in shown
        if row.decoder is not None and hasattr(row.decoder, "analysis_size"):
            row.decoder.analysis_size = (640, 360) if visible else (320, 180)
        reader = getattr(row.decoder, "pictures", None)
        if reader is not None:
            if key == reference:
                megabytes = 192 if comparisons else 256
            elif visible:
                megabytes = max(1, 128 // comparisons)
            else:
                megabytes = max(1, (64 if comparisons else 128) // inactive)
            reader.configure(megabytes * 1024 * 1024, getattr(row.decoder, "fps", 30), active=visible)
