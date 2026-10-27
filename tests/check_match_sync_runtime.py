"""Two native cached feeds, real matching, audio and on-picture chat; local only."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def run(app, host, output, sources, module=None):
    from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget
    from ddm import plugins
    if module is None:
        from plugins.match_sync import viewer as module
    from plugins.match_sync.media import Events

    class Chat:
        def __init__(self, *_args):
            self.events = Events()
        def start(self):
            pass
        def stop(self):
            pass

    with tempfile.TemporaryDirectory(dir=output.parent) as directory, patch.object(module, 'Chat', Chat):
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        viewer = module.Viewer(plugins.PluginContext(manager, 'runtime_check'), sources)
        if hasattr(host, '_content'):
            viewer.embed(host._content)
        else:
            viewer.resize(1400, 900)
        viewer.show()
        for key in sources:
            viewer.add_room(key, {'alias': key, 'quality': 10000})
        viewer.prefer_highest = False
        viewer.picture.danmaku_button.setChecked(True)
        viewer.toggle_running()
        events, gaps = [], []
        phase = ['warmup']
        last = [time.perf_counter()]
        previous_frame = [None]
        previous_stamp = [None]
        comment_x = {}
        scroll_moved = [False]
        loop = QEventLoop()
        chat = QTimer()
        def message():
            for row in viewer.rows.values():
                row.chat.events.message.emit({'uname': 'fixture', 'text': 'Local scrolling comment'})
        chat.timeout.connect(message)
        chat.start(25)
        observer = QTimer()
        def observe():
            now = time.perf_counter()
            if phase[0] == 'measure':
                events.append(max(0, (now - last[0]) * 1000 - 5))
                if viewer.canvas.frame_key != previous_frame[0]:
                    if previous_stamp[0] is not None:
                        gaps.append((now - previous_stamp[0]) * 1000)
                    previous_stamp[0] = now
            previous_frame[0] = viewer.canvas.frame_key
            last[0] = now
            for comment in viewer.video_danmaku.comments:
                before = comment_x.get(id(comment))
                if before is not None and comment.x < before - .5:
                    scroll_moved[0] = True
                comment_x[id(comment)] = comment.x
        observer.timeout.connect(observe)
        observer.start(5)
        report = {}
        try:
            QTimer.singleShot(10500, loop.quit)
            loop.exec()
            assert all(row.decoder.history.received_frames > 500 for row in viewer.rows.values())
            origins = {key: row.decoder.history.origin for key, row in viewer.rows.items()}
            reference = viewer.alignment.reference
            viewer.alignment.lags = {key: origin - origins[reference] for key, origin in origins.items()}
            common_end = min(row.decoder.history.latest()[0] - viewer.alignment.lags[key]
                             for key, row in viewer.rows.items())
            viewer.stable_reference_shift = common_end - 8 - viewer.audio.clock()
            viewer.rendered_shifts.clear()
            viewer.rematch_rooms.clear()
            viewer.rematch_admit.clear()
            for key, row in viewer.rows.items():
                row.match_confirmed = row.user_confirmed = True
                row.user_confirmed_lag = viewer.alignment.lags[key]
                row.awaiting_confirmation = False
            phase[0] = 'measure'
            frames, cpu, started = viewer.canvas.presented, time.process_time(), time.perf_counter()
            QTimer.singleShot(15000, loop.quit)
            loop.exec()
            elapsed = time.perf_counter() - started
            def stats(values):
                values = sorted(values)
                return {'p95': values[int((len(values) - 1) * .95)] if values else None,
                        'max': values[-1] if values else None}
            overlay = viewer.video_danmaku
            image = QImage(overlay.size() * overlay.devicePixelRatioF(), QImage.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(overlay.devicePixelRatioF())
            image.fill(Qt.transparent)
            overlay.render(image, QPoint(), QRegion(), QWidget.DrawChildren)
            painted = sum(alpha > 0 for alpha in bytes(image.constBits())[3::4])
            image.save(str(output.with_suffix('.overlay.png')))
            report = {'fps': (viewer.canvas.presented - frames) / elapsed,
                      'cpu_cores': (time.process_time() - cpu) / elapsed,
                      'frame_gap_ms': stats(gaps), 'event_loop_lateness_ms': stats(events),
                      'overlay_comments': len(viewer.video_danmaku.comments),
                      'overlay_quick_requested': viewer.video_danmaku._quick_requested,
                      'overlay_native': viewer.video_danmaku.native_video,
                      'painted_comment_pixels': painted, 'scroll_moved': scroll_moved[0],
                      'diagnostics': {key: row.decoder.diagnostics() for key, row in viewer.rows.items()},
                      'ahead': {key: row.decoder.history.bounds()[1] - viewer.audio.clock() - viewer.shifts()[key]
                                for key, row in viewer.rows.items()}}
            output.write_text(json.dumps(report, indent=2), encoding='utf-8')
            assert report['overlay_comments'] > 0 and not report['overlay_quick_requested']
            assert painted > 100 and scroll_moved[0], 'Scrolling comments must paint pixels and advance'
            assert report['fps'] >= 55, report
            assert report['frame_gap_ms']['max'] < 150, report
            print(json.dumps(report), flush=True)
        finally:
            observer.stop()
            chat.stop()
            decoders = [row.decoder for row in viewer.rows.values() if row.decoder is not None]
            viewer.close()
            for decoder in decoders:
                decoder.thread.join(5)
                assert not decoder.thread.is_alive()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(args.host.resolve()), str(ROOT)]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from ddm import recording
    app = QApplication([])
    sources = {}
    for key, codec in (('1', 'libx264'), ('2', 'libx265')):
        path = args.output.parent / f'{codec}-1440p60.mkv'
        if not path.exists():
            print(f'Generating {path.name}', flush=True)
            options = ['-threads', '2'] if codec == 'libx264' else ['-x265-params', 'pools=2:frame-threads=2:log-level=error']
            subprocess.run([recording.ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-f', 'lavfi', '-i', 'testsrc2=size=2560x1440:rate=60', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                '-t', '35', '-c:v', codec, '-preset', 'ultrafast', *options, '-g', '120', '-bf', '2',
                '-c:a', 'pcm_s16le', str(path)], check=True, capture_output=True, timeout=120,
                creationflags=recording._FFMPEG_FLAGS)
        sources[key] = {'url': str(path), 'quality': 10000}
    host = QWidget()
    host.settings = {'video_danmaku_size': 24, 'video_danmaku_area': 100}
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        try:
            run(app, host, args.output, sources)
        finally:
            host.close()


if __name__ == '__main__':
    main()
