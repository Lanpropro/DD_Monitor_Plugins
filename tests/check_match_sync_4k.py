"""Offline UHD capture/presentation regression; never opens a live room."""
import argparse
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT), str(args.host.resolve())]
    os.chdir(args.host.resolve())
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from ddm import recording
    from plugins.match_sync import media, video
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix='ddm_uhd_') as folder:
        source = Path(folder) / '4k60.mkv'
        subprocess.run([recording.ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'lavfi', '-i', 'testsrc2=size=3840x2160:rate=60',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
            '-t', '7', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24',
            '-threads', '4', '-g', '120', '-bf', '2', '-c:a', 'pcm_s16le', str(source)],
            check=True, capture_output=True, timeout=90, creationflags=recording._FFMPEG_FLAGS)
        decoder = media.Decoder('local-uhd', {'url': str(source)})
        decoder.pictures.configure(256 * 1024 * 1024, 60)
        created = []
        create = video.av.CodecContext.create
        def create_codec(*args):
            codec = create(*args)
            created.append(codec)
            return codec
        proxy = SimpleNamespace(Packet=video.av.Packet, open=video.av.open,
                                CodecContext=SimpleNamespace(create=create_codec))
        try:
            decoder.start()
            deadline = time.monotonic() + 12
            while decoder.history.received_frames < 150 and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            assert decoder.history.received_frames >= 150, decoder.diagnostics()
            assert decoder.history.video_size == (3840, 2160)
            assert decoder.fps == 60
            stamp = decoder.history.origin + .5
            with patch.object(video, 'av', proxy):
                deadline = time.monotonic() + 3
                image = None
                while image is None and time.monotonic() < deadline:
                    image = decoder.picture_at(stamp, exact=True)
                    app.processEvents()
                    time.sleep(.004)
                assert image is not None, decoder.diagnostics()
                assert image[1].width() == 3840 and image[1].height() == 2160
                assert created[0].thread_count == 4
                assert created[0].thread_type.name == 'AUTO', created[0].thread_type
                with video.av.open(str(source)) as container:
                    index = round((image[0] - decoder.history.frames[0][0]) * 60)
                    reference = next(f for i, f in enumerate(container.decode(video=0)) if i == index)
                    assert image[1] == video.image_from_frame(reference), 'UHD source pixels changed'
                start = time.monotonic()
                shown, polls, missing = set(), 0, 0
                while time.monotonic() - start < 2:
                    elapsed = time.monotonic() - start
                    result = decoder.picture_at(stamp + elapsed)
                    polls += 1
                    if result is None:
                        missing += 1
                    else:
                        shown.add(result[0])
                        assert 0 <= stamp + elapsed - result[0] <= .17
                    app.processEvents()
                    time.sleep(.004)
                assert len(shown) >= 100, (len(shown), decoder.diagnostics())
                assert missing / polls < .1, (missing, polls, decoder.diagnostics())
                assert len(created) == 1, 'Forward UHD playback restarted decoding'
                assert sum(i.sizeInBytes() for i in decoder.pictures.images.values()) <= 256 * 1024 * 1024
                print(f'PASS: native 4K60 capture + playback, {len(shown) / 2:.1f} distinct fps, '
                      f'{missing}/{polls} unprepared polls; pixels and memory bound preserved')
                # Disabling and reopening a UHD picture worker must also release its frame threads.
                decoder.pictures.configure(256 * 1024 * 1024, 60, active=False)
                assert not decoder.pictures.images
                deadline = time.monotonic() + 3
                result = None
                while result is None and time.monotonic() < deadline:
                    result = decoder.picture_at(stamp + 1, exact=True)
                    app.processEvents()
                    time.sleep(.005)
                assert result is not None
        finally:
            decoder.stop()
            if decoder.thread is not None:
                decoder.thread.join(4)
                assert not decoder.thread.is_alive(), 'UHD capture did not stop'
            if decoder.pictures.thread is not None:
                decoder.pictures.thread.join(4)
                assert not decoder.pictures.thread.is_alive(), 'UHD picture worker did not stop'
        print('PASS: UHD reactivation and shutdown release decoder workers')


if __name__ == '__main__':
    main()
