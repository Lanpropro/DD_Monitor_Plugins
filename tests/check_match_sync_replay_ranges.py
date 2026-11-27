"""Replay interval reuse preserves gaps, offsets, eviction and PCM limits."""
import argparse
from collections import deque
from fractions import Fraction
from importlib import import_module
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch


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
    for minutes in (5, 10, 20, 30):
        configured = replay.ReplayTimeline(minutes * 60)
        history = History([(0, 2000)])
        history.audio.end = 2001 * 48000
        history.audio.capacity = 2001 * 48000
        for stamp in range(2001):
            configured.observe(stamp, stamp, {'1': 0})
            configured.record_message(stamp, {'text': 'fixture'})
        first = 2000 - minutes * 60
        assert configured.available(2000, {'1': history}) == (first, 2000)
        assert configured.messages[0][0] == first
        assert configured.seek(0, 2000, {'1': history}) and configured.cursor == first
    check_reconnect(replay)
    return {'passed': True, 'states': 3000, 'bounds_queries_before': old_calls,
            'bounds_queries_after': 1, 'gap_offset_audio_cases': 80, 'reconnect_ranges': True,
            'duration_presets': (5, 10, 20, 30)}


def check_reconnect(replay):
    media = import_module(replay.__package__ + '.media')
    video = import_module(replay.__package__ + '.video')
    presentation = import_module(replay.__package__ + '.presentation')
    decoder = media.Decoder('fixture')
    base = 100.

    class Packet:
        dts, duration, time_base, is_keyframe = None, 1, Fraction(1, 5), True

        def __bytes__(self):
            return b'packet'

    packet = Packet()
    first_pcm, second_pcm, third_pcm = b'\x01\x00' * 2, b'\x02\x00' * 2, b'\x03\x00' * 2

    def fill(history, start, samples, pcm):
        history.set_origin(start)
        history.audio.append(pcm * (samples * 9600 + 1))
        for index in range(samples + 1):
            packet.pts = index
            history.append_packet(packet)
            history.append(start + index / 5, index / 5)

    with patch.object(video.time, 'monotonic', return_value=base + 20), \
         patch.object(decoder, '_history', side_effect=lambda: video.VideoHistory(seconds=20, disk_cache=True)):
        decoder.history = decoder._history()
        first = decoder.history
        fill(first, base, 20, first_pcm)
        decoder.playback_timestamp = base + 3
        decoder._reconnect_history()
        second = decoder.history
        assert second.previous is first and second.retained == (first,)
        assert first.audio.capacity == first.audio.end
        fill(second, base + 4.6, 10, second_pcm)
        second.previous = None  # Normal live handover has already finished.
        decoder._reconnect_history()
        third = decoder.history
        fill(third, base + 6.4, 10, third_pcm)
        third.previous = None
        assert third.retained == (second, first)
        assert third.frame_at(base + 2)[0] == base + 2
        assert third.frame_at(base + 4.3) is None
        assert third.segment_at(base + 6.4) is third, 'Prefer the new packet epoch at overlapping times'
        assert third.pcm_at(base + 2, 2) == first_pcm * 2
        assert third.pcm_at(base + 6.4 - 1 / 48000, 2) == second_pcm + third_pcm

        timeline = replay.ReplayTimeline()
        timeline.states = deque((base + index / 5, {'fixture': 0.}) for index in range(93))
        histories = {'fixture': third}
        now = base + 8.4
        assert timeline.available(now, histories) == (base, now)
        assert timeline.seek(base + 2, now, histories)
        assert timeline.cursor == base + 2
        assert timeline.seek(base + 4.3, now, histories)
        assert timeline.cursor == base + 4.6
        timeline.seek(base + 3, now, histories)
        viewer = SimpleNamespace(replay=timeline, replay_histories=lambda: histories, rows={
            'fixture': SimpleNamespace(reset_pending=False, sync_state='已同步')},
            live_clock=lambda: now, target_shifts=lambda: {'fixture': 0.}, _smooth_shifts=lambda shifts: shifts,
            _recover_clock=lambda clock, shifts: clock, alignment=SimpleNamespace(reference='fixture'),
            audio=SimpleNamespace(flush=Mock()), return_live=Mock(), overlay_pending={'fixture': object()})
        with patch.object(presentation.time, 'monotonic', return_value=now + 1.3):
            _, _, _, shown, _ = presentation._playback_position(viewer, None)
        assert shown == base + 4.6 and timeline.active and timeline.cursor == shown
        viewer.return_live.assert_not_called()
        viewer.audio.flush.assert_called_once()
        assert not viewer.overlay_pending

        decoder._reconnect_history()
        fourth = decoder.history
        fill(fourth, base + 16, 12, third_pcm)
        fourth.previous = None
        assert timeline.available(base + 18.4, {'fixture': fourth}) == (base + 16, base + 18.4)
        assert timeline.advance_gap(base + 10, base + 18.4, {'fixture': fourth}) is None
        fourth.byte_limit = fourth.byte_size + third.byte_size
        fourth._trim_retained(base + 20)
        assert fourth.retained == (third,), 'Apply a single byte budget to all epochs of one source'
        fourth._trim_retained(base + 30)
        assert fourth.retained == (), 'Expire a complete old epoch once it leaves the cache window'


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
