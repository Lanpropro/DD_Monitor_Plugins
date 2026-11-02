"""Independent secondary playback; synthetic histories and no accounts or network."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from ddm import plugins
    with tempfile.TemporaryDirectory() as directory:
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(plugins.PluginManager(window=host, plugins_dir=directory), 'independent_check'), {})
        class History(SimpleNamespace):
            __hash__ = object.__hash__
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        histories = []
        try:
            for key in ('1', '2', '3'):
                viewer.add_room(key)
                history = History(bounds=lambda: (100., 130.), bounds_at=lambda _stamp: (100., 130.), snapshots=lambda: [])
                viewer.rows[key].decoder = SimpleNamespace(history=history, stop=lambda: None)
                histories.append(history)
            viewer.resize(2200, 800)
            viewer.show()
            app.processEvents()
            viewer.align_room_controls()
            app.processEvents()
            for current in viewer.rows.values():
                left = current.decrease.geometry()
                value = current.delay.geometry()
                right = current.increase.geometry()
                assert 0 <= value.left() - left.right() <= 12
                assert 0 <= right.left() - value.right() <= 12, 'Keep offset minus/value/plus together'
            main = viewer.rows['1']
            for attribute in ('audible', 'volume', 'channel', 'delay', 'increase'):
                first, second = getattr(main, attribute), getattr(viewer.rows['2'], attribute)
                assert first.mapTo(main, QPoint()).x() == second.mapTo(viewer.rows['2'], QPoint()).x(), attribute
            viewer.running = True
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            viewer.alignment.reference = '1'
            row = viewer.rows['2']
            viewer.alignment.lags = {'1': 0, '3': -2}
            with patch.object(viewer, 'live_clock', return_value=120.), patch.object(viewer, 'render'), patch.object(viewer, 'analyse'):
                before = viewer.shifts()
                for lag in (None, 0., -4.):
                    if lag is None:
                        viewer.alignment.lags.pop('2', None)
                    else:
                        viewer.alignment.lags['2'] = lag
                    QTest.mouseClick(row.independent, Qt.LeftButton)
                    assert '2' in viewer.independent_playback
                    shifts = viewer.shifts()
                    assert abs(shifts['1'] - before['1']) < .001
                    assert abs(shifts['3'] - before['3']) < .001
                    assert 100 <= 120 + shifts['2'] < 130
                    earlier = shifts['2']
                    QTest.qWait(30)
                    assert viewer.shifts()['2'] > earlier + .015, 'Independent clock advances even if the main clock is stalled'
                    viewer.sync_waiting = True
                    viewer._update_sync_health(120, viewer.shifts())
                    assert row.sync_state == '已同步' and row.mix_gain > 0
                    row.audible.setChecked(True)
                    row.volume.setValue(37)
                    viewer.audio.update_snapshot()
                    assert any(item[0] is histories[1] and item[2] == 1. and item[4] == 37 for item in viewer.audio.snapshot[1]), 'PCM input includes unmatched source during main buffering'
                    row.audible.setChecked(False)
                    viewer.audio.update_snapshot()
                    assert not any(item[0] is histories[1] for item in viewer.audio.snapshot[1])
                    row.audible.setChecked(True)
                    QTest.mouseClick(row.independent, Qt.LeftButton)
                    assert '2' not in viewer.independent_playback and '2' not in viewer.independent_shifts
                    assert '2' in viewer.rematch_rooms
                    viewer.sync_waiting = False
                viewer.toggle_independent('1')
                assert '1' not in viewer.independent_playback
            import numpy as np
            class WaveHistory:
                def __init__(self):
                    self.reads = []
                def pcm_at(self, stamp, frames):
                    start = round(stamp * module.RATE)
                    self.reads.append((start, frames))
                    wave = (np.sin(np.arange(start, start + frames) * (2 * np.pi * 880 / module.RATE)) * 9000).astype('<i2')
                    return np.column_stack((wave, wave)).tobytes()
            class Sink:
                def processedUSecs(self):
                    return 0
                def bytesFree(self):
                    return 2048 * 4
            class Device:
                def write(self, data):
                    return len(data)
            wave = WaveHistory()
            pump = module.AudioPump(viewer)
            pump.sink, pump.device = Sink(), Device()
            pump.anchor = 100.
            pump.independent_histories = frozenset((wave,))
            try:
                for index in range(30):
                    jitter = (0, .004, -.003, .001)[index % 4]
                    shift = -pump.anchor + pump.written / module.RATE - pump.media_written + jitter
                    pump.snapshot = (.98, ((wave, shift, 1., 0, 100),))
                    pump._fill()
                for previous, current in zip(wave.reads, wave.reads[1:]):
                    assert current[0] == previous[0] + previous[1], 'Independent PCM must stay sample-contiguous during main slowdown and GUI jitter'
            finally:
                pump.sink = pump.device = None
                pump.deleteLater()
            return {'passed': True, 'continuous_pcm': True, 'no_zero_nonzero_offset': True, 'main_progress_preserved': True,
                    'audio_snapshot': True, 'mute_volume': True, 'resume_alignment': True}
        finally:
            for row in viewer.rows.values():
                row.decoder = None
            viewer.running = False
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
