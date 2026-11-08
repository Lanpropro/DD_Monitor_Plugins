"""Shared text pixels, legacy projects, real MP4 and native Qt style editing."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import check_ddcut_projects as fixtures
from check_ddcut_projects import app, args, remainder, engine, media, project, run, ffmpeg, ui, until
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QMainWindow

styles = importlib.import_module('ddcut_ranges_test.graphic_style')
cover = importlib.import_module('ddcut_ranges_test.cover_model')
cover_render = importlib.import_module('ddcut_ranges_test.cover_render')


class StyleTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProjectTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ProjectTests.tearDownClass.__func__)
    document = fixtures.ProjectTests.document

    def test_style_validation_persistence_history_and_legacy_pixels(self):
        data, _, _ = self.document()
        identity = project.add_layer(data, 'text', 0, text='旧工程\n第二行')
        item = data['layers'][0]
        item.pop('text_style')
        before = engine.ProjectGraphics(data, 320, 180).frame_at(.5)
        doc = project.ProjectDocument(self.root/'styles.ddcut.json', data)
        doc.save()
        restored = project.read_project(doc.path)
        self.assertNotIn('text_style', restored['layers'][0])
        after = engine.ProjectGraphics(restored, 320, 180).frame_at(.5)
        self.assertEqual(before.constBits().tobytes(), after.constBits().tobytes())
        style = dict(styles.TEXT_DEFAULTS, bold=True, italic=True, outline=8, background='#008800', align='right', shadow=True)
        doc.commit(lambda value: value['layers'][0].update(text_style=style))
        self.assertEqual(project.read_project(doc.path)['layers'][0]['text_style'], style)
        doc.undo()
        self.assertNotIn('text_style', doc.data['layers'][0])
        doc.redo()
        for invalid in [dict(outline=float('nan')), dict(outline=31), dict(stroke='red'), dict(align='other'), dict(shadow=1)]:
            previous = deepcopy(doc.data)
            with self.assertRaises(ValueError):
                doc.commit(lambda value: value['layers'][0]['text_style'].update(invalid))
            self.assertEqual(doc.data, previous)
        self.assertEqual(doc.data['layers'][0]['id'], identity)

    def test_cover_and_project_share_text_pixels_alignment_and_image_flip(self):
        data, _, _ = self.document()
        project.add_layer(data, 'text', 0, text='共同绘制 TEST\n第二行')
        item = data['layers'][0]
        item.update(x=.125, y=.25, w=.75, h=.5, font_size=100)
        item['text_style'].update(bold=True, italic=True, outline=8, stroke='#ff0000',
                                  background='#0033ff', shadow=True, align='right')
        for align in ('left', 'center', 'right'):
            item['text_style']['align'] = align
            doc = cover.new_cover()
            element = cover.new_element('text')
            element.update({key: item[key] for key in ('text', 'x', 'y', 'w', 'h', 'font_size', 'color')})
            element.update(item['text_style'])
            doc['views']['wide']['elements'] = [element]
            expected = cover_render.render_cover(doc, 'wide')
            actual = QImage(1920, 1080, QImage.Format_RGB32)
            actual.fill(QColor('#16191f'))
            painter = QPainter(actual)
            painter.drawImage(0, 0, engine.ProjectGraphics(data, 1920, 1080).frame_at(.5))
            painter.end()
            differences = [abs(a-b) for a,b in zip(actual.constBits().tobytes(), expected.constBits().tobytes())]
            # Project layers pass through premultiplied alpha before the final RGB canvas.
            self.assertLessEqual(max(differences), 4)
            self.assertLess(sum(differences)/len(differences), .05)
        image = QImage(100, 100, QImage.Format_RGB32)
        image.fill(QColor('red'))
        painter = QPainter(image)
        painter.fillRect(50, 0, 50, 100, QColor('blue'))
        painter.end()
        source = self.root/'two-colors.png'
        image.save(str(source))
        data['layers'] = []
        data['media'][str(source)] = dict(path=str(source), type='image', duration=0)
        project.add_layer(data, 'image', 0, str(source))
        data['layers'][0].update(x=0, y=0, w=1, h=1, flip=True)
        result = engine.ProjectGraphics(data, 320, 180).frame_at(.5)
        self.assertGreater(result.pixelColor(10, 10).blue(), 200)
        self.assertGreater(result.pixelColor(300, 10).red(), 200)

    def test_native_editor_style_controls_preview_export_and_reopen(self):
        hub = media.TaskHub(self.root/'style-ui')
        host = QMainWindow()
        ui.theme.apply('dark')
        host.setStyleSheet(ui.theme.qss())
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
        data, _, _ = self.document()
        data['render'] = dict(width=320, height=180, fps=20)
        doc = project.ProjectDocument(hub.root/'projects/styled.ddcut.json', data)
        doc.save()
        editor = view.project_editor
        try:
            view.open_project_document(project.ProjectDocument(doc.path))
            editor.add_text()
            identity = editor.selected_id
            editor.text.setPlainText('样式验证\n第二行')
            editor.fields['font_size'].setValue(160)
            editor.fields['w'].setValue(90)
            editor.fields['h'].setValue(80)
            editor.text_style.presets.setCurrentIndex(4)
            count = len(editor.document.undo_stack)
            editor.text_style.preset_button.click()
            self.assertEqual(len(editor.document.undo_stack), count+1)
            item = editor.selected()[0]
            self.assertEqual(item['text_style']['background'], '#111111')
            self.assertTrue(item['text_style']['bold'])
            editor.text_style.fields['italic'].setChecked(True)
            editor.text_style.fields['shadow'].setChecked(True)
            editor.text_style.align.setCurrentIndex(1)
            editor.apply_fields()
            stored = deepcopy(editor.selected()[0]['text_style'])
            until(lambda: editor.preview.core is not None and not editor.preview_dirty, timeout=30)
            if args.native:
                until(lambda: editor.resume is None and editor.preview.core.time_pos is not None)
                frame = self.root/'styled-native.png'
                editor.preview.core.command('screenshot-to-file', str(frame), 'video')
                image = QImage(str(frame))
                yellow = sum(image.pixelColor(x,y).red()>130 and image.pixelColor(x,y).green()>100 and image.pixelColor(x,y).blue()<100
                             for x in range(0,image.width(),2) for y in range(0,image.height(),2))
                self.assertGreater(yellow, 15)
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    image.save(str(args.screenshots/'styled-native.png'))
                stage = editor.stage
                geometry = importlib.import_module('ddcut_ranges_test.graphic_geometry').geometry
                item = editor.selected()[0]
                frame = stage.frame()
                corners = geometry(item, frame)[2]
                revision = editor.document.revision
                stage.pointer('down', corners[2])
                stage.pointer('move', corners[0]+(corners[2]-corners[0])*1.1)
                self.assertGreater(stage.drag['draft']['font_size'], 170)
                stage.pointer('up', corners[2])
                self.assertEqual(editor.document.revision, revision+1)
                until(lambda: not editor.preview_dirty, timeout=30)
                item = editor.selected()[0]
                frame = stage.frame()
                center = geometry(item, frame)[0].map(QPointF())
                stage.pointer('down', center)
                stage.pointer('move', frame.center()+QPointF(2, 2))
                draft = stage.drag['draft']
                self.assertAlmostEqual(draft['x']+draft['w']/2, .5)
                self.assertAlmostEqual(draft['y']+draft['h']/2, .5)
                stage.pointer('up', frame.center())
                until(lambda: not editor.preview_dirty and editor.resume is None, timeout=30)
                core = editor.preview.core
                frame = stage.frame()
                core.command('mouse', round(frame.center().x()), round(frame.center().y()))
                core.command('keypress', 'MBTN_LEFT_DBL')
                until(lambda: editor.text.textCursor().hasSelection())
            for size in [(1440, 900), (920, 600)]:
                host.resize(*size)
                app.processEvents()
                self.assertEqual((host.width(), host.height()), size)
                self.assertTrue(editor.export_button.isVisible())
            target = self.root/'styled-project.mp4'
            engine.render_project(media.Job('styles export', 'export'), ffmpeg, editor.document.data,
                                  target, self.root/'work', begin=0, length=1)
            frame = self.root/'styled-output.png'
            run(ffmpeg, '-y', '-v', 'error', '-i', target, '-frames:v', '1', frame)
            image = QImage(str(frame))
            self.assertFalse(image.isNull())
            self.assertGreater(sum(image.pixelColor(x,y).green()>100 for x in range(320) for y in range(180)), 25)
            editor.leave()
            view.open_project_document(project.ProjectDocument(doc.path))
            editor.select_item(identity)
            self.assertEqual(editor.selected()[0]['text_style'], stored)
            self.assertTrue(editor.text_style.fields['italic'].isChecked())
            if args.screenshots:
                args.screenshots.mkdir(parents=True, exist_ok=True)
                editor.inspector_scroll.ensureWidgetVisible(editor.text_style)
                app.processEvents()
                host.grab().save(str(args.screenshots/'styled-editor.png'))
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
