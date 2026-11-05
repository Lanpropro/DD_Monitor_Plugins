"""Real encoded-video checks for shared-scene chat burn-in; isolated/local only."""
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from check_ddcut_ranges_chat import args, remainder, app, chat, ffmpeg, media, run, ui, until
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QMainWindow


class BurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='ddcut-burn-')
        cls.root = Path(cls.temporary.name)
        cls.source = cls.root / 'black.mp4'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:size=640x360:rate=30000/1001',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '12',
            '-c:v', 'libx264', '-threads', '1', '-g', '30', '-c:a', 'aac', cls.source)
        icon = cls.root / 'green.png'
        image = QImage(32, 32, QImage.Format_ARGB32)
        image.fill(QColor('#00ff00'))
        image.save(str(icon))
        cls.log = cls.root / 'source.danmaku.json'
        chat.write_json(cls.log, [
            {'time': 0, 'text': '中文回放 TEST', 'color': '#00ffff'},
            {'time': 1, 'text': '[local]', 'emoticon': 'offline-emote', 'image_paths': {'offline-emote': str(icon)}},
            {'time': 2, 'kind': 'super_chat', 'text': 'SC有效内容', 'uname': 'Test', 'price': 30, 'duration': 3},
            {'time': 7, 'text': '第二段', 'color': '#ffffff'},
        ])
        cls.entry = {'path': str(cls.source), 'title': '压制功能验证', 'duration': 12,
                     'created_at': time.time(), 'danmaku_path': str(cls.log),
                     'chat_size': 48, 'chat_opacity': 65, 'danmaku_offset': .5, 'chat_enabled': False}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def extract(self, path, stamp, name):
        output = self.root / (name + '.png')
        run(ffmpeg, '-y', '-v', 'error', '-ss', stamp, '-i', path, '-frames:v', '1', output)
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output, args.screenshots / output.name)
        return QImage(str(output))

    def assert_scene(self, actual, stamp, entry=None):
        from ddcut_ranges_test.chat_scene import ChatScene
        entry = entry or self.entry
        scene = ChatScene(chat.load_events(entry), actual.width(), actual.height(), entry['chat_size'], entry['chat_opacity'])
        expected = QImage(actual.size(), QImage.Format_ARGB32)
        expected.fill(Qt.black)
        painter = QPainter(expected)
        painter.drawImage(0, 0, scene.frame_at(stamp - entry.get('danmaku_offset', 0)))
        painter.end()
        error, pixels, bright, green = 0, 0, 0, 0
        for y in range(0, actual.height(), 2):
            for x in range(0, actual.width(), 2):
                a, b = actual.pixelColor(x, y), expected.pixelColor(x, y)
                error += sum(abs(i - j) for i, j in zip(a.getRgb()[:3], b.getRgb()[:3]))
                pixels += 3
                bright += max(a.getRgb()[:3]) > 60
                green += a.green() > 100 and a.red() < 50 and a.blue() < 50
        self.assertLess(error / pixels, 3, 'Encoded frame differs from preview scene')
        self.assertGreater(bright, 30)
        return green

    def test_encoded_text_image_sc_styles_offset_and_plain_output(self):
        output = self.root / 'burn.mp4'
        result = media.export_clip(media.Job('burn', 'export'), ffmpeg, self.entry, 4, 8,
                                   'precise', output, self.root / 'work', True)
        info = media.probe(media.Job('probe', 'test'), ffmpeg, output, self.root / 'work')
        self.assertAlmostEqual(info['duration'], 4, delta=.08)
        self.assertEqual(info['video_width'], 640)
        self.assertTrue(info['has_audio'])
        self.assertEqual(info['frame_rate'], '30000/1001')
        # The first frame retains messages that started before the selected inpoint.
        self.assert_scene(self.extract(output, 0, 'burn-first-frame'), 4)
        green = self.assert_scene(self.extract(output, 2.002, 'burn-emoji-after-sc'), 6.002)
        self.assertGreater(green, 20, 'Emote was replaced with fallback text')
        reader = chat.ChatReader()
        reader.read(result['danmaku_path'])
        self.assertTrue(reader.video_burned)
        self.assertTrue(result['chat_burned'])
        run(ffmpeg, '-v', 'error', '-i', output, '-f', 'null', '-')
        plain = self.root / 'plain.mp4'
        media.export_clip(media.Job('plain', 'export'), ffmpeg, self.entry, 4, 8, 'precise', plain, self.root / 'work')
        image = self.extract(plain, 1, 'plain-without-burn')
        self.assertLess(max(image.pixelColor(400, 20).getRgb()[:3]), 8)
        plain_reader = chat.ChatReader()
        plain_reader.read(plain.with_suffix('.danmaku.json'))
        self.assertFalse(plain_reader.video_burned)
        # Output audio remains decodable, at the original sample rate, for the whole selected range.
        pcm = run(ffmpeg, '-v', 'error', '-i', output, '-vn', '-ac', '1', '-ar', '48000', '-f', 's16le', '-').stdout
        self.assertAlmostEqual(len(pcm) / 96000, 4, delta=.06)

    def test_hls_frozen_input_and_burned_merge(self):
        from ddcut_ranges_test.hls import read_segments
        playlist = self.root / 'source.m3u8'
        run(ffmpeg, '-y', '-v', 'error', '-i', self.source, '-c', 'copy', '-f', 'hls',
            '-hls_time', '1', '-hls_list_size', '0', playlist)
        entry = {**self.entry, 'path': str(playlist), 'segments': read_segments(playlist), 'index_format': 'hls'}
        output = self.root / 'hls-burn.mp4'
        result = media.export_clip(media.Job('HLS burn', 'export'), ffmpeg, entry, 4, 6,
                                   'precise', output, self.root / 'work', True)
        self.assertAlmostEqual(result['actual_duration'], 2, delta=.1)
        self.assert_scene(self.extract(output, 0, 'hls-burn-first'), 4)
        merged = self.root / 'merged-burn.mp4'
        result = media.export_ranges(media.Job('merge burn', 'export'), ffmpeg, self.entry,
            [{'start': 6, 'end': 8}, {'start': 4, 'end': 5}], 'precise', [merged], self.root / 'work', True, True)
        self.assertTrue(result['outputs'][0]['chat_burned'])
        self.assertAlmostEqual(result['outputs'][0]['actual_duration'], 3, delta=.15)
        self.assert_scene(self.extract(merged, 0, 'merged-first'), 6)
        self.assert_scene(self.extract(merged, 2.035, 'merged-second'), 4)
        self.assertFalse(list(self.root.glob('.clip-*.m3u8')))
        self.assertFalse(list((self.root / 'work').glob('ranges-*')))
        separate = media.export_ranges(media.Job('separate burn', 'export'), ffmpeg, self.entry,
            [{'start': 4, 'end': 5}, {'start': 6, 'end': 7}], 'precise',
            [self.root / 'separate-1.mp4', self.root / 'separate-2.mp4'], self.root / 'work', False, True)
        self.assertTrue(all(part['chat_burned'] for part in separate['outputs']))
        self.assert_scene(self.extract(separate['outputs'][1]['output'], 0, 'separate-second'), 6)

    def test_rotation_aspect_and_variable_frame_rate(self):
        anamorphic = self.root / 'anamorphic.mp4'
        rotated = self.root / 'rotated.mp4'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:size=320x240:rate=20',
            '-vf', "setsar=2,select='not(mod(n,3))+not(mod(n,7))'", '-fps_mode', 'vfr',
            '-t', '8', '-c:v', 'libx264', '-threads', '1', anamorphic)
        run(ffmpeg, '-y', '-v', 'error', '-display_rotation', '90', '-i', anamorphic, '-c', 'copy', rotated)
        self.assertEqual(media.probe(media.Job('probe', 'test'), ffmpeg, rotated, self.root / 'work')['rotation'], 90)
        entry = {**self.entry, 'path': str(rotated), 'duration': 8}
        output = self.root / 'rotated-burn.mp4'
        media.export_clip(media.Job('rotated', 'export'), ffmpeg, entry, 4, 6, 'precise', output, self.root / 'work', True)
        info = media.probe(media.Job('probe', 'test'), ffmpeg, output, self.root / 'work')
        self.assertEqual((info['video_width'], info['video_height']), (240, 640))
        self.assertAlmostEqual(info['duration'], 2, delta=.2)
        self.assert_scene(self.extract(output, 0, 'rotated-first'), 4, entry)

    def test_av1_fmp4_archive_burn(self):
        from ddcut_ranges_test.hls import read_segments
        playlist = self.root / 'av1.m3u8'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:size=320x180:rate=10',
            '-t', '8', '-c:v', 'libaom-av1', '-cpu-used', '8', '-threads', '2', '-g', '10',
            '-f', 'hls', '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', self.root / 'av1-init.mp4',
            '-hls_segment_filename', self.root / 'av1-%03d.m4s', '-hls_time', '1', '-hls_list_size', '0', playlist)
        entry = {**self.entry, 'path': str(playlist), 'duration': 8,
                 'segments': read_segments(playlist), 'index_format': 'hls'}
        self.assertTrue(entry['segments'][0]['init'])
        target = self.root / 'av1-burn.mp4'
        result = media.export_clip(media.Job('AV1 burn', 'export'), ffmpeg, entry, 4, 6,
                                   'precise', target, self.root / 'work', True)
        self.assertAlmostEqual(result['actual_duration'], 2, delta=.1)
        self.assert_scene(self.extract(target, 0, 'av1-first'), 4, entry)

    def test_cancel_failure_reaps_writer_and_refuses_double_burn(self):
        job = media.Job('cancel burn', 'export')
        target = self.root / 'cancelled.mp4'
        original = media.run_process
        def cancel_during_feed(job, command, folder, **options):
            if options.get('frames') is not None:
                stream = options['frames']
                def interrupted():
                    for frame in stream:
                        yield frame
                        job.cancel.set()
                options['frames'] = interrupted()
            return original(job, command, folder, **options)
        with patch('ddcut_ranges_test.burn.run_process', side_effect=cancel_during_feed), self.assertRaises(media.Cancelled):
            media.export_clip(job, ffmpeg, self.entry, 0, 10, 'precise', target, self.root / 'work', True)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob('*.partial.mp4')))
        self.assertFalse(any(thread.name == 'ddcut-chat-frames' for thread in threading.enumerate()))
        for mode, source in [('copy', self.entry), ('precise', {**self.entry, 'chat_burned': True})]:
            with self.assertRaises(ValueError):
                media.export_clip(media.Job('invalid', 'export'), ffmpeg, source, 1, 2, mode,
                                  self.root / 'invalid.mp4', self.root / 'work', True)
        # A broken frame producer must fail even if FFmpeg accepts a shorter stream.
        def broken():
            yield b'\0' * (4 * 16 * 16)
            raise ValueError('scripted raster failure')
        with self.assertRaisesRegex(ValueError, 'scripted raster failure'):
            media.run_process(media.Job('writer failure', 'test'), [ffmpeg, '-v', 'error', '-f', 'rawvideo',
                '-pixel_format', 'bgra', '-video_size', '16x16', '-i', 'pipe:0', '-f', 'null', '-'],
                self.root / 'work', frames=broken())
        self.assertFalse(any(thread.name == 'ddcut-chat-frames' for thread in threading.enumerate()))

    def test_ui_export_switch_and_baked_reimport(self):
        hub = media.TaskHub(self.root / 'ui')
        hub.add_entry(dict(self.entry))
        host = QMainWindow()
        host.setAttribute(Qt.WA_DontShowOnScreen)
        ui.theme.apply('dark')
        host.setStyleSheet(ui.theme.qss())
        owner = SimpleNamespace(context=SimpleNamespace(window=host), hub=hub,
            service=SimpleNamespace(sources=lambda: [], options=lambda: {'ffmpeg': ffmpeg}),
            preview_server=SimpleNamespace(source_url=lambda path: path))
        with patch.object(ui.Preview, 'load'):
            view = ui.Workspace(owner)
            host.setCentralWidget(view)
            host.resize(920, 600)
            host.show()
            try:
                view.review(hub.entries[0])
                self.assertFalse(view.burn_chat.isChecked())
                view.mode.setCurrentIndex(0)
                view.burn_chat.setChecked(True)
                self.assertEqual(view.mode.currentData(), 'precise')
                self.assertFalse(view.mode.model().item(0).isEnabled())
                view.add_range(4, 5)
                target = self.root / 'ui-burn.mp4'
                with patch.object(ui.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
                    view.export_button.click()
                job = hub.jobs[-1]
                until(lambda: job.future.done())
                self.assertEqual(job.state, 'done', job.message)
                self.assertTrue(job.result['chat_burned'])
                hub.add_export(job)
                view.review(hub.entries[0])
                self.assertFalse(view.chat_enabled.isChecked(), 'Baked video must not get duplicate preview overlays')
                self.assertFalse(view.burn_chat.isEnabled())
                view.show_library()
                fresh = {'path': str(target), 'title': '重新导入', 'created_at': time.time()}
                view.load_entry(fresh)
                imported = hub.jobs[-1]
                until(lambda: imported.future.done())
                self.assertTrue(imported.result['chat_burned'])
                view.review(imported.result)
                self.assertFalse(view.chat_enabled.isChecked())
                view.toggle_tasks(False)
                self.assertTrue(view.export_button.isVisible())
                self.assertEqual((host.width(), host.height()), (920, 600))
            finally:
                view.close()
                hub.close()
                host.close()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remainder])
