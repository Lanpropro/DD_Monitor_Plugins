"""Real local mux/decode: copied video and separate identifiable AAC tracks."""
import argparse
from fractions import Fraction
import os
import socket
from pathlib import Path
import sys
import time
from unittest.mock import patch
import tempfile
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root), str(args.host.resolve())]
    import av
    import numpy as np
    from plugins.match_sync.engine import History, RATE
    from plugins.match_sync.video import VideoHistory
    from plugins.match_sync.recording import CacheMuxer, MultiTrackRecorder
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    dll = os.add_dll_directory(str(args.host.resolve())) if sys.platform == 'win32' else None
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    args.output.mkdir(parents=True, exist_ok=True)
    for codec in ('libx264', 'libx265'):
        source = args.output / f'{codec}-source.mkv'
        with av.open(str(source), 'w') as output:
            stream = output.add_stream(codec, rate=25)
            stream.width, stream.height, stream.pix_fmt = 160, 90, 'yuv420p'
            stream.options = {'preset': 'ultrafast', 'g': '25', 'bf': '2', 'x265-params': 'log-level=error:pools=2'}
            for index in range(75):
                rgb = np.zeros((90, 160, 3), dtype=np.uint8)
                rgb[:, :, 0] = 180
                frame = av.VideoFrame.from_ndarray(rgb, format='rgb24')
                frame.pts, frame.time_base = index, Fraction(1, 25)
                for packet in stream.encode(frame):
                    output.mux(packet)
            for packet in stream.encode(None):
                output.mux(packet)
        main_history = VideoHistory()
        main_history.set_origin(1000)
        source_container = av.open(str(source))
        stream = source_container.streams.video[0]
        main_history.codec = (stream.codec_context.name, stream.codec_context.extradata)
        main_history.stream_template = stream
        main_history.video_size = (stream.width, stream.height)
        for packet in source_container.demux(video=0):
            if packet.size:
                main_history.append_packet(packet)
        peer = History()
        peer.origin = 1000
        for history, frequency in ((main_history, 440), (peer, 880)):
            mono = (np.sin(2 * np.pi * frequency * np.arange(4 * RATE) / RATE) * 10000).astype(np.int16)
            timeline = np.arange(4 * RATE) / RATE
            shift = .25 if history is peer else 0
            mono[(timeline < .6 + shift) | (timeline > 2 + shift)] = 0
            history.audio.append(np.column_stack((mono, mono)).tobytes())
        tracks = (('1', 'Main'), ('2', 'Commentary'), ('3', 'Missing'))
        sources = {'1': (main_history, 0), '2': (peer, .25)}
        result = args.output / f'{codec}-multitrack.mkv'
        muxer = CacheMuxer(result, main_history, 1000.2, tracks)
        try:
            for index in range(5, 71):
                muxer.write_until(1000 + index / 25, sources)
        finally:
            muxer.close()
        with av.open(str(result)) as container:
            assert len(container.streams.video) == 1 and len(container.streams.audio) == 3
            assert [stream.metadata.get('title') for stream in container.streams.audio] == [f'{label} ({key})' for key, label in tracks]
            video = list(container.decode(video=0))
            assert len(video) >= 65 and video[0].width == 160
            assert video[0].to_ndarray(format='rgb24')[:, :, 0].mean() > 150
        onsets = []
        for index, expected in enumerate((440, 880, 0)):
            with av.open(str(result)) as container:
                frames = list(container.decode(audio=index))
                pcm = np.concatenate([frame.to_ndarray()[0] for frame in frames])
                assert len(pcm) >= 2.7 * RATE
                if expected:
                    segment = pcm[RATE:2 * RATE]
                    frequency = np.argmax(abs(np.fft.rfft(segment)))
                    assert abs(frequency - expected) < 2, (index, frequency)
                    onsets.append(np.flatnonzero(abs(pcm) > .05)[0] / RATE)
                else:
                    assert np.max(abs(pcm)) < .001
        assert abs(onsets[0] - onsets[1]) < .03, 'The 250ms source delay must be removed from the recorded stems'
        worker = MultiTrackRecorder(args.output / f'{codec}-stop.mkv', main_history, tracks,
                                    (1000.4, sources))
        worker.start()
        time.sleep(.1)
        worker.snapshot = (1000.8, sources)
        worker.stop()
        worker.thread.join(5)
        assert not worker.thread.is_alive(), 'Stopping must finish the container'
        with av.open(str(worker.path)) as container:
            assert len(list(container.decode(video=0))) >= 15
        main_history.stream_template = None
        source_container.close()
        # Starting during reconnect must fail safely, without a dangling AVStream.
        try:
            CacheMuxer(args.output / f'{codec}-reconnect.mkv', main_history, 1000.2, tracks)
        except ValueError as error:
            assert 'reconnecting' in str(error)
        else:
            raise AssertionError('Disconnected template must be rejected')

    from ddm import plugins
    from plugins.match_sync import viewer as module
    from dev.selfcheck_match_sync import FakeChat, FakeDecoder

    class StubRecorder(QObject):
        completed = Signal(str, str)

        def __init__(self, path, history, tracks, snapshot, parent=None, **kwargs):
            super().__init__(parent)
            self.path, self.history, self.tracks, self.snapshot = path, history, tracks, snapshot
            self.cancelled = threading.Event()

        def start(self):
            pass

        def stop(self, wait=False):
            self.cancelled.set()

    with tempfile.TemporaryDirectory(dir=args.output) as directory, \
            patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
            patch.object(module, 'Decoder', FakeDecoder), patch.object(module, 'Chat', FakeChat), \
            patch.object(module.AudioPump, 'start', lambda _: None), \
            patch.object(module, 'MultiTrackRecorder', StubRecorder):
        host = QWidget()
        host.settings = {}
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'recording_check'), {})
        try:
            for key in ('1', '2'):
                viewer.add_room(key, {'alias': key})
            viewer.toggle_running()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            history = viewer.rows['1'].decoder.history
            history.origin, history.codec = 1000, ('h264', b'')
            viewer._record_picture()
            assert viewer.multi_recorder is None and '选择保存目录' in viewer.notice.text()
            host.settings.update(recording_dir=str(args.output), recording_min_free_mb=0)
            viewer.rows['2'].audible.setChecked(False)
            viewer._record_picture()
            worker = viewer.multi_recorder
            assert worker.tracks == (('1', '1'), ('2', '2'))
            assert len(worker.snapshot[1]) == 2, 'Muted sources must still retain their editing track'
            assert viewer.picture._recording_state == 'record'
            viewer._record_picture()
            assert worker.cancelled.is_set() and not viewer.picture.recording_button.isEnabled()
            worker.completed.emit(str(worker.path), '')
            assert viewer.multi_recorder is None and viewer.picture.recording_button.isEnabled()
            viewer._record_picture()
            worker = viewer.multi_recorder
            viewer.add_room('3')
            assert worker.cancelled.is_set(), 'Changing track membership must close the old container'
            worker.completed.emit(str(worker.path), '')
            viewer._record_picture()
            worker = viewer.multi_recorder
            viewer.stop()
            assert worker.cancelled.is_set()
            worker.completed.emit(str(worker.path), '')
        finally:
            viewer.close()
            host.close()
    print('PASS: H264/HEVC video copy, 3 named independent tracks, 440/880Hz isolation, silent missing track and stop finalization')


if __name__ == '__main__':
    main()
