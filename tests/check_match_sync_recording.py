"""Local encoded-file and native routing checks; no accounts, streams or real messages."""
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def check_mux(output):
    import av
    import numpy as np
    from plugins.match_sync.engine import History, RATE
    from plugins.match_sync.video import VideoHistory, recording_template
    from plugins.match_sync.recording import CacheMuxer, MultiTrackRecorder
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for codec in ('libx264', 'libx265'):
        path = output / (codec + '-source.mkv')
        with av.open(str(path), 'w') as container:
            video = container.add_stream(codec, rate=25)
            video.width, video.height, video.pix_fmt = 160, 90, 'yuv420p'
            video.options = {'preset': 'ultrafast', 'g': '25', 'bf': '2', 'x265-params': 'log-level=error:pools=2'}
            for index in range(150):
                rgb = np.zeros((90, 160, 3), dtype=np.uint8)
                rgb[:, :, 0] = 180
                frame = av.VideoFrame.from_ndarray(rgb, format='rgb24')
                frame.pts, frame.time_base = index, Fraction(1, 25)
                for packet in video.encode(frame):
                    container.mux(packet)
            for packet in video.encode(None):
                container.mux(packet)
        history = VideoHistory(disk_cache=True, seconds=660)
        history.set_origin(1000)
        peer = History()
        peer.origin = 1000
        for source, frequency, shift in ((history, 440, 0), (peer, 880, .25)):
            stamp = np.arange(4 * RATE) / RATE
            left = np.sin(2 * np.pi * frequency * stamp) * 10000
            right = np.sin(2 * np.pi * (frequency + 110) * stamp) * 10000
            pcm = np.column_stack((left, right)).astype(np.int16)
            pcm[(stamp < .6 + shift) | (stamp > 2 + shift)] = 0
            source.audio.append(pcm.tobytes())
        tracks = ((1, '主路、解说', ('main', 'peer')), (2, '主路', ('main',)),
                  (3, '解说', ('peer',)), (6, '缺失来源', ('missing',)))
        sources = {'main': (history, 0), 'peer': (peer, .25)}
        result = output / (codec + '-matrix.mkv')
        with av.open(str(path)) as original, recording_template(history, original):
            stream = original.streams.video[0]
            history.codec = (stream.codec_context.name, stream.codec_context.extradata)
            for packet in original.demux(video=0):
                if packet.size:
                    history.append_packet(packet)
            muxer = CacheMuxer(result, history, 1000.2, tracks)
            try:
                for index in range(5, 71):
                    muxer.write_until(1000 + index / 25, sources)
            finally:
                muxer.close()
            with av.open(str(result)) as container:
                assert len(container.streams.video) == 1 and len(container.streams.audio) == 4
                assert container.streams.video[0].codec_context.name == history.codec[0]
                assert [s.metadata.get('title') for s in container.streams.audio] == [f'音轨 {n} · {label}' for n, label, _ in tracks]
                assert [s.metadata.get('DDM_TRACK_NUMBER') for s in container.streams.audio] == ['1', '2', '3', '6']
                video_frames = list(container.decode(video=0))
                assert len(video_frames) >= 65 and video_frames[0].width == 160
                assert video_frames[0].to_ndarray(format='rgb24')[:, :, 0].mean() > 150
            onsets = []
            for index, expected in enumerate(((440, 880), (440,), (880,), ())):
                with av.open(str(result)) as container:
                    frames = list(container.decode(audio=index))
                    pcm = np.concatenate([frame.to_ndarray() for frame in frames], axis=1)
                    assert pcm.shape[0] == 2 and pcm.shape[1] >= 2.7 * RATE
                    if expected:
                        spectrum = abs(np.fft.rfft(pcm[0, RATE:2 * RATE]))
                        peaks = np.argsort(spectrum)[-4:]
                        assert all(any(abs(int(peak) - frequency) < 2 for peak in peaks) for frequency in expected), (codec, index, peaks)
                        if len(expected) == 1:
                            right_peak = np.argmax(abs(np.fft.rfft(pcm[1, RATE:2 * RATE])))
                            assert abs(right_peak - expected[0] - 110) < 2, 'Preserve original stereo'
                            onsets.append(np.flatnonzero(abs(pcm[0]) > .05)[0] / RATE)
                    else:
                        assert np.max(abs(pcm)) < .001
            assert abs(onsets[0] - onsets[1]) < .03, 'Remove the 250ms source offset'
            worker = MultiTrackRecorder(output / (codec + '-stop.mkv'), history, tracks, (1000.4, sources))
            worker.start()
            worker.snapshot = (1000.8, sources)
            worker.stop(wait=True)
            assert not worker.thread.is_alive()
            with av.open(str(worker.path)) as container:
                assert len(list(container.decode(video=0))) >= 15, 'Stop must finalize a readable container'
            with patch('plugins.match_sync.recording.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
                failed = MultiTrackRecorder(output / (codec + '-low-disk.mkv'), history, tracks, (1000.4, sources), minimum_free=1)
                errors = []
                failed.completed.connect(lambda _path, error: errors.append(error))
                failed.start()
                failed.thread.join(5)
                assert not failed.thread.is_alive()
                from PySide6.QtCore import QCoreApplication
                QCoreApplication.processEvents()
                assert errors and 'Insufficient disk space' in errors[0]
            muxer = CacheMuxer(output / (codec + '-changed.mkv'), history, 1000.2, tracks)
            history.origin += 1
            try:
                muxer.write_until(1000.4, sources)
            except ValueError as error:
                assert 'changed' in str(error)
            else:
                raise AssertionError('Changed source must stop recording')
            finally:
                history.origin -= 1
                muxer.close()
        assert history.stream_template is None, 'Clear AVStream before the container closes'
        try:
            CacheMuxer(output / (codec + '-reconnect.mkv'), history, 1000.2, tracks)
        except ValueError as error:
            assert 'reconnecting' in str(error)
        else:
            raise AssertionError('Do not copy a closed AVStream')
        results.append(dict(codec=codec,tracks=[1, 2, 3, 6],copied_video=True,stereo=True,mixed=True,aligned=True,finalized=True))
    return dict(passed=True, files=results, real_network_requests=0)


def check_pipeline(app, host, output, module=None):
    from importlib import import_module
    import av
    import numpy as np
    from PySide6.QtTest import QTest
    from ddm.recording import ffmpeg_path
    package = module.__package__ if module is not None else 'plugins.match_sync'
    Decoder = import_module(package + '.media').Decoder
    MultiTrackRecorder = import_module(package + '.recording').MultiTrackRecorder
    decoders = []
    states = []
    try:
        inputs = []
        for codec, frequency in (('libx264', 440), ('libx265', 880)):
            source = output / (codec + '-capture-input.mkv')
            command = [ffmpeg_path({}), '-nostdin', '-v', 'error', '-y', '-i', str(output / (codec + '-source.mkv')),
                       '-f', 'lavfi', '-i', f'sine=frequency={frequency}:sample_rate=48000:duration=6',
                       '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-ac', '2', '-shortest', str(source)]
            subprocess.run(command, check=True, capture_output=True, timeout=20,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            inputs.append((frequency, source))
        for frequency, source in inputs:
            decoder = Decoder(str(frequency), {'url': str(source)})
            decoder.events.state.connect(lambda text, f=frequency: states.append((f, text)))
            decoders.append(decoder)
            decoder.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if all(d.history.origin is not None and d.history.stream_template is not None and
                   (bounds := d.history.bounds()) is not None and bounds[1] - bounds[0] > .5 for d in decoders):
                break
            time.sleep(.01)
            app.processEvents()
        else:
            raise AssertionError('Real FFmpeg capture did not prepare cached video/audio')
        main, peer = (d.history for d in decoders)
        delta = peer.origin - main.origin
        sources = {'main': (main, 0), 'peer': (peer, delta)}
        tracks = ((1, '混合', ('main', 'peer')), (2, '主路', ('main',)), (3, '副路', ('peer',)))
        target = min(main.bounds()[1], peer.bounds()[1] - delta) - .05
        first = main.packet_range(None, target - main.origin)[1][0]
        beginning = main.origin + float((first.dts if first.dts is not None else first.pts) * first.time_base)
        initial_target = target
        result = output / 'real-capture-matrix.mkv'
        worker = MultiTrackRecorder(result, main, tracks, (target, sources))
        completed = []
        progress = []
        worker.completed.connect(lambda path, error: completed.append((path, error)))
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                time.sleep(.05)
                app.processEvents()
                target = min(main.bounds()[1], peer.bounds()[1] - delta) - .05
                worker.snapshot = (target, sources)
                progress.append(dict(main_bounds=main.bounds(), peer_bounds=peer.bounds(), target=target,
                                     template=main.stream_template is not None, main_sequence=main.sequence,
                                     peer_sequence=peer.sequence))
                if target - initial_target >= .8:
                    break
        finally:
            worker.stop(wait=True)
        QTest.qWait(20)
        (output / 'pipeline-progress.json').write_text(json.dumps(dict(initial=initial_target, final=target,
            progress=progress, states=states, completed=completed), ensure_ascii=False, indent=2), encoding='utf-8')
        assert not worker.thread.is_alive() and completed and not completed[0][1], completed
        with av.open(str(result)) as container:
            assert len(container.streams.audio) == 3
            frames = list(container.decode(video=0))
            duration = float(frames[-1].pts * frames[-1].time_base) + 1 / 25
            assert target - initial_target >= .8, f'Capture must advance: {target - initial_target:.3f}s; {states}'
            assert abs(duration - (target - beginning)) < .15, (duration, target - beginning)
        for index, expected in ((1, 440), (2, 880)):
            with av.open(str(result)) as container:
                frames = list(container.decode(audio=index))
                pcm = np.concatenate([frame.to_ndarray()[0] for frame in frames])
                assert np.max(abs(pcm)) > .01, 'Actual captured source must remain audible'
                size = min(24000, len(pcm))
                peak = np.argmax(abs(np.fft.rfft(pcm[-size:]))) * 48000 / size
                assert abs(peak - expected) < 5, (index, peak)
        return dict(passed=True, real_ffmpeg_capture=True, source_codecs=['h264', 'hevc'],
                    audio_tracks=3, video_copy=True, real_network_requests=0)
    finally:
        for decoder in decoders:
            decoder.stop()
        for decoder in decoders:
            if decoder.thread is not None:
                decoder.thread.join(5)


def check(app, host, module, output=None):
    from PySide6.QtCore import QObject, Signal, Qt
    from PySide6.QtTest import QTest
    from ddm import plugins

    class StubRecorder(QObject):
        completed = Signal(str, str)

        def __init__(self, path, history, tracks, snapshot, parent=None, minimum_free=0):
            super().__init__(parent)
            self.path, self.history, self.tracks, self.snapshot = path, history, tracks, snapshot
            self.cancelled = threading.Event()

        def start(self):
            pass

        def stop(self, wait=False):
            self.cancelled.set()

    with tempfile.TemporaryDirectory() as directory, \
            patch.object(module.Decoder, 'start', lambda _self: None), \
            patch.object(module.Chat, 'start', lambda _self: None), \
            patch.object(module.AudioPump, 'start', lambda _self: None), \
            patch.object(module, 'MultiTrackRecorder', StubRecorder):
        host.settings, host.rooms = {}, []
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        viewer = module.Viewer(plugins.PluginContext(manager, 'track_matrix_check'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            for key in ('1', '2'):
                viewer.add_room(key, {'alias': '主播' + key})
            assert viewer.rows['1'].record_tracks == (1,) and viewer.rows['2'].record_tracks == (2,)
            viewer.open_match_settings()
            dialog = viewer.match_settings
            one, two = dialog.track_checks['1'], dialog.track_checks['2']
            two[0].click()
            assert viewer.rows['2'].record_tracks == (1, 2), 'Allow multiple destinations per source'
            one[1].click()
            assert viewer.rows['1'].preferences()['record_tracks'] == [1, 2]
            dialog.hide()
            viewer.open_match_settings()
            assert dialog.track_checks['1'][1].isChecked(), 'Reopening preserves the matrix'
            if output:
                dialog.grab().save(str(output))
            viewer.toggle_running()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            viewer.audio.timer.stop()
            history = viewer.rows['1'].decoder.history
            history.origin, history.codec = time.monotonic() - 2, ('h264', b'')
            viewer._record_picture()
            assert viewer.multi_recorder is None and '选择保存目录' in viewer.notice.text()
            host.settings.update(recording_dir=directory, recording_min_free_mb=256)
            peer = viewer.rows['2']
            peer.sync_state = '已同步'
            peer.audible.setChecked(False)
            peer.volume.setValue(1)
            peer.channel.setCurrentIndex(peer.channel.findData(3))
            viewer._record_picture()
            worker = viewer.multi_recorder
            assert worker.tracks == ((1, '主播1、主播2', ('1', '2')), (2, '主播1、主播2', ('1', '2')))
            assert worker.snapshot[1]['2'][0] is peer.decoder.history, 'Monitor mute/volume/LR must not affect recording'
            assert not dialog.recording_routes.isEnabled()
            assert viewer.picture._recording_state == 'record'

            def finish():
                worker.completed.emit(str(worker.path), '')
                QTest.qWait(10)
                assert viewer.multi_recorder is None and viewer.picture.recording_button.isEnabled()

            viewer._record_picture()
            assert worker.cancelled.is_set() and not viewer.picture.recording_button.isEnabled()
            finish()
            assert dialog.recording_routes.isEnabled()
            viewer._record_picture()
            worker = viewer.multi_recorder
            viewer.add_room('3')
            assert worker.cancelled.is_set(), 'Adding a source finalizes the old matrix'
            finish()
            viewer._record_picture()
            worker = viewer.multi_recorder
            viewer.main.setCurrentIndex(viewer.main.findData('2'))
            assert worker.cancelled.is_set(), 'Changing main video finalizes the old file'
            finish()
            peer.decoder.history.origin, peer.decoder.history.codec = history.origin, history.codec
            viewer._record_picture()
            worker = viewer.multi_recorder
            viewer.remove_room('3')
            assert worker.cancelled.is_set(), 'Removing a source finalizes the old matrix'
            finish()
            viewer._record_picture()
            worker = viewer.multi_recorder
            with patch.object(viewer.replay, 'seek', return_value=True), patch.object(viewer, '_rebuild_replay_chat'):
                viewer.seek_replay(time.monotonic() - 1)
            assert worker.cancelled.is_set(), 'Seeking finalizes the old file'
            finish()
            viewer._record_picture()
            worker = viewer.multi_recorder
            viewer.replay.delay = 1
            with patch.object(viewer, '_rebuild_replay_chat'):
                viewer.return_live()
            assert worker.cancelled.is_set(), 'Returning live finalizes a replay recording'
            finish()
            viewer._record_picture()
            worker = viewer.multi_recorder
            viewer.stop()
            assert worker.cancelled.is_set()
            finish()
            for key in ('3', '4', '5', '6', '7'):
                viewer.add_room(key)
            assert viewer.rows['7'].record_tracks == (), 'Do not silently mix a seventh source into an occupied track'
            for row in viewer.rows.values():
                row.record_tracks = ()
            viewer.running = True
            peer.decoder = SimpleNamespace(history=history)
            viewer._record_picture()
            assert viewer.multi_recorder is None and '至少一条' in viewer.notice.text()
            peer.decoder = None
            viewer.running = False
            viewer.save()
            assert viewer.context.setting('rooms')[0]['record_tracks'] == []
            return dict(passed=True, matrix=True, persisted=True, monitor_independent=True,
                        membership_and_main_stop=True, finalized_feedback=True, real_messages=0)
        finally:
            manager.unload()
            viewer.close()
            viewer.deleteLater()
            app.sendPostedEvents(None, 52)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mux-only', action='store_true')
    parser.add_argument('--pipeline-only', action='store_true')
    args = parser.parse_args()
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(args.host.resolve())]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    args.output.mkdir(parents=True, exist_ok=True)
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        if args.pipeline_only:
            print(json.dumps(check_pipeline(app, args.host, args.output)))
        elif args.mux_only:
            print(json.dumps(check_mux(args.output)))
        else:
            print(json.dumps(check(app, QWidget(), module, args.output / 'matrix.png')))
    app.sendPostedEvents(None, 52)
    app.shutdown()


if __name__ == '__main__':
    main()
