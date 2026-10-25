"""Independent secondary playback with synthetic PCM; no network or device."""
import argparse
from array import array
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(args.host.resolve()), str(root)]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    dll = os.add_dll_directory(str(args.host.resolve())) if sys.platform == 'win32' else None
    import plugins_user
    package = types.ModuleType('plugins_user._match_sync')
    package.__path__ = [str(root / 'plugins/match_sync')]
    sys.modules[package.__name__] = package
    plugins_user._match_sync = package
    from PySide6.QtWidgets import QApplication, QWidget
    from ddm import plugins
    from plugins_user._match_sync import viewer as module
    from dev.selfcheck_match_sync import FakeChat, FakeDecoder, FakeDevice, FakeSink, jpeg
    app = QApplication([])
    now = [120.0]
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
            patch.object(module, 'Decoder', FakeDecoder), patch.object(module, 'Chat', FakeChat), \
            patch.object(module.AudioPump, 'start', lambda _: None), \
            patch.object(module.time, 'monotonic', side_effect=lambda: now[0]):
        host = QWidget()
        host.settings = {}
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'independent_check'), {})
        try:
            for key in ('1', '2', '3'):
                viewer.add_room(key, {'volume': 100, 'audible': True})
            viewer.toggle_running()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            image = jpeg()
            for row in viewer.rows.values():
                for frame in range(1000, 1201):
                    row.decoder.history.append(frame / 10, image)
            peer = viewer.rows['2']
            for lag in (None, 0.0, 3.0):
                peer.independent.blockSignals(True)
                peer.independent.setChecked(False)
                peer.independent.blockSignals(False)
                viewer.independent_shifts.clear()
                if lag is None:
                    viewer.alignment.lags.pop('2', None)
                else:
                    viewer.alignment.lags['2'] = lag
                peer.match_confirmed = lag is not None
                viewer.rendered_shifts.clear()
                main_before = viewer.shifts()['1']
                peer.independent.click()
                now[0] += .25
                viewer._update_sync_health(now[0], viewer.shifts())
                assert peer.sync_state == '已同步' and peer.mix_gain > 0, lag
                assert viewer.shifts()['1'] == main_before, 'Independent audio must not seek main'
                viewer.rows['1'].audible.setChecked(False)
                viewer.rows['3'].audible.setChecked(False)
                viewer.audio.sink = FakeSink()
                viewer.audio.device = FakeDevice(viewer.audio.sink)
                viewer.audio.anchor = now[0]
                viewer.audio.clock_segments.clear()
                with patch.object(peer.decoder.history, 'pcm_at', side_effect=lambda stamp, frames:
                        array('h', [1000, -1000] * frames).tobytes()) as pcm:
                    viewer.sync_waiting = True
                    viewer.audio.fill()
                    assert pcm.called and any(viewer.audio.device.data), 'Independent secondary must be audible'
                    assert abs(pcm.call_args.args[0] - (now[0] + viewer.shifts()['2'])) < .1
                    peer.audible.setChecked(False)
                    viewer.audio.update_snapshot()
                    assert not viewer.audio.snapshot[1]
                    peer.audible.setChecked(True)
                    peer.paused = True
                    viewer.audio.update_snapshot()
                    assert not viewer.audio.snapshot[1]
                    peer.paused = False
                viewer.audio.stop()
                viewer.sync_waiting = False
                before = viewer.shifts()['2']
                viewer._matched(viewer.generation, {'2': module.Match(8, 1, 'stale', refined=True)})
                assert viewer.shifts()['2'] == before, 'Queued match must not override explicit mode'
            # New epoch: independent playback recovers within the new cache.
            peer.decoder.history.frames.clear()
            for frame in range(2000, 2101):
                peer.decoder.history.append(frame / 10, image)
            viewer._reset(peer, peer.decoder)
            viewer.render()
            target = viewer.audio.clock() + viewer.shifts()['2']
            assert 200 <= target < 210 and peer.independent.isChecked()
            with patch.object(viewer, 'analyse'):
                peer.independent.click()
            assert not peer.independent.isChecked() and '2' not in viewer.independent_shifts
            assert '2' in viewer.rematch_rooms and peer.mix_gain == 0
            # Manual mode also permits independent playback, then follows its offsets again.
            viewer.automatic.setChecked(False)
            peer.independent.click()
            assert '2' in viewer.independent_shifts
            peer.independent.click()
            assert '2' not in viewer.independent_shifts
        finally:
            viewer.close()
            host.close()
    print('PASS: no/zero/nonzero lag audio, mute/pause, main isolation, stale match, reconnect and return to sync')


if __name__ == '__main__':
    main()
