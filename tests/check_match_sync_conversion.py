"""Frame conversion must reuse a worker-local scaler and own displayed pixels."""
import sys
from pathlib import Path
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.match_sync import video
from PySide6.QtGui import QImage


def run():
    made = []
    factory = video.VideoReformatter
    def create():
        made.append(threading.get_ident())
        return factory()
    errors = []
    def worker():
        try:
            retained = []
            for width, height, color, fmt in ((97, 55, 17, 'yuv420p'), (128, 72, 180, 'yuv420p'),
                                               (97, 55, 230, 'yuv444p'), (128, 72, 80, 'yuv420p')):
                frame = video.av.VideoFrame.from_ndarray(
                    video.np.full((height, width, 3), color, dtype=video.np.uint8), format='rgb24').reformat(format=fmt)
                rgb = frame.reformat(format='bgra')
                expected = QImage(bytes(rgb.planes[0]), width, height, rgb.planes[0].line_size,
                                  QImage.Format_ARGB32).copy()
                actual = video.image_from_frame(frame)
                assert actual == expected, (width, height, color, fmt)
                gray = video.reformat_frame(frame, width=32, height=18, format='gray')
                old_gray = frame.reformat(width=32, height=18, format='gray')
                assert gray.to_ndarray().tobytes() == old_gray.to_ndarray().tobytes()
                retained.append((actual, expected))
            assert all(a == b for a, b in retained), 'Reusing a scaler mutated an earlier display image'
        except BaseException as exc:
            errors.append(exc)
    with patch.object(video, 'VideoReformatter', side_effect=create):
        workers = [threading.Thread(target=worker) for _ in range(2)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(10)
            assert not thread.is_alive()
    assert not errors, errors
    assert len(made) == 2, ('Converters should be reused within each worker', made)
    print('PASS: one converter per worker; RGB/gray pixels, size/format changes and retained image ownership preserved')


if __name__ == '__main__':
    run()
