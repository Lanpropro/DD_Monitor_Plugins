"""Audio controls change by signal; periodic snapshots do not reread those widgets."""
import argparse
from contextlib import ExitStack
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module):
    from ddm import plugins
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(module.Viewer, 'sync_picture', lambda *_args, **_kwargs: None):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'audio_controls'), {})
        try:
            for key in ('1', '2'):
                viewer.add_room(key)
                row = viewer.rows[key]
                row.decoder = module.Decoder(key)
                row.sync_state, row.mix_gain = '已同步', 1.
                row.audible.setChecked(True)
                row.volume.setValue(37 if key == '1' else 61)
            first, second = viewer.rows.values()
            pump = viewer.audio
            shifts = {'1': -2., '2': 3.}
            clock = [100.]
            with patch.object(viewer, 'shifts', return_value=shifts), \
                    patch.object(pump, 'clock', side_effect=lambda: clock[0]):
                pump.update_snapshot()
                original = pump.snapshot
                controls = [row.audio_controls for row in viewer.rows.values()]
                with ExitStack() as stack:
                    for row in viewer.rows.values():
                        for control, getter in ((row.audible, 'isChecked'), (row.channel, 'currentData'),
                                                (row.volume, 'value')):
                            stack.enter_context(patch.object(control, getter,
                                side_effect=AssertionError('Periodic audio read a control widget')))
                    for index in range(100):
                        clock[0] = 100. + index * .017
                        pump.update_snapshot()
                        assert pump.snapshot is original, 'Unchanged live inputs should reuse the published snapshot'
                    assert controls == [row.audio_controls for row in viewer.rows.values()]
                first.volume.setValue(19)
                assert first.audio_controls[2] == 19
                pump.update_snapshot()
                assert pump.snapshot is not original
                assert pump.snapshot[1][0][4] == 19
                first.channel.setCurrentIndex(1)
                channel = first.channel.currentData()
                pump.update_snapshot()
                assert pump.snapshot[1][0][3] == channel
                first.audible.setChecked(False)
                pump.update_snapshot()
                assert len(pump.snapshot[1]) == 1 and pump.snapshot[1][0][0] is second.decoder.history
                first.audible.setChecked(True)
                first.paused = True
                pump.update_snapshot()
                assert len(pump.snapshot[1]) == 1
                first.paused = False
                first.mix_gain = .25
                viewer.playback_rate = .98
                viewer.relative_rates['1'] = 1.01
                pump.update_snapshot()
                assert abs(pump.snapshot[1][0][2] - .99) < 1e-12
                assert pump.snapshot[1][0][4] == 19 * .25
                viewer.sync_waiting = True
                viewer.independent_playback['1'] = (0., 0.)
                pump.update_snapshot()
                assert len(pump.snapshot[1]) == 1 and pump.snapshot[1][0][2] == 1.
                assert pump.independent_histories == frozenset((first.decoder.history,))
                viewer.sync_waiting = False
                viewer.independent_playback.clear()
                viewer.replay.delay = 5.
                pump.replay_offset = None
                clock[0] = 100.
                with patch.object(viewer.replay, 'presentation', return_value=(95., shifts)), \
                        patch.object(viewer.replay, 'shifts_at', return_value=shifts):
                    pump.update_snapshot()
                    replay_snapshot = pump.snapshot
                    assert replay_snapshot[0] == 1.
                    assert replay_snapshot[1][0][1:5] == (-7., 1., channel, 19.)
                    clock[0] += .017
                    pump.update_snapshot()
                    assert pump.snapshot is replay_snapshot, 'Replay GUI polling must not change the media offset'
                viewer.replay.live()
                old_history = first.decoder.history
                first.decoder.history = module.Decoder('replacement').history
                pump.update_snapshot()
                assert pump.replay_offset is None
                assert pump.snapshot[1][0][0] is first.decoder.history and first.decoder.history is not old_history
                viewer.rows.pop('2')
                pump.update_snapshot()
                assert len(pump.snapshot[1]) == 1
                second.decoder.stop()
                second.deleteLater()
            return dict(passed=True, widget_reads_per_static_100_polls=0,
                        unchanged_snapshot_reused=True, volume_mute_channel_signals=True,
                        pause_gain_rates=True, independent=True, replay=True, replaced_source=True,
                        media_playback=False)
        finally:
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
    app = QApplication([])
    host = QWidget()
    try:
        print(check(app, host, import_module('plugins.match_sync.viewer')))
    finally:
        host.close()
        app.processEvents()
        app.shutdown()


if __name__ == '__main__':
    main()
