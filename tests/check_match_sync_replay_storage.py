"""Ten-minute disk history, bounded analysis, PCM wrap and reader-safe eviction."""
import argparse
from fractions import Fraction
import gc
import mmap
from pathlib import Path
import sys
import tempfile
import time
import weakref
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(args.output.resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import av
    from plugins.match_sync.engine import AudioRing, Sample
    from plugins.match_sync.video import VideoHistory, DiskPacketData, PictureReader, REPLAY_HISTORY_SECONDS
    history = VideoHistory(disk=True, seconds=REPLAY_HISTORY_SECONDS, byte_limit=1024 * 1024)
    assert isinstance(history.audio.data, mmap.mmap)
    history.set_origin(1000)
    held = None
    for second in range(721):
        packet = av.Packet(bytes([second % 256]) * 32)
        packet.pts = packet.dts = second
        packet.duration = 1
        packet.time_base = Fraction(1, 1)
        packet.is_keyframe = second % 2 == 0
        history.append_packet(packet)
        history.append(1000 + second, second, Sample(1000 + second, second, 10))
        if second == 0:
            held = history.packets[0]
    assert all(isinstance(packet.data, DiskPacketData) for packet in history.packets)
    assert history.bounds()[0] <= 1720 - 6 - 600
    assert history.bounds()[1] == 1720
    assert history.samples[-1].time - history.samples[0].time <= 60
    assert bytes(held.data) == bytes(32), 'Decoder-held GOP must remain readable after history eviction'
    owner = weakref.ref(held.data.owner)
    held = None
    gc.collect()
    assert owner() is None, 'Evicted GOP temporary files must be released after the last reader'
    history.trim_bytes(128)
    assert history.byte_size <= 128 and history.packets[0].keyframe
    audio = AudioRing(seconds=4 / 48000, disk=True)
    audio.append(bytes([1, 2, 3, 4] * 6))
    assert audio.read(0, 6) == bytes(8) + bytes([1, 2, 3, 4] * 4)
    audio.append_at(7, bytes([5, 6, 7, 8]))
    assert audio.read(6, 2) == bytes(4) + bytes([5, 6, 7, 8])
    with patch('plugins.match_sync.video.PacketFile.append', side_effect=OSError('Disk full')):
        packet = av.Packet(bytes(32))
        packet.pts = packet.dts = 722
        packet.time_base = Fraction(1, 1)
        packet.is_keyframe = True
        history.append_packet(packet)
        assert not history.disk and history.cache_warning and history.seconds == 60
        assert isinstance(history.packets[-1].data, bytes), 'Live ingest must fall back when disk storage fails'
    with patch('plugins.match_sync.engine.tempfile.TemporaryFile', side_effect=OSError('Disk unavailable')):
        fallback = VideoHistory(disk=True, seconds=REPLAY_HISTORY_SECONDS)
        assert not fallback.disk and fallback.cache_warning
    # Actual encoded 12-minute content, created faster than wall time, then seek 10 minutes back.
    import numpy as np
    source = args.output / 'twelve-minute-content.mkv'
    with av.open(str(source), 'w') as container:
        stream = container.add_stream('libx264', rate=1)
        stream.width, stream.height, stream.pix_fmt = 160, 90, 'yuv420p'
        stream.options = {'preset': 'ultrafast', 'g': '2', 'bf': '0'}
        for index in range(720):
            image = np.zeros((90, 160, 3), dtype=np.uint8)
            image[:, :, 0] = index % 200
            frame = av.VideoFrame.from_ndarray(image, format='rgb24')
            frame.pts, frame.time_base = index, Fraction(1, 1)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    history = VideoHistory(disk=True, seconds=REPLAY_HISTORY_SECONDS)
    history.set_origin(1000)
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        history.codec = (stream.codec_context.name, stream.codec_context.extradata)
        history.video_size = (stream.width, stream.height)
        for packet in container.demux(video=0):
            if packet.size:
                history.append_packet(packet)
                for frame in packet.decode():
                    pts = float(frame.pts * frame.time_base)
                    history.append(1000 + pts, pts)
    reader = PictureReader()
    reader.configure(4 * 1024 * 1024, 1, active=True)
    try:
        for position in (1113, 1713):
            frame = history.frame_at(position)
            assert frame is not None
            deadline = time.monotonic() + 5
            result = None
            while result is None and time.monotonic() < deadline:
                result = reader.get(history, frame)
                time.sleep(.01)
            assert result is not None and abs(result[0] - position) < .1
            assert abs(result[1].pixelColor(0, 0).red() - (int(position - 1000) % 200)) < 5
    finally:
        reader.stop()
    print('PASS: 10-minute disk media, 60-second analysis, bounded GOP eviction, reader lifetime and disk PCM wrap/gaps')


if __name__ == '__main__':
    main()
