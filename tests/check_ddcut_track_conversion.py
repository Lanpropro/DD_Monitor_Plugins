"""Main/overlay conversion, carried anchors and real timeline drag/export checks."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import check_ddcut_projects as fixtures
from check_ddcut_projects import app, args, remainder, engine, media, project, run, ffmpeg, ui, until
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMainWindow
from PySide6.QtTest import QTest

tracks = importlib.import_module('ddcut_ranges_test.project_tracks')
captions = importlib.import_module('ddcut_ranges_test.subtitles')


class TrackTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProjectTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ProjectTests.tearDownClass.__func__)
    document = fixtures.ProjectTests.document

    def test_conversion_carries_visible_anchors_drops_hidden_and_preserves_source_sound(self):
        data, first, second = self.document()
        data['clips'][0].update(speed=2, volume=.4)
        kept = project.add_layer(data, 'text', .4, text='随片段移动')
        late = project.add_layer(data, 'text', 1.8, text='落在新主轨之后')
        hidden = project.add_layer(data, 'text', .1, text='被裁掉的锚点')
        data['layers'][-1]['source_at'] = 5.5
        captions.set_cue(data, '', .2, 1.5, '随片段移动的字幕')
        doc = project.ProjectDocument(self.root/'conversion.ddcut.json', data)
        before = deepcopy(data)
        video_id = doc.commit(lambda value: tracks.clip_to_overlay(value, first, 1))
        self.assertEqual(len(doc.data['clips']), 1)
        video = next(item for item in doc.data['layers'] if item['id'] == video_id)
        self.assertEqual((video['source'], video['in'], video['speed'], video['duration'], video['volume']),
                         (str(self.red), 1, 2, 2, .4))
        self.assertFalse(any(item['id'] in (late, hidden) for item in doc.data['layers']))
        carried = next(row for row in project.placed_layers(doc.data) if row[0]['id'] == kept)
        self.assertAlmostEqual(carried[1], 1.4)
        cue = captions.placed_cues(doc.data)[0]
        self.assertAlmostEqual(cue[1], 1.2)
        self.assertAlmostEqual(cue[2], 2)
        doc.undo()
        self.assertEqual(doc.data['clips'], before['clips'])
        self.assertEqual(doc.data['layers'], before['layers'])
        doc.redo()
        clip_id = doc.commit(lambda value: tracks.overlay_to_clip(value, video_id, 0))
        clip = next(item for item in doc.data['clips'] if item['id'] == clip_id)
        self.assertEqual((clip['start'], clip['end'], clip['speed'], clip['volume']), (1, 5, 2, .4))
        self.assertNotIn('opacity', clip)
        self.assertEqual(project.read_project(doc.path)['clips'], doc.data['clips'])
        single = project.new_project()
        single['media'] = deepcopy(self.media_info)
        only = project.append_clip(single, str(self.red))
        copy = deepcopy(single)
        with self.assertRaises(ValueError):
            tracks.clip_to_overlay(single, only, 0)
        self.assertEqual(single, copy)

    def test_copied_and_split_text_styles_have_independent_ownership(self):
        data, first, _ = self.document()
        original = project.add_layer(data, 'text', .5)
        duplicate = project.duplicate_item(data, original)
        data['layers'][1]['text_style']['background'] = '#112233'
        self.assertEqual(data['layers'][0]['text_style']['background'], '')
        project.split_clip(data, first, 2)
        pieces = [item for item in data['layers'] if item['id'] not in (original, duplicate)]
        self.assertTrue(pieces)
        pieces[0]['text_style']['bold'] = True
        self.assertFalse(data['layers'][0]['text_style']['bold'])
        project.validate(data)

    def test_real_qt_vertical_drag_buttons_history_and_export(self):
        hub = media.TaskHub(self.root/'track-ui')
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
        data, first, _ = self.document()
        doc = project.ProjectDocument(hub.root/'projects/tracks.ddcut.json', data)
        doc.save()
        view.open_project_document(doc)
        editor, timeline = view.project_editor, view.project_editor.timeline
        try:
            editor.select_item(first)
            editor.convert_button.click()
            self.assertEqual(len(doc.data['clips']), 1)
            self.assertEqual(editor.selected()[0]['kind'], 'video')
            video_id = editor.selected_id
            editor.convert_button.click()
            self.assertEqual(len(doc.data['clips']), 2)
            self.assertFalse(any(item['id'] == video_id for item in doc.data['layers']))
            revision = doc.revision
            timeline.reflow()
            main = next(block for block in timeline.blocks if block[4] and block[0]['id'] == editor.selected_id)
            destination = QPoint(round(main[3].center().x()), 30+timeline.rows.index(next(row for row in timeline.rows if row[0]=='visual_new'))*46+20)
            QTest.mousePress(timeline, Qt.LeftButton, pos=main[3].center().toPoint())
            QTest.mouseMove(timeline, destination)
            QTest.mouseRelease(timeline, Qt.LeftButton, pos=destination)
            self.assertEqual(doc.revision, revision+1)
            self.assertEqual(len(doc.data['clips']), 1)
            self.assertEqual(editor.selected()[0]['kind'], 'video')
            timeline.reflow()
            overlay = next(block for block in timeline.blocks if not block[4] and block[0]['kind']=='video')
            destination = QPoint(round(overlay[3].center().x()), 30+timeline.rows.index(('main',0))*46+20)
            QTest.mousePress(timeline, Qt.LeftButton, pos=overlay[3].center().toPoint())
            QTest.mouseMove(timeline, destination)
            QTest.mouseRelease(timeline, Qt.LeftButton, pos=destination)
            self.assertEqual(len(doc.data['clips']), 2)
            editor.history(False)
            self.assertEqual(len(doc.data['clips']), 1)
            editor.select_item(doc.data['clips'][0]['id'])
            self.assertFalse(editor.convert_button.isEnabled())
            audio_id = editor.change(lambda value: project.add_layer(value, 'audio', .2, str(self.audio)))
            editor.select_item(audio_id)
            timeline.reflow()
            audio = next(block for block in timeline.blocks if block[0]['id']==audio_id)
            previous_track = audio[0]['track']
            destination = QPoint(round(audio[3].center().x()), 30+timeline.rows.index(next(row for row in timeline.rows if row[0]=='audio_new'))*46+20)
            QTest.mousePress(timeline, Qt.LeftButton, pos=audio[3].center().toPoint())
            QTest.mouseMove(timeline, destination)
            QTest.mouseRelease(timeline, Qt.LeftButton, pos=destination)
            self.assertGreater(editor.selected()[0]['track'], previous_track)
            until(lambda: editor.preview.core is not None and not editor.preview_dirty, timeout=30)
            output = self.root/'converted.mp4'
            engine.render_project(media.Job('converted', 'export'), ffmpeg, doc.data,
                                  output, self.root/'work', begin=0, length=.7)
            frame = self.root/'converted.png'
            run(ffmpeg, '-y', '-v', 'error', '-i', output, '-frames:v', '1', frame)
            image = QImage(str(frame))
            self.assertGreater(image.pixelColor(160, 90).blue(), 180)
            self.assertGreater(image.pixelColor(256, 144).red(), 180)
            for size in [(1440,900),(920,600)]:
                host.resize(*size)
                app.processEvents()
                self.assertEqual((host.width(),host.height()),size)
                self.assertTrue(editor.export_button.isVisible())
            if args.screenshots:
                args.screenshots.mkdir(parents=True,exist_ok=True)
                image.save(str(args.screenshots/'converted.png'))
                host.grab().save(str(args.screenshots/'track-editor.png'))
        finally:
            view.close()
            hub.close()
            host.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
            app.processEvents()


if __name__ == '__main__':
    results=[]
    def checks():
        result=unittest.main(argv=[sys.argv[0],*remainder],exit=False)
        results.append(result.result.wasSuccessful())
        app.quit()
    app.setQuitOnLastWindowClosed(False)
    QTimer.singleShot(0,checks)
    app.exec()
    app.shutdown()
    raise SystemExit(0 if results and results[0] else 1)
