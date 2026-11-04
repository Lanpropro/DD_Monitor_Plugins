"""Chat highlight regression with isolated Qt, local media and scripted chat.

Scripted messages verify functionality, not real-stream highlight quality or performance.
"""
import argparse
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', required=True, type=Path)
parser.add_argument('--screenshots', type=Path)
parser.add_argument('--native', action='store_true')
args, remainder = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'windows' if args.native else 'offscreen'
os.environ['PATH'] = str(args.host.resolve()) + os.pathsep + os.environ['PATH']
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtTest import QTest
from ddm import config, images
from ddm.plugins import PluginContext, PluginManager
from ddm.recording import RecordingManager, input_args

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ddcut_heat_test', ROOT / 'plugins/DDCut_Replay/plugin.py')
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)
heat = sys.modules[spec.name + '.highlights']
media = sys.modules[spec.name + '.media']
ui = sys.modules[spec.name + '.workspace']
hls = sys.modules[spec.name + '.hls']
app = QApplication.instance() or QApplication([])
for font_path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/segoeui.ttf'):
    if Path(font_path).is_file():
        QFontDatabase.addApplicationFont(font_path)
app.setFont(QFont('Microsoft YaHei', 10))
ffmpeg = shutil.which('ffmpeg')


def until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Local highlight check timed out')


def run(*args):
    subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', *map(str, args)],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


class HighlightTests(unittest.TestCase):
    def test_threshold_keywords_and_distinct_bursts(self):
        records = [(stamp + index / 10, '哈哈 高能') for stamp in range(35, 55) for index in range(5)]
        records += [(stamp + index / 10, '精彩') for stamp in range(155, 175) for index in range(4)]
        result = heat.detect_peaks(records, 210)
        self.assertEqual(len(result['peaks']), 2)
        self.assertTrue(all(0 <= peak['start'] < peak['end'] <= 210 for peak in result['peaks']))
        self.assertLess(result['peaks'][0]['end'], result['peaks'][1]['start'])
        self.assertEqual(result['threshold'], 80)
        filtered = heat.detect_peaks(records, 210, keywords=['精彩'])
        self.assertEqual(filtered['matched'], 80)
        self.assertEqual(len(filtered['peaks']), 1)
        self.assertGreater(filtered['peaks'][0]['start'], 130)
        strict = heat.detect_peaks(records, 210, percent=100)
        self.assertEqual(strict['threshold'], 100)
        self.assertLess(sum(p['level'] == 'core' for p in strict['points']),
                        sum(p['level'] == 'core' for p in result['points']))
        self.assertFalse(heat.detect_peaks(records, 210, keywords=['不存在'])['peaks'])

    def test_quiet_short_and_contiguous_peak(self):
        self.assertFalse(heat.detect_peaks([], 300)['peaks'])
        self.assertFalse(heat.detect_peaks([(3, '哈哈')], 20)['peaks'])
        result = heat.detect_peaks([(i, 'burst') for i in range(40, 70)], 120)
        self.assertEqual(len(result['peaks']), 1)
        self.assertEqual(result['matched'], 30)

    def test_incremental_reader_and_reconnect_media_time(self):
        with tempfile.TemporaryDirectory(prefix='ddcut-chat-reader-') as temporary:
            path = Path(temporary) / 'danmaku.jsonl'
            records = [{'received_at': 1001, 'text': 'first'}, {'received_at': 1025, 'text': 'gap'},
                       {'received_at': 1041, 'text': 'second'}, {'received_at': 1059, 'text': 'tail'}]
            segments = [{'wall_start': 1000, 'duration': 20}, {'wall_start': 1040, 'duration': 15}]
            self.assertEqual(heat.media_records(records, segments), [(1, 'first'), (21, 'second')])
            path.write_text(''.join(json.dumps(row) + '\n' for row in records), encoding='utf-8')
            reader = heat.HighlightReader()
            self.assertEqual(reader.analyze(path, segments, 35, 80, [])['total'], 2)
            self.assertEqual(reader.analyze(path, segments, 35, 80, [])['total'], 2)
            segments.append({'wall_start': 1055, 'duration': 5})
            self.assertEqual(reader.analyze(path, segments, 40, 80, [])['total'], 3)
            with path.open('a', encoding='utf-8') as handle:
                handle.write('{"received_at": 1058, "text": "partial"}')
            self.assertEqual(reader.analyze(path, segments, 40, 80, [])['total'], 3)
            with path.open('a', encoding='utf-8') as handle:
                handle.write('\n')
            self.assertEqual(reader.analyze(path, segments, 40, 80, [])['total'], 4)

    def test_real_capture_event_routing_and_chat_survives_stop(self):
        with tempfile.TemporaryDirectory(prefix='ddcut-chat-capture-') as temporary:
            root = Path(temporary)
            source = root / 'local.mp4'
            run('-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10', '-t', '8',
                '-c:v', 'libx264', '-threads', '1', '-g', '10', source)
            tile = SimpleNamespace(room={'room_id': 'offline', 'uname': 'offline', 'live': True}, stream_url=str(source),
                                   stream_headers={}, stream_codec='avc', isVisible=lambda: True)
            # Host sessions use the actual tile object as their dictionary key.
            class Tile:
                pass
            tile_object = Tile()
            tile_object.__dict__.update(tile.__dict__)
            host = QMainWindow()
            host.settings = {'recording_dir': str(root / 'recordings'), 'recording_replay_enabled': False,
                             'recording_codec': 'copy', 'recording_min_free_mb': 256}
            host.wall = SimpleNamespace(tiles=[tile_object])
            host.capture_control = SimpleNamespace()
            host.recorder = RecordingManager(host.settings)
            manager = PluginManager(window=host, plugins_dir=str(root / 'plugins'), enabled=[])
            plugin = plugin_module.DDCutReplay()
            with patch.object(config, 'data_root', return_value=str(root)), patch.object(images, 'REPO', str(root)), \
                 patch('ddm.recording.input_args', side_effect=lambda url, headers: ['-re', *input_args(url, headers)]):
                plugin.on_load(PluginContext(manager, 'ddcut_replay'))
                manager.plugins.append(plugin)
                try:
                    plugin.set_capture_mode('clip')
                    self.assertTrue(host.recorder.start(tile_object, recording=True))
                    session = host.recorder.sessions[tile_object]
                    until(lambda: session.review_info() is not None)
                    for text in ('first', 'second'):
                        manager.emit('danmaku.message', room_id='offline', message={'kind': 'danmaku', 'text': text})
                    manager.emit('danmaku.message', room_id='other', message={'text': 'wrong room'})
                    manager.emit('danmaku.message', room_id='offline', message={'kind': 'gift', 'text': 'gift'})
                    session.update_review()
                    chat = Path(session.review_info()['danmaku_path'])
                    self.assertEqual(len(chat.read_text(encoding='utf-8').splitlines()), 2)
                    host.recorder.stop(tile_object)
                    until(lambda: tile_object not in host.recorder.sessions)
                    metadata = json.loads(chat.with_name('recording.json').read_text(encoding='utf-8'))
                    self.assertTrue(metadata['complete'])
                    segments = hls.read_segments(metadata['path'])
                    self.assertTrue(all('wall_start' in segment for segment in segments))
                    rows = [json.loads(line) for line in chat.read_text(encoding='utf-8').splitlines()]
                    mapped = heat.media_records(rows, segments)
                    self.assertEqual([text for _stamp, text in mapped], ['first', 'second'])
                    plugin.on_unload()
                    manager.emit('danmaku.message', room_id='offline', message={'text': 'after unload'})
                    self.assertEqual(len(chat.read_text(encoding='utf-8').splitlines()), 2)
                finally:
                    if plugin.hub and not plugin.hub.closed:
                        plugin.on_unload()
                    host.recorder.shutdown()
                    host.close()

    def test_real_hls_heat_ui_selection_export_and_old_recording_empty_state(self):
        with tempfile.TemporaryDirectory(prefix='ddcut-chat-ui-') as temporary:
            root = Path(temporary)
            playlist = root / 'recording.m3u8'
            run('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10', '-t', '90',
                '-c:v', 'libx264', '-threads', '1', '-g', '10', '-f', 'hls', '-hls_time', '2',
                '-hls_list_size', '0', '-hls_flags', 'program_date_time', playlist)
            segments = hls.read_segments(playlist)
            start = segments[0]['wall_start']
            chat = root / 'danmaku.jsonl'
            records = [{'received_at': start + stamp + i / 10, 'text': '哈哈 高能'}
                       for stamp in range(30, 50) for i in range(5)]
            chat.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records), encoding='utf-8')
            hub = media.TaskHub(root / 'data')
            entry = {'path': str(playlist), 'title': '本地弹幕亮点验证', 'duration': 90,
                     'created_at': start, 'segments': segments, 'danmaku_path': str(chat), 'index_format': 'hls'}
            hub.add_entry(entry)
            host = QMainWindow()
            host.setAttribute(Qt.WA_ShowWithoutActivating if args.native else Qt.WA_DontShowOnScreen)
            if args.native:
                host.move(-30000, -30000)
            owner = SimpleNamespace(context=SimpleNamespace(window=host), hub=hub,
                service=SimpleNamespace(sources=lambda: [], options=lambda: {'ffmpeg': ffmpeg}),
                preview_server=SimpleNamespace(source_url=lambda path: path))
            with (nullcontext() if args.native else patch.object(ui.Preview, 'load')):
                view = ui.Workspace(owner)
                host.setCentralWidget(view)
                host.resize(1440, 900)
                host.show()
                try:
                    view.review(hub.entries[0])
                    if args.native:
                        until(lambda: view.preview.position() > .1)
                        original_core = view.preview.core
                    view.show_highlights()
                    until(lambda: view.heat_results.count() > 0)
                    self.assertEqual(view.tools.currentIndex(), 2)
                    self.assertEqual(len(view.timeline.heat_points), 13)
                    rect = view.heat_results.visualItemRect(view.heat_results.item(0))
                    QTest.mouseClick(view.heat_results.viewport(), Qt.LeftButton, pos=rect.center())
                    self.assertGreater(view.inpoint.value(), 0)
                    self.assertGreater(view.outpoint.value(), view.inpoint.value())
                    self.assertTrue(view.export_button.isEnabled())
                    if args.native:
                        until(lambda: abs(view.preview.position() - view.inpoint.value()) < .8)
                        view.preview.core.pause = True
                        self.assertIs(view.preview.core, original_core)
                    saved = [view.inpoint.value(), view.outpoint.value()]
                    if args.screenshots:
                        args.screenshots.mkdir(parents=True, exist_ok=True)
                        for mode in ('dark', 'light'):
                            ui.theme.apply(mode)
                            host.setStyleSheet(ui.theme.qss())
                            app.processEvents()
                            host.grab().save(str(args.screenshots / f'ddcut-highlights-{mode}.png'))
                    view.heat_keywords.setText('不存在')
                    view.save_heat_options()
                    until(lambda: view.heat_future is None and view.heat_results.count() == 0)
                    self.assertFalse(view.timeline.heat_points)
                    self.assertEqual([view.inpoint.value(), view.outpoint.value()], saved)
                    view.heat_keywords.clear()
                    view.save_heat_options()
                    until(lambda: view.heat_results.count() > 0)
                    job = hub.submit('highlight export', 'export', media.export_clip, ffmpeg, view.current,
                        *saved, 'precise', root / 'highlight.mp4', root / 'work')
                    job.future.result(timeout=30)
                    self.assertEqual(job.state, 'done', job.message)
                    duration = media.probe(media.Job('probe', 'test'), ffmpeg, job.output, root / 'work')['duration']
                    self.assertAlmostEqual(duration, saved[1] - saved[0], delta=.2)
                    view.review({**entry, 'danmaku_path': '', 'title': '旧录像'})
                    self.assertEqual(view.heat_results.count(), 0)
                    self.assertFalse(view.timeline.heat_points)
                    self.assertIn('没有弹幕记录', view.heat_status.text())
                    self.assertIsNotNone(view.current)
                finally:
                    view.close()
                    hub.close()
                    host.close()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remainder])
