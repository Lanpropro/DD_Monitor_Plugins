"""A delayed GUI request must not turn normal playback into repeated GOP seeks."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(module):
    video = import_module(module.__package__ + '.video')
    from ddm import recording
    with tempfile.TemporaryDirectory(prefix='ddm-picture-stall-') as directory:
        path = Path(directory) / 'source.mkv'
        subprocess.run([recording.ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30', '-t', '6',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '60', '-bf', '2',
                        '-an', str(path)], check=True, capture_output=True, timeout=30,
                       creationflags=recording._FFMPEG_FLAGS)
        history = video.VideoHistory(seconds=60)
        history.set_origin(100)
        with video.av.open(str(path)) as container:
            stream = container.streams.video[0]
            history.codec = (stream.codec_context.name, stream.codec_context.extradata)
            history.video_size = (320, 180)
            for packet in container.demux(stream):
                if packet.size:
                    history.append_packet(packet)
                for frame in packet.decode():
                    pts = float(frame.pts * frame.time_base)
                    history.append(100 + pts, pts)
        reader = video.PictureReader()
        reader.configure(64 * 1024 ** 2, 30)
        creates = []
        original = video.av.CodecContext.create
        def create(*args, **kwargs):
            creates.append(args)
            return original(*args, **kwargs)
        def ready(stamp):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = reader.get(history, history.frame_at(100 + stamp))
                if result is not None and abs(result[0] - (100 + stamp)) < .04:
                    return result
                time.sleep(.005)
            raise AssertionError(('Displayed frame did not catch up', stamp, len(creates)))
        with patch.object(video.av, 'CodecContext', SimpleNamespace(create=create)):
            try:
                ready(.4)
                assert len(creates) == 1
                time.sleep(.8)  # Model GUI work delaying the next playback poll.
                ready(1.2)
                assert len(creates) == 1, 'Normal time advancement after a GUI stall must not reset the decoder'
                time.sleep(1.15)
                with reader.lock:
                    assert reader.images, 'A busy GUI must not discard an active source picture cache after one second'
                ready(2.35)
                assert len(creates) == 1
                ready(4.8)
                assert len(creates) == 2, 'An explicit forward seek still restarts from a suitable GOP'
                ready(.2)
                assert len(creates) == 3, 'An uncached backward seek still resets decoding'
                reader.configure(64 * 1024 ** 2, 30, active=False)
                assert not reader.images and reader.target is None
                return {'passed': True, 'normal_stall_resets': 0, 'explicit_seek_resets': 2,
                        'active_cache_retained': True, 'inactive_cache_released': True}
            finally:
                reader.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    from plugins.match_sync import video as module
    print(check(module))


if __name__ == '__main__':
    main()
