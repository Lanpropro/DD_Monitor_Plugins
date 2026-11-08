"""Manual captions and software encoding: local UI, pixels and actual media streams."""
from copy import deepcopy
import importlib
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import check_ddcut_projects as fixtures
from check_ddcut_projects import app, args, remainder, engine, media, project, run, ffmpeg, ui, until
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow

captions = importlib.import_module('ddcut_ranges_test.subtitles')
srt = importlib.import_module('ddcut_ranges_test.subtitle_srt')


class SubtitleTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProjectTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ProjectTests.tearDownClass.__func__)
    document = fixtures.ProjectTests.document

    def test_srt_anchoring_trim_split_speed_move_and_history(self):
        content = '\ufeff1\r\n00:00:01,000 --> 00:00:05,000\r\n中文 {test}\\\r\n第二行\r\n'
        parsed = srt.parse_srt(content)
        self.assertEqual(parsed[0]['text'], '中文 {test}\\\n第二行')
        for invalid in ('1\n00:61:00,000 --> 00:62:00,000\nwrong', '1\n00:00:02,000 --> 00:00:01,000\nwrong', 'broken'):
            with self.assertRaises(ValueError):
                srt.parse_srt(invalid)
        data, first, second = self.document()
        data['subtitles'] = captions.anchor_cues(data, parsed)
        self.assertEqual(len(data['subtitles']), 2)
        doc = project.ProjectDocument(self.root/'captions.ddcut.json', data)
        doc.save()
        doc.commit(lambda value: value['clips'][0].update(speed=2))
        rows = captions.placed_cues(doc.data)
        self.assertEqual([(start, end) for _, start, end in rows], [(.5, 2), (2, 3)])
        doc.commit(lambda value: project.split_clip(value, first, 1))
        self.assertEqual(len(doc.data['subtitles']), 3)
        self.assertEqual([(a, b) for _, a, b in captions.placed_cues(doc.data)], [(.5, 1), (1, 2), (2, 3)])
        doc.undo()
        doc.commit(lambda value: value['clips'][0].update(start=4, end=5))
        self.assertEqual(captions.placed_cues(doc.data)[0][1:3], (0, .5))
        doc.undo()
        self.assertEqual(captions.placed_cues(doc.data)[0][1:3], (.5, 2))
        doc.commit(lambda value: value['clips'].insert(0, value['clips'].pop(1)))
        self.assertEqual(captions.placed_cues(doc.data)[0][1:3], (0, 1))
        self.assertTrue(srt.parse_srt(srt.srt_text(doc.data)))
        restored = project.read_project(doc.path)
        self.assertEqual(restored['subtitles'], doc.data['subtitles'])
        count = len(restored['subtitles'])
        project.duplicate_item(restored, first)
        self.assertGreater(len(restored['subtitles']), count)
        project.remove_item(restored, first)
        self.assertFalse(any(cue['anchor'] == first for cue in restored['subtitles']))

    def test_caption_pixels_preview_export_sidecar_and_disabled_burn(self):
        data, first, _ = self.document()
        data['clips'] = data['clips'][:1]
        data['clips'][0].update(start=0, end=1)
        captions.set_cue(data, '', .2, .8, '字幕 TEST\n第二行')
        data['subtitle_style'] = dict(size=120, position='middle', color='#ffffff', background=True)
        target = self.root/'captioned.mp4'
        result = engine.render_project(media.Job('caption', 'export'), ffmpeg, data, target, self.root/'work')
        self.assertTrue(Path(result['subtitle_path']).is_file())
        self.assertIn('00:00:00,200 --> 00:00:00,800', Path(result['subtitle_path']).read_text(encoding='utf-8-sig'))
        def frame(path, stamp):
            png = self.root/(path.stem+'-'+str(stamp)+'.png')
            run(ffmpeg, '-y', '-v', 'error', '-ss', stamp, '-i', path, '-frames:v', '1', png)
            return QImage(str(png))
        def white(image):
            return sum(min(image.pixelColor(x, y).getRgb()[:3]) > 160 for y in range(image.height()) for x in range(image.width()))
        self.assertEqual(white(frame(target, .1)), 0)
        self.assertGreater(white(frame(target, .5)), 30)
        self.assertEqual(white(frame(target, .9)), 0)
        preview = self.root/'caption-preview.mp4'
        engine.render_project(media.Job('preview', 'project_preview'), ffmpeg, data, preview, self.root/'work', begin=.4, length=.2, preview=True)
        self.assertGreater(white(frame(preview, .05)), 30)
        self.assertFalse(preview.with_suffix('.srt').exists())
        data['burn_subtitles'] = False
        plain = self.root/'caption-unburned.mp4'
        engine.render_project(media.Job('plain', 'export'), ffmpeg, data, plain, self.root/'work')
        self.assertEqual(white(frame(plain, .5)), 0)
        self.assertTrue(plain.with_suffix('.srt').exists())
        kept = self.root/'protected.srt'
        kept.write_text('keep', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            engine.render_project(media.Job('protected', 'export'), ffmpeg, data, kept.with_suffix('.mp4'), self.root/'work')
        self.assertEqual(kept.read_text(), 'keep')
        if args.screenshots:
            frame(target, .5).save(str(args.screenshots/'manual-caption-frame.png'))

    def test_h265_project_precise_burn_and_merge_encoding(self):
        settings = dict(codec='h265', priority='quality', bitrate=1200)
        source = dict(self.media_info[str(self.red)])
        commands = []
        original = engine.run_process
        def capture(job, command, work, **kwargs):
            commands.append(command)
            return original(job, command, work, **kwargs)
        data, _, _ = self.document()
        data['clips'] = data['clips'][:1]
        data['clips'][0].update(start=0, end=.5)
        data['encoding'] = settings
        target = self.root/'hevc-project.mp4'
        with patch.object(engine, 'run_process', side_effect=capture):
            engine.render_project(media.Job('HEVC', 'export'), ffmpeg, data, target, self.root/'work')
        self.assertTrue(any('libx265' in command and '1200k' in command and 'slow' in command for command in commands))
        self.assertEqual(media.probe(media.Job('probe', 'test'), ffmpeg, target, self.root/'work')['video_codec'], 'hevc')
        from ddcut_ranges_test.chat import write_json
        log = self.root/'encoding-chat.json'
        write_json(log, [dict(time=.1, text='编码测试')])
        source['danmaku_path'] = str(log)
        clip = self.root/'hevc-burn.mp4'
        media.export_clip(media.Job('HEVC burn', 'export'), ffmpeg, source, 0, .5, 'precise', clip, self.root/'work', True, settings)
        self.assertEqual(media.probe(media.Job('probe', 'test'), ffmpeg, clip, self.root/'work')['video_codec'], 'hevc')
        merged = self.root/'hevc-merged.mp4'
        media.export_ranges(media.Job('HEVC merge', 'export'), ffmpeg, source, [dict(start=0, end=.3), dict(start=.5, end=.8)], 'precise', [merged], self.root/'work', True, False, settings)
        self.assertEqual(media.probe(media.Job('probe', 'test'), ffmpeg, merged, self.root/'work')['video_codec'], 'hevc')

    def test_subtitle_cancel_cleanup_and_headless_protocol_boundary(self):
        script = """import importlib,sys,types
package=types.ModuleType('caption_data')
package.__path__=[sys.argv[1]]
sys.modules['caption_data']=package
srt=importlib.import_module('caption_data.subtitle_srt')
assert srt.parse_srt('1\\n00:00:00,000 --> 00:00:01,000\\n中文')[0]['text']=='中文'
assert not any(name.startswith('PySide6') for name in sys.modules)
"""
        run(sys.executable, '-c', script, Path(__file__).resolve().parents[1]/'plugins/DDCut_Replay')
        data, _, _ = self.document()
        data['clips'] = data['clips'][:1]
        captions.set_cue(data, '', 0, 1, '取消字幕供帧')
        job = media.Job('cancel subtitle', 'export')
        original = engine.run_process
        def cancel(job, command, folder, **options):
            if options.get('frames') is not None:
                frames = options['frames']
                def produce():
                    for frame in frames:
                        yield frame
                        job.cancel.set()
                options['frames'] = produce()
            return original(job, command, folder, **options)
        target = self.root/'cancel-subtitles.mp4'
        with patch.object(engine, 'run_process', side_effect=cancel), self.assertRaises(media.Cancelled):
            engine.render_project(job, ffmpeg, data, target, self.root/'work')
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix('.srt').exists())
        self.assertFalse(list((self.root/'work').glob('project-*')))

    def test_subtitle_panel_srt_timeline_history_and_full_recording(self):
        hub = media.TaskHub(self.root/'subtitle-ui')
        host = QMainWindow()
        host.setAttribute(Qt.WA_ShowWithoutActivating if args.native else Qt.WA_DontShowOnScreen)
        if args.native:
            host.move(-30000, -30000)
        owner = SimpleNamespace(context=SimpleNamespace(window=host), hub=hub,
            service=SimpleNamespace(sources=lambda: [], options=lambda: {'ffmpeg': ffmpeg}),
            preview_server=SimpleNamespace(source_url=lambda path: path))
        view = ui.Workspace(owner)
        host.setCentralWidget(view)
        host.resize(1280, 900)
        host.show()
        try:
            data, _, _ = self.document()
            doc = project.ProjectDocument(self.root/'subtitle-ui.ddcut.json', data)
            doc.save()
            view.open_project_document(doc)
            editor, panel = view.project_editor, view.project_editor.subtitle_panel
            editor.position = .5
            panel.add()
            self.assertIs(editor.right_tabs.currentWidget(), panel)
            panel.start.setValue(.5)
            panel.end.setValue(1.5)
            panel.text.setPlainText('手动打轴\n第二行')
            panel.apply()
            self.assertEqual(panel.rows.topLevelItemCount(), 1)
            panel.fields['size'].setValue(80)
            panel.apply_style()
            self.assertEqual(doc.data['subtitle_style']['size'], 80)
            timeline = editor.timeline
            block = next(block for block in timeline.blocks if block[0].get('kind') == 'subtitle')
            QTest.mousePress(timeline, Qt.LeftButton, pos=block[3].center().toPoint())
            destination = QPoint(round(block[3].center().x()+timeline.scale*.5), round(block[3].center().y()))
            QTest.mouseMove(timeline, destination)
            QTest.mouseRelease(timeline, Qt.LeftButton, pos=destination)
            self.assertAlmostEqual(captions.placed_cues(doc.data)[0][1], 1, delta=.07)
            editor.history(False)
            self.assertAlmostEqual(captions.placed_cues(doc.data)[0][1], .5)
            path = self.root/'ui-captions.srt'
            with patch.object(ui.QFileDialog, 'getSaveFileName', return_value=(str(path), '')):
                panel.export_srt()
            self.assertTrue(path.is_file())
            with patch.object(ui.QFileDialog, 'getOpenFileName', return_value=(str(path), '')):
                panel.import_srt()
            self.assertEqual(len(doc.data['subtitles']), 2)
            until(lambda: editor.loaded_key and not editor.preview_dirty and not editor.preview_job and editor.resume is None, timeout=30)
            for size in ((920, 600), (1280, 900)):
                host.resize(*size)
                app.processEvents()
                self.assertEqual((host.width(), host.height()), size)
            if args.native:
                editor.seek(1)
                until(lambda: abs(editor.preview.position()-1) < .1)
                picture = self.root/'native-caption.png'
                editor.preview.core.command('screenshot-to-file', str(picture), 'video')
                frame = QImage(str(picture))
                if args.screenshots:
                    shutil.copyfile(picture, args.screenshots/picture.name)
                # Thin 13px Chinese glyphs need not contain many fully white pixels.
                # Check actual glyph coverage and its bottom placement against the red source.
                marked = [(x, y) for y in range(frame.height()) for x in range(frame.width()) if frame.pixelColor(x, y).red() < 180]
                self.assertGreater(len(marked), 100)
                self.assertTrue(all(y > frame.height()*.4 for x, y in marked))
            editor.leave()
            view.review({**self.media_info[str(self.red)], 'created_at': 0})
            view.export_scope.setCurrentIndex(view.export_scope.findData('full'))
            view.mode.setCurrentIndex(1)
            view.encoding.codec.setCurrentIndex(view.encoding.codec.findData('h265'))
            full = self.root/'full-recording.mp4'
            with patch.object(ui.QFileDialog, 'getSaveFileName', return_value=(str(full), '')):
                view.export()
            job = hub.jobs[-1]
            until(lambda: job.future.done(), timeout=30)
            self.assertEqual(job.state, 'done', job.message)
            self.assertAlmostEqual(job.result['actual_duration'], 6, delta=.1)
            self.assertEqual(media.probe(media.Job('probe', 'test'), ffmpeg, full, self.root/'work')['video_codec'], 'hevc')
            self.assertEqual(project.read_project(doc.path)['subtitles'], doc.data['subtitles'])
        finally:
            view.close()
            hub.close()
            host.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


if __name__ == '__main__':
    result = []
    def checks():
        outcome = unittest.main(argv=[sys.argv[0], *remainder], exit=False)
        result.append(outcome.result.wasSuccessful())
        app.quit()
    app.setQuitOnLastWindowClosed(False)
    QTimer.singleShot(0, checks)
    app.exec()
    app.shutdown()
    raise SystemExit(0 if result and result[0] else 1)
