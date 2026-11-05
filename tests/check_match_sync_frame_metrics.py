"""Timestamp-only checks; no generated images, audio, video or playback."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
from unittest.mock import patch


def check(module):
    cadence = module.FrameCadence()
    # Repainting the same displayed frame is not a new source frame.
    for wall, media in ((10., 100.), (10.008, 100.), (10.017, 100. + 1 / 60),
                        (10.025, 100. + 1 / 60), (10.05, 100. + 3 / 60)):
        cadence.record(wall, media)
    state = cadence.stats(10.1, 60)
    assert state['calls'] == 5 and state['distinct'] == 3, state
    assert state['skipped_frames'] == 1 and state['backwards'] == 0, state
    assert state['gap_max_ms'] == 33 and state['gaps_over_25ms'] == 1, state
    assert state['gaps_over_50ms'] == 0, state
    assert cadence.stats(12., 60)['calls'] == 0, 'Old observations must expire'
    cadence.samples.clear()
    cadence.record(20., 100.)
    cadence.record(20.2, 90.)
    state = cadence.stats(20.3, 60)
    assert state['backwards'] == 1 and state['skipped_frames'] == 0, state
    assert state['gap_max_ms'] == 200 and state['gaps_over_50ms'] == 1, state
    cadence.samples.clear()
    cadence.record(30., 100.)
    cadence.record(31.2, 100. + 1 / 60)
    state = cadence.stats(31.3, 60)
    assert state['distinct'] == 1 and state['gap_max_ms'] == 1200, state
    cadence.samples.clear()
    for index in range(2000):
        cadence.record(index / 1000, index / 60)
    assert len(cadence.samples) == 512
    assert cadence.stats(2., 60)['calls'] == 512
    with patch.dict(os.environ, {'DDM_MATCH_DIAGNOSTICS': '0'}):
        disabled = module.FrameCadence()
        disabled.record(1., 1.)
        assert not disabled.samples
    return dict(passed=True, timestamp_only=True, held_frame_not_counted_as_new=True,
                skipped_frame_detected=True, backwards_separated=True,
                bounded_samples=512, expiry_and_opt_out=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    print(check(import_module('plugins.match_sync.diagnostics')))


if __name__ == '__main__':
    main()
