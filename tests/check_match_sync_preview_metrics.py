"""Preview lifecycle evidence only; no playback or performance claim."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module):
    diagnostic = import_module(module.__package__ + '.diagnostics')
    from PySide6.QtTest import QTest
    clock = [10.]
    metrics = diagnostic.PreviewMetrics()
    with patch.object(diagnostic.time, 'perf_counter', side_effect=lambda: clock[0]):
        metrics.request()
        clock[0] += .08
        clock[0] += .08
        metrics.ready(.02)
        metrics.ready(.03)
        clock[0] += .01
        metrics.paint()
        metrics.paint()
        result = metrics.snapshot(clock[0])
        assert result['targets'] == result['images_ready'] == result['images_painted'] == 1
        assert result['request_to_image_ms'] == 160 and result['request_to_paint_ms'] == 170
        assert result['frame_error_ms'] == 20 and not result['pending']
        assert result['request_age_ms'] == 170
        metrics.request()
        metrics.request()
        assert metrics.snapshot(clock[0])['superseded'] == 1
        assert metrics.state['request_to_image_ms'] is None
        metrics.dismiss()
        metrics.ready(0.)
        metrics.paint()
        assert not metrics.state['active'] and not metrics.state['pending']
        assert metrics.state['dismissed_pending'] == 1
        metrics.dismiss()
        assert metrics.state['dismissed_pending'] == 1
        assert metrics.state['images_ready'] == 1
        metrics.request()
        metrics.ready(0., cached=True)
        assert metrics.state['cache_hits'] == 1
        assert metrics.state['images_ready'] == 2
    prepared = import_module(module.__package__ + '.preview_frames').PreviewFrames()
    prepared.thread_cpu_s = .012
    prepared.private_url = 'https://private.invalid/DO_NOT_LOG'
    metrics.frames = prepared
    with prepared.lock:
        assert metrics.snapshot(diagnostic.time.perf_counter())['worker_busy']
    result = metrics.snapshot(diagnostic.time.perf_counter())
    assert result['thread_cpu_total_ms'] == 12 and result['sample_fps'] == 8
    assert result['prepared_bytes'] == 0 and 'worker_busy' not in result
    with tempfile.TemporaryDirectory() as directory:
        probe = diagnostic.PlaybackProbe(host, Path(directory) / 'preview.jsonl')
        try:
            probe.preview = metrics
            metrics.request()
            probe.start()
            QTest.qWait(1100)
            probe.stop()
            raw = probe.path.read_text(encoding='utf-8')
            rows = [json.loads(line) for line in raw.splitlines()]
            assert rows[0]['replay_preview_metrics'] == rows[0]['replay_preview_continuous'] == 1
            exported = [row['replay_preview'] for row in rows if row['event'] == 'playback']
            assert exported and exported[-1]['active'] and exported[-1]['pending']
            assert 'private.invalid' not in raw and 'DO_NOT_LOG' not in raw
            assert rows[-1]['event'] == 'stop'
        finally:
            probe.stop()
            probe.deleteLater()
            app.processEvents()
    with patch.dict(os.environ, DDM_MATCH_DIAGNOSTICS='0'):
        disabled = diagnostic.PreviewMetrics()
        disabled.request()
        disabled.ready(0.)
        disabled.paint()
        assert not disabled.timings.samples and disabled.snapshot(0.) == {}
    return dict(passed=True, lifecycle_counts=True, latency_stages=True, superseded_requests=True,
                nonblocking_worker_snapshot=True, export_privacy=True, disabled=True, media_playback=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    host = QWidget()
    try:
        print(check(app, host, import_module('plugins.match_sync.viewer')))
    finally:
        host.close()
        app.shutdown()


if __name__ == '__main__':
    main()
