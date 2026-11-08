"""Cover drafts, real capture/exports and embedded Qt pointer regression."""
from copy import deepcopy
import importlib
from pathlib import Path
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import check_ddcut_projects as fixtures
from check_ddcut_projects import app, args, remainder, engine, media, project, run, ffmpeg, ui, until
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow

model = importlib.import_module('ddcut_ranges_test.cover_model')
render = importlib.import_module('ddcut_ranges_test.cover_render')
capture = importlib.import_module('ddcut_ranges_test.cover_media')


class CoverTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProjectTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ProjectTests.tearDownClass.__func__)
    document = fixtures.ProjectTests.document

    def cover(self):
        value = model.new_cover()
        value['background'] = str(self.red_image())
        return value

    def red_image(self):
        path = self.root/'cover-red.png'
        if not path.exists():
            image = QImage(400, 200, QImage.Format_RGB32)
            image.fill(QColor('#ee2200'))
            image.save(str(path))
        return path

    def test_dual_draft_sync_independence_history_and_relative_paths(self):
        data = self.cover()
        text = model.new_element('text')
        for view in data['views'].values():
            view['elements'].append(deepcopy(text))
        doc = model.CoverDocument(self.root/'cover.ddcover.json', data)
        doc.commit(lambda value: model.change_element(value, 'wide', text['id'], dict(text='双比例中文')))
        self.assertEqual(doc.data['views']['home']['elements'][0]['text'], '双比例中文')
        doc.commit(lambda value: value.update(sync=False))
        doc.commit(lambda value: model.change_element(value, 'home', text['id'], dict(text='独立4:3')))
        self.assertEqual(doc.data['views']['wide']['elements'][0]['text'], '双比例中文')
        doc.history()
        self.assertEqual(doc.data['views']['home']['elements'][0]['text'], '双比例中文')
        doc.history(True)
        copy = self.root/'portable-cover/copy.ddcover.json'
        model.write_cover(copy, doc.data)
        self.assertEqual(model.read_cover(copy), doc.data)
        previous = deepcopy(doc.data)
        with self.assertRaises(ValueError):
            doc.commit(lambda value: value['views']['home']['frame'].update(zoom=99))
        self.assertEqual(doc.data, previous)
        copy.unlink()
        self.assertTrue(self.red_image().is_file())
        data, _, _ = self.document()
        data['cover_draft'] = str(doc.path)
        project.write_project(self.root/'with-cover.ddcut.json', data)
        self.assertEqual(project.read_project(self.root/'with-cover.ddcut.json')['cover_draft'], str(doc.path))

    def test_crop_pixels_text_sticker_flip_rotation_and_real_png_jpeg(self):
        background = self.root/'split-background.png'
        picture = QImage(200, 100, QImage.Format_RGB32)
        for y in range(100):
            for x in range(200):
                picture.setPixelColor(x, y, QColor('red' if x < 100 else 'blue'))
        picture.save(str(background))
        data = model.new_cover()
        data['background'] = str(background)
        data['views']['home']['frame']['x'] = 0
        wide, home = render.render_cover(data, 'wide'), render.render_cover(data, 'home')
        self.assertGreater(wide.pixelColor(1500, 500).blue(), 200)
        self.assertGreater(home.pixelColor(700, 500).red(), 200)
        text = model.new_element('text')
        text.update(text='封面 TEST\n第二行', color='#ffffff', x=.2, y=.1, w=.6, h=.3, outline=8, shadow=True)
        sticker = model.new_element('image', str(self.image))
        sticker.update(x=.4, y=.6, w=.2, h=.1, rotation=90)
        for view in data['views'].values():
            view['elements'] += [deepcopy(text), deepcopy(sticker)]
        image = render.render_cover(data, 'wide')
        self.assertGreater(image.pixelColor(960, 740).green(), 200)
        self.assertGreater(sum(min(image.pixelColor(x, y).getRgb()[:3]) > 200 for y in range(100, 400, 3) for x in range(300, 1500, 3)), 50)
        for extension in ('png', 'jpg'):
            targets = {key: self.root/(key+'-cover.'+extension) for key in model.SIZES}
            result = render.export_covers(media.Job('cover export', 'cover_export'), data, targets, extension)
            for key, path in result['images'].items():
                actual = QImage(path)
                self.assertEqual((actual.width(), actual.height()), model.SIZES[key])
                self.assertGreater(actual.pixelColor(actual.width()//2, 740).green(), 180)
            if extension == 'png':
                self.assertEqual(QImage(str(targets['wide'])).pixelColor(960, 740), image.pixelColor(960, 740))
            with self.assertRaises(FileExistsError):
                render.export_covers(media.Job('keep', 'cover_export'), data, targets, extension)
        two_colors = self.root/'two-color-sticker.png'
        pattern = QImage(100, 50, QImage.Format_RGB32)
        for y in range(50):
            for x in range(100):
                pattern.setPixelColor(x, y, QColor('green' if x < 50 else 'blue'))
        pattern.save(str(two_colors))
        row = data['views']['wide']['elements'][-1]
        row.update(path=str(two_colors), rotation=0)
        normal = render.render_cover(data, 'wide')
        row['flip'] = True
        flipped = render.render_cover(data, 'wide')
        self.assertGreater(normal.pixelColor(820, 690).green(), normal.pixelColor(820, 690).blue())
        self.assertGreater(flipped.pixelColor(820, 690).blue(), flipped.pixelColor(820, 690).green())
        if args.screenshots:
            image.save(str(args.screenshots/'cover-wide.png'))
            render.render_cover(data, 'home').save(str(args.screenshots/'cover-home.png'))

    def test_missing_images_cancel_and_partial_output_cleanup(self):
        data = self.cover()
        data['views']['home']['elements'].append(model.new_element('image', str(self.root/'missing.png')))
        targets = {key: self.root/(key+'-failed.png') for key in model.SIZES}
        with self.assertRaises(FileNotFoundError):
            render.export_covers(media.Job('missing', 'cover_export'), data, targets)
        self.assertFalse(any(path.exists() for path in targets.values()))
        job = media.Job('cancel cover', 'cover_export')
        job.cancel.set()
        with self.assertRaises(media.Cancelled):
            render.export_covers(job, self.cover(), targets)
        self.assertFalse(any(path.exists() for path in targets.values()))
        self.assertFalse(list(self.root.glob('.cover-*')))

    def test_capture_archive_hls_project_subtitles_and_final_frame(self):
        source = self.media_info[str(self.red)]
        first = capture.capture_frame(media.Job('frame', 'cover_frame'), ffmpeg, source, 5.999,
                                      self.root/'last-frame.png', self.root/'work')
        self.assertGreater(first['time'], 5.8)
        self.assertGreater(QImage(first['path']).pixelColor(40, 40).red(), 180)
        folder = self.root/'cover-hls'
        folder.mkdir()
        playlist = folder/'record.m3u8'
        run(ffmpeg, '-y', '-v', 'error', '-i', self.red, '-c', 'copy', '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0', playlist)
        info = engine.inspect_file(media.Job('HLS', 'test'), ffmpeg, playlist, self.root/'work')
        result = capture.capture_frame(media.Job('HLS frame', 'cover_frame'), ffmpeg, info, 2.5,
                                       self.root/'hls-frame.png', self.root/'work')
        self.assertGreater(QImage(result['path']).pixelColor(40, 40).red(), 180)
        data, _, _ = self.document()
        from ddcut_ranges_test.subtitles import set_cue
        set_cue(data, '', .2, 1.5, '工程字幕')
        data['subtitle_style'] = dict(size=120, position='middle')
        result = capture.capture_frame(media.Job('project frame', 'cover_frame'), ffmpeg, {'project': data}, .7,
                                       self.root/'project-cover-frame.png', self.root/'work')
        picture = QImage(result['path'])
        self.assertGreater(sum(min(picture.pixelColor(x, y).getRgb()[:3]) > 140 for y in range(picture.height()) for x in range(picture.width())), 20)
        self.assertFalse(list((self.root/'work').glob('cover-frame-*')))

    def test_embedded_ui_gestures_ratio_sync_frame_race_export_and_reopen(self):
        from ddcut_ranges_test.graphic_geometry import geometry
        hub = media.TaskHub(self.root/'cover-ui')
        host = QMainWindow()
        host.setAttribute(Qt.WA_ShowWithoutActivating if args.native else Qt.WA_DontShowOnScreen)
        if args.native:
            host.move(-30000, -30000)
        owner = SimpleNamespace(context=SimpleNamespace(window=host), hub=hub,
            service=SimpleNamespace(sources=lambda: [], options=lambda: {'ffmpeg': ffmpeg}),
            preview_server=SimpleNamespace(source_url=lambda path: path))
        view = ui.Workspace(owner)
        host.setCentralWidget(view)
        host.resize(1440, 900)
        host.show()
        try:
            data, _, _ = self.document()
            doc = project.ProjectDocument(self.root/'cover-ui.ddcut.json', data)
            doc.save()
            view.open_project_document(doc)
            view.open_cover()
            editor = view.cover_editor
            self.assertIs(view.library_review.currentWidget(), editor)
            self.assertFalse(editor.isWindow())
            pending = editor.frame_job[0]
            editor.set_background(str(self.red_image()))
            until(lambda: pending.future.done(), timeout=30)
            self.assertEqual(editor.document.data['background'], str(self.red_image()))
            editor.add_text()
            editor.text.setPlainText('封面文字\n第二行')
            editor.apply_fields()
            identity = editor.selection
            canvas = editor.canvas
            actual, virtual = canvas.area()
            center = geometry(editor.current(), virtual)[0].map(QPointF()).toPoint()
            revisions = len(editor.document.undo_stack)
            target = center+QPoint(round(virtual.width()*.05), round(virtual.height()*.03))
            QTest.mousePress(canvas, Qt.LeftButton, pos=center)
            QTest.mouseMove(canvas, target)
            self.assertEqual(len(editor.document.undo_stack), revisions)
            QTest.mouseRelease(canvas, Qt.LeftButton, pos=target)
            self.assertEqual(len(editor.document.undo_stack), revisions+1)
            self.assertAlmostEqual(editor.current()['x'], .23, delta=.01)
            self.assertEqual(editor.document.data['views']['home']['elements'][0]['x'], editor.current()['x'])
            editor.history(False)
            self.assertAlmostEqual(editor.current()['x'], .18)
            editor.history(True)
            virtual = canvas.area()[1]
            corners = geometry(editor.current(), virtual)[2]
            target = corners[0]+(corners[2]-corners[0])*1.1
            QTest.mousePress(canvas, Qt.LeftButton, pos=corners[2].toPoint())
            QTest.mouseMove(canvas, target.toPoint())
            QTest.keyClick(canvas, Qt.Key_Escape)
            QTest.mouseRelease(canvas, Qt.LeftButton, pos=target.toPoint())
            self.assertAlmostEqual(editor.current()['w'], .64)
            QTest.mousePress(canvas, Qt.LeftButton, pos=corners[2].toPoint())
            QTest.mouseMove(canvas, target.toPoint())
            QTest.mouseRelease(canvas, Qt.LeftButton, pos=target.toPoint())
            self.assertAlmostEqual(editor.current()['w'], .704, delta=.015)
            self.assertAlmostEqual(editor.current()['font_size'], 110, delta=2)
            editor.history(False)
            transform, _, _, handle = geometry(editor.current(), canvas.area()[1])
            center = transform.map(QPointF())
            vector = handle-center
            target = center+QPointF(-vector.y(), vector.x())
            QTest.mousePress(canvas, Qt.LeftButton, pos=handle.toPoint())
            QTest.mouseMove(canvas, target.toPoint())
            QTest.mouseRelease(canvas, Qt.LeftButton, pos=target.toPoint())
            self.assertAlmostEqual(editor.current()['rotation'], 90, delta=2)
            editor.history(False)
            editor.zoom.setValue(200)
            editor.zoom.editingFinished.emit()
            actual = canvas.area()[0]
            background_point = QPoint(round(actual.left()+15), round(actual.bottom()-15))
            QTest.mousePress(canvas, Qt.LeftButton, pos=background_point)
            QTest.mouseMove(canvas, background_point+QPoint(20, 0))
            QTest.mouseRelease(canvas, Qt.LeftButton, pos=background_point+QPoint(20, 0))
            self.assertLess(editor.document.data['views']['wide']['frame']['x'], .5)
            self.assertEqual(editor.document.data['views']['home']['frame']['x'], .5)
            editor.select(identity)
            editor.sync.setChecked(False)
            editor.ratios.setCurrentIndex(1)
            editor.text.setPlainText('4:3独立内容')
            editor.apply_fields()
            self.assertEqual(editor.document.data['views']['wide']['elements'][0]['text'], '封面文字\n第二行')
            editor.add_sticker(self.image)
            editor.fields['rotation'].setValue(35)
            editor.apply_fields()
            for size in ((920, 600), (1440, 900)):
                host.resize(*size)
                app.processEvents()
                self.assertEqual((host.width(), host.height()), size)
                self.assertTrue(editor.export_button.isVisible())
                self.assertTrue(editor.rect().contains(editor.export_button.mapTo(editor, editor.export_button.rect().bottomRight())))
            if args.screenshots:
                host.grab().save(str(args.screenshots/'cover-editor-ui.png'))
            target = self.root/'ui.cover.v1.png'
            with patch.object(ui.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
                editor.export_button.click()
            job = editor.export_job
            until(lambda: job.future.done(), timeout=30)
            self.assertEqual(job.state, 'done', job.message)
            self.assertEqual(Path(job.result['images']['wide']).name, 'ui.cover.v1-16x9.png')
            self.assertEqual(Path(job.result['images']['home']).name, 'ui.cover.v1-4x3.png')
            cover_path = editor.document.path
            editor.leave()
            self.assertIs(view.library_review.currentWidget(), view.project_editor)
            view.open_cover()
            self.assertEqual(view.cover_editor.document.path, cover_path)
            self.assertEqual(len(view.cover_editor.document.data['views']['home']['elements']), 2)
            view.cover_editor.time.setValue(.7)
            view.cover_editor.capture()
            until(lambda: view.cover_editor.frame_job is None, timeout=30)
            self.assertIn('cover_frames', view.cover_editor.document.data['background'])
            self.assertFalse(QImage(view.cover_editor.document.data['background']).isNull())
            self.assertTrue(Path(doc.data['cover_draft']).is_file())
            self.assertTrue(self.image.is_file())
        finally:
            view.close()
            hub.close()
            host.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


if __name__ == '__main__':
    results = []
    def checks():
        result = unittest.main(argv=[sys.argv[0], *remainder], exit=False)
        results.append(result.result.wasSuccessful())
        app.quit()
    app.setQuitOnLastWindowClosed(False)
    QTimer.singleShot(0, checks)
    app.exec()
    app.shutdown()
    raise SystemExit(0 if results and results[0] else 1)
