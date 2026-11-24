"""PTS interval recovery with timestamp descriptors only; no media, network or sound output."""
import argparse
from collections import deque
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtGui import QImage
    from ddm import plugins
    video = import_module(module.__package__ + '.video')
    diagnostic = import_module(module.__package__ + '.diagnostics')
    history = video.VideoHistory(audio_factory=lambda seconds: SimpleNamespace(end=0, capacity=48000))
    history.origin = 8600.
    history.frames.extend((stamp, stamp - history.origin) for stamp in
                          (8646.094, 8696.044, 8696.094, 8700.261, 8707.711))
    history.intervals.extend(((8646.094, 8696.094), (8700.261, 8707.711)))
    with tempfile.TemporaryDirectory() as directory:
        host.rooms, host.settings = [], {}
        context = plugins.PluginContext(plugins.PluginManager(window=host, plugins_dir=directory), 'gap_recovery_check')
        viewer = module.Viewer(context, {})
        try:
            assert viewer.add_room('1') and viewer.add_room('2')
            main = viewer.rows['1']
            main.decoder = SimpleNamespace(history=history, last_frame_received=100., fps=60., stop=lambda: None)
            main.was_isolated = True
            viewer.stable_reference_shift = -6.
            viewer.alignment.lags = {'1': 0., '2': -2.}
            shifts = {'1': -6., '2': -8.}
            clock = 8696.044 - shifts['1']
            viewer.locked_clock, viewer.sync_waiting = clock, True
            with patch.object(module.time, 'monotonic', return_value=100.):
                recovered = viewer._recover_clock(clock, shifts)
            assert abs(recovered + shifts['1'] - 8700.261) < 1e-8
            assert not viewer.sync_waiting and abs(viewer.audio.correction - (recovered - clock)) < 1e-8
            assert viewer.stable_reference_shift == -6. and viewer.alignment.lags == {'1': 0., '2': -2.}
            assert viewer.playback_probe.clock_gaps[-1][1:] == (8696.044, 8700.261)
            # A retained tail still plays before advancing across the hole.
            viewer.audio.correction = 0.
            clock = 8695.9 - shifts['1']
            viewer.locked_clock, viewer.sync_waiting = clock, True
            with patch.object(module.time, 'monotonic', return_value=100.):
                assert viewer._recover_clock(clock, shifts) == clock
            assert not viewer.sync_waiting and viewer.audio.correction == 0
            # No later interval means genuine starvation must keep waiting.
            history.intervals = deque(((8646.094, 8696.094),))
            history.frames.pop()
            history.frames.pop()
            clock = 8696.044 - shifts['1']
            viewer.locked_clock, viewer.sync_waiting = clock, True
            with patch.object(module.time, 'monotonic', return_value=100.):
                assert viewer._recover_clock(clock, shifts) == clock
            assert viewer.sync_waiting and viewer.audio.correction == 0
            # A new interval needs enough retained duration for the existing
            # 300ms resume rule; do not count missing PTS time as that reserve.
            history.frames.extend(((8700.261, 100.261), (8700.461, 100.461)))
            history.intervals.append((8700.261, 8700.461))
            with patch.object(module.time, 'monotonic', return_value=100.):
                assert viewer._recover_clock(clock, shifts) == clock
            assert viewer.sync_waiting
            # Manual out-of-range offsets and an explicit pause stay untouched.
            main.was_isolated = False
            viewer.sync_waiting = False
            with patch.object(module.time, 'monotonic', return_value=100.):
                assert viewer._recover_clock(8000., shifts) == 8000.
            main.paused = True
            with patch.object(module.time, 'monotonic', return_value=100.):
                assert viewer._recover_clock(clock, shifts) == clock
            history.frames[-1] = (8707.711, 107.711)
            history.intervals[-1] = (8700.261, 8707.711)
            # Diagnostic descriptors expose span versus continuous reserve.
            decoder = SimpleNamespace(history=history,
                pictures=SimpleNamespace(lock=SimpleNamespace(acquire=lambda **kw: False)), fps=60.)
            state = diagnostic.picture_state(decoder, 8696.044, 8696.044, True)
            assert state['encoded_ahead_s'] == 11.667 and state['contiguous_ahead_s'] == .05
            assert state['following_bounds'] == (8700.261, 8707.711)
            # CCTV-5+ stayed 770 ms from the old tail while the replacement
            # connection received frames for minutes. Refill can no longer
            # grow that tail, but there is enough data to play it and move on.
            old = video.VideoHistory(audio_factory=lambda seconds: SimpleNamespace(end=0, capacity=48000))
            replacement = video.VideoHistory(audio_factory=lambda seconds: SimpleNamespace(end=0, capacity=48000))
            for epoch, interval in ((old, (87772.471, 87804.791)),
                                    (replacement, (87810.071, 87810.391))):
                epoch.intervals.append(interval)
                epoch.frames.extend((stamp, stamp - interval[0]) for stamp in interval)
            replacement.previous = old
            decoder = module.Decoder('offline-same-target')
            try:
                decoder.history = replacement
                ready = (87772.471, QImage(16, 9, QImage.Format_RGB32))
                with patch.object(old, 'frame_at', wraps=old.frame_at) as lookup, \
                        patch.object(decoder.pictures, 'get', return_value=ready) as decode:
                    assert viewer.canvas.show_at(decoder, 87773.) == ready
                    lookup.assert_called_once_with(87773.)
                    decode.assert_called_once_with(old, old.frames[0], exact=False)
                    assert viewer.canvas.requested_frame_key == 87772.471
                with patch.object(decoder.pictures, 'get') as decode:
                    assert viewer.canvas.show_at(decoder, 87807.) is None
                    assert viewer.canvas.requested_frame_key is None
                    assert decoder.playback_timestamp == 87773.
                    assert decoder.picture_at(87807.) is None
                    assert decoder.playback_timestamp == 87807.
                    decode.assert_not_called()
            finally:
                decoder.stop()
            main.paused = False
            main.was_isolated = True
            main.decoder.history = replacement
            viewer.audio.correction = 0.
            held = 87804.021 - shifts['1']
            viewer.locked_clock, viewer.sync_waiting = held, True
            with patch.object(module.time, 'monotonic', return_value=100.):
                assert viewer._recover_clock(held, shifts) == held
                assert not viewer.sync_waiting, 'Recovered input must release a held old tail'
                at_tail = 87804.791 - .05 - shifts['1']
                viewer.sync_waiting, viewer.locked_clock = True, at_tail
                recovered = viewer._recover_clock(at_tail, shifts)
            assert abs(recovered + shifts['1'] - 87810.071) < 1e-8
            assert replacement.previous is old, 'Retained playback history must remain available'
            old.frames.append((87806.01, 87806.01 - 87772.471))
            old.intervals.append((87806.01, 87806.01))
            assert replacement.bounds_at(87804.891, minimum_duration=.3) == (87810.071, 87810.391), (
                'An unusable fragment in the retained epoch must not hide the new epoch')
            # Real follow-up evidence: the immediately following interval is
            # a singleton, followed by a normal continuous interval.
            fragmented = video.VideoHistory(audio_factory=lambda seconds: SimpleNamespace(end=0, capacity=48000))
            fragmented.origin = 10200.
            intervals = ((10249.937, 10251.137), (10254.104, 10254.104), (10261.537, 10281.437))
            fragmented.intervals.extend(intervals)
            fragmented.frames.extend((stamp, stamp - fragmented.origin) for bounds in intervals for stamp in bounds)
            assert fragmented.bounds_at(10251.237) == intervals[1]
            assert fragmented.bounds_at(10251.237, minimum_duration=.3) == intervals[2]
            assert fragmented.bounds_at(10251.237, minimum_duration=30.) is None
            fragmented.previous = history
            assert fragmented.bounds_at(8696.044, minimum_duration=.3) == (8646.094, 8696.094)
            fragmented.previous = None
            main.paused = False
            main.decoder.history = fragmented
            main.was_isolated = True
            viewer.sync_waiting = True
            viewer.audio.correction = 0.
            clock = 10251.087 - shifts['1']
            viewer.locked_clock = clock
            with patch.object(module.time, 'monotonic', return_value=100.):
                recovered = viewer._recover_clock(clock, shifts)
            assert abs(recovered + shifts['1'] - intervals[2][0]) < 1e-8, 'A singleton must not block a later ready interval'
            # Startup cannot wait forever for the first short fragment to grow.
            main.was_isolated = False
            viewer.stable_reference_shift = None
            viewer.sync_waiting = False
            viewer.audio.correction = 0.
            clock = 10244.463 - shifts['1']
            with patch.object(module.time, 'monotonic', return_value=100.):
                recovered = viewer._recover_clock(clock, shifts)
            assert intervals[2][0] <= recovered + shifts['1'] <= intervals[2][1] - viewer.safety_buffer.target
            main.was_isolated = False
            viewer.stable_reference_shift = shifts['1']
            viewer.sync_waiting = False
            with patch.object(fragmented, 'bounds_at', wraps=fragmented.bounds_at) as bounds, \
                    patch.object(module.time, 'monotonic', return_value=100.):
                clock = 10265. - shifts['1']
                assert viewer._recover_clock(clock, shifts) == clock
                bounds.assert_called_once_with(10265.)
            # Rebuilding the main source must re-establish automatic startup,
            # while a manually locked timeline retains its chosen offsets.
            viewer.running = True
            viewer.stable_reference_shift, viewer.locked_clock, viewer.sync_waiting = -56., clock, True
            with patch.object(viewer, '_stop_row'), patch.object(viewer, '_start_row'):
                viewer._reload_picture()
            assert viewer.stable_reference_shift is None and viewer.locked_clock is None and not viewer.sync_waiting
            viewer.alignment.manual_locked = True
            viewer.alignment.lags = {'1': 0., '2': -2.}
            viewer.stable_reference_shift = -56.
            with patch.object(viewer, '_stop_row'), patch.object(viewer, '_start_row'):
                viewer._reload_picture()
            assert viewer.stable_reference_shift == -56. and viewer.alignment.lags == {'1': 0., '2': -2.}
            return dict(passed=True, media_playback=False, pts_gap_resumes=True,
                        retained_tail_played=True, starvation_still_waits=True,
                        early_wait_after_reconnect_resumes=True,
                        insufficient_next_reserve_waits=True, offsets_preserved=True,
                        pause_preserved=True, contiguous_reserve_reported=True,
                        singleton_does_not_block=True, fragmented_startup=True,
                        main_reload_reanchors=True, manual_lock_preserved=True)
        finally:
            main.decoder = None
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['DDM_MATCH_DIAGNOSTICS'] = '0'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    host = QWidget()
    try:
        print(check(app, host, module))
    finally:
        host.close()
        app.processEvents()
        app.shutdown()


if __name__ == '__main__':
    main()
