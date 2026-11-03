"""Prepared-frame bounds, capture handoff and source cancellation, offline."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    import av
    import numpy as np
    backend = import_module(module.__package__ + '.preview_frames')
    pixels = np.zeros((180, 320, 3), dtype=np.uint8)
    pixels[:, :, 0] = 180
    frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
    first = SimpleNamespace(history=object())
    second = SimpleNamespace(history=object())
    prepared = backend.PreviewFrames(byte_limit=4096)
    entered, released = threading.Event(), threading.Event()
    convert = backend.image_from_frame
    def hold(*args, **kwargs):
        entered.set()
        assert released.wait(3)
        return convert(*args, **kwargs)
    def wait_for(count):
        deadline = time.monotonic() + 3
        while prepared.produced < count and time.monotonic() < deadline:
            time.sleep(.005)
        assert prepared.produced >= count
    try:
        prepared.select(first)
        with prepared.lock:
            assert not prepared.submit(first, first.history, frame, 0.), 'Capture must not wait for the cache lock'
        with patch.object(backend, 'image_from_frame', side_effect=hold):
            assert prepared.submit(first, first.history, frame, 0.)
            assert entered.wait(2)
            for index in range(1, 100):
                prepared.submit(first, first.history, frame, index / 8)
            assert prepared.snapshot(time.perf_counter())['queued'] == 1
            assert prepared.dropped >= 98
            prepared.select(second)
            assert prepared.pending is None
            released.set()
            deadline = time.monotonic() + 2
            while prepared.thread_cpu_s == 0 and time.monotonic() < deadline:
                time.sleep(.005)
            assert prepared.produced == 0, 'Discard a conversion completed after changing sources'
        assert not prepared.submit(first, first.history, frame, 20.), 'Reject a late callback from the old source'
        for index in range(8):
            before = prepared.produced
            assert prepared.submit(second, second.history, frame, float(index))
            wait_for(before + 1)
        snapshot = prepared.snapshot(time.perf_counter())
        assert 0 < snapshot['prepared_bytes'] <= 4096 and snapshot['prepared_count'] < 8
        result = prepared.get(second.history, (107.1, 7.1))
        assert result is not None and abs(result[0] - 107.) < .0001
        assert result[1].width() == 240 and result[1].pixelColor(100, 60).red() > 160
        assert prepared.get(first.history, (107.1, 7.1)) is None
        assert prepared.get(second.history, (109., 9.)) is None, 'Do not pretend an old picture covers a gap'
        assert prepared.get(second.history, (100., 0.)) is None, 'Evicted frames must stay unavailable'
        before = prepared.produced
        assert prepared.submit(second, second.history, frame, 800.)
        wait_for(before + 1)
        assert len(prepared.frames) == 1, 'Expire frames outside the replay retention'
        replacement = object()
        second.history = replacement
        before = prepared.produced
        assert prepared.submit(second, replacement, frame, 0.)
        wait_for(before + 1)
        assert prepared.get(replacement, (200., 0.)) is not None
        prepared.select(None)
        assert not prepared.frames and prepared.byte_size == 0
        return dict(passed=True, bounded_single_slot=True, nonblocking_capture=True,
                    source_generation=True, byte_and_time_limits=True, jpeg_roundtrip=True,
                    gaps_and_reconnect=True, generated_fixture=True, live_performance=False)
    finally:
        released.set()
        prepared.stop()
        assert not prepared.thread.is_alive()
        assert prepared.pending is None and not prepared.frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    try:
        print(check(app, QWidget(), import_module('plugins.match_sync.viewer')))
    finally:
        app.shutdown()


if __name__ == '__main__':
    main()
