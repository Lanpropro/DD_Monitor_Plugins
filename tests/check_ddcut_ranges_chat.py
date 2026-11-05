"""Isolated multi-range/chat regression. Scripted chat tests behavior, not live quality."""
import argparse
from contextlib import nullcontext
import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', required=True, type=Path)
parser.add_argument('--native', action='store_true')
parser.add_argument('--screenshots', type=Path)
args, remainder = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'windows' if args.native else 'offscreen'
os.environ['PATH'] = str(args.host.resolve()) + os.pathsep + os.environ['PATH']
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QListWidgetItem
from PySide6.QtTest import QTest
from ddm import images

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ddcut_ranges_test', ROOT / 'plugins/DDCut_Replay/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ui = sys.modules[spec.name + '.workspace']
media = sys.modules[spec.name + '.media']
chat = sys.modules[spec.name + '.chat']
selection = sys.modules[spec.name + '.selections']
renderer = sys.modules[spec.name + '.chat_render']
recorder = sys.modules[spec.name + '.recorder']
app = QApplication.instance() or QApplication([])
ffmpeg = shutil.which('ffmpeg')


def until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Range/chat regression timed out')


def run(*command):
    return subprocess.run([str(item) for item in command], check=True, capture_output=True,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


class RangeChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='ddcut-ranges-chat-')
        cls.root = Path(cls.temporary.name)
        cls.source = cls.root / 'source.mp4'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:size=640x360:rate=20:duration=6',
            '-f', 'lavfi', '-i', 'color=blue:size=640x360:rate=20:duration=6',
            '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
            '-c:v', 'libx264', '-threads', '1', '-g', '20', cls.source)
        cls.black = cls.root / 'black.mp4'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:size=640x360:rate=20',
            '-t', '15', '-c:v', 'libx264', '-threads', '1', cls.black)
        cls.emote = cls.root / 'emote.png'
        picture = QImage(32, 32, QImage.Format_ARGB32)
        picture.fill(QColor('#00ff00'))
        picture.save(str(cls.emote))
        cls.events = [
            {'time': 1, 'text': 'first {test} \\ 中文', 'uname': 'Alice', 'color': '#00ffff'},
            {'time': 2, 'text': '[smile]', 'emoticon': 'https://invalid.local/emote.png',
             'image_paths': {'https://invalid.local/emote.png': str(cls.emote)}},
            {'time': 3, 'kind': 'super_chat', 'text': '醒目留言', 'uname': 'Bob', 'price': 30, 'duration': 8, 'id': 'sc'},
            {'time': 8, 'text': 'second', 'uname': 'Alice'},
        ]
        cls.log = cls.root / 'source.danmaku.json'
        chat.write_json(cls.log, cls.events)
        cls.entry = {'path': str(cls.source), 'title': '本地功能回归', 'duration': 12,
                     'created_at': time.time(), 'danmaku_path': str(cls.log)}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_recording_whitelist_sc_delete_color_and_local_emote(self):
        archive = self.root / 'capture'
        archive.mkdir()
        session = recorder.ReplayRecordingSession.__new__(recorder.ReplayRecordingSession)
        session._archive, session._saved, session.stopping = archive, False, False
        session.process = SimpleNamespace(poll=lambda: None)
        session._danmaku_pending, session._emoticon_loaders = [], {}
        picture = QPixmap(str(self.emote))
        with patch.object(images, 'load_cached_emoticon', return_value=picture):
            session.record_danmaku({'kind': 'danmaku', 'text': '[smile]', 'emoticon': 'local-emote',
                                    'uname': 'Alice', 'color': '#123456', 'uid': 'private-user'})
            session.record_danmaku({'kind': 'super_chat', 'message': 'sc', 'id': 'one', 'price': 30,
                                    'end_time': time.time() + 20, 'uname': 'Bob'})
            session.record_danmaku({'kind': 'super_chat_delete', 'ids': ['one']})
            session.record_danmaku({'kind': 'gift', 'text': 'skip'})
        session._flush_danmaku()
        rows = chat.ChatReader().read(archive / 'danmaku.jsonl')
        self.assertEqual([row['kind'] for row in rows], ['danmaku', 'super_chat', 'super_chat_delete'])
        self.assertEqual(rows[0]['color'], '#123456')
        self.assertNotIn('uid', rows[0])
        self.assertTrue(Path(rows[0]['image_paths']['local-emote']).is_file())
        wall = rows[0]['received_at'] - 1
        events = chat.align_events(rows, [{'wall_start': wall, 'duration': 10}])
        self.assertEqual(len(events), 2)
        self.assertLess(events[1]['duration'], .2)

    def test_mapping_dedup_offset_crop_and_portable_images(self):
        events = chat.align_events([
            {'received_at': 101, 'kind': 'super_chat', 'id': '1', 'text': 'SC', 'duration': 60},
            {'received_at': 102, 'kind': 'super_chat', 'id': '1', 'text': 'duplicate'},
            {'received_at': 110, 'text': 'reconnect gap'},
            {'received_at': 122, 'kind': 'super_chat_delete', 'ids': ['1']},
            {'received_at': 123, 'text': 'second'}],
            [{'wall_start': 100, 'duration': 5}, {'wall_start': 120, 'duration': 5}])
        self.assertEqual([row['time'] for row in events], [1, 8])
        self.assertEqual(events[0]['duration'], 6)
        cropped = chat.trim_events(events, 4, 10, shift=1)
        self.assertEqual([row['time'] for row in cropped], [0, 5])
        self.assertEqual(cropped[0]['duration'], 4)
        exported = self.root / 'portable' / 'clip.danmaku.json'
        exported.parent.mkdir()
        chat.write_json(exported, chat.load_events(self.entry))
        moved = self.root / 'moved'
        shutil.move(str(exported.parent), moved)
        loaded = chat.load_events({'danmaku_path': str(moved / exported.name)})
        image = Path(loaded[1]['image_paths']['https://invalid.local/emote.png'])
        self.assertTrue(image.is_file())
        self.assertEqual(image.parent.parent, moved)
        ass = self.root / 'chat.ass'
        chat.write_ass(ass, chat.load_events(self.entry))
        text = ass.read_text(encoding='utf-8-sig')
        self.assertIn('｛test｝ ＼ 中文', text)
        self.assertIn('SC ¥30 Bob', text)
        self.assertIn('0:00:11.00', text)
        # Real libass parsing/decoding of the exported subtitle, not a string-only check.
        run(ffmpeg, '-v', 'error', '-i', self.black, '-vf', f"ass='{ass.as_posix().replace(':', r'\:')}'",
            '-frames:v', '1', '-f', 'null', '-')

    def test_recording_downloads_uncached_emote_into_archive(self):
        picture = self.emote.read_bytes()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.end_headers()
                self.wfile.write(picture)

            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        archive = self.root / 'uncached-capture'
        archive.mkdir()
        session = recorder.ReplayRecordingSession.__new__(recorder.ReplayRecordingSession)
        session._archive, session._saved, session.stopping = archive, False, False
        session.process = SimpleNamespace(poll=lambda: None)
        session._danmaku_pending, session._emoticon_loaders = [], {}
        url = f'http://127.0.0.1:{server.server_port}/emote.png'
        try:
            with patch.object(images, 'REPO', str(self.root / 'image-cache')):
                session.record_danmaku({'kind': 'danmaku', 'text': '[local]', 'emoticon': url})
                target = Path(session._danmaku_pending[0]['image_paths'][url])
                until(lambda: target.is_file() and not session._emoticon_loaders)
                self.assertFalse(QImage(str(target)).isNull())
        finally:
            for loader in list(session._emoticon_loaders.values()):
                loader.wait()
            app.processEvents()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_separate_merge_order_chat_and_cancelled_partial_recovery(self):
        work = self.root / 'work'
        ranges = [{'start': 7, 'end': 9}, {'start': 1, 'end': 4}]
        targets = [self.root / 'one.mp4', self.root / 'two.mp4']
        job = media.Job('separate', 'export')
        result = media.export_ranges(job, ffmpeg, self.entry, ranges, 'precise', targets, work)
        self.assertEqual(len(result['outputs']), 2)
        self.assertAlmostEqual(result['outputs'][0]['actual_duration'], 2, delta=.1)
        self.assertEqual(chat.load_events(result['outputs'][0])[0]['text'], '醒目留言')
        merged = self.root / 'merged.mp4'
        merge = media.export_ranges(media.Job('merge', 'export'), ffmpeg, self.entry, ranges,
                                    'precise', [merged], work, True)['outputs'][0]
        self.assertAlmostEqual(merge['actual_duration'], 5, delta=.15)
        rows = chat.load_events(merge)
        self.assertEqual([row['time'] for row in rows if row['kind'] == 'danmaku'], [1, 2, 3])
        for stamp, channel in ((.5, 2), (2.5, 0)):
            rgb = run(ffmpeg, '-v', 'error', '-ss', stamp, '-i', merged, '-frames:v', '1',
                      '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-').stdout
            self.assertGreater(rgb[channel], 180, (stamp, rgb))
        run(ffmpeg, '-v', 'error', '-i', merged, '-f', 'null', '-')
        cancelled = media.Job('cancel', 'export')
        original = media.export_clip
        def first_then_cancel(*arguments):
            output = original(*arguments)
            cancelled.cancel.set()
            return output
        with patch.object(media, 'export_clip', side_effect=first_then_cancel), self.assertRaises(media.Cancelled):
            media.export_ranges(cancelled, ffmpeg, self.entry, ranges, 'precise',
                                [self.root / 'kept.mp4', self.root / 'absent.mp4'], work)
        self.assertEqual(len(cancelled.result['outputs']), 1)
        self.assertFalse((self.root / 'absent.mp4').exists())
        cancelled.state = 'cancelled'
        hub = media.TaskHub(self.root / 'restore')
        hub.jobs.append(cancelled)
        hub.save()
        hub.close()
        restored = media.TaskHub(hub.root)
        self.assertEqual(len(restored.entries), 1)
        self.assertEqual(Path(restored.entries[0]['path']).name, 'kept.mp4')
        restored.close()
        self.assertFalse(list(work.glob('ranges-*')))
        untouched = self.root / 'untouched.mp4'
        with self.assertRaises(FileExistsError):
            media.export_ranges(media.Job('collision', 'export'), ffmpeg, self.entry, ranges,
                                'precise', [untouched, targets[0]], work)
        self.assertFalse(untouched.exists())

    def test_ui_ranges_persistence_filters_and_small_window(self):
        hub = media.TaskHub(self.root / 'ui')
        hub.add_entry(dict(self.entry))
        host = QMainWindow()
        ui.theme.apply('dark')
        host.setStyleSheet(ui.theme.qss())
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
                until(lambda: view.chat_model.rowCount() == 4)
                self.assertEqual(view.current['ranges'], [])
                for peak in ({'start': 1, 'end': 4}, {'start': 7, 'end': 9}):
                    item = QListWidgetItem('peak')
                    item.setData(Qt.UserRole, peak)
                    view.heat_results.addItem(item)
                view.add_all_highlights()
                view.add_all_highlights()
                self.assertEqual(len(view.current['ranges']), 2)
                view.remove_range()
                view.remove_range()
                view.add_range(1, 4)
                view.add_range(7, 9)
                view.select_range(0)
                view.outpoint.setValue(5)
                self.assertEqual(view.current['ranges'][1]['end'], 9)
                view.range_list.item(0).setCheckState(Qt.Unchecked)
                self.assertEqual(len(selection.enabled_ranges(view.current)), 1)
                view.select_range(1)
                view.move_range(-1)
                self.assertEqual(view.current['ranges'][0]['start'], 7)
                view.export_scope.setCurrentIndex(2)
                self.assertEqual(view.mode.currentData(), 'precise')
                view.toggle_all_ranges(False)
                self.assertFalse(view.export_button.isEnabled())
                view.toggle_all_ranges(True)
                self.assertTrue(view.export_button.isEnabled())
                # Click another range on the real timeline, then edit only its handle.
                line = view.timeline
                QTest.mouseClick(line, Qt.LeftButton, pos=QPoint(round(line.x_for(2)), round(line.track().center().y())))
                self.assertEqual(view.current['selected_range'], 1)
                view.review(view.current)
                self.assertEqual(len(view.current['ranges']), 2)
                self.assertEqual(view.current['selected_range'], 1)
                until(lambda: view.chat_model.rowCount() == 4)
                view.chat_search.setText('Bob')
                self.assertEqual(view.chat_model.rowCount(), 1)
                self.assertEqual(view.chat_model.index(0).data(Qt.UserRole)['kind'], 'super_chat')
                view.chat_offset.setValue(2)
                self.assertEqual(view.chat_model.times, [5])
                view.chat_search.clear()
                self.assertEqual(view.chat_model.rowCount(), 4)
                view.chat_kind.setCurrentIndex(1)
                self.assertEqual(view.chat_model.rowCount(), 3)
                view.tools.setCurrentIndex(1)
                self.assertEqual(view.tools.tabText(2), '标记')
                for size in ((920, 600), (1440, 900)):
                    host.resize(*size)
                    app.processEvents()
                    self.assertEqual((host.width(), host.height()), size)
                    self.assertTrue(view.export_button.isVisible())
                    self.assertTrue(view.rect().contains(view.export_button.mapTo(view, view.export_button.rect().bottomRight())))
                    self.assertTrue(view.tools.rect().contains(view.chat_scope.mapTo(view.tools, view.chat_scope.rect().bottomRight())))
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    for tab, name in ((0, 'ranges'), (1, 'chat')):
                        view.tools.setCurrentIndex(tab)
                        app.processEvents()
                        host.grab().save(str(args.screenshots / f'{name}-ui.png'))
                view.remove_range()
                view.remove_range()
                self.assertEqual(view.current['ranges'], [])
                self.assertFalse(view.export_button.isEnabled())
            finally:
                view.close()
                hub.close()
                host.close()
        restored = media.TaskHub(hub.root)
        self.assertEqual(restored.entries[0]['ranges'], [])
        self.assertEqual(restored.entries[0]['danmaku_offset'], 2)
        restored.close()
        old = {'duration': 10, 'selection': [2, 6]}
        self.assertEqual(selection.restore_ranges(old), [{'start': 2, 'end': 6, 'enabled': True}])

    @unittest.skipUnless(args.native, 'Native MPV overlay check is explicit')
    def test_native_overlay_pixels_pause_rate_seek_and_image(self):
        host = QMainWindow()
        host.setAttribute(Qt.WA_ShowWithoutActivating)
        host.move(-30000, -30000)
        preview = ui.Preview(lambda path: path)
        host.setCentralWidget(preview)
        host.resize(1100, 700)
        host.show()
        try:
            preview.load(str(self.black))
            until(lambda: preview.position() > .1)
            preview.core.pause = True
            events = chat.load_events(self.entry)
            events[2]['duration'] = 60
            preview.chat.set_events(events)
            preview.seek(8)
            until(lambda: abs(preview.position() - 8) < .1)
            until(lambda: bool(preview.chat.visible))
            self.assertFalse(preview.chat_error, preview.chat_error)
            def capture(name):
                path = self.root / (name + '.png')
                preview.core.command('screenshot-to-file', str(path), 'window')
                picture = QImage(str(path))
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, args.screenshots / path.name)
                return picture
            picture = capture('native-chat')
            colors = [picture.pixelColor(x, y) for y in range(0, picture.height(), 2) for x in range(0, picture.width(), 2)]
            self.assertGreater(sum(c.green() > 120 and c.red() < 60 and c.blue() < 60 for c in colors), 10, 'Emote pixels missing')
            self.assertGreater(sum(c.blue() > 80 and c.green() > 80 for c in colors), 30, 'Chat/SC pixels missing')
            position = preview.position()
            QTest.qWait(250)
            self.assertAlmostEqual(preview.position(), position, delta=.02)
            self.assertEqual(capture('native-paused'), picture)
            preview.core.speed = 2
            preview.core.pause = False
            until(lambda: preview.position() > 9)
            preview.core.pause = True
            self.assertEqual(preview.core.speed, 2)
            core = preview.core
            preview.seek(14.5)
            until(lambda: abs(preview.position() - 14.5) < .1)
            QTest.qWait(100)
            self.assertFalse(preview.chat_error, preview.chat_error)
            self.assertTrue(any(packet['event']['kind'] == 'super_chat' and packet['end'] > 14.5 for packet in preview.chat.packets))
            preview.seek(0)
            until(lambda: preview.position() < .1)
            until(lambda: not preview.chat.visible)
            self.assertIs(preview.core, core)
            blank = capture('native-before-chat')
            self.assertLess(max(blank.pixelColor(50, 50).getRgb()[:3]), 10)
            preview.chat.enabled = False
            preview.seek(5)
            until(lambda: abs(preview.position() - 5) < .1)
            QTest.qWait(100)
            self.assertFalse(preview.chat.visible)
            preview.chat.enabled = True
            preview.chat.offset = 2
            preview.chat.frame = None
            preview.seek(1)
            until(lambda: abs(preview.position() - 1) < .1)
            QTest.qWait(100)
            self.assertFalse(preview.chat.visible)
            preview.seek(6)
            until(lambda: abs(preview.position() - 6) < .1)
            until(lambda: bool(preview.chat.visible))
            host.resize(800, 500)
            QTest.qWait(150)
            self.assertIs(preview.core, core)
            self.assertFalse(preview.chat_error, preview.chat_error)
            preview.stop()
            self.assertIsNone(preview.core)
            self.assertFalse(preview.chat.cache)
        finally:
            preview.stop()
            host.close()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remainder])
