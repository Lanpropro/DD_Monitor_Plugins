"""Pipeline numbers and descriptor routing only; no image/audio/video data."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, diagnostic, media):
    from PySide6.QtTest import QTest
    timings = diagnostic.StageTimings()
    timings.record('decode', .999, at=8.)
    for index, duration in enumerate((.001, .002, .003, .004, .005)):
        timings.record('decode', duration, items=2, at=10. + index * .01)
    timings.record('copy', .003, payload_bytes=1920 * 1080 * 4, at=10.1)
    state = timings.stats(10.2)
    decode = state['stages']['decode']
    assert decode == dict(calls=5, items=10, payload_bytes=0, total_ms=15., p50_ms=3., p95_ms=4., max_ms=5.), decode
    assert state['stages']['copy']['payload_bytes'] == 1920 * 1080 * 4
    assert not timings.stats(12.)['stages']
    # Baseline instrumentation counts repeats but does not reuse conversions yet.
    made = []
    def convert(frame, **options):
        result = SimpleNamespace(source=frame, options=options)
        made.append(result)
        return result
    frame = SimpleNamespace(width=1920, height=1080)
    conversions = {}
    with patch.object(media, 'reformat_frame', convert):
        first = media.analysis_gray(frame, timings, conversions, width=320, height=180, format='gray')
        second = media.analysis_gray(frame, timings, conversions, width=320, height=180, format='gray')
        third = media.analysis_gray(frame, timings, conversions, width=640, height=360, format='gray')
        assert first is not second and third is not second and len(made) == 3
    stages = timings.stats()['stages']
    assert stages['analysis_gray_repeat']['calls'] == 1
    assert stages['analysis_gray_unique']['calls'] == 2
    assert stages['analysis_gray_convert']['calls'] == 3
    timings.samples.clear()
    def produce():
        for _ in range(3000):
            timings.record('bounded', .001, at=20.)
    worker = threading.Thread(target=produce)
    worker.start()
    for _ in range(5):
        timings.stats(20.)
    worker.join(2)
    assert not worker.is_alive() and len(timings.samples) == 2048
    assert timings.stats(20.)['capacity_limited']
    assert not timings.stats(22.)['stages']
    with patch.dict(os.environ, DDM_MATCH_DIAGNOSTICS='0'):
        disabled = diagnostic.StageTimings()
        disabled.record('disabled', 1.)
        assert not disabled.samples
    with tempfile.TemporaryDirectory(prefix='ddm-pipeline-metrics-') as directory:
        probe = diagnostic.PlaybackProbe(host, Path(directory) / 'pipeline.jsonl')
        source = SimpleNamespace(fps=60., analysis_size=(320, 180), timings=diagnostic.StageTimings(),
            pictures=SimpleNamespace(timings=diagnostic.StageTimings()),
            source_url='https://private.invalid/DO_NOT_LOG', source_headers={'Cookie': 'DO_NOT_LOG'})
        try:
            probe.start()
            probe.pipeline_sources = ((0, source),)
            source.timings.record('capture_video_decode', .002, items=1)
            source.pictures.timings.record('display_image_copy', .001, payload_bytes=100)
            probe.timings.record('main_draw_image', .003)
            report = probe.snapshot()
            assert report['source_pipelines'][0]['capture']['stages']['capture_video_decode']['items'] == 1
            assert report['source_pipelines'][0]['display']['stages']['display_image_copy']['payload_bytes'] == 100
            assert report['gui_pipeline']['stages']['main_draw_image']['calls'] == 1
            assert report['process_cpu_cores'] >= 0
            QTest.qWait(1100)
            probe.stop()
            assert not probe.pipeline_sources
            raw = probe.path.read_text(encoding='utf-8')
            rows = list(map(json.loads, raw.splitlines()))
            assert rows[0]['schema'] == 3 and rows[-1]['event'] == 'stop'
            assert rows[0]['capture_code'] and rows[0]['analysis_gray_code']
            assert any(row.get('source_pipelines') for row in rows if row['event'] == 'playback')
            assert 'DO_NOT_LOG' not in raw and 'private.invalid' not in raw
        finally:
            probe.stop()
            probe.deleteLater()
            app.processEvents()
    return dict(passed=True, media_generated=False, timestamp_only=True, independent_stages=True,
                same_frame_repeat_count=True, baseline_conversions_preserved=True,
                bounded_nonblocking_snapshot=True, all_source_export=True, privacy=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    host = QWidget()
    try:
        print(check(app, host, import_module('plugins.match_sync.diagnostics'), import_module('plugins.match_sync.media')))
    finally:
        host.close()
        app.processEvents()
        app.shutdown()


if __name__ == '__main__':
    main()
