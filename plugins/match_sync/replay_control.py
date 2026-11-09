"""Replay control. Session state remains on the Viewer facade."""
from __future__ import annotations

import time

def replay_histories(viewer):
    primary = viewer.alignment.reference or viewer.main.currentData()
    return {key: row.decoder.history for key, row in viewer.rows.items()
            if key == primary and row.decoder is not None and not row.paused}


def live_clock(viewer):
    if not viewer.replay.active:
        return viewer.audio.clock()
    now = time.monotonic()
    viewer._replay_live_anchor += (now - viewer._replay_live_updated) * viewer.playback_rate
    viewer._replay_live_updated = now
    return viewer._replay_live_anchor + viewer.audio.correction


def seek_replay(viewer, stamp):
    now = time.monotonic()
    live_clock = viewer.live_clock()
    was_replaying = viewer.replay.active
    if viewer.replay.seek(stamp, now, viewer.replay_histories()):
        viewer._stop_recording()
        if not was_replaying:
            viewer._replay_live_anchor = live_clock - viewer.audio.correction
            viewer._replay_live_updated = now
        viewer.canvas.set_frame(None)
        viewer._rebuild_replay_chat(now - viewer.replay.delay)
        viewer.audio.flush()
        for floating in viewer.floating_crops.values():
            floating.set_frame(None)
        viewer.replay_bar.refresh(force=True)


def return_live(viewer):
    if viewer.replay.active:
        viewer._stop_recording()
    clock = viewer.live_clock()
    viewer.replay.live()
    viewer.canvas.set_frame(None)
    viewer._rebuild_replay_chat(time.monotonic(), live=True)
    viewer.audio.flush(clock)
    for floating in viewer.floating_crops.values():
        floating.set_frame(None)
    viewer.replay_bar.refresh(force=True)
