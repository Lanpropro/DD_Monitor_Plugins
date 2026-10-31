"""Common content can move/scale inside independent overlays; never infer time from stills."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import time
import tempfile
import threading
import subprocess
from types import SimpleNamespace
from unittest.mock import patch


def check(module):
    import cv2
    import numpy as np
    from PySide6.QtGui import QImage
    regions = import_module(module.__package__ + '.regions')
    rng = np.random.default_rng(14)
    background = rng.integers(35, 200, (180, 320), dtype=np.uint8)
    background = cv2.GaussianBlur(background, (3, 3), 0)
    for _ in range(80):
        x, y = rng.integers((0, 0), (300, 160))
        cv2.rectangle(background, (int(x), int(y)), (int(x + 15), int(y + 12)), int(rng.choice((10, 240))), 2)
    def frame(t, wrapped=False, unrelated=False):
        image = background.copy()
        for index in range(14):
            phase = t * (1 + index * .11) + index + (13 if unrelated else 0)
            x = round(160 + 130 * np.sin(phase * .7))
            y = round(90 + 65 * np.cos(phase * .9))
            cv2.circle(image, (x, y), 8 + index % 4, 0 if index % 2 else 255, -1)
        if wrapped:
            canvas = np.full((180, 320), 210, np.uint8)
            canvas[16:160, 8:264] = cv2.resize(image, (256, 144))
            cv2.rectangle(canvas, (273, 10), (316, 165), 30, -1)
            cv2.circle(canvas, (290, 150), 16, round(128 + 100 * np.sin(t * 3)), -1)
            image = canvas
        large = QImage(image.data, 320, 180, image.strides[0], QImage.Format_Grayscale8).copy()
        small = large.scaled(160, 90)
        return small, large
    def sequence(wrapped=False, unrelated=False, static=False):
        result = []
        for index in range(240):
            t = index / 10
            small, large = frame(0 if static else t, wrapped, unrelated)
            result.append((100 + t + (1.4 if wrapped else 0), small, large if index % 10 == 0 else None))
        return result
    a, b = sequence(), sequence(wrapped=True)
    began = time.perf_counter()
    located = regions.locate_regions(a, b)
    assert located is not None, 'Find corresponding gameplay despite a shifted, scaled wrapper'
    result = regions.match_regions(a, b, located)
    assert result.refined and result.lag is not None and abs(result.lag - 1.4) < .11, result
    assert result.confidence >= .65 and result.regions
    cached = regions.match_regions(a, b, result.regions, result.lag)
    assert cached.lag is not None and abs(cached.lag - result.lag) < .11
    assert regions.match_regions(sequence(static=True), sequence(wrapped=True, static=True)).lag is None
    assert regions.match_regions(a, sequence(wrapped=True, unrelated=True), located).lag is None, 'The same game background is not enough to establish time'
    media = import_module(module.__package__ + '.media')
    from ddm import recording
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'region-capture.mkv'
        executable = recording.ffmpeg_path()
        subprocess.run([executable, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=20', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
            '-t', '12', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'pcm_s16le', str(path)],
            check=True, capture_output=True, timeout=30, creationflags=recording._FFMPEG_FLAGS)
        command = media.decode_command
        def unpaced(*args, **kwargs):
            result = command(*args, **kwargs)
            index = result.index('-readrate')
            del result[index:index + 2]
            return result
        decoder = media.Decoder('offline-regions')
        try:
            with patch.object(media, 'decode_command', side_effect=unpaced):
                decoder._capture(executable, str(path), {})
            assert len(decoder.region_frames) >= 80
            assert all(image.width() == 160 and image.height() == 90 for _stamp, image, _large in decoder.region_frames)
            assert sum(large is not None for _stamp, _image, large in decoder.region_frames) >= 10
            assert decoder.region_frames[0][1] != decoder.region_frames[-1][1], 'Retained gray images must own independent pixels'
        finally:
            decoder.stop()
    from PySide6.QtWidgets import QWidget
    from ddm import plugins
    with tempfile.TemporaryDirectory() as directory:
        host = QWidget()
        host.settings, host.rooms = {}, []
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        viewer = module.Viewer(plugins.PluginContext(manager, 'regions_check'), {})
        try:
            for key, frames in (('1', a), ('2', b)):
                viewer.add_room(key)
                samples = regions.region_samples(frames, (0, 0, 1, 1))
                history = SimpleNamespace(lock=threading.RLock(), origin=100, details=samples,
                                          snapshots=lambda data=samples: data[::5])
                viewer.rows[key].decoder = SimpleNamespace(history=history, lock=threading.RLock(), region_frames=frames)
            captured = []
            viewer.results = SimpleNamespace(completed=SimpleNamespace(emit=lambda *args: captured.append(args)))
            viewer.running = True
            with patch.object(viewer, 'live_clock', return_value=124), patch.object(viewer, 'shifts', return_value={'1': 0, '2': 0}), \
                    patch.object(module, 'match_scenes', return_value=module.Match(None, 0, 'whole frame mismatch')), \
                    patch.object(module, 'refine_match', return_value=module.Match(None, 0, 'whole frame mismatch')):
                viewer.analyse()
                deadline = time.monotonic() + 5
                while not captured and time.monotonic() < deadline:
                    time.sleep(.01)
            assert captured and captured[0][2]['2'].regions, (captured, viewer.matching, viewer.alignment.reference)
            assert abs(captured[0][2]['2'].lag - 1.4) < .11
        finally:
            viewer.running = False
            for row in viewer.rows.values():
                row.decoder = None
            viewer.close()
            viewer.deleteLater()
            host.close()
    return {'passed': True, 'scaled_wrapper_delay': result.lag, 'static_rejected': True,
            'independent_gameplay_rejected': True, 'real_decoder_gray_history': True,
            'seconds': round(time.perf_counter() - began, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(module))


if __name__ == '__main__':
    main()
