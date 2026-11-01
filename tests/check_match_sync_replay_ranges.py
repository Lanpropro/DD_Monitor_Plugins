"""Replay interval reuse preserves gaps, offsets, eviction and PCM limits."""
import argparse
from collections import deque
from importlib import import_module
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace


def check(module):
    replay = import_module(module.__package__ + '.replay')
    class History:
        def __init__(self, intervals):
            self.intervals = intervals
            self.origin = 0
            self.audio = SimpleNamespace(end=700 * 48000, capacity=720 * 48000)
            self.calls = 0
        def bounds_at(self, target):
            self.calls += 1
            return next((bounds for bounds in self.intervals if bounds[0] <= target <= bounds[1]), None)

    def reference(timeline, now, histories):
        lower = upper = None
        for stamp, shifts in reversed(timeline.states):
            if stamp < now - 600:
                break
            if not timeline.valid(stamp, shifts, histories):
                if upper is not None:
                    break
                continue
            if upper is None:
                upper = stamp
            lower = stamp
        return (lower, upper) if lower is not None else None

    timeline = replay.ReplayTimeline()
    timeline.states = deque((100 + index * .2, {'1': 0}) for index in range(3000))
    history = History([(0, 700)])
    expected = reference(timeline, 700, {'1': history})
    old_calls = history.calls
    history.calls = 0
    assert timeline.available(700, {'1': history}) == expected
    assert history.calls == 1 and old_calls == 3000, (history.calls, old_calls)
    # No interval cache survives the call, including after trimming/reconnection.
    history.intervals = []
    assert timeline.available(700, {'1': history}) is None
    replacement = History([(300, 650)])
    assert timeline.available(700, {'1': replacement}) == reference(timeline, 700, {'1': replacement})
    randomizer = random.Random(31)
    for _ in range(80):
        histories = {'1': History([(0, 230), (260, 700)]), '2': History([(40, 400), (430, 690)])}
        for item in histories.values():
            item.audio.end = randomizer.randrange(450, 720) * 48000
            item.audio.capacity = randomizer.randrange(100, 720) * 48000
        timeline.states = deque((100 + i * 2, {'1': randomizer.choice((0, 20, -40)),
                                              '2': randomizer.choice((4, -80, 35))}) for i in range(300))
        if randomizer.randrange(2):
            timeline.states[-5][1].pop('2')
        assert timeline.available(700, histories) == reference(timeline, 700, histories)
    timeline.states = deque([(600, {'1': 0}), (601, {'1': 0})])
    history = History([(0, 700)])
    history.audio.end = 601 * 48000
    assert timeline.available(601, {'1': history}) == (600, 600), 'PCM upper bound remains exclusive'
    history.origin = None
    assert timeline.available(601, {'1': history}) is None
    assert timeline.available(601, {}) is None
    return {'passed': True, 'states': 3000, 'bounds_queries_before': old_calls,
            'bounds_queries_after': 1, 'gap_offset_audio_cases': 80}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    from plugins.match_sync import replay as module
    print(check(module))


if __name__ == '__main__':
    main()
