"""Alignment control. Session state remains on the Viewer facade."""
from __future__ import annotations

import logging
import threading
import time
from .engine import Match, match_scenes, refine_match
from .playback_timing import cache_gap
from .regions import match_regions
from dataclasses import replace
from PySide6.QtCore import QObject, Signal

_VIEWER_LOGGER_NAME = __package__ + '.viewer'

class Results(QObject):
    matched = Signal(int, dict)
    completed = Signal(int, int, dict)


def _queue_rematch(viewer, row, local=False):
    viewer.independent_playback.pop(row.room_id, None)
    row.large_candidate = None
    row.match_confirmed = False
    row.match_failures = 0
    row.alignment_started = time.monotonic()
    row.recheck_alignment = False
    row.rematch_locally = local
    row.next_analysis = 0.0
    row.search_failures = 0
    row.match_text = "正在重新对齐比赛画面"
    viewer.independent_shifts.pop(row.room_id, None)
    viewer.alignment.candidates.pop(row.room_id, None)
    viewer.rematch_rooms.add(row.room_id)
    if row.room_id in viewer.admitted_rooms:
        viewer.rematch_admit.add(row.room_id)
    row.refresh_status()


def retry_alignment(viewer, room_id):
    viewer.independent_playback.pop(room_id, None)
    if not viewer.running or room_id not in viewer.rows or viewer.rows[room_id].decoder is None:
        viewer.notice.setText("请先播放直播间，再重新对齐比赛画面")
        return
    viewer.analysis_serial += 1
    viewer.matching = False
    reference = viewer.alignment.reference
    before = viewer.shifts()
    if not viewer.alignment.automatic:
        viewer.alignment.lags = {key: value - before[reference] for key, value in before.items()}
    viewer.stable_reference_shift = before[reference]
    viewer.alignment.automatic = True
    viewer.alignment.manual_locked = False
    viewer.automatic.blockSignals(True)
    viewer.automatic.setChecked(True)
    viewer.automatic.blockSignals(False)
    targets = set(viewer.rows) - {reference} if room_id == reference else {room_id}
    for key in targets:
        viewer.alignment.lags[key] = before[key] - before[reference]
        row = viewer.rows[key]
        row.alignment_hint, row.hint_pending, row.hint_searching = None, False, False
        viewer._queue_rematch(row, local=row.user_confirmed and not row.needs_epoch_samples)
        viewer.rematch_admit.add(key)
    viewer.generation += 1
    viewer.changed()
    viewer.render()
    viewer.analyse()


def submit_alignment_hint(viewer, room_id, direction):
    row = viewer.rows.get(room_id)
    if (row is None or direction not in (-1, 1) or not viewer.running or not viewer.alignment.automatic
            or room_id == viewer.alignment.reference or row.hint_pending or row.paused
            or viewer.replay.active or room_id in viewer.independent_playback):
        return
    clock, shifts = viewer.live_clock(), viewer.shifts()
    main_position = viewer.canvas.frame_key
    if main_position is None:
        main_position = clock + shifts[viewer.alignment.reference]
    position = clock + shifts[room_id]
    floating = viewer.floating_crops.get(room_id)
    card = viewer.comparison_panel.cards.get(room_id)
    if viewer.compare.isChecked() and card is not None and card[2].isVisible() and card[2].frame_key is not None:
        position = card[2].frame_key
    elif floating is not None and floating.isVisible() and floating.frame_key is not None:
        position = floating.frame_key
    previous = row.alignment_hint
    weight = min(4, previous[2] + 1) if previous is not None and previous[1] == direction else 1
    # An ahead picture needs a smaller same-scene arrival lag than the displayed difference.
    row.alignment_hint = (position - main_position, direction, weight)
    row.hint_pending, row.hint_serial = True, None
    row.hint_searching = True
    row.match_text = f"已提交{'偏快' if direction < 0 else '偏慢'}判断（权重 {weight}），正在完整重新搜索"
    row.next_analysis = 0.
    viewer.alignment.candidates.pop(room_id, None)
    viewer.analysis_serial += 1
    viewer.matching = False
    row.refresh_status()
    logging.getLogger(_VIEWER_LOGGER_NAME).info('Alignment hint submitted: slot=%s baseline=%.3f direction=%s weight=%s',
        tuple(viewer.rows).index(room_id), row.alignment_hint[0], direction, weight)
    viewer.analyse()


def _delay_changed(viewer, *_args):
    key = next((key for key, row in viewer.rows.items() if row.delay is viewer.sender()), "")
    if viewer.alignment.automatic:
        value = viewer.rows[key].delay.value()
        viewer._save_delay_offsets(viewer.shifts())
        viewer.rows[key].delay.blockSignals(True)
        viewer.rows[key].delay.setValue(value)
        viewer.rows[key].delay.blockSignals(False)
        viewer.automatic.setChecked(False)
    viewer.offset_target = key
    before = viewer.alignment.shifts({key: 0 if viewer.alignment.automatic else row.previous_delay
                                   for key, row in viewer.rows.items()})
    if viewer.stable_reference_shift is not None and viewer.alignment.reference in before:
        main = viewer.rows[viewer.alignment.reference]
        previous_delay = 0 if viewer.alignment.automatic else main.previous_delay
        correction = viewer.stable_reference_shift - previous_delay - before[viewer.alignment.reference]
        before = {room_id: position + correction for room_id, position in before.items()}
    for row in viewer.rows.values():
        row.previous_delay = row.delay.value()
    if not viewer.alignment.automatic and not viewer.alignment.manual_locked:
        viewer.alignment.lags = {viewer.alignment.reference: 0}
    viewer.alignment.manual_locked = True
    viewer.automatic.setChecked(False)
    viewer.alignment.candidates.clear()
    viewer.locked_clock = None
    viewer.sync_waiting = False
    viewer.offset_repositioning = key != viewer.alignment.reference
    reference = viewer.alignment.reference
    shifts = viewer.shifts()
    ranges = [(bounds[0] - shifts[key], bounds[1] - shifts[key])
              for key, row in viewer.rows.items() if row.decoder is not None and not row.paused
              if (bounds := row.decoder.history.bounds())]
    if key != reference and (not ranges or max(start for start, _ in ranges) > min(end for _, end in ranges) - .25):
        viewer.audio.correction += before.get(reference, 0) - shifts.get(reference, 0)
    for row in viewer.rows.values():
        if row.user_confirmed and (row.room_id == key or key == reference):
            row.review_alignment = True
        row.match_text = "手动时间已锁定；勾选自动对齐可解除"
        row.refresh_status()
    viewer.show_comparison(key)
    viewer.generation += 1
    viewer.changed()
    viewer.render()


def _save_delay_offsets(viewer, shifts):
    reference = viewer.alignment.reference
    if reference in shifts:
        origin = shifts[reference]
        for key, row in viewer.rows.items():
            row.delay.blockSignals(True)
            row.delay.setValue(origin - shifts[key])
            row.delay.blockSignals(False)
            row.previous_delay = row.delay.value()
        # Retain the sub-display rounding residual, without applying the offset twice.
        viewer.alignment.lags = {key: value - origin + viewer.rows[key].delay.value()
                               for key, value in shifts.items()}
        viewer.stable_reference_shift = origin
    viewer.alignment.manual_locked = True
    viewer.independent_playback.clear()
    viewer.independent_shifts.clear()
    viewer.rematch_rooms.clear()
    viewer.rematch_admit.clear()


def _automatic_changed(viewer, *_args):
    for current in viewer.rows.values():
        current.alignment_hint, current.hint_pending, current.hint_searching = None, False, False
    viewer.analysis_serial += 1
    viewer.matching = False
    preserved_main = (viewer.shifts().get(viewer.alignment.reference)
                      if viewer.running and not viewer.alignment.automatic else None)
    main = viewer.rows.get(viewer.alignment.reference)
    bounds = main.decoder.history.bounds() if main is not None and main.decoder is not None else None
    if bounds is None or preserved_main is None or not bounds[0] <= viewer.live_clock() + preserved_main <= bounds[1] - .05:
        preserved_main = None
    if (not viewer.automatic.isChecked() and viewer.alignment.automatic and not viewer.alignment.manual_locked
            and (viewer.running or viewer.rendered_shifts)):
        viewer._save_delay_offsets(viewer.shifts())
    viewer.alignment.automatic = viewer.automatic.isChecked()
    if viewer.alignment.automatic:
        viewer.independent_playback.clear()
        viewer.independent_shifts.clear()
        viewer.rematch_rooms = set(viewer.rows) - {viewer.alignment.reference}
        viewer.rematch_admit = set(viewer.rematch_rooms) if viewer.running else set()
        viewer.alignment.lags = {viewer.alignment.reference: 0}
        viewer.stable_reference_shift = preserved_main
        viewer.rendered_shifts.clear()
        viewer.relative_rates.clear()
        viewer.shift_reference = None
        viewer.shift_updated = None
        for row in viewer.rows.values():
            row.match_confirmed = False
            row.match_failures = 0
            row.alignment_started = time.monotonic()
            row.next_analysis = 0.0
            row.search_failures = 0
        for row in viewer.rows.values():
            row.delay.blockSignals(True)
            row.delay.setValue(0)
            row.delay.blockSignals(False)
            row.previous_delay = 0
        if viewer.alignment.manual_locked:
            for key, row in viewer.rows.items():
                row.match_text = "主画面基准" if key == viewer.alignment.reference else "等待重新确认"
                row.refresh_status()
        viewer.alignment.manual_locked = False
        viewer.sync_waiting = False
        viewer.locked_clock = None
        viewer.offset_repositioning = False
        viewer.alignment.candidates.clear()
        viewer.generation += 1
    for row in viewer.rows.values():
        row.realign.setEnabled(viewer.alignment.automatic and row.room_id != viewer.alignment.reference)
        row.refresh_status()
    viewer.changed()


def toggle_independent(viewer, room_id):
    row = viewer.rows[room_id]
    if room_id == viewer.alignment.reference:
        return
    if room_id in viewer.independent_playback:
        viewer.independent_playback.pop(room_id)
        viewer.retry_alignment(room_id)
        row.refresh_status()
        return
    if not viewer.running or row.decoder is None or row.decoder.history.bounds() is None:
        row.independent.setChecked(False)
        viewer.notice.setText('请先开始播放，收到本路缓存后再独立播放')
        return
    before = viewer.shifts()
    viewer.stable_reference_shift = before.get(viewer.alignment.reference)
    bounds = row.decoder.history.bounds()
    position = max(bounds[0], bounds[1] - viewer.safety_buffer.target)
    viewer.independent_playback[room_id] = (time.monotonic(), position)
    viewer.independent_shifts[room_id] = position - viewer.live_clock()
    viewer.analysis_serial += 1
    viewer.matching = False
    viewer.rematch_rooms.discard(room_id)
    viewer.rematch_admit.discard(room_id)
    row.awaiting_confirmation = False
    row.mix_gain = 1.0
    row.match_text = '独立播放；点击图标恢复自动对齐'
    viewer.rendered_shifts.clear()
    row.refresh_status()


def target_shifts(viewer):
    shifts = viewer.alignment.shifts({key: 0 if viewer.alignment.automatic else row.delay.value()
                                   for key, row in viewer.rows.items() if key not in viewer.independent_shifts})
    reference = viewer.alignment.reference
    if viewer.stable_reference_shift is not None and reference in shifts:
        delay = 0 if viewer.alignment.automatic else viewer.rows[reference].delay.value()
        correction = viewer.stable_reference_shift - delay - shifts[reference]
        shifts = {key: value + correction for key, value in shifts.items()}
    for key, (started, position) in viewer.independent_playback.items():
        viewer.independent_shifts[key] = position + time.monotonic() - started - viewer.live_clock()
    shifts.update(viewer.independent_shifts)
    return shifts


def _block_alignment(viewer, room_id, reason):
    viewer.rematch_rooms.add(room_id)
    viewer.rematch_admit.add(room_id)
    main = viewer.rows.get(viewer.alignment.reference)
    main_bounds = main.decoder.history.bounds() if main is not None and main.decoder is not None else None
    main_shift = viewer.shifts().get(viewer.alignment.reference)
    if main_bounds is not None and main_shift is not None and main_bounds[0] <= viewer.live_clock() + main_shift <= main_bounds[1] - .05:
        viewer.stable_reference_shift = main_shift
    row = viewer.rows[room_id]
    bounds = row.decoder.history.bounds() if row.decoder is not None else None
    viewer.independent_shifts[room_id] = (bounds[1] - viewer.safety_buffer.target - viewer.live_clock()
                                       if bounds is not None else -viewer.safety_buffer.target)
    viewer.alignment.candidates.pop(room_id, None)
    viewer.admitted_rooms.discard(room_id)
    viewer.rendered_shifts.clear()
    row.match_confirmed = False
    row.awaiting_confirmation = False
    row.mix_gain = 0.0
    row.rematch_locally = False
    row.next_analysis = max(row.next_analysis, time.monotonic() + 2)
    row.match_failures = 0
    row.alignment_started = time.monotonic()
    row.match_text = reason + "；后台继续重新搜索"
    row.refresh_status()


def shifts(viewer):
    if viewer.independent_playback:
        return viewer.target_shifts()
    if (viewer.alignment.automatic and viewer.shift_reference == viewer.alignment.reference
            and set(viewer.rendered_shifts) == set(viewer.rows)):
        return dict(viewer.rendered_shifts)
    viewer.relative_rates.clear()
    return viewer.target_shifts()


def _smooth_shifts(viewer, wanted):
    now = time.monotonic()
    dt = max(0, min(.1, now - viewer.shift_updated)) if viewer.shift_updated is not None else 0
    viewer.shift_updated = now
    previous = viewer.rendered_shifts if viewer.shift_reference == viewer.alignment.reference else {}
    viewer.shift_reference = viewer.alignment.reference
    viewer.relative_rates = {}
    result = dict(wanted)
    for row in viewer.rows.values():
        row.correction_remaining = 0.0
    if viewer.alignment.automatic:
        for key in wanted:
            if key == viewer.alignment.reference or key not in previous or key in viewer.independent_shifts:
                continue
            delta = wanted[key] - previous[key]
            row = viewer.rows[key]
            tracking = (row.user_confirmed and row.match_confirmed and row.sync_state == "已同步" and not row.needs_epoch_samples
                        and now - row.alignment_started >= 10
                        and key not in viewer.rematch_rooms and row.decoder is not None)
            buffered = False
            if tracking and .2 < abs(delta) <= .8:
                clock = viewer.live_clock()
                bounds = row.decoder.history.bounds_at(clock + previous[key])
                buffered = (bounds is not None and bounds[0] <= clock + min(previous[key], wanted[key])
                            and clock + max(previous[key], wanted[key]) <= bounds[1] - .05)
            if abs(delta) <= .2 or buffered:
                rate = .04 if abs(delta) > .2 else .02
                step = 0 if abs(delta) <= .1 else max(-rate * dt, min(rate * dt, delta))
                result[key] = previous[key] + step
                viewer.relative_rates[key] = 1 + step / dt if dt else 1
                row.correction_remaining = abs(wanted[key] - result[key])
    viewer.rendered_shifts = result
    return result


def _update_sync_health(viewer, clock, shifts):
    now = time.monotonic()
    dt = max(0, min(.25, now - viewer.last_health_update)) if viewer.last_health_update is not None else 0
    viewer.last_health_update = now
    for key, row in viewer.rows.items():
        decoder = row.decoder
        bounds = decoder.history.bounds_at(clock + shifts[key]) if decoder is not None else None
        arrival = getattr(decoder, "last_frame_received", 0)
        fresh = not arrival or now - arrival <= 3
        target = clock + shifts[key]
        available = bounds is not None and bounds[0] <= target <= bounds[1] - .05
        if viewer.alignment.automatic and key != viewer.alignment.reference and key not in viewer.independent_shifts:
            if not available and row.match_confirmed and not row.paused and (row.sync_state == "已同步" or row.was_isolated):
                row.recheck_alignment = True
            elif fresh and available and row.recheck_alignment:
                viewer._queue_rematch(row, local=True)
                viewer.generation += 1
        visible = key == viewer.alignment.reference or (viewer.compare.isChecked() and key in viewer.comparison_panel.shown_rooms)
        if available and visible and not viewer.replay.active and key != viewer.alignment.reference and hasattr(decoder, "picture_at"):
            displayed = row.actual_position is not None and abs(row.actual_position - target) <= .2
            if displayed:
                row.picture_wait_since = None
            else:
                if row.picture_wait_since is None:
                    row.picture_wait_since = now
                # RGB preparation can briefly lag valid compressed/PCM history.
                # Do not turn a short cache miss into repeated half-second mutes.
                available = row.sync_state == "已同步" and now - row.picture_wait_since <= .3
        else:
            row.picture_wait_since = None
        known = (key == viewer.alignment.reference or not viewer.alignment.automatic
                 or key in viewer.alignment.lags and row.match_confirmed)
        known = known and not (key != viewer.alignment.reference and row.awaiting_confirmation)
        ready = (decoder is not None and not row.paused and available and known
                 and not (viewer.sync_waiting and key == viewer.alignment.reference))
        if key in viewer.independent_playback:
            ready = decoder is not None and not row.paused and available
        before = row.sync_state
        if not ready:
            row.was_isolated |= before == "已同步"
            row.sync_state = ("等待确认对齐" if row.awaiting_confirmation else "等待对齐" if not known else "暂时隔离" if row.was_isolated else "准备缓存")
            row.mix_gain = 0.0
            row.recovery_since = None
        else:
            if row.recovery_since is None:
                row.recovery_since = now
            recovered = not row.was_isolated or (now - row.recovery_since >= .5 and bounds[1] - target >= .3)
            row.sync_state = "已同步" if recovered else "恢复缓存"
            if recovered:
                if key not in viewer.independent_shifts:
                    viewer.admitted_rooms.add(key)
                row.mix_gain = min(1.0, row.mix_gain + dt / .2)
        eligible = (viewer.running and not viewer.replay.active and not row.paused
                    and not viewer.offset_repositioning and decoder is not None
                    and (known or key in viewer.independent_playback))
        exhausted = ((before == '已同步' or row.was_isolated)
                     and (bounds is None or target > bounds[1] - .05
                          or key == viewer.alignment.reference and viewer.sync_waiting))
        viewer._observe_buffer_exhaustion(row, exhausted, eligible, now)
        if before != row.sync_state:
            row.refresh_status()
        if row.sync_state == "已同步" and key == viewer.alignment.reference:
            viewer.safety_buffer.observe(key, arrival, getattr(decoder, "fps", 30))
        else:
            viewer.safety_buffer.arrivals.pop(key, None)
        confirming = key != viewer.alignment.reference and row.awaiting_confirmation and row.match_confirmed
        reviewing = key != viewer.alignment.reference and row.review_alignment and row.match_confirmed
        row.join.setVisible(confirming or reviewing)
        if confirming:
            row.join.setText("确认微调并持续同步" if not viewer.alignment.automatic else
                             "确认当前偏移" if not available else "确认对齐并加入")
            row.join.setToolTip("仅记录偏移，不改变主画面或副路播放进度；缺少同步内容时本路继续静音等待，缓存覆盖后自动加入。")
        elif reviewing:
            row.join.setText("确认当前偏移（可选）" if viewer.alignment.automatic else "确认微调并持续同步")
            row.join.setToolTip("当前继续播放；不点击不会撤销偏移或反复重新对齐。确认用于加强当前偏移的依据，不改变播放进度。")
    main = viewer.rows.get(viewer.alignment.reference)
    if (viewer.alignment.automatic and viewer.stable_reference_shift is None
            and main is not None and main.sync_state == "已同步"):
        viewer.stable_reference_shift = shifts[viewer.alignment.reference]


def confirm_alignment(viewer, room_id):
    row = viewer.rows.get(room_id)
    if row is None:
        return
    if (row.awaiting_confirmation or row.review_alignment) and not viewer.alignment.automatic:
        before = viewer.shifts()
        reference = viewer.alignment.reference
        viewer.analysis_serial += 1
        viewer.matching = False
        viewer.alignment.lags = {key: shift - before[reference] for key, shift in before.items()}
        viewer.stable_reference_shift = before[reference]
        viewer.alignment.automatic = True
        viewer.alignment.manual_locked = False
        viewer.alignment.candidates.clear()
        viewer.automatic.blockSignals(True)
        viewer.automatic.setChecked(True)
        viewer.automatic.blockSignals(False)
        for item in viewer.rows.values():
            item.delay.blockSignals(True)
            item.delay.setValue(0)
            item.delay.blockSignals(False)
            item.previous_delay = 0
        viewer.rendered_shifts.clear()
        viewer.shift_reference = None
        viewer.generation += 1
    if (row.review_alignment and row.user_confirmed and row.match_confirmed
            and not row.awaiting_confirmation and row.sync_state == "已同步"):
        # A later human review reinforces the displayed offset, without
        # seeking the clock or restoring the initial admission reserve.
        shifts = viewer.shifts()
        lag = shifts[room_id] - shifts[viewer.alignment.reference]
        viewer.alignment.lags[room_id] = lag
        viewer.alignment.candidates.pop(room_id, None)
        row.user_confirmed_lag = lag
        row.review_alignment = False
        row.match_failures = 0
        row.next_analysis = time.monotonic() + 2
        viewer.analysis_serial += 1
        viewer.matching = False
        viewer.generation += 1
        row.match_text = "用户已确认当前偏移，继续自动同步"
        viewer.render()
    else:
        viewer.admit_pending(room_id)
    viewer.changed()


def admit_pending(viewer, room_id, automatic=False):
    row = viewer.rows.get(room_id)
    main = viewer.rows.get(viewer.alignment.reference)
    if (row is None or main is None or row.decoder is None or main.decoder is None
            or not viewer.alignment.automatic or room_id not in viewer.alignment.lags or row.paused):
        return
    clock, shifts = viewer.live_clock(), viewer.shifts()
    target = clock + shifts[room_id]
    bounds = row.decoder.history.bounds_at(target)
    if row.needs_epoch_samples and bounds is not None and target < bounds[0]:
        viewer._queue_rematch(row)
        viewer.generation += 1
        row.match_text = "候选偏移对应的内容早于副路缓存，继续重新匹配"
        row.refresh_status()
        return False
    if not automatic:
        row.user_confirmed = True
        row.user_confirmed_lag = viewer.alignment.lags[room_id]
        row.review_alignment = False
        row.awaiting_confirmation = False
        row.recheck_alignment = False
        viewer.rematch_rooms.discard(room_id)
        viewer.rematch_admit.discard(room_id)
        row.rematch_locally = False
        viewer.analysis_serial += 1
        viewer.matching = False
    if bounds is None or not bounds[0] <= target <= bounds[1] - .05:
        row.match_text = (cache_gap(row.decoder.history, target) or "正在准备目标画面") + "；本路静音，主画面继续播放"
        row.mix_gain = 0.0
        row.refresh_status()
        viewer.render()
        return False
    row.needs_epoch_samples = False
    viewer.generation += 1
    viewer.render()
    return True


def analyse(viewer):
    if not viewer.running or not viewer.alignment.automatic or viewer.matching:
        return
    row = viewer.rows.get(viewer.alignment.reference)
    if row is None or row.decoder is None:
        return
    if row.reset_pending:
        return
    reference = row.decoder.history.snapshots()
    others = {key: value.decoder.history.snapshots() for key, value in viewer.rows.items()
              if key != row.room_id and not value.paused and value.decoder is not None
              and not value.reset_pending and key not in viewer.independent_playback
              and time.monotonic() >= value.next_analysis}
    if not others:
        return
    keys = [key for key in others if viewer.rows[key].hint_pending] or list(others)
    key = keys[viewer.analysis_cursor % len(keys)]
    viewer.analysis_cursor += 1
    others = {key: others[key]}
    details = {}
    region_frames = {}
    for key, value in viewer.rows.items():
        if value.decoder is not None:
            with value.decoder.history.lock:
                details[key] = (list(getattr(value.decoder.history, "details", ()))
                                if value.decoder.history.origin is not None else [])
            if hasattr(value.decoder, 'region_frames'):
                with value.decoder.lock:
                    region_frames[key] = list(value.decoder.region_frames)
    known = {key: lag for key, lag in viewer.alignment.lags.items() if key not in viewer.independent_playback}
    hints = {key: viewer.rows[key].alignment_hint for key in others if viewer.rows[key].hint_searching}
    for key in hints:
        known.pop(key, None)
    for key in viewer.rematch_rooms:
        if not viewer.rows[key].rematch_locally:
            known.pop(key, None)
    confirmed_peers = {key for key, value in viewer.rows.items()
                       if value.match_confirmed and value.sync_state == "已同步"}
    tracking_rooms = {key for key, value in viewer.rows.items()
                      if value.match_confirmed and key in known and key not in viewer.rematch_rooms}
    reacquiring_rooms = {key for key, value in viewer.rows.items() if value.needs_epoch_samples}
    reference_id = row.room_id
    cached_regions = {key: value.auto_regions[1] for key, value in viewer.rows.items()
                      if getattr(value, 'auto_regions', (None, ()))[0] == reference_id}
    clock, shifts = viewer.live_clock(), viewer.shifts()
    positions = {key: clock + shifts[key] for key in details}
    has_picture = viewer.canvas.frame_key is not None and not viewer.replay.active
    if has_picture:
        positions[reference_id] = viewer.canvas.frame_key
    preview_rooms = set()
    for key in viewer.comparison_panel.shown_rooms if viewer.compare.isChecked() and not viewer.replay.active else ():
        canvas = viewer.comparison_panel.cards[key][2]
        if canvas.preview_only:
            preview_rooms.add(key)
        elif canvas.frame_key is not None:
            positions[key] = canvas.frame_key
    epoch, bridge = viewer.generation, viewer.results
    viewer.analysis_serial += 1
    serial = viewer.analysis_serial
    for key in hints:
        a, b = details.get(reference_id, []), details.get(key, [])
        ready = min(len(a), len(b)) >= 40 if a and b else min(len(reference), len(others[key])) >= 8
        if viewer.rows[key].hint_pending:
            viewer.rows[key].hint_serial = serial if ready else None
    viewer.matching = True

    def calculate():
        results = {}
        for key, samples in others.items():
            hint_options = {'hint': hints[key]} if key in hints else {}
            reference_details = details.get(reference_id, [])
            other_details = details.get(key, [])
            if key in tracking_rooms and has_picture:
                reference_details = [sample for sample in reference_details if sample.time <= positions[reference_id]]
            if reference_details and other_details:
                candidate = (refine_match(Match(known[key], .9, "跟踪已确认偏移"), reference_details, other_details)
                             if key in known else Match(None, 0, "首次匹配"))
                if candidate.lag is None:
                    candidate = match_scenes(reference_details[::4], other_details[::4], **hint_options)
            else:
                candidate = match_scenes(reference, samples, **hint_options)
            if reference_details and other_details:
                if candidate.lag is None and not candidate.candidates and key in known:
                    candidate = Match(known[key], .9, "复核已确认偏移")
                if not candidate.refined:
                    candidate = refine_match(candidate, reference_details, other_details, **hint_options)
                if candidate.lag is not None and key in known and has_picture and key not in preview_rooms:
                    shown_reference = [s for s in details[reference_id] if s.time <= positions[reference_id]]
                    shown_other = [s for s in details[key] if s.time <= positions[key]]
                    verified = refine_match(Match(known[key], .9, "播放进度复核"), shown_reference, shown_other)
                    if verified.lag is not None:
                        candidate = replace(candidate, playback_error=verified.lag - (positions[key] - positions[reference_id]))
                peers = [peer for peer in known if peer not in (key, reference_id)
                         and peer in confirmed_peers and details.get(peer)]
                if candidate.lag is not None and peers:
                    peer = peers[0]
                    peer_details = details[peer]
                    if key in tracking_rooms and has_picture:
                        peer_details = [sample for sample in peer_details if sample.time <= positions[peer]]
                    check = refine_match(Match(candidate.lag - known[peer], .9, "三路一致性检查"), peer_details, other_details)
                    if check.lag is None and key not in known:
                        candidate = Match(None, 0, "三路共同动态画面不足，继续复核已确认偏移")
                    elif check.lag is not None and abs(check.lag - (candidate.lag - known[peer])) > .2:
                        candidate = Match(None, 0, "三路时间差不一致，保持已确认偏移")
            if (key in reacquiring_rooms and candidate.refined and reference_details and other_details
                    and min(reference_details[-1].time - reference_details[0].time,
                            other_details[-1].time - other_details[0].time) < 4):
                candidate = Match(None, 0, "恢复精校正需要连续至少 4 秒比赛画面，继续收集")
            if candidate.lag is None and region_frames.get(reference_id) and region_frames.get(key):
                regional = match_regions(region_frames[reference_id], region_frames[key],
                                         cached_regions.get(key, ()), known.get(key), **hint_options)
                if regional.lag is not None:
                    peers = [peer for peer in known if peer not in (key, reference_id)
                             and peer in confirmed_peers and region_frames.get(peer)]
                    if peers:
                        peer = peers[0]
                        checked = match_regions(region_frames[peer], region_frames[key],
                                                expected=regional.lag - known[peer])
                        if checked.lag is None or abs(checked.lag - (regional.lag - known[peer])) > .2:
                            regional = Match(None, 0, '共同区域的三路时间差尚未一致')
                    candidate = regional
            results[key] = candidate
        return results

    def run():
        try:
            results = calculate()
        except Exception as error:
            results = {room: Match(None, 0, f"匹配异常：{type(error).__name__}: {error}") for room in others}
        bridge.completed.emit(serial, epoch, results)
    threading.Thread(target=run, name="match-sync-analysis", daemon=True).start()


def _analysis_finished(viewer, serial, epoch, results):
    if serial == viewer.analysis_serial:
        if viewer.running and epoch == viewer.generation and viewer.alignment.automatic:
            for key, match in results.items():
                row = viewer.rows.get(key)
                if row is not None and row.hint_pending and row.hint_serial == serial:
                    if not match.reason.startswith('匹配异常：'):
                        row.hint_pending = False
                    logging.getLogger(_VIEWER_LOGGER_NAME).info('Alignment hint search: slot=%s lag=%s confidence=%.3f refined=%s reason=%s',
                        tuple(viewer.rows).index(key), match.lag, match.confidence, match.refined, match.reason)
        viewer.results.matched.emit(epoch, results)


def _matched(viewer, epoch, results):
    viewer.matching = False
    if not viewer.running or epoch != viewer.generation or not viewer.alignment.automatic:
        return
    for room_id, match in results.items():
        row = viewer.rows.get(room_id)
        if row is None:
            continue
        if match.regions and match.lag is not None and match.confidence >= .65:
            row.auto_regions = (viewer.alignment.reference, match.regions)
        if match.reason.startswith("匹配异常："):
            row.match_text = match.reason + "；稍后自动重试"
            row.next_analysis = time.monotonic() + 2
            row.refresh_status()
            continue
        if not row.match_confirmed and (match.lag is None or match.confidence < .65):
            # Once detailed matching has enough history, repeated failed
            # searches need fresh evidence, not another scan every 500 ms.
            details = getattr(getattr(row.decoder, 'history', None), 'details', ())
            if len(details) >= 80:
                row.search_failures = min(3, row.search_failures + 1)
                row.next_analysis = time.monotonic() + 2 ** row.search_failures
        else:
            row.search_failures = 0
        if match.lag is not None and match.confidence >= .65 and abs(match.lag) > 15:
            changed = (row.user_confirmed and row.user_confirmed_lag is not None and abs(match.lag - row.user_confirmed_lag) > 3
                       and not row.needs_epoch_samples)
            if changed:
                row.match_text = "候选与已确认偏移相差较大；保留当前进度，请用对照微调复核"
                row.next_analysis = time.monotonic() + 2
                row.refresh_status()
                continue
            previous, count, observed, evidence = row.large_candidate or (match.lag, 0, -float('inf'), -float('inf'))
            now = time.monotonic()
            if not match.refined or abs(previous - match.lag) > .15:
                count, observed, evidence = 0, -float('inf'), -float('inf')
            fresh = match.evidence_end is None or match.evidence_end - evidence >= 1
            if match.refined and fresh and (now - observed >= 2 or row.needs_epoch_samples and row.user_confirmed):
                count, observed = count + 1, now
                evidence = match.evidence_end if match.evidence_end is not None else evidence
            row.large_candidate = (match.lag, count, observed, evidence)
            if count < (2 if row.needs_epoch_samples and row.user_confirmed else 3):
                row.match_text = "大偏差候选正在连续精校验；保持当前播放，后台复核"
                row.next_analysis = now + 2
                row.refresh_status()
                continue
            viewer.alignment.candidates[room_id] = (match.lag, count - 1)
        else:
            row.large_candidate = None
        main = viewer.rows.get(viewer.alignment.reference)
        if match.lag is not None and match.confidence >= .65 and main is not None and main.decoder is not None and row.decoder is not None:
            main_bounds, other_bounds = main.decoder.history.bounds(), row.decoder.history.bounds()
            main_shift = viewer.shifts()[main.room_id]
            if viewer.stable_reference_shift is None:
                main_shift = min(main_shift, -viewer.alignment.buffer_seconds - max(0, match.lag))
            target = viewer.live_clock() + main_shift
            if viewer.stable_reference_shift is None and main_bounds and other_bounds and not (
                    main_bounds[0] <= target <= main_bounds[1] - .05
                    and other_bounds[0] <= target + match.lag <= other_bounds[1] - .05):
                row.match_text = "正在积累对齐所需缓存；主画面继续播放"
                row.refresh_status()
                continue
        was_confirmed = row.match_confirmed
        previous_lag = viewer.alignment.lags.get(room_id)
        position = viewer.live_clock() + viewer.shifts().get(room_id, 0)
        bounds = row.decoder.history.bounds_at(position) if row.decoder is not None else None
        buffered = bounds is not None and bounds[0] <= position <= bounds[1] - .05
        established = was_confirmed and previous_lag is not None and room_id not in viewer.independent_shifts
        inconclusive = match.lag is None or match.confidence < .65 or not match.refined
        if established and buffered and inconclusive and "三路时间差不一致" not in match.reason:
            viewer.alignment.candidates.pop(room_id, None)
            row.match_failures = 0
            row.next_analysis = time.monotonic() + 2
            row.match_text = match.reason + "；沿用已确认偏移，继续缓存播放"
            row.refresh_status()
            continue
        applied = viewer.alignment.accept(room_id, match)
        if applied and match.refined:
            row.hint_searching = False
        if match.lag is None:
            row.large_candidate = None
            insufficient = any(reason in match.reason for reason in (
                "变化不足", "正在收集", "需要连续", "共同动态画面不足", "共同比赛区域不足"))
            if not insufficient:
                row.match_failures += 1
            if row.match_failures >= 3 or (not row.match_confirmed and time.monotonic() - row.alignment_started >= 10):
                row.match_confirmed = False
                viewer._block_alignment(room_id, "无法确认共同比赛画面；本路已静音，可重新对齐，或放大计时器后手动微调")
                continue
            row.match_text = match.reason
        elif match.confidence < .65:
            row.match_failures += 1
            if row.match_failures >= 3:
                row.match_confirmed = False
                viewer._block_alignment(room_id, "无法确认共同比赛画面；本路已静音，可重新对齐，或放大计时器后手动微调")
                continue
            row.match_text = "匹配置信度不足；保持已确认偏移，继续收集画面"
        else:
            row.match_failures = 0
            if match.refined:
                if applied:
                    if room_id in viewer.independent_shifts:
                        viewer.independent_shifts.pop(room_id)
                        viewer.rendered_shifts.clear()
                        viewer.shift_reference = None
                    # playback_error describes the old displayed position;
                    # it is not evidence against the correction just applied.
                    row.match_confirmed = (was_confirmed and row.user_confirmed) or match.playback_error is None or abs(match.playback_error) <= .2
                    if row.user_confirmed and (not was_confirmed or row.user_confirmed_lag is None
                                               or abs(viewer.alignment.lags[room_id] - row.user_confirmed_lag) > .2):
                        row.review_alignment = True
                    if row.match_confirmed:
                        row.awaiting_confirmation = not row.user_confirmed
                        if room_id in viewer.rematch_admit:
                            viewer.rendered_shifts.clear()
                            viewer.shift_reference = None
                        if room_id not in viewer.rematch_admit or viewer.admit_pending(room_id, automatic=True):
                            viewer.rematch_rooms.discard(room_id)
                            viewer.rematch_admit.discard(room_id)
                            row.rematch_locally = False
                        else:
                            row.rematch_locally = row.match_confirmed
                            continue
                verified = match.playback_error is not None and abs(match.playback_error) <= .15
                status = "播放进度已复核" if applied and verified else "已应用精校正，待复核" if applied else "正在确认精校正"
                actual = viewer.compare.isChecked() and room_id in viewer.comparison_panel.shown_rooms
                error = f"，{'实际画面' if actual else '预计播放'}误差约 {abs(match.playback_error):.2f} 秒" if match.playback_error is not None else ""
                row.match_text = f"{status}：相对主画面 {match.lag:+.2f} 秒{error}"
                if row.awaiting_confirmation:
                    row.match_text += "；请打开对照检查并确认对齐"
            else:
                if applied:
                    row.match_confirmed = False
                row.match_text = (f"{'已应用粗匹配，待精校正' if applied else '正在确认'}：相对主画面 {match.lag:+.1f} 秒，"
                                  f"置信度 {match.confidence:.0%}")
        row.refresh_status()
