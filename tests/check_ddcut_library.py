"""Real temporary files and Qt library management; never touches user recordings."""
from datetime import date, datetime
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys

from check_ddcut_ranges_chat import app, args, remainder, media, module, ui, until
from PySide6.QtCore import QCoreApplication, QDate, QEvent, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QMessageBox

data = importlib.import_module('ddcut_ranges_test.library_data')
tools_module = importlib.import_module('ddcut_ranges_test.library_tools')
project = importlib.import_module('ddcut_ranges_test.project')


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ddcut-library-')
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def entry(self, name='sample', size=17, **extra):
        path = self.root / (name + '.mp4')
        path.write_bytes(b'v' * size)
        return dict(path=str(path), title=name, kind='recording',
                    created_at=datetime(2026, 11, 8, 23, 59, 50).timestamp(), duration=20, **extra)

    def archive(self):
        folder = self.root / 'DDCut_Replay/owned'
        folder.mkdir(parents=True)
        path = folder / 'source.m3u8'
        path.write_text('#EXTM3U', encoding='utf-8')
        (folder / 'part.ts').write_bytes(b't' * 101)
        entry = dict(path=str(path), title='archive', kind='recording', index_format='hls',
                     created_at=datetime(2026, 11, 9, 0, 10).timestamp(), duration=30)
        (folder / 'recording.json').write_text(json.dumps(entry), encoding='utf-8')
        return entry

    def test_physical_accounting_unique_files_unknown_missing_and_calendar_midnight(self):
        entry, archive = self.entry(), self.archive()
        sidecar = Path(entry['path']).with_suffix('.danmaku.json')
        sidecar.write_bytes(b'{}')
        copy = dict(entry, kind='clip')
        missing = dict(path=str(self.root/'missing.ts'), title='missing')
        result = data.collect_stats(media.Job('stats', 'test'), [entry, archive, copy, missing], self.root)
        actual = sum(path.stat().st_size for path in self.root.rglob('*') if path.is_file())
        self.assertEqual(sum(row['bytes'] for row in result['categories'].values()), actual)
        self.assertEqual(result['categories']['recording']['unknown'], 1)
        self.assertTrue(result['entries'][data.identity(missing['path'])]['missing'])
        self.assertGreater(result['disk']['free'], 0)
        self.assertTrue(data.on_date(entry, date(2026, 11, 8)))
        self.assertTrue(data.on_date(entry, date(2026, 11, 9)))
        self.assertFalse(data.on_date(entry, date(2026, 11, 10)))
        entry['ended_at'] = datetime(2026, 11, 9).timestamp()
        self.assertFalse(data.on_date(entry, date(2026, 11, 9)))

    def test_delete_plan_active_project_shared_and_real_sidecar_deletion(self):
        entry, archive = self.entry(), self.archive()
        for protected, references in [([entry['path']], []), ([], [entry['path']])]:
            with self.assertRaises(ValueError):
                data.deletion_plan([entry], [entry], protected, references)
        # Same-path alias is one identity, while another HLS record sharing parts is rejected.
        other = dict(archive, path=str(Path(archive['path']).with_name('alias.m3u8')))
        Path(other['path']).write_text('#EXTM3U', encoding='utf-8')
        with self.assertRaises(ValueError):
            data.deletion_plan([archive], [archive, other])
        Path(other['path']).unlink()
        pictures = Path(entry['path']).with_suffix('.danmaku.images')
        pictures.mkdir()
        (pictures/'image.png').write_bytes(b'image')
        Path(entry['path']).with_suffix('.danmaku.json').write_text('{}')
        unrelated = self.root/'keep.txt'
        unrelated.write_text('keep')
        plans = data.deletion_plan([entry, archive], [entry, archive])
        result = data.delete_files(media.Job('delete', 'library_delete'), plans)
        self.assertEqual(set(result['removed']), {entry['path'], archive['path']})
        self.assertFalse(result['errors'])
        self.assertTrue(unrelated.is_file())
        self.assertFalse(any(Path(name).exists() for plan in plans for name in plan['files']))
        foreign = self.root/'foreign.m3u8'
        foreign.write_text('#EXTM3U')
        with self.assertRaises(ValueError):
            data.deletion_plan([dict(path=str(foreign))], [])
        self.assertTrue(foreign.exists())

    def test_cancel_failure_retains_unfinished_entries_and_reimport_survives_restart(self):
        first, second = self.entry('one'), self.entry('two')
        hub = media.TaskHub(self.root/'hub')
        hub.add_entry(first)
        hub.add_entry(second)
        try:
            hub.forget_entries([first['path']])
            self.assertTrue(Path(first['path']).exists())
            hub.close()
            hub = media.TaskHub(self.root/'hub')
            self.assertTrue(hub.is_forgotten(first['path']))
            self.assertEqual([row['path'] for row in hub.entries], [second['path']])
            hub.add_entry(first)
            self.assertFalse(hub.is_forgotten(first['path']))
            plans = data.deletion_plan([first, second], hub.entries)
            job = media.Job('delete', 'library_delete')
            original = Path.unlink
            def remove(path, **kwargs):
                if str(path) == first['path']:
                    original(path, **kwargs)
                    job.cancel.set()
                else:
                    original(path, **kwargs)
            with patch.object(Path, 'unlink', remove), self.assertRaises(media.Cancelled):
                data.delete_files(job, plans)
            hub.jobs.append(job)
            hub.apply_library_delete(job)
            self.assertEqual(job.result['removed'], [first['path']])
            self.assertTrue(Path(second['path']).is_file())
            # Explicitly importing a recreated file clears its tombstone, including after restart.
            Path(first['path']).write_bytes(b'new')
            hub.add_entry(first)
            hub.close()
            hub = media.TaskHub(self.root/'hub')
            self.assertFalse(hub.is_forgotten(first['path']))
            self.assertEqual(len(hub.entries), 2)
            failure = data.delete_files(media.Job('failure', 'test'), [dict(path='bad', files=[str(self.root)])])
            self.assertTrue(failure['errors'])
            self.assertFalse(failure['removed'])
        finally:
            hub.close()

    def test_qt_multi_select_filters_calendar_stats_remove_and_delete_poll(self):
        entries = [self.entry('alpha'), self.entry('bravo'), self.archive()]
        hub = media.TaskHub(self.root/'ui')
        for entry in entries:
            hub.add_entry(entry)
        host = QMainWindow()
        ui.theme.apply('dark')
        host.setStyleSheet(ui.theme.qss())
        host.setAttribute(Qt.WA_ShowWithoutActivating if args.native else Qt.WA_DontShowOnScreen)
        if args.native:
            host.move(-30000, -30000)
        owner = SimpleNamespace(context=SimpleNamespace(window=host, notify=lambda *a, **k: None), hub=hub,
            service=SimpleNamespace(sources=lambda: [], options=lambda: {'directory': str(self.root)}),
            preview_server=SimpleNamespace(source_url=lambda path: path), _ticks=0)
        view = ui.Workspace(owner)
        owner.workspace = view
        host.setCentralWidget(view)
        host.resize(1440, 900)
        host.show()
        view.category.setCurrentIndex(1)
        controls = view.library_tools
        try:
            controls.batch.setChecked(True)
            with patch.object(view, 'open_selected') as opened:
                item = view.library.item(0)
                QTest.mouseClick(view.library.viewport(), Qt.LeftButton, pos=view.library.visualItemRect(item).center())
                self.assertFalse(opened.called)
            controls.select_all()
            self.assertEqual(len(controls.selected()), 3)
            view.refresh_library()
            self.assertEqual(len(controls.selected()), 3)
            view.search.setText('alpha')
            self.assertEqual(len(controls.selected()), 1)
            self.assertEqual(view.selected_entry()['title'], 'alpha')
            view.search.clear()
            controls.date.setDate(QDate(2026, 11, 9))
            controls.calendar_toggle.setChecked(True)
            app.processEvents()
            self.assertEqual(len(controls.calendar.entries), 3)
            controls.calendar.grab()
            self.assertTrue(controls.calendar.hits)
            controls.calendar.selected.emit(entries[0]['path'], False)
            self.assertEqual(view.library.currentItem().data(Qt.UserRole), entries[0]['path'])
            controls.request_stats()
            until(lambda: controls.stats is not None)
            self.assertIn('切片归档', controls.summary.text())
            controls.sort.setCurrentIndex(3)
            self.assertEqual(view.library.item(0).data(Qt.UserRole), entries[2]['path'])
            for size in [(1440, 900), (920, 600)]:
                host.resize(*size)
                app.processEvents()
                self.assertEqual((host.width(), host.height()), size)
                self.assertTrue(view.rect().contains(controls.actions.mapTo(view, controls.actions.rect().bottomRight())))
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    host.grab().save(str(args.screenshots/f'library-{size[0]}.png'))
            controls.date.setDate(QDate(2026, 11, 10))
            self.assertEqual(view.library_stack.currentIndex(), 2)
            controls.date_filter.setChecked(False)
            controls.clear()
            item = next(view.library.item(i) for i in range(3) if view.library.item(i).data(Qt.UserRole) == entries[0]['path'])
            item.setSelected(True)
            reference = project.new_project('registered reference')
            reference['media'][entries[0]['path']] = {'path': entries[0]['path'], 'duration': 20}
            project_path = self.root/'external.ddcut.json'
            project.write_project(project_path, reference)
            hub.preferences['project_paths'] = [str(project_path)]
            before = len(hub.jobs)
            controls.remove(True)
            self.assertEqual(len(hub.jobs), before)
            self.assertIn('工程引用', view.status.text())
            self.assertTrue(Path(entries[0]['path']).exists())
            hub.preferences['project_paths'] = []
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
                controls.remove(False)
            self.assertEqual(len(hub.entries), 3)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
                controls.remove(False)
            self.assertEqual(len(hub.entries), 2)
            self.assertTrue(Path(entries[0]['path']).exists())
            # A finished scan must not resurrect an explicitly removed record.
            scan = media.Job('scan', 'scan', state='done', result=entries)
            from concurrent.futures import Future
            scan.future = Future()
            scan.future.set_result(entries)
            hub.jobs.append(scan)
            module.DDCutReplay.poll(owner)
            self.assertEqual(len(hub.entries), 2)
            controls.select_all()
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
                controls.remove(True)
            job = hub.jobs[-1]
            until(lambda: job.future.done())
            module.DDCutReplay.poll(owner)
            self.assertEqual(job.state, 'done', job.message)
            self.assertFalse(hub.entries)
            self.assertFalse(Path(entries[1]['path']).exists())
            self.assertFalse(Path(entries[2]['path']).exists())
        finally:
            view.close()
            self.assertFalse(controls.timer.isActive())
            hub.close()
            host.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()

    def test_hidden_export_not_restored_by_history_but_new_export_can_restore(self):
        entry = self.entry('output')
        hub = media.TaskHub(self.root/'exports')
        result = dict(output=entry['path'], source='external', actual_duration=20)
        job = media.Job('export', 'export', state='done', result=result, delivered=True)
        hub.jobs.append(job)
        hub.add_export(job, restore=True)
        hub.forget_entries([entry['path']])
        hub.close()
        hub = media.TaskHub(self.root/'exports')
        try:
            self.assertFalse(hub.entries)
            self.assertTrue(Path(entry['path']).exists())
            hub.add_export(media.Job('new export', 'export', result=result), restore=True)
            self.assertFalse(hub.is_forgotten(entry['path']))
            self.assertEqual(len(hub.entries), 1)
        finally:
            hub.close()


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
