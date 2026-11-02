"""Converter routing checks with descriptors only; no image/video data is generated."""
import argparse
from importlib import import_module
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch


def check(video):
    made = []
    class Converter:
        def __init__(self):
            self.owner = threading.get_ident()
            self.calls = []
            made.append(self)
        def reformat(self, frame, **options):
            assert self.owner == threading.get_ident(), 'Never share mutable scalers across workers'
            self.calls.append(options)
            return frame
    frame = SimpleNamespace(width=1920, height=1080, format=SimpleNamespace(name='yuv420p'))
    def convert(width):
        assert video.reformat_frame(frame, width=width, height=width * 9 // 16, format='gray') is frame
    with patch.object(video, '_conversion', threading.local()), patch.object(video, 'VideoReformatter', Converter):
        for _ in range(5):
            for width in (160, 320, 640):
                convert(width)
        assert len(made) == 3, ('Alternating analysis sizes rebuilt a scaler', len(made))
        assert all(len({tuple(sorted(call.items())) for call in converter.calls}) == 1 for converter in made)
        assert all(call['threads'] == 1 for converter in made for call in converter.calls)
        video.reformat_frame(frame, format='bgra')
        assert 'threads' not in made[-1].calls[-1], 'Do not change full-resolution display conversion threads'
        convert(160)  # Make this configuration most recently used before eviction.
        video.reformat_frame(frame, width=80, height=45, format='gray', threads=2)
        assert made[-1].calls[-1]['threads'] == 2
        assert len(video._conversion.reformatters) == 4
        before = len(made)
        convert(160)
        assert len(made) == before, 'Eviction discarded a recently used scaler'
        convert(320)
        assert len(made) == before + 1, 'Old configurations must be evicted under the bound'
        errors = []
        def worker():
            try:
                for _ in range(3):
                    convert(160)
            except BaseException as error:
                errors.append(error)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        before = len(made)
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()
        assert not errors, errors
        assert len(made) == before + 2
    return dict(passed=True, media_generated=False, analysis_configurations_reused=3,
                gray_threads=1, full_resolution_options_preserved=True,
                cache_limit=4, worker_isolation=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    print(check(import_module('plugins.match_sync.video')))


if __name__ == '__main__':
    main()
