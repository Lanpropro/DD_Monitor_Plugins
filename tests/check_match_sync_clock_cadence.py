"""Check clock and frame deadlines with numbers and empty QObjects; no media playback."""
import argparse
import os
from pathlib import Path
import sys
from unittest.mock import patch


def check(app, host, module):
    pump = module.AudioPump(host)
    class Sink:
        def processedUSecs(self):
            return 0
        def reset(self):
            pass
        def start(self):
            return object()
        def stop(self):
            pass
        def deleteLater(self):
            pass
    class ForbiddenLock:
        def acquire(self, **_kwargs):
            raise AssertionError('Interpolated GUI clock must not wait on audio mixing')
    try:
        pump.thread = pump.sink = object()
        pump.anchor, pump.played, pump.written = 100., 480, 1920
        segments = ((0, 960, 0., 1.), (960, 1920, .02, .98))
        pump.clock_segments.extend(segments)
        pump.clock_observation = (10., 480, 1920, 100., segments)
        pump.clock_floor = None
        with patch.object(module.time, 'perf_counter', return_value=10.005):
            assert abs(pump.clock() - 100.015) < 1e-8, 'Clock must advance between 10ms device polls'
        with patch.object(pump, 'lock', ForbiddenLock()):
            with patch.object(module.time, 'perf_counter', return_value=10.015):
                assert abs(pump.clock() - 100.0249) < 1e-8, 'Map across the actual queued rate segments'
        with patch.object(module.time, 'perf_counter', return_value=10.2):
            assert abs(pump.clock() - 100.0396) < 1e-8, 'Never predict past submitted audio'
            pump.clock_observation = (10.2, 0, 1920, 100., segments)
            assert abs(pump.clock() - 100.0396) < 1e-8, 'A coarse device report must not reverse playback'
            pump.correction = -4.5
            assert abs(pump.clock() - 95.5396) < 1e-8, 'Explicit correction remains authoritative'
        pump.thread = None
        pump.sink = Sink()
        pump.correction = 0
        pump.flush_clock = 321.25
        module.AudioFeed(pump, None, None).flush()
        assert pump.clock_observation is None and pump.clock_floor is None
        assert pump.clock() == 321.25, 'Seek/flush resets interpolation and accepts the requested anchor'
        pump.clock_observation = (0., 0, 1, 0., ())
        pump.clock_floor = 1
        pump.stop()
        assert pump.clock_observation is None and pump.clock_floor is None
        for fps in (24., 30., 59.94, 60., 120.):
            normal = max(4, int(500 / fps))
            assert module.picture_poll_interval(100., None, fps) == normal
            for phase in (0., .2, .5, .9, .99):
                delay = module.picture_poll_interval(100 + phase / fps, 100., fps)
                assert 1 <= delay <= normal
        assert module.picture_poll_interval(100.016, 100., 60) == 1
        assert module.picture_poll_interval(100.010, 100., 60, elapsed=.006) == 1
        assert module.picture_poll_interval(100.050, 100., 60) == 1
        assert module.picture_poll_interval(100.010, 100., 60, rate=.98) == 7
        return dict(passed=True, media_playback=False, continuous_device_clock=True,
                    queued_audio_bound=True, rate_segments_preserved=True, nonblocking_clock=True,
                    seek_and_stop_reset=True, next_frame_deadlines=True)
    finally:
        pump.thread = None
        pump.sink = pump.device = None
        pump.stop()
        pump.deleteLater()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
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
