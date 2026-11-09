"""Playback timing. Session state remains on the Viewer facade."""
from __future__ import annotations

import time
from math import ceil
from .engine import LateBuffer

def picture_poll_interval(target, frame_stamp, fps, rate=1., elapsed=0.):
    fps = max(1., fps)
    normal = max(4, int(500 / fps))
    if frame_stamp is None:
        return normal
    remaining = (frame_stamp + 1 / fps - target) / rate - elapsed
    return max(1, min(normal, ceil(remaining * 1000)))


def cache_gap(history, target):
    """Describe missing target content, separately from scene-match confidence."""
    bounds = history.bounds()
    if bounds is None:
        return "尚未收到播放缓存"
    previous = getattr(history, 'previous', None)
    previous_bounds = previous.bounds() if previous is not None else None
    earliest = min(bounds[0], previous_bounds[0]) if previous_bounds else bounds[0]
    latest = max(bounds[1], previous_bounds[1]) if previous_bounds else bounds[1]
    if target < earliest:
        return f"目标早于已保留缓存约 {earliest - target:.1f} 秒"
    if target > latest:
        return f"目标内容尚未收到，缺少约 {target - latest:.1f} 秒"
    continuous = history.bounds_at(target)
    if continuous is None or not continuous[0] <= target <= continuous[1]:
        return "目标位置存在断流缓存缺口"
    return ""


def _recover_clock(viewer, clock, shifts):
    now = time.monotonic()
    main = viewer.rows.get(viewer.alignment.reference)
    if main is None or main.decoder is None or main.paused:
        viewer.late_buffer = LateBuffer(viewer.reserve_seconds)
        viewer.late_notice.hide()
        viewer.playback_rate = 1.0
        viewer.sync_waiting = False
        return clock
    secondary_edit = viewer.offset_target in viewer.rows and viewer.offset_target != main.room_id
    main_bounds = main.decoder.history.bounds_at(clock + shifts[main.room_id])
    if main_bounds is None or not main_bounds[0] <= clock + shifts[main.room_id] <= main_bounds[1]:
        viewer.late_buffer = LateBuffer(viewer.reserve_seconds)
        viewer.late_notice.hide()
    if (viewer.offset_repositioning and secondary_edit and main_bounds
            and main_bounds[0] <= clock + shifts[main.room_id] <= main_bounds[1] - .05):
        viewer.offset_repositioning = False
    if viewer.offset_repositioning:
        reposition_rows = {viewer.alignment.reference}
        if not secondary_edit:
            reposition_rows |= {viewer.offset_target} | {
                key for key in viewer.admitted_rooms
                if key in viewer.rows and viewer.rows[key].sync_state == "已同步"}
        ranges = [(bounds[0] - shifts[key], bounds[1] - shifts[key])
                  for key, row in viewer.rows.items() if key in reposition_rows and row.decoder is not None and not row.paused
                  if (bounds := row.decoder.history.bounds())]
        if len(ranges) == sum(row.decoder is not None and not row.paused for key, row in viewer.rows.items() if key in reposition_rows):
            lower = max((start for start, _end in ranges), default=clock)
            upper = min((end for _start, end in ranges), default=clock)
            if lower <= upper - .25:
                recovered = min(max(clock, lower + .1), upper - .25)
                viewer.audio.correction += recovered - clock
                clock = recovered
                viewer.offset_repositioning = False
    bounds = main.decoder.history.bounds_at(clock + shifts[main.room_id])
    reserve = viewer.safety_buffer.target + (max(0, main.delay.value()) if not viewer.alignment.automatic else 0)
    if bounds is None:
        viewer.sync_waiting = main.was_isolated
        recovered = viewer.locked_clock if viewer.sync_waiting and viewer.locked_clock is not None else clock
        viewer.audio.correction += recovered - clock
        return recovered
    arrival = getattr(main.decoder, "last_frame_received", 0)
    fresh = not arrival or now - arrival <= 3
    position = clock + shifts[main.room_id]
    if (viewer.sync_waiting and not viewer.offset_repositioning
            and bounds[0] <= position <= bounds[1] + .05
            and position >= bounds[1] - .3):
        # bounds_at keeps separate PTS intervals. Once a later interval is
        # buffered, waiting for the old interval to grow can never finish.
        following = main.decoder.history.bounds_at(bounds[1] + .1, minimum_duration=.3)
        if following is not None and following[0] > bounds[1] + .05:
            viewer.sync_waiting = False
            if position >= bounds[1] - .05 - 1e-6:
                recovered = following[0] - shifts[main.room_id]
                viewer.playback_probe.clock_gaps.append((time.perf_counter(), position, following[0]))
                viewer.audio.correction += recovered - clock
                clock, bounds = recovered, following
            # Otherwise play the retained tail before crossing the gap.
    if clock + shifts[main.room_id] < bounds[0]:
        starting = viewer.alignment.automatic and viewer.stable_reference_shift is None
        if (main.was_isolated or starting) and fresh and bounds[1] - bounds[0] < reserve:
            ready = main.decoder.history.bounds_at(clock + shifts[main.room_id], minimum_duration=reserve)
            if ready is not None:
                bounds = ready
        if (main.was_isolated or starting) and fresh and bounds[1] - bounds[0] >= reserve:
            recovered = bounds[1] - shifts[main.room_id] - reserve
            viewer.audio.correction += recovered - clock
            viewer.sync_waiting = False
            return recovered
        viewer.sync_waiting = main.was_isolated
        recovered = viewer.locked_clock if viewer.sync_waiting and viewer.locked_clock is not None else clock
        viewer.audio.correction += recovered - clock
        return recovered
    upper = bounds[1] - shifts[main.room_id]
    if clock > upper + 1 and fresh:
        recovered = max(bounds[0] - shifts[main.room_id] + .1, upper - reserve)
        viewer.audio.correction += recovered - clock
        viewer.sync_waiting = False
        return recovered
    waiting = clock > upper - (.3 if viewer.sync_waiting else .05)
    recovered = min(clock, viewer.locked_clock if viewer.sync_waiting and viewer.locked_clock is not None else upper - .05) if waiting else clock
    viewer.sync_waiting = waiting
    viewer.locked_clock = recovered
    viewer.audio.correction += recovered - clock
    ahead = bounds[1] - recovered - shifts[main.room_id]
    viewer._update_late_buffer(now, recovered, shifts, ahead, bounds[1] - bounds[0] - .5)
    viewer.playback_rate = viewer.safety_buffer.rate(now, ahead, minimum=viewer.late_buffer.floor)
    if viewer.late_buffer.building:
        viewer.playback_rate = min(viewer.playback_rate, .98)
    return recovered


def _update_late_buffer(viewer, now, clock, shifts, ahead, capacity):
    if not viewer.alignment.automatic:
        viewer.late_buffer = LateBuffer(viewer.reserve_seconds)
        viewer.late_notice.hide()
        return
    deficits = {}
    main = viewer.rows[viewer.alignment.reference]
    if not viewer.sync_waiting and ahead >= .05 and main.sync_state == "已同步":
        for key, row in viewer.rows.items():
            trusted = row.match_confirmed or (row.user_confirmed and row.rematch_locally and not row.needs_epoch_samples)
            if (key == main.room_id or row.paused or row.decoder is None or not trusted
                    or row.reset_pending or key in viewer.independent_shifts
                    or key not in viewer.alignment.lags):
                continue
            arrival = getattr(row.decoder, 'last_frame_received', 0)
            history = row.decoder.history
            target = clock + shifts[main.room_id] + viewer.alignment.lags[key]
            bounds = history.bounds_at(target)
            if bounds is None:
                latest = history.bounds()
                bounds = latest if latest and target > latest[1] else None
            if bounds is not None and (not arrival or 0 <= now - arrival <= 3):
                deficits[key] = target - bounds[1]
                capacity = min(capacity, ahead + deficits[key] + bounds[1] - bounds[0] - .5)
    viewer.late_buffer.update(now, ahead, capacity, deficits)
    blocked = [viewer.rows[key].label() for key in viewer.rows if key in viewer.late_buffer.blocked]
    if blocked:
        text = f"{'、'.join(blocked)}过慢，请将较晚的一路设为主画面以更快对齐"
        detail = "该路持续晚到，超出平滑建立缓冲的能力；可将较晚的一路设为主画面，或移除该路。"
    elif viewer.late_buffer.building:
        names = [viewer.rows[key].label() for key in viewer.rows if key in viewer.late_buffer.rooms]
        available = max(0, -max((deficits[key] for key in viewer.late_buffer.rooms), default=0))
        if max((deficits[key] for key in viewer.late_buffer.rooms), default=0) <= .05:
            text = f"{'、'.join(names)}正在积累缓冲：{available:.1f} / {viewer.reserve_seconds} 秒；切换较晚的一路为主画面可更快对齐"
        else:
            text = f"{'、'.join(names)}持续晚到，正在平滑增加主路缓冲；切换较晚的一路为主画面可更快对齐"
        detail = f"主画面持续播放，以最多约 2% 的轻微降速积累共同缓冲；目标副路余量 {viewer.reserve_seconds} 秒，需要数分钟逐步积累。"
    elif viewer.late_buffer.limited:
        text = f"当前缓存容量不足以保留 {viewer.reserve_seconds} 秒播放余量"
        detail = "继续播放并保留已积累余量；历史缓存容量或断流缺口限制了连续可播放时长，不表示比赛画面失配。"
    else:
        text, detail = "", ""
    viewer.late_notice.setVisible(bool(text))
    if viewer.late_notice.text() != text:
        viewer.late_notice.setText(text)
        viewer.late_notice.setToolTip(detail)


def dismiss_buffer_notice(viewer):
    viewer.buffer_notice_timer.stop()
    viewer.buffer_notice.hide()
    viewer.notice.show()


def _observe_buffer_exhaustion(viewer, row, exhausted, eligible, now):
    if not eligible:
        row.clear_buffer_warning()
        return
    while row.buffer_exhaustions and now - row.buffer_exhaustions[0] > 60:
        row.buffer_exhaustions.popleft()
    if exhausted:
        row.buffer_recovered_since = None
    if exhausted and not row.buffer_exhausted:
        row.buffer_exhausted = True
        row.buffer_exhaustions.append(now)
        if len(row.buffer_exhaustions) == 3:
            row.buffer_warning.show()
            if row.buffer_notice_at is None or now - row.buffer_notice_at >= 120:
                row.buffer_notice_at = now
                text = f'{row.label()}：为获得更好的观看体验，建议切换主画面'
                viewer.buffer_notice.setText(text)
                viewer.buffer_notice.setToolTip(text)
                viewer.notice.hide()
                viewer.buffer_notice.show()
                viewer.buffer_notice_timer.start()
    elif not exhausted and row.sync_state == '已同步':
        row.buffer_exhausted = False
        if row.buffer_recovered_since is None:
            row.buffer_recovered_since = now
        if now - row.buffer_recovered_since >= 60:
            row.buffer_warning.hide()
