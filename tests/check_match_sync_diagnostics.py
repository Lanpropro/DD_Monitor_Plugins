"""Identify a short GUI stall separately from encoded/RGB frame availability."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def block_gui():
    time.sleep(.3)


def check(app, host, module):
    from PySide6.QtCore import QEvent, Qt, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from ddm import plugins
    diagnostic = import_module(module.__package__ + '.diagnostics')
    video = import_module(module.__package__ + '.video')
    with tempfile.TemporaryDirectory(prefix='ddm-playback-diag-') as directory:
        host.settings, host.rooms = {}, []
        with patch.object(module.config_module, 'REPO', directory):
            viewer = module.Viewer(plugins.PluginContext(
                plugins.PluginManager(window=host, plugins_dir=directory), 'playback_diag'), {})
        probe = viewer.playback_probe
        reader = video.PictureReader()
        history = video.VideoHistory(seconds=60)
        history.frames.extend([(100., 0.), (110., 10.)])
        history.received_frames = 301
        image = QImage(320, 180, QImage.Format_ARGB32)
        image.fill(Qt.red)
        reader.images[102.] = image
        decoder = SimpleNamespace(history=history, pictures=reader,
                                  source_url='https://secret.invalid/?token=DO_NOT_LOG',
                                  seed={'cookie': 'DO_NOT_LOG'})
        try:
            probe.start()
            viewer.eventFilter(viewer.canvas, QEvent(QEvent.Enter))
            probe.observe(decoder, 102., 102., False)
            viewer.canvas.set_frame((102., image))
            # Exercise the actual paintEvent wrapper without showing a test window.
            viewer.canvas.grab()
            probe.begin()
            probe.mark('picture')
            QTimer.singleShot(0, block_gui)
            QTest.qWait(370)
            probe.end()
            probe.observe(decoder, 102.5, 102., True)
            QTest.qWait(1100)
            probe.observe(decoder, 102.5, 102.5, False)
            QTest.qWait(100)

            # A sampler must not acquire a media lock held by a decoder worker.
            held, release = threading.Event(), threading.Event()
            def hold_locks():
                with history.lock, reader.lock:
                    held.set()
                    release.wait(2)
            worker = threading.Thread(target=hold_locks)
            worker.start()
            try:
                assert held.wait(1)
                started = time.perf_counter()
                state = probe.snapshot()['picture']
                assert time.perf_counter() - started < .05, 'Diagnostic blocked on decoding'
                assert state['history_busy'] and state['picture_busy'], state
            finally:
                release.set()
                worker.join(1)
            probe.stop()
            assert probe.thread is None and not probe.timer.isActive()
            raw = probe.path.read_text(encoding='utf-8')
            records = [json.loads(line) for line in raw.splitlines()]
            stalls = [item for item in records if item['event'] == 'ui_stall']
            assert any(any(frame['function'] == 'block_gui' for frame in item['stack']) for item in stalls), stalls
            playback = [item for item in records if item['event'] == 'playback']
            assert any(item['render_ms'] >= 300 and item['stages_ms']['picture'] >= 300 for item in playback), playback
            assert any(item['mouse_events'] >= 1 and item['paint_calls'] >= 1 for item in playback), playback
            assert any(item['picture']['encoded_ahead_s'] == 7.5 and item['picture']['rgb_bounds'] == [102., 102.]
                       for item in playback), playback
            transitions = [entry for item in records if item['event'] == 'frame_wait' for entry in item['transitions']]
            assert [entry['waiting'] for entry in transitions] == [False, True, False], transitions
            assert records[0]['event'] == 'start' and records[-1]['event'] == 'stop'
            assert 'DO_NOT_LOG' not in raw and 'secret.invalid' not in raw
            with patch.dict(os.environ, {'DDM_MATCH_DIAGNOSTICS': '0'}):
                disabled = diagnostic.PlaybackProbe(host, Path(directory) / 'disabled.jsonl')
                disabled.start()
                assert disabled.thread is None and not disabled.path.exists()
                disabled.stop()
            # Reusing the viewer starts a fresh sampler and stops it again.
            probe.start()
            QTest.qWait(100)
            probe.stop()
            assert probe.thread is None
            return dict(passed=True, short_stall_captured=True, frame_wait_transitions=3,
                        nonblocking_media_snapshot=True, private_data_logged=False,
                        paint_and_mouse_tracked=True, worker_stopped=True)
        finally:
            reader.stop()
            viewer.close()
            viewer.deleteLater()
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
