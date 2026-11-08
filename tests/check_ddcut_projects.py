"""Editable project regression using external local files and real FFmpeg output."""
from copy import deepcopy
from array import array
from contextlib import nullcontext
import importlib
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from check_ddcut_ranges_chat import args, remainder, app, ffmpeg, media, run, ui, until
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QMainWindow
from PySide6.QtTest import QTest

project = importlib.import_module('ddcut_ranges_test.project')
engine = importlib.import_module('ddcut_ranges_test.project_media')


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='ddcut-project-')
        cls.root = Path(cls.temporary.name)
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
        cls.red, cls.blue = cls.root / 'red.mp4', cls.root / 'blue.mp4'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:size=320x180:rate=20',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '6',
            '-c:v', 'libx264', '-threads', '1', '-g', '20', '-c:a', 'aac', cls.red)
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:size=240x320:rate=10',
            '-t', '6', '-c:v', 'libx264', '-threads', '1', '-g', '10', cls.blue)
        cls.audio = cls.root / 'tone.wav'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000', '-t', '8', cls.audio)
        cls.image = cls.root / 'green.png'
        picture = QImage(32, 32, QImage.Format_ARGB32)
        picture.fill(QColor('#00ff00'))
        picture.save(str(cls.image))
        cls.media_info = {str(path): engine.inspect_file(media.Job('inspect', 'test'), ffmpeg, path, cls.root / 'work')
                          for path in (cls.red, cls.blue, cls.audio, cls.image)}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def document(self):
        value = project.new_project('外部文件工程')
        value['media'] = deepcopy(self.media_info)
        value['render'] = {'width': 320, 'height': 180, 'fps': 20}
        first = project.append_clip(value, str(self.red), 1, 5)
        second = project.append_clip(value, str(self.blue), 0, 2)
        return value, first, second

    def test_document_split_anchors_trim_move_history_and_portability(self):
        value, first, second = self.document()
        layer = project.add_layer(value, 'text', 2, text='分割字幕')
        item = value['layers'][0]
        item['duration'] = 3
        doc = project.ProjectDocument(self.root / 'edit.ddcut.json', value)
        new = doc.commit(lambda data: project.split_clip(data, first, 3))
        self.assertEqual(len(doc.data['clips']), 3)
        self.assertEqual(len(doc.data['layers']), 2)
        self.assertAlmostEqual(project.duration(doc.data), 6)
        doc.undo()
        self.assertEqual(len(doc.data['clips']), 2)
        self.assertEqual(len(doc.data['layers']), 1)
        doc.redo()
        doc.commit(lambda data: data['clips'].insert(0, data['clips'].pop(2)))
        self.assertEqual(project.placed_layers(doc.data)[0][1], 4)
        previous = deepcopy(doc.data)
        with self.assertRaises(ValueError):
            doc.commit(lambda data: data['clips'][0].update(end=999))
        self.assertEqual(doc.data, previous)
        copied = self.root / 'portable' / 'copy.ddcut.json'
        project.write_project(copied, doc.data)
        restored = project.read_project(copied)
        self.assertEqual(restored['clips'], doc.data['clips'])
        self.assertTrue(all(Path(path).is_file() for path in restored['media']))
        project.remove_item(restored, new)
        self.assertFalse(any(row['anchor'] == new for row in restored['layers']))
        copied.unlink()
        self.assertTrue(self.red.is_file())
        # Moving a project together with a referenced HLS archive must also move its segment references.
        bundle = self.root/'bundle'
        archive = bundle/'media'
        archive.mkdir(parents=True)
        playlist = archive/'recording.m3u8'
        run(ffmpeg, '-y', '-v', 'error', '-i', self.red, '-c', 'copy', '-f', 'hls',
            '-hls_time', '1', '-hls_list_size', '0', playlist)
        info = engine.inspect_file(media.Job('HLS import', 'test'), ffmpeg, playlist, self.root/'work')
        data = project.new_project('移动HLS工程')
        data['media'][str(playlist)] = info
        project.append_clip(data, str(playlist), 0, 1)
        project.write_project(bundle/'project.ddcut.json', data)
        moved = self.root/'moved-bundle'
        self.assertTrue(bundle.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(moved.resolve().is_relative_to(self.root.resolve()))
        bundle.rename(moved)
        restored = project.read_project(moved/'project.ddcut.json')
        self.assertTrue(all(Path(segment['path']).is_file() for info in restored['media'].values() for segment in info['segments']))
        result = engine.render_project(media.Job('moved HLS', 'export'), ffmpeg, restored,
                                       self.root/'moved-hls.mp4', self.root/'work')
        self.assertAlmostEqual(result['actual_duration'], 1, delta=.1)
        av1 = self.root/'av1'
        av1.mkdir()
        playlist = av1/'av1.m3u8'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:size=160x90:rate=10',
            '-t', '2', '-c:v', 'libaom-av1', '-cpu-used', '8', '-threads', '2', '-g', '10',
            '-f', 'hls', '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', av1/'init.mp4',
            '-hls_segment_filename', av1/'part-%03d.m4s', '-hls_time', '1', '-hls_list_size', '0', playlist)
        from ddcut_ranges_test.hls import read_segments, write_playlist
        # Match the recorder's canonical archive, rather than FFmpeg's Windows backslash MAP URI.
        write_playlist(playlist, read_segments(playlist), complete=True)
        info = engine.inspect_file(media.Job('AV1 project import', 'test'), ffmpeg, playlist, self.root/'work',
                                   thumbnails=self.root/'thumbnails')
        self.assertTrue(Path(info['cover_path']).is_file())
        data = project.new_project('AV1工程')
        data['media'][str(playlist)] = info
        project.append_clip(data, str(playlist), 0, 1)
        result = engine.render_project(media.Job('AV1 project', 'export'), ffmpeg, data,
                                       self.root/'av1-project.mp4', self.root/'work')
        self.assertAlmostEqual(result['actual_duration'], 1, delta=.1)

    def test_real_waveform_and_project_export_layers_audio_speed(self):
        value, first, second = self.document()
        value['clips'][0]['speed'] = 2
        text = project.add_layer(value, 'text', .2, text='工程文字 TEST')
        value['layers'][-1].update(x=.05, y=.05, w=.8, h=.25, font_size=72, duration=1)
        sticker = project.add_layer(value, 'image', .2, str(self.image))
        value['layers'][-1].update(x=.1, y=.5, w=.2, h=.2, duration=1)
        pip = project.add_layer(value, 'video', .3, str(self.blue))
        value['layers'][-1].update(x=.6, y=.5, w=.3, h=.4, duration=1)
        sound = project.add_layer(value, 'audio', 3, str(self.audio))
        value['layers'][-1].update(duration=2, fade_in=.1, fade_out=.2)
        peaks = engine.waveform(media.Job('waveform', 'test'), ffmpeg, value['media'][str(self.audio)], self.root / 'work')
        self.assertGreater(len(peaks), 100)
        self.assertGreater(max(peaks), .9)
        target = self.root / 'project.mp4'
        job = media.Job('project', 'export')
        result = engine.render_project(job, ffmpeg, value, target, self.root / 'work', project_path='external.ddcut.json')
        self.assertAlmostEqual(result['actual_duration'], 5, delta=.15)
        info = media.probe(media.Job('probe', 'test'), ffmpeg, target, self.root / 'work')
        self.assertTrue(info['has_audio'])
        for stamp, name in ((.5, 'layers'), (2.5, 'second'), (4.5, 'tail')):
            frame = self.root / (name + '.png')
            run(ffmpeg, '-y', '-v', 'error', '-ss', stamp, '-i', target, '-frames:v', '1', frame)
            picture = QImage(str(frame))
            if name == 'layers':
                self.assertGreater(picture.pixelColor(40, 100).green(), 180)
                self.assertGreater(picture.pixelColor(230, 110).blue(), 180)
                self.assertGreater(picture.pixelColor(150, 150).red(), 180)
            elif name == 'second':
                self.assertGreater(picture.pixelColor(160, 100).blue(), 180)
            else:
                self.assertLess(max(picture.pixelColor(160, 100).getRgb()[:3]), 10)
            if args.screenshots:
                args.screenshots.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(frame, args.screenshots / frame.name)
        run(ffmpeg, '-v', 'error', '-i', target, '-f', 'null', '-')
        preview = self.root / 'preview.mp4'
        engine.render_project(media.Job('preview', 'project_preview'), ffmpeg, value, preview,
                              self.root / 'work', begin=2, length=1, preview=True)
        self.assertAlmostEqual(media.probe(media.Job('probe', 'test'), ffmpeg, preview, self.root / 'work')['duration'], 1, delta=.1)
        # A preview beginning inside a fade must use the same gain as the full export.
        value['layers'][-1].update(fade_in=1, fade_out=1)
        fading, section = self.root/'fading.mp4', self.root/'fade-section.mp4'
        engine.render_project(media.Job('fade', 'export'), ffmpeg, value, fading, self.root/'work')
        engine.render_project(media.Job('fade preview', 'project_preview'), ffmpeg, value, section,
                              self.root/'work', begin=4.2, length=.7, preview=True)
        def rms(path, offset):
            content = run(ffmpeg, '-v', 'error', '-ss', offset, '-i', path, '-t', '0.25', '-vn',
                          '-ac', '1', '-ar', '48000', '-f', 'f32le', '-').stdout
            values = array('f')
            values.frombytes(content)
            return (sum(sample*sample for sample in values)/len(values))**.5
        self.assertAlmostEqual(rms(fading, 4.3), rms(section, .1), delta=.004)

    def test_embedded_editor_import_edit_history_timeline_preview_export_and_reopen(self):
        hub = media.TaskHub(self.root/'ui-data')
        host = QMainWindow()
        host.setAttribute(Qt.WA_ShowWithoutActivating if args.native else Qt.WA_DontShowOnScreen)
        if args.native:
            host.move(-30000, -30000)
        ui.theme.apply('dark')
        host.setStyleSheet(ui.theme.qss())
        owner = SimpleNamespace(context=SimpleNamespace(window=host), hub=hub,
            service=SimpleNamespace(sources=lambda: [], options=lambda: {'ffmpeg': ffmpeg}),
            preview_server=SimpleNamespace(source_url=lambda path: path))
        with (nullcontext() if args.native else patch.object(ui.Preview, 'load')):
            view = ui.Workspace(owner)
            host.setCentralWidget(view)
            host.resize(1440, 900)
            host.show()
            try:
                view.projects_button.click()
                view.new_project()
                editor = view.project_editor
                self.assertIs(view.library_review.currentWidget(), editor)
                self.assertFalse(editor.isWindow())
                editor.import_files([str(self.red), str(self.blue), str(self.audio), str(self.image)])
                until(lambda: not editor.imports)
                self.assertEqual(editor.bin.count(), 4)
                def select_file(path):
                    editor.bin.setCurrentItem(next(editor.bin.item(i) for i in range(editor.bin.count())
                                                  if editor.bin.item(i).data(Qt.UserRole) == str(path)))
                select_file(self.red)
                editor.append_selected()
                first = editor.selected_id
                editor.fields['in'].setValue(1)
                editor.fields['out'].setValue(5)
                editor.fields['speed'].setValue(2)
                editor.apply_fields()
                self.assertEqual(project.duration(editor.document.data), 2)
                select_file(self.blue)
                editor.append_selected()
                editor.select_item(first)
                editor.seek(1)
                editor.split()
                self.assertEqual(len(editor.document.data['clips']), 3)
                editor.history(False)
                self.assertEqual(len(editor.document.data['clips']), 2)
                editor.history(True)
                self.assertEqual(len(editor.document.data['clips']), 3)
                # Drag a real timeline block to reorder the magnetic track.
                timeline = editor.timeline
                timeline.reflow()
                main = [block for block in timeline.blocks if block[4]]
                last = main[-1]
                editor.debounce.stop()
                QTest.mousePress(timeline, Qt.LeftButton, pos=last[3].center().toPoint())
                QTest.mouseMove(timeline, QPoint(round(timeline.x(.1)), round(last[3].center().y())))
                QTest.mouseRelease(timeline, Qt.LeftButton, pos=QPoint(round(timeline.x(.1)), round(last[3].center().y())))
                self.assertEqual(editor.document.data['clips'][0]['source'], str(self.blue))
                editor.history(False)
                editor.seek(.3)
                editor.add_text()
                editor.text.setPlainText('UI工程文字\n第二行')
                editor.fields['duration'].setValue(1)
                editor.apply_fields()
                select_file(self.image)
                editor.add_media_layer('image')
                select_file(self.audio)
                editor.add_media_layer('audio')
                editor.analyze_waveform()
                until(lambda: not editor.waves)
                self.assertTrue(editor.document.data['media'][str(self.audio)]['peaks'])
                # Wait for the actual shared renderer before checking the preview player.
                until(lambda: editor.loaded_key is not None and not editor.pending_preview and editor.preview_job is None, timeout=30)
                if args.native:
                    until(lambda: editor.preview.core is not None and editor.preview.core.time_pos is not None)
                    until(lambda: editor.resume is None)
                    editor.seek(.5)
                    until(lambda: abs(editor.preview.position()-.5) < .1)
                    frame = self.root/'native-project.png'
                    editor.preview.core.command('screenshot-to-file', str(frame), 'video')
                    picture = QImage(str(frame))
                    self.assertGreater(picture.width(), 0)
                    self.assertGreater(picture.pixelColor(300, 300).red(), 150)
                    if args.screenshots:
                        shutil.copyfile(frame, args.screenshots/frame.name)
                for size in ((920, 600), (1440, 900)):
                    host.resize(*size)
                    app.processEvents()
                    self.assertEqual((host.width(), host.height()), size)
                    self.assertTrue(editor.export_button.isVisible())
                    self.assertTrue(editor.rect().contains(editor.export_button.mapTo(editor, editor.export_button.rect().bottomRight())))
                if args.screenshots:
                    host.grab().save(str(args.screenshots/'project-editor-ui.png'))
                output = self.root/'ui-project.mp4'
                with patch.object(ui.QFileDialog, 'getSaveFileName', return_value=(str(output), '')):
                    editor.export_button.click()
                exported = hub.jobs[-1]
                until(lambda: exported.future.done(), timeout=30)
                self.assertEqual(exported.state, 'done', exported.message)
                self.assertEqual(len(editor.document.data['clips']), 3)
                path = editor.document.path
                editor.leave()
                self.assertIsNone(editor.preview.core)
                view.refresh_projects()
                self.assertEqual(view.project_list.topLevelItemCount(), 1)
                view.project_list.setCurrentItem(view.project_list.topLevelItem(0))
                view.open_project_selection()
                self.assertEqual(len(view.project_editor.document.data['clips']), 3)
                self.assertEqual(len(view.project_editor.document.data['layers']), 3)
                editor = view.project_editor
                editor.select_item(editor.document.data['clips'][-1]['id'])
                editor.duplicate()
                self.assertEqual(len(editor.document.data['clips']), 4)
                if args.native:
                    until(lambda: not editor.preview_dirty and editor.preview_job is None and editor.resume is None, timeout=30)
                    editor.seek(7.7)
                    editor.toggle_play()
                    until(lambda: editor.preview_begin == 8 and editor.playing and editor.position > 8.1, timeout=30)
                    editor.toggle_play()
                    self.assertFalse(editor.playing)
                editor.history(False)
                view.project_editor.shutdown_preview()
                restored = project.read_project(path)
                self.assertTrue(restored['media'][str(self.audio)]['peaks'])
                self.assertTrue(self.red.is_file() and self.blue.is_file())
            finally:
                view.close()
                hub.close()
                host.close()
                host.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()

    def test_missing_media_cancel_and_no_overwrite(self):
        value, first, _ = self.document()
        copied = deepcopy(value)
        missing = str(self.root/'missing.mp4')
        copied['media'][missing] = dict(copied['media'].pop(str(self.red)), path=missing)
        copied['clips'][0]['source'] = missing
        with self.assertRaisesRegex(FileNotFoundError, '缺失'):
            engine.render_project(media.Job('missing', 'export'), ffmpeg, copied, self.root/'missing-out.mp4', self.root/'work')
        target = self.root/'keep.mp4'
        target.write_bytes(b'keep existing')
        with self.assertRaises(FileExistsError):
            engine.render_project(media.Job('overwrite', 'export'), ffmpeg, value, target, self.root/'work')
        self.assertEqual(target.read_bytes(), b'keep existing')
        job = media.Job('cancel', 'export')
        job.cancel.set()
        with self.assertRaises(media.Cancelled):
            engine.render_project(job, ffmpeg, value, self.root/'cancelled.mp4', self.root/'work')
        self.assertFalse((self.root/'cancelled.mp4').exists())
        self.assertFalse(list((self.root/'work').glob('project-*')))

    def test_rotated_visual_order_legacy_migration_and_export(self):
        value = project.new_project('混合视觉轨道')
        value['media'] = deepcopy(self.media_info)
        value['render'] = {'width': 320, 'height': 180, 'fps': 20}
        project.append_clip(value, str(self.red), 0, 1)
        project.add_layer(value, 'image', 0, str(self.image))
        image = value['layers'][-1]
        image.update(x=.2, y=.2, w=.5, h=.2, rotation=90, duration=1, track=1)
        project.add_layer(value, 'video', 0, str(self.blue))
        video = value['layers'][-1]
        video.update(x=.2, y=.2, w=.5, h=.2, rotation=90, duration=1, track=2)
        def picture(name):
            target = self.root/(name+'.mp4')
            engine.render_project(media.Job('rotated layers', 'export'), ffmpeg, value, target, self.root/'work')
            frame = self.root/(name+'.png')
            run(ffmpeg, '-y', '-v', 'error', '-ss', '0.5', '-i', target, '-frames:v', '1', frame)
            if args.screenshots:
                shutil.copyfile(frame, args.screenshots/frame.name)
            return QImage(str(frame))
        result = picture('video-over-image')
        self.assertGreater(result.pixelColor(144, 110).blue(), 180)
        self.assertGreater(result.pixelColor(90, 54).red(), 180)
        image['track'], video['track'] = 3, 2
        result = picture('image-over-video')
        self.assertGreater(result.pixelColor(144, 110).green(), 180)
        self.assertGreater(result.pixelColor(90, 54).red(), 180)
        # Graphics above and below a video must both survive in the same output.
        project.add_layer(value, 'image', 0, str(self.image))
        value['layers'][-1].update(x=.75, y=.65, w=.1, h=.1, duration=1, track=1, opacity=.5)
        result = picture('graphics-around-video')
        self.assertAlmostEqual(result.pixelColor(260, 125).green(), 128, delta=15)
        self.assertAlmostEqual(result.pixelColor(260, 125).red(), 128, delta=15)
        self.assertGreater(result.pixelColor(144, 110).green(), 180)
        legacy = deepcopy(value)
        legacy.pop('visual_order')
        legacy['layers'][0]['track'] = 1
        legacy['layers'][1]['track'] = 9
        legacy['layers'][0].pop('rotation')
        path = self.root/'legacy-layers.ddcut.json'
        project.write_project(path, legacy)
        restored = project.read_project(path)
        self.assertEqual(restored['visual_order'], 'mixed')
        self.assertGreater(restored['layers'][0]['track'], restored['layers'][1]['track'])
        self.assertEqual(restored['layers'][0].get('rotation', 0), 0)
        invalid = deepcopy(value)
        invalid['layers'][0]['rotation'] = float('nan')
        with self.assertRaises(ValueError):
            project.validate(invalid)

    @unittest.skipUnless(args.native, 'Native project stage check is explicit')
    def test_native_stage_selection_move_resize_rotate_cancel_history_and_layers(self):
        from ddcut_ranges_test.project_stage import geometry
        hub = media.TaskHub(self.root/'stage-ui')
        host = QMainWindow()
        host.setAttribute(Qt.WA_ShowWithoutActivating)
        host.move(-30000, -30000)
        owner = SimpleNamespace(context=SimpleNamespace(window=host), hub=hub,
            service=SimpleNamespace(sources=lambda: [], options=lambda: {'ffmpeg': ffmpeg}),
            preview_server=SimpleNamespace(source_url=lambda path: path))
        view = ui.Workspace(owner)
        host.setCentralWidget(view)
        host.resize(1440, 900)
        host.show()
        try:
            value = project.new_project('画面操作')
            value['media'] = deepcopy(self.media_info)
            value['render'] = {'width': 320, 'height': 180, 'fps': 20}
            project.append_clip(value, str(self.red), 0, 2)
            identity = project.add_layer(value, 'image', 0, str(self.image))
            value['layers'][-1].update(x=.1, y=.1, w=.3, h=.25, duration=2, rotation=25)
            doc = project.ProjectDocument(self.root/'stage.ddcut.json', value)
            doc.save()
            view.open_project_document(doc)
            editor = view.project_editor
            def ready():
                until(lambda: editor.loaded_key is not None and not editor.preview_dirty and editor.preview_job is None and editor.resume is None, timeout=30)
            ready()
            editor.seek(.5)
            until(lambda: abs(editor.preview.position()-.5) < .06)
            editor.select_item(identity)
            stage, core = editor.stage, editor.preview.core
            until(lambda: stage.visible)
            frame = self.root/'stage-handles.png'
            core.command('screenshot-to-file', str(frame), 'window')
            picture = QImage(str(frame))
            cyan = sum(picture.pixelColor(x, y).blue() > 150 and picture.pixelColor(x, y).green() > 120
                       and picture.pixelColor(x, y).red() < 80
                       for y in range(0, picture.height(), 2) for x in range(0, picture.width(), 2))
            self.assertGreater(cyan, 30)
            if args.screenshots:
                shutil.copyfile(frame, args.screenshots/frame.name)
            def item():
                return next(row for row in doc.data['layers'] if row['id'] == identity)
            # Route through the real libmpv mouse/key bindings, not just Qt's parent widget.
            bounds = stage.frame()
            center = geometry(item(), bounds)[0].map(QPointF())
            core.command('mouse', round(center.x()), round(center.y()))
            core.command('keydown', 'MBTN_LEFT')
            until(lambda: stage.drag is not None)
            revision = doc.revision
            core.command('mouse', round(center.x()+bounds.width()*.1), round(center.y()+bounds.height()*.1))
            until(lambda: stage.drag['draft']['x'] > .19)
            self.assertEqual(doc.revision, revision)
            core.command('keyup', 'MBTN_LEFT')
            until(lambda: doc.revision == revision+1)
            self.assertAlmostEqual(item()['x'], .2, delta=.01)
            self.assertAlmostEqual(item()['y'], .2, delta=.01)
            ready()
            # Qt input is also supported; use the actual preview surface coordinates.
            def widget_point(point):
                dim = editor.preview.core.osd_dimensions
                return QPoint(round(point.x()*editor.preview.width()/dim['w']), round(point.y()*editor.preview.height()/dim['h']))
            bounds = stage.frame()
            corners = geometry(item(), bounds)[2]
            origin, target = corners[2], corners[0]+(corners[2]-corners[0])*1.2
            revision = doc.revision
            QTest.mousePress(editor.preview, Qt.LeftButton, pos=widget_point(origin))
            QTest.mouseMove(editor.preview, widget_point(target))
            QTest.mouseRelease(editor.preview, Qt.LeftButton, pos=widget_point(target))
            self.assertEqual(doc.revision, revision+1)
            self.assertAlmostEqual(item()['w'], .36, delta=.01)
            self.assertAlmostEqual(item()['h'], .3, delta=.01)
            ready()
            bounds = stage.frame()
            transform, _, _, handle = geometry(item(), bounds)
            center = transform.map(QPointF())
            vector = handle-center
            target = center+QPointF(-vector.y(), vector.x())
            previous = deepcopy(item())
            QTest.mousePress(editor.preview, Qt.LeftButton, pos=widget_point(handle))
            QTest.mouseMove(editor.preview, widget_point(target))
            QTest.keyClick(editor.preview, Qt.Key_Escape)
            QTest.mouseRelease(editor.preview, Qt.LeftButton, pos=widget_point(target))
            self.assertEqual(item(), previous)
            QTest.mousePress(editor.preview, Qt.LeftButton, pos=widget_point(handle))
            QTest.mouseMove(editor.preview, widget_point(target))
            QTest.mouseRelease(editor.preview, Qt.LeftButton, pos=widget_point(target))
            self.assertAlmostEqual(item()['rotation'], 115, delta=2)
            editor.history(False)
            self.assertAlmostEqual(item()['rotation'], 25, delta=.01)
            editor.history(True)
            self.assertAlmostEqual(item()['rotation'], 115, delta=2)
            editor.place_selected('center')
            self.assertAlmostEqual(item()['x']+item()['w']/2, .5)
            editor.place_selected('reset')
            self.assertEqual(item()['rotation'], 0)
            pip = editor.change(lambda data: project.add_layer(data, 'video', 0, str(self.blue)))
            editor.select_item(identity)
            editor.move_visual(1)
            self.assertGreater(item()['track'], next(row['track'] for row in doc.data['layers'] if row['id'] == pip))
            editor.history(False)
            self.assertLess(item()['track'], next(row['track'] for row in doc.data['layers'] if row['id'] == pip))
            ready()
            host.resize(920, 600)
            QTest.qWait(150)
            self.assertEqual((host.width(), host.height()), (920, 600))
            self.assertTrue(stage.visible)
            loaded = project.read_project(doc.path)
            self.assertEqual(loaded['layers'], doc.data['layers'])
            editor.shutdown_preview()
            self.assertFalse(stage.visible)
            self.assertIsNone(stage.buffer)
        finally:
            view.close()
            hub.close()
            host.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()

    def test_ranges_to_project_relocate_save_as_and_delete_only_project(self):
        source = self.root/'relocate-source.mp4'
        moved = self.root/'relocated-source.mp4'
        shutil.copyfile(self.red, source)
        hub = media.TaskHub(self.root/'ranges-ui')
        host = QMainWindow()
        host.setAttribute(Qt.WA_DontShowOnScreen)
        owner = SimpleNamespace(context=SimpleNamespace(window=host), hub=hub,
            service=SimpleNamespace(sources=lambda: [], options=lambda: {'ffmpeg': ffmpeg}),
            preview_server=SimpleNamespace(source_url=lambda path: path))
        with patch.object(ui.Preview, 'load'):
            view = ui.Workspace(owner)
            host.setCentralWidget(view)
            host.show()
            try:
                view.review({**self.media_info[str(self.red)], 'path': str(source), 'created_at': 0})
                view.add_range(1, 2)
                view.add_range(3, 4)
                view.project_from_ranges()
                until(lambda: (view.refresh_tasks() or True) and view.pending_project is None)
                editor = view.project_editor
                self.assertIs(view.library_review.currentWidget(), editor)
                self.assertEqual([(row['start'], row['end']) for row in editor.document.data['clips']], [(1, 2), (3, 4)])
                internal = editor.document.path
                source.rename(moved)
                editor.refresh()
                editor.bin.setCurrentItem(editor.bin.item(0))
                self.assertIn('缺失', editor.bin.item(0).text())
                with patch.object(ui.QFileDialog, 'getOpenFileName', return_value=(str(moved), '')):
                    editor.relocate()
                until(lambda: not editor.imports)
                self.assertTrue(all(row['source'] == str(moved) for row in editor.document.data['clips']))
                saved = self.root/'external-copy.ddcut.json'
                with patch.object(ui.QFileDialog, 'getSaveFileName', return_value=(str(saved), '')):
                    editor.save_as()
                self.assertTrue(saved.is_file())
                self.assertEqual(project.read_project(saved)['clips'], editor.document.data['clips'])
                editor.leave()
                item = next(view.project_list.topLevelItem(i) for i in range(view.project_list.topLevelItemCount())
                            if Path(view.project_list.topLevelItem(i).data(0, Qt.UserRole)) == internal)
                view.project_list.setCurrentItem(item)
                with patch.object(ui.QMessageBox, 'question', return_value=ui.QMessageBox.Yes):
                    view.delete_project()
                self.assertFalse(internal.exists())
                self.assertTrue(saved.is_file() and moved.is_file() and self.red.is_file())
            finally:
                view.close()
                hub.close()
                host.close()
                host.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()

    def test_project_chat_burn_and_cancel_during_composite(self):
        from ddcut_ranges_test.chat import write_json, ChatReader
        value, _, _ = self.document()
        log = self.root/'project-source.danmaku.json'
        write_json(log, [{'time': 1.1, 'kind': 'super_chat', 'text': '工程弹幕', 'uname': 'Test', 'price': 30, 'duration': 2}])
        value['media'][str(self.red)]['danmaku_path'] = str(log)
        value['danmaku'] = True
        project.add_layer(value, 'text', .2, text='文字轨道')
        output = self.root/'project-burn.mp4'
        result = engine.render_project(media.Job('project burn', 'export'), ffmpeg, value, output, self.root/'work')
        self.assertTrue(result['chat_burned'])
        reader = ChatReader()
        self.assertEqual(reader.read(result['danmaku_path'])[0]['text'], '工程弹幕')
        self.assertTrue(reader.video_burned)
        job = media.Job('cancel composite', 'export')
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
        with patch.object(engine, 'run_process', side_effect=cancel), self.assertRaises(media.Cancelled):
            engine.render_project(job, ffmpeg, value, self.root/'cancel-composite.mp4', self.root/'work')
        self.assertFalse((self.root/'cancel-composite.mp4').exists())
        self.assertFalse(list((self.root/'work').glob('project-*')))
        self.assertFalse(any(thread.name == 'ddcut-chat-frames' for thread in threading.enumerate()))


if __name__ == '__main__':
    outcome = []
    def checks():
        result = unittest.main(argv=[sys.argv[0], *remainder], exit=False)
        outcome.append(result.result.wasSuccessful())
        app.quit()
    # Exercise native windows inside the same Qt lifecycle as the application.
    app.setQuitOnLastWindowClosed(False)
    QTimer.singleShot(0, checks)
    app.exec()
    # Destroy Qt's native platform resources while the Python runtime is still live.
    app.shutdown()
    raise SystemExit(0 if outcome and outcome[0] else 1)
