"""Group replay from bounded media/chat histories; no network or device."""
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
            plugins.PluginManager(window=host, plugins_dir=directory), 'replay_check'), {})
        try:
            for key in ('1', '2'):
                viewer.add_room(key, {'alias': key, 'volume': 100})
            viewer.toggle_running()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            image = jpeg()
            for key, row in viewer.rows.items():
                for frame in range(1000 if key == '1' else 1080, 1201):
                    row.decoder.history.append(frame / 10, image)
                row.match_confirmed = row.user_confirmed = True
                row.sync_state, row.mix_gain = '已同步', 1.0
                row.chat_history.append((109.2 if key == '1' else 111.2,
                                         {'uname': 'tester', 'text': 'past', 'kind': 'danmaku'}))
            viewer.alignment.lags = {'1': 0, '2': 2}
            viewer.stable_reference_shift = -6
            viewer.rendered_shifts.clear()
            viewer.rematch_rooms.clear()
            viewer.rematch_admit.clear()
            viewer.render()
            baseline = viewer.shifts()
            assert abs(viewer.replay_available() - 8) < .1
            assert not viewer.rewind(9) and not viewer.replay_seconds
            assert viewer.rewind(5)
            assert abs(viewer.canvas.frame_key - 109) < .11
            assert viewer.shifts() == baseline, 'The live alignment timeline must continue independently'
            assert all(abs(viewer.playback_shifts()[key] - (shift - 5)) < 1e-8 for key, shift in baseline.items())
            assert viewer.live.isEnabled()
            lags = dict(viewer.alignment.lags)
            with patch.object(viewer.alignment, 'accept', return_value=False) as accept:
                viewer._matched(viewer.generation, {'2': module.Match(2, 1, 'live match', refined=True)})
                assert accept.called, 'Automatic live matching must continue during replay'
            assert viewer.alignment.lags == lags
            viewer.matching = False
            with patch.object(module.threading, 'Thread') as thread:
                viewer.analyse()
                assert thread.return_value.start.called, 'Replay must still schedule live analysis'
            viewer.matching = False
            now[0] += .3
            with patch.object(viewer.panel, 'add_event') as chat:
                viewer.render()
                assert chat.call_count == 2
                assert all(call.args[0]['uname'].count('【') == 1 for call in chat.call_args_list)
            assert viewer.rows['1'].chat_history[0][1]['uname'] == 'tester'
            viewer.audio.sink = FakeSink()
            viewer.audio.device = FakeDevice(viewer.audio.sink)
            viewer.audio.anchor = now[0]
            viewer.audio.clock_segments.clear()
            with patch.object(viewer.rows['1'].decoder.history, 'pcm_at', side_effect=lambda stamp, frames:
                    array('h', [1000, -1000] * frames).tobytes()) as first, \
                    patch.object(viewer.rows['2'].decoder.history, 'pcm_at', side_effect=lambda stamp, frames:
                    array('h', [2000, -2000] * frames).tobytes()) as second:
                viewer.audio.fill()
                assert abs(first.call_args.args[0] - 109.3) < .1
                assert abs(second.call_args.args[0] - 111.3) < .1
                assert any(viewer.audio.device.data)
            viewer.audio.stop()
            viewer.return_live()
            viewer.render()
            assert viewer.shifts() == baseline and not viewer.live.isEnabled()
            assert abs(viewer.canvas.frame_key - 114.3) < .11
            assert all(not row.pending for row in viewer.rows.values()), 'Old chat must not flood live return'
            assert viewer.rewind(2)
            # Cached history expires: return to live, rather than stall on removed content.
            for row in viewer.rows.values():
                row.decoder.history.frames.clear()
                for frame in range(1140, 1201):
                    row.decoder.history.append(frame / 10, image)
            viewer.render()
            assert not viewer.replay_seconds
            # A simulated 720-second receive session exposes an absolute 10-minute slider.
            from collections import deque
            from plugins_user._match_sync.engine import History, Sample
            now[0] = 1000
            for key, row in viewer.rows.items():
                row.decoder.history = History(seconds=660, byte_limit=64 * 1024 * 1024)
                for stamp in range(300, 1001):
                    row.decoder.history.append(stamp, image)
                row.decoder.history.details = deque(Sample(stamp, stamp, 10) for stamp in range(940, 1001))
                row.match_confirmed = row.user_confirmed = True
                row.awaiting_confirmation = False
                row.was_isolated = False
                row.sync_state, row.mix_gain = '已同步', 1
            viewer.alignment.lags = {'1': 0, '2': 2}
            viewer.rendered_shifts.clear()
            viewer.render()
            assert viewer.replay_available() == 600
            assert not viewer.seek_replay(601)
            assert viewer.seek_replay(600)
            assert viewer.replay.minimum() == -600 and viewer.replay.value() == -600
            assert viewer.replay_position.text() == '-10:00'
            assert abs(viewer.canvas.frame_key - 394) < .1
            viewer.replay.setValue(-120)
            assert viewer.replay_seconds == 120 and abs(viewer.canvas.frame_key - 874) < .1
            viewer.matching = False
            for row in viewer.rows.values():
                row.next_analysis = 0
            with patch.object(module.threading, 'Thread') as thread, \
                    patch.object(module, 'refine_match', return_value=module.Match(2, 1, 'live fixture', refined=True)) as refine:
                viewer.analyse()
                thread.call_args.kwargs['target']()
                assert any(args.args[1] and args.args[1][-1].time >= 990 for args in refine.call_args_list), \
                    'Matching must inspect current live frames, not the 120-second-old canvas'
            # Incoming media and automatic alignment both advance while viewing the past.
            now[0] += 5
            for row in viewer.rows.values():
                for stamp in range(1001, 1006):
                    row.decoder.history.append(stamp, image)
            viewer.render()
            assert viewer.rows['1'].decoder.history.latest()[0] == 1005
            assert abs(viewer.canvas.frame_key - 879) < .1
            viewer.replay_range.setCurrentIndex(viewer.replay_range.findData(1))
            assert viewer.replay_limit == 1 and viewer.replay_seconds == 60
            assert viewer.replay.minimum() == -60
            viewer.return_live()
            viewer.render()
            assert viewer.replay.value() == 0 and abs(viewer.canvas.frame_key - 999) < .1
        finally:
            viewer.close()
            host.close()
    print('PASS: picture/PCM/chat replay, 10-minute slider, range setting, live ingest/alignment during replay, return and expiry')


if __name__ == '__main__':
    main()
