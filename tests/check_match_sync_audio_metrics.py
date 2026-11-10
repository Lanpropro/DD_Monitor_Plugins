"""Numeric audio-read evidence and descriptor-only pump checks; no media or output device."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
from unittest.mock import patch


def check(app, host, module):
    audio = import_module(module.__package__ + '.audio')
    diagnostic = import_module(module.__package__ + '.diagnostics')
    metrics = diagnostic.AudioReadMetrics()
    metrics.record(10, 100., .01, 1., 3, 40., at=8.)
    metrics.record(10, 110., .01, 1., 3, 40., at=10.)
    assert metrics.samples[-1][2] is None, 'Isolation/return starts a new baseline'
    metrics.record(10, 110.01, .01, 1., 3, 40., at=10.01)
    metrics.record(10, 110.023, .01, 1.02, 4, 60., at=10.02)
    metrics.record(10, 110.031, .01, 1.02, 4, 60., at=10.03)
    metrics.record(20, 200., .01, 1., 0, 20., at=10.03)
    stats = metrics.stats(10.1)
    first, second = stats['sources']
    assert first == dict(audio_slot=1, blocks=4, gap_max_ms=3., overlap_max_ms=2.,
                         discontinuities_over_1ms=2, rate_min=1., rate_max=1.02,
                         channels=[3, 4], gain_min=40., gain_max=60.), first
    assert second['audio_slot'] == 2 and second['gap_max_ms'] == second['overlap_max_ms'] == 0
    assert not metrics.stats(12.)['sources']
    for index in range(1200):
        metrics.record(20, 201. + index * .001, .001, 1., 0, 20., at=20.)
    assert len(metrics.samples) == 1024 and metrics.stats(20.)['capacity_limited']
    for index in range(50):
        metrics.record(1000 + index, 0., .01, 1., 0, 20., at=21.)
    assert len(metrics.previous) == 32
    metrics.reset()
    assert not metrics.previous and not metrics.stats(21.)['sources']
    with patch.dict(os.environ, DDM_MATCH_DIAGNOSTICS='0'):
        disabled = diagnostic.AudioReadMetrics()
        disabled.record(10, 0., .01, 1., 3, 40.)
        assert not disabled.samples and not disabled.previous
    pump = module.AudioPump(host)
    output = object()
    requested = []
    class History:
        def pcm_at(self, start, frames):
            requested.append((start, frames))
            return output
    history = History()
    class Sink:
        free = 0
        def processedUSecs(self):
            return 0
        def bytesFree(self):
            count, self.free = self.free, 0
            return count
    class Device:
        accepted = 0
        def write(self, value):
            assert value is output
            return self.accepted
    sink, device = Sink(), Device()
    pump.sink, pump.device = sink, device
    pump.snapshot = (1., ((history, 0., 1.02, 3, 40.),))
    try:
        with patch.object(audio, 'route_pcm_s16_stereo', lambda data, channel: data), \
             patch.object(audio, 'resample_pcm', lambda data, frames: data), \
             patch.object(audio, 'mix_pcm', lambda inputs, frames: output):
            sink.free, device.accepted = 480 * 4, 240 * 4
            with patch.object(pump, 'update_snapshot'):
                pump.fill()
            assert set(pump.timings.stats()['stages']) == {
                'audio_lock_wait', 'audio_prepare', 'audio_mix', 'audio_device_write'}
            assert len(pump.read_metrics.samples) == 1
            assert abs(pump.read_metrics.previous[id(history)][2] - 490 / 48000 * .5) < 1e-9
            sink.free, device.accepted = 480 * 4, 0
            pump._fill()
            assert len(pump.read_metrics.samples) == 1, 'Rejected writes must not advance evidence'
            report = pump.read_metrics.stats()
            assert report['sources'][0]['channels'] == [3]
            assert report['sources'][0]['rate_min'] == 1.02
            probe = diagnostic.PlaybackProbe(host, Path('unused-audio-metrics.jsonl'))
            probe.audio = pump
            try:
                assert probe.snapshot()['audio_reads'] == report
                assert probe.snapshot()['audio_pipeline'] == pump.timings.stats()
            finally:
                probe.deleteLater()
        pump.sink = pump.device = None
        pump.stop()
        assert not pump.read_metrics.samples and not pump.read_metrics.previous
        assert not pump.timings.samples
        return dict(passed=True, media_playback=False, numeric_gap_and_overlap=True,
                    bounded_sources_and_samples=True, partial_and_rejected_write=True,
                    manual_channel_preserved=True, privacy=True, stop_reset=True,
                    disabled_no_records=True, background_export=True, audio_stage_export=True)
    finally:
        pump.sink = pump.device = None
        pump.stop()
        pump.deleteLater()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    host = QWidget()
    try:
        print(check(app, host, module))
    finally:
        host.close()
        app.processEvents()
        app.shutdown()


if __name__ == '__main__':
    main()
