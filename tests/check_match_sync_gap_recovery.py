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
            return dict(passed=True, media_playback=False, pts_gap_resumes=True,
                        retained_tail_played=True, starvation_still_waits=True,
                        insufficient_next_reserve_waits=True, offsets_preserved=True,
                        pause_preserved=True, contiguous_reserve_reported=True)
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
