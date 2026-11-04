"""DDCut Replay real local-media, lifecycle and offscreen Qt regression."""
import argparse
import importlib.util
import json
import os
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

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--screenshots', type=Path)
args, remainder = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['DDM_NO_SAVE'] = '1'

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton
from PySide6.QtTest import QTest
from ddm.plugins import PluginContext, PluginManager

spec = importlib.util.spec_from_file_location('ddcut_test_plugin', ROOT / 'plugins/DDCut_Replay/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
media = sys.modules['ddcut_test_plugin.media']
ui = sys.modules['ddcut_test_plugin.workspace']
APP = QApplication.instance() or QApplication([])
for font_path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/segoeui.ttf'):
    if Path(font_path).is_file():
        QFontDatabase.addApplicationFont(font_path)
APP.setFont(QFont('Microsoft YaHei', 10))
FFMPEG = shutil.which('ffmpeg')


def wait_job(job):
    job.future.result(timeout=40)
    return job


class DDCutReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ddcut-offline-')
        self.root = Path(self.temp.name)
        self.hub = media.TaskHub(self.root / 'data')
        self.source = self.root / "source 片段'甲.mp4"
        subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30', '-f', 'lavfi',
            '-i', 'anullsrc=r=48000:cl=stereo', '-t', '4.2', '-c:v', 'libx264',
            '-threads', '1', '-g', '30', '-c:a', 'aac', str(self.source)], check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.entry = {'path': str(self.source), 'title': '本地静音测试 · 房间示例',
                      'created_at': time.time(), **media.probe(media.Job('probe', 'test'), FFMPEG, self.source, self.root)}

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def test_precise_and_copy_export_have_real_decodable_results(self):
        for mode, suffix in [('precise', '.mp4'), ('copy', '.mkv')]:
            target = self.root / ('clip-' + mode + suffix)
            job = wait_job(self.hub.submit(mode, 'export', media.export_clip,
                FFMPEG, self.entry, .75, 2.25, mode, target, self.root))
            self.assertEqual(job.state, 'done', job.message)
            info = media.probe(media.Job('probe', 'test'), FFMPEG, target, self.root)
            self.assertTrue(info['has_audio'])
            self.assertGreater(info['duration'], 1)
            if mode == 'precise':
                self.assertAlmostEqual(info['duration'], 1.5, delta=.15)
            self.assertFalse(list(self.root.glob('*.partial.*')))
        # Results completed just before shutdown are indexed, and old persisted
        # successful jobs also recover into the new clip category on restart.
        self.hub.close()
        self.assertEqual(len([e for e in self.hub.entries if e.get('kind') == 'clip']), 2)
        saved = json.loads(self.hub.state_file.read_text(encoding='utf-8'))
        saved['entries'] = []
        self.hub.state_file.write_text(json.dumps(saved), encoding='utf-8')
        reopened = media.TaskHub(self.hub.root)
        self.assertEqual(len(reopened.entries), 2)
        reopened.close()

    def test_existing_output_is_untouched_and_invalid_bounds_fail(self):
        target = self.root / 'existing.mp4'
        target.write_bytes(b'old-finished-output')
        for start, end in [(0, 2), (3, 2), (-1, 2), (0, 20)]:
            job = wait_job(self.hub.submit('reject', 'export', media.export_clip,
                FFMPEG, self.entry, start, end, 'precise', target, self.root))
            self.assertEqual(job.state, 'failed')
            self.assertEqual(target.read_bytes(), b'old-finished-output')

    def test_cancel_reaps_ffmpeg_and_cleans_own_partial(self):
        output = self.root / 'cancel.partial.mp4'

        def long_process(job):
            try:
                return media.run_process(job, [FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
                    '-stream_loop', '-1', '-i', str(self.source), '-t', '600', '-c:v', 'libx264',
                    '-preset', 'veryslow', '-an', str(output)], self.root, duration=600)
            finally:
                output.unlink(missing_ok=True)

        job = self.hub.submit('cancel', 'export', long_process)
        deadline = time.monotonic() + 5
        while job.progress == 0 and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(job.state, 'running')
        self.hub.cancel(job)
        wait_job(job)
        self.assertEqual(job.state, 'cancelled', job.message)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('*.log')))

    def test_snapshot_is_decodable_and_persists_selection_markers(self):
        snapshot = {'parts': [str(self.source)], 'room': {'uname': '测试房间'},
                    'session_id': 'offline-session', 'created_at': 1, 'snapshot_at': 5}
        job = wait_job(self.hub.submit('snapshot', 'snapshot', media.prepare_snapshot,
            FFMPEG, snapshot, self.root / 'review', self.root))
        self.assertEqual(job.state, 'done', job.message)
        self.assertAlmostEqual(job.result['duration'], self.entry['duration'], delta=.2)
        self.hub.add_entry({**job.result, 'selection': [.5, 2], 'markers': [{'time': 1, 'title': '精彩点'}]})
        job.delivered = True
        self.hub.close()
        loaded = media.TaskHub(self.root / 'data')
        self.assertEqual(loaded.entries[0]['selection'], [.5, 2])
        self.assertEqual(loaded.entries[0]['markers'][0]['title'], '精彩点')
        loaded.close()

    def test_restart_marks_pending_tasks_interrupted(self):
        self.hub.jobs.append(media.Job(title='interrupted', kind='export'))
        self.hub.save()
        loaded = media.TaskHub(self.root / 'data')
        self.assertEqual(loaded.jobs[-1].state, 'interrupted')
        loaded.close()

    def test_snapshot_rejects_incompatible_segments(self):
        other = self.root / 'other.mp4'
        subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-i',
            str(self.source), '-vf', 'scale=160:90', '-c:v', 'libx264', '-threads', '1',
            '-an', str(other)], check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        snapshot = {'parts': [str(self.source), str(other)], 'room': {'uname': '测试'},
                    'session_id': 'changed', 'created_at': 1, 'snapshot_at': 5}
        job = wait_job(self.hub.submit('snapshot', 'snapshot', media.prepare_snapshot,
            FFMPEG, snapshot, self.root / 'review', self.root))
        self.assertEqual(job.state, 'failed')
        self.assertIn('编码', job.message)
        self.assertFalse(list((self.root / 'review').glob('*.mkv')))

    def test_workspace_uses_host_components_and_readable_rows(self):
        host = QMainWindow()
        host.setAttribute(Qt.WA_DontShowOnScreen)
        data = [{'source_id': str(i), 'session_id': 'local',
                 'room': {'uname': title, 'room_id': str(i)},
                 'state': 'recording', 'created_at': time.time() - 120}
                for i, title in enumerate(('本地测试房间甲', '本地测试房间乙'))]
        owner = SimpleNamespace(context=SimpleNamespace(window=host), hub=self.hub,
            service=SimpleNamespace(sources=lambda: data),
            preview_server=SimpleNamespace(source_url=lambda path: path))
        view = ui.Workspace(owner)
        host.setCentralWidget(view)
        host.resize(1180, 760)
        reference = QPushButton('本体按钮', host)
        reference.setObjectName('IconButton')
        reference.resize(100, ui.theme.CONTROL_HEIGHT)
        reference.hide()
        self.assertEqual(view.back_button.objectName(), reference.objectName())
        self.assertEqual(view.export_button.objectName(), 'PrimaryButton')
        self.assertEqual(view.tabs.objectName(), 'PluginTabs')
        self.assertEqual(view.markers.objectName(), 'FollowList')
        try:
            host.show()
            for mode in ('dark', 'light'):
                ui.theme.apply(mode)
                host.setStyleSheet(ui.theme.qss())
                APP.processEvents()
                first = view.sources.topLevelItem(0)
                row = view.sources.visualItemRect(first)
                self.assertGreaterEqual(row.height(), 40)
                self.assertEqual(view.sources.font().pixelSize(), ui.theme.FONT_BODY)
                self.assertEqual(view.back_button.font().pixelSize(), reference.font().pixelSize())
                self.assertIsNotNone(view.sources.itemWidget(first, 0))
                self.assertIsNotNone(view.sources.itemWidget(first, 1))
                self.assertGreaterEqual(view.back_button.height(), ui.theme.CONTROL_HEIGHT)
                QTest.mouseClick(view.sources.viewport(), Qt.LeftButton, pos=QPoint(20, row.center().y()))
                self.assertTrue(first.isSelected())
                pixel = view.sources.viewport().grab().toImage().pixelColor(
                    view.sources.columnWidth(0) - 20, row.center().y())
                self.assertNotEqual(pixel, ui.theme.qcolor(ui.theme.ACCENT), 'Selection must use the soft host color')
                actual = view.back_button.grab().toImage()
                expected = reference.grab().toImage()
                self.assertEqual(actual.pixelColor(actual.width() // 2, 3),
                                 expected.pixelColor(expected.width() // 2, 3))
                self.assertEqual(view.sources.currentItem().data(0, Qt.UserRole), '0')
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    host.grab().save(str(args.screenshots / f'ddcut-live-list-{mode}.png'))
                view.search.setText('没有匹配')
                self.assertEqual(view.library_stack.currentIndex(), 2)
                self.assertIn('没有找到', view.library_empty.text())
                self.assertFalse(view.open_button.isEnabled())
                view.search.clear()
                self.assertEqual(view.library_stack.currentIndex(), 0)
                view.category.setCurrentIndex(1)
                self.assertEqual(view.library_stack.currentIndex(), 2)
                self.assertEqual(view.library_empty.text(), '暂无历史录播')
                self.assertFalse(view.folder_button.isEnabled())
                self.assertNotEqual(view.open_button.palette().color(QPalette.Disabled, QPalette.ButtonText),
                                    view.open_button.palette().color(QPalette.Disabled, QPalette.Button))
                if args.screenshots:
                    host.grab().save(str(args.screenshots / f'ddcut-empty-{mode}.png'))
                view.category.setCurrentIndex(0)
            view.resize(920, 600)
            APP.processEvents()
            self.assertLessEqual(view.minimumSizeHint().width(), 920)
            self.assertLessEqual(view.minimumSizeHint().height(), 600)
        finally:
            view.close()
            host.close()
            host.deleteLater()
            APP.processEvents()

    def test_workspace_layout_close_reopen_and_unload(self):
        host = QMainWindow()
        host.recorder = SimpleNamespace(exports=[])
        host.setStyleSheet(ui.theme.qss())
        data = {'source_id': '1', 'session_id': 'offline',
                'room': {'uname': '本地测试房间', 'room_id': 'offline'},
                'state': 'recording', 'created_at': time.time() - 120}
        service = SimpleNamespace(api_version=1, sources=lambda: [data], options=lambda: {'directory': str(self.root), 'ffmpeg': FFMPEG},
                                  close=lambda: None, open_settings=lambda: None)
        manager = PluginManager(plugins_dir=str(self.root / 'plugins'), enabled=[])
        manager.window = host
        context = PluginContext(manager, 'ddcut_replay')
        context._recording = service
        plugin = module.DDCutReplay()
        with patch.object(module.config, 'data_root', return_value=str(self.root)), patch.object(ui.Preview, 'load'):
            plugin.on_load(context)
            self.assertTrue(plugin.toolbar.actions())
            # Avoid activation in tests; no desktop focus is requested.
            view = ui.Workspace(plugin)
            plugin.workspace = view
            plugin.hub.add_entry(self.entry)
            view.show()
            self.assertEqual(view.tabs.count(), 1)
            self.assertEqual(view.tabs.currentWidget(), view.library_page)
            self.assertEqual(view.category.currentData(), 'live')
            self.assertEqual(view.sources.topLevelItemCount(), 1)
            view.category.setCurrentIndex(1)
            self.assertEqual(view.library.topLevelItemCount(), 1)
            view.search.setText('不存在的录像')
            self.assertTrue(view.library.topLevelItem(0).isHidden())
            view.search.clear()
            self.assertFalse(view.library.topLevelItem(0).isHidden())
            view.category.setCurrentIndex(2)
            self.assertEqual(view.library.topLevelItemCount(), 0)
            view.review(plugin.hub.entries[0])
            view.sources.setCurrentItem(view.sources.topLevelItem(0))
            with patch.object(service, 'control', create=True, side_effect=RuntimeError('录制会话已变化，请刷新后重试')):
                view.control('stop')
            self.assertIn('录制会话已变化', view.status.text())
            with patch.object(ui.Preview, 'load', side_effect=RuntimeError('无法初始化本地播放器')):
                view.review(plugin.hub.entries[0])
            self.assertEqual(view.status.text(), '无法初始化本地播放器')
            view.inpoint.setValue(1)
            view.outpoint.setValue(2.5)
            self.assertTrue(view.export_button.isEnabled())
            view.inpoint.setValue(3)
            self.assertFalse(view.export_button.isEnabled())
            view.set_selection(1, 2.5)
            self.assertEqual((view.timeline.start, view.timeline.end), (1, 2.5))
            view.inpoint.lineEdit().setText('00:00:00.500')
            view.inpoint.interpretText()
            self.assertEqual(view.inpoint.value(), .5)
            view.timeline.set_zoom(2)
            self.assertGreater(view.timeline_scroll.maximum(), 0)
            view.timeline_scroll.setValue(500)
            self.assertEqual(view.timeline.view_start, .5)
            view.timeline.set_zoom(1)
            with patch.object(ui.QInputDialog, 'getText', return_value=('精彩点', True)):
                view.add_marker()
            self.assertEqual(view.current['markers'][0]['title'], '精彩点')
            self.assertEqual(view.timeline.markers[0]['title'], '精彩点')
            # The actual export action opens the shared pane, keeps the review
            # page, and delivers a real decodable result into the clip library.
            target = self.root / 'ui-export.mp4'
            view.mode.setCurrentIndex(1)
            view.set_selection(.5, 2)
            with patch.object(ui.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
                view.export_button.click()
            self.assertFalse(view.task_panel.isHidden())
            self.assertEqual(view.tabs.currentWidget(), view.review_page)
            wait_job(plugin.hub.jobs[-1])
            plugin.poll()
            self.assertTrue(target.is_file())
            self.assertAlmostEqual(plugin.hub.jobs[-1].result['actual_duration'], 1.5, delta=.1)
            view.category.setCurrentIndex(2)
            self.assertEqual(view.library.topLevelItemCount(), 1)
            self.assertEqual(view.library.topLevelItem(0).data(0, Qt.UserRole), str(target))
            view.tabs.setCurrentWidget(view.library_page)
            self.assertFalse(view.task_panel.isHidden())
            view.toggle_tasks(False)
            self.assertTrue(view.task_panel.isHidden())
            view.tabs.setCurrentWidget(view.review_page)
            gate = threading.Event()
            background = plugin.hub.submit('窗口关闭后继续', 'test', lambda job: gate.wait(3))
            for size in [(1180, 760), (920, 600)]:
                view.resize(*size)
                APP.processEvents()
                self.assertLessEqual(view.minimumSizeHint().width(), size[0])
                self.assertLessEqual(view.minimumSizeHint().height(), size[1])
                view.toggle_tasks(True)
                APP.processEvents()
                self.assertLessEqual(view.minimumSizeHint().width(), size[0])
                view.toggle_tasks(False)
                self.assertEqual(view.tools.isHidden(), not view.edit_button.isChecked())
            # Selection handles must update the time inputs using mouse drags.
            view.set_selection(.5, 2)
            timeline = view.timeline
            QTest.mousePress(timeline, Qt.LeftButton, pos=QPoint(round(timeline.x_for(.5)), 50))
            QTest.mouseMove(timeline, QPoint(round(timeline.x_for(1)), 50))
            QTest.mouseRelease(timeline, Qt.LeftButton, pos=QPoint(round(timeline.x_for(1)), 50))
            self.assertAlmostEqual(view.inpoint.value(), 1, delta=.03)
            seeks = []
            timeline.seekRequested.connect(seeks.append)
            QTest.mouseClick(timeline, Qt.LeftButton, pos=QPoint(round(timeline.x_for(3)), 50))
            self.assertAlmostEqual(seeks[-1], 3, delta=.03)
            if args.screenshots:
                args.screenshots.mkdir(parents=True, exist_ok=True)
                view.resize(1180, 760)
                view.setWindowTitle('DDCut Replay · 原生界面（本地测试数据）')
                for dark in (False, True):
                    ui.theme.apply('dark' if dark else 'light')
                    host.setStyleSheet(ui.theme.qss())
                    APP.processEvents()
                    theme = 'dark' if dark else 'light'
                    view.grab().save(str(args.screenshots / f'ddcut-review-{theme}.png'))
                    view.toggle_tasks(True)
                    APP.processEvents()
                    view.grab().save(str(args.screenshots / f'ddcut-tasks-{theme}.png'))
                    view.toggle_tasks(False)
                    view.tabs.setCurrentWidget(view.library_page)
                    APP.processEvents()
                    view.grab().save(str(args.screenshots / f'ddcut-library-{theme}.png'))
                    view.tabs.setCurrentWidget(view.review_page)
            view.close()
            APP.processEvents()
            self.assertIsNone(view.preview.core)
            self.assertFalse(plugin.hub.closed)
            self.assertFalse(background.cancel.is_set())
            gate.set()
            wait_job(background)
            self.assertEqual(background.state, 'done')
            view.show()
            plugin.on_unload()
            self.assertTrue(plugin.hub.closed)
            self.assertIsNone(plugin.toolbar)
            APP.processEvents()
        host.close()


if __name__ == '__main__':
    if not FFMPEG:
        raise SystemExit('FFmpeg is required for real local-media verification')
    unittest.main(argv=[sys.argv[0], *remainder])
