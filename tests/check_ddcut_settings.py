"""Actual Qt workspace foundation and host recording settings regression."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--screenshots', type=Path)
args = parser.parse_args()
if args.screenshots:
    args.screenshots.mkdir(parents=True, exist_ok=True)
ROOT = Path(__file__).resolve().parents[1] if Path(__file__).parent.name == 'tests' else Path('F:/CodexAppManager/Code/DD_Monitor_Plugins')
sys.path.insert(0, str(args.host.resolve()))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['DDM_NO_SAVE'] = '1'
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget
from ddm import theme
from ddm.config import DEFAULT_SETTINGS
from ddm.dialogs import SettingsDialog
from ddm.workspace_pages import WorkspacePages

spec = importlib.util.spec_from_file_location('ddcut_settings_check', ROOT/'plugins/DDCut_Replay/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
APP = QApplication.instance() or QApplication([])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ddcut-settings-')
        self.owner = module.DDCutReplay()
        self.host = QMainWindow()
        self.host.setAttribute(Qt.WA_DontShowOnScreen)
        self.owner.context = SimpleNamespace(name='ddcut_replay', window=self.host)
        self.owner.hub = module.TaskHub(Path(self.temp.name))
        self.busy = False
        self.backend = False
        def set_backend(value):
            if self.busy:
                raise RuntimeError('请先停止当前录制和缓存，再切换录制方式')
            self.backend = value
        self.owner.service = SimpleNamespace(set_backend_enabled=set_backend,
            sources=lambda: [], options=lambda: {}, select_backend=lambda factory=None: set_backend(factory is not None))
        self.owner.preview_server = SimpleNamespace(source_url=lambda path: path)

    def tearDown(self):
        self.owner.hub.close()
        self.host.close()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def test_load_without_startup_dialog_defaults_to_native_and_preserves_preferences(self):
        from PySide6.QtTest import QTest
        from ddm import images
        from ddm.plugins import PluginContext, PluginManager
        from ddm.recording import RecordingManager, RecordingSession
        self.host.settings = dict(DEFAULT_SETTINGS)
        self.host.wall = SimpleNamespace(tiles=[])
        self.host.capture_control = SimpleNamespace()
        self.host.recorder = RecordingManager(self.host.settings)
        for saved in (None, 'clip', 'both'):
            folder = Path(self.temp.name) / (saved or 'unset')
            if saved:
                hub = module.TaskHub(folder / 'ddcut_replay')
                hub.preferences['capture_mode'] = saved
                hub.save()
                hub.close()
            plugin = module.DDCutReplay()
            manager = PluginManager(window=self.host, plugins_dir=str(folder/'plugins'), enabled=[])
            with patch.dict(os.environ), patch.object(module.config, 'data_root', return_value=str(folder)), \
                    patch.object(images, 'REPO', str(folder)), patch.object(QDialog, 'open') as opened:
                os.environ.pop('DDM_NO_SAVE', None)  # Exercise real startup, without the old test-only suppression.
                try:
                    plugin.on_load(PluginContext(manager, 'ddcut_replay'))
                    QTest.qWait(650)
                    self.assertFalse(opened.called)
                    self.assertEqual(plugin.capture_mode, saved or 'original')
                    if saved is None:
                        self.assertIs(self.host.recorder.session_factory, RecordingSession)
                        self.assertNotIn('capture_mode', plugin.hub.preferences)
                finally:
                    plugin.on_unload()
        self.host.recorder.shutdown()

    def test_workspace_matches_host_wall_veil_at_real_sizes_and_restores(self):
        from ddm.skin_background import BackgroundLayer
        container = QWidget()
        container.setObjectName('Root')
        self.host.setCentralWidget(container)
        image = QImage(160, 90, QImage.Format_RGB32)
        painter = QPainter(image)
        painter.fillRect(0, 0, 80, 90, QColor('#f04e2a'))
        painter.fillRect(80, 0, 80, 90, QColor('#2785ee'))
        painter.end()
        path = Path(self.temp.name) / 'background.png'
        self.assertTrue(image.save(str(path)))
        layer = BackgroundLayer(container, self.host)
        layer.load(str(path))
        layer.mask = 35
        self.host.show()
        pages = WorkspacePages(container)
        view = self.owner.workspace = module.Workspace(self.owner)
        reference = QWidget(container)
        reference.setObjectName('Tile')
        reference.setAttribute(Qt.WA_StyledBackground)
        try:
            theme.set_skin_active(True)
            for mode in ('dark', 'light'):
                theme.apply(mode)
                self.host.setStyleSheet(theme.qss())
                for width, height in ((920, 600), (3840, 2160)):
                    self.host.resize(width, height)
                    APP.processEvents()
                    reference.setGeometry(container.rect())
                    for mask in (0, 35, 80, 100):
                        layer.mask = mask
                        layer.update()
                        reference.show()
                        reference.raise_()
                        APP.processEvents()
                        origin = container.mapTo(self.host, QPoint())
                        # Compare fully composited window pixels. Child grabs retain alpha.
                        expected = self.host.grab().toImage().copy(origin.x(), origin.y(), container.width(), container.height())
                        reference.hide()
                        pages.show_page('ddcut_replay', view)
                        APP.processEvents()
                        actual = self.host.grab().toImage().copy(origin.x(), origin.y(), container.width(), container.height())
                        behind = layer.grab().toImage()
                        # Sample the flat side margins, outside Tile's rounded corners and text.
                        points = ((3, actual.height()//2), (actual.width()-4, actual.height()//2))
                        for x, y in points:
                            pixel = actual.pixelColor(x, y)
                            wall = expected.pixelColor(x, y)
                            self.assertTrue(all(abs(a-b) <= 1 for a, b in zip(pixel.getRgb(), wall.getRgb())),
                                (mode, width, mask, pixel.name(), wall.name()))
                            if mask < 100:
                                self.assertNotEqual(pixel, behind.pixelColor(x, y), 'Page must retain the host veil')
                                self.assertNotEqual(pixel, theme.qcolor(theme.BG), 'Wallpaper must remain visible')
                            else:
                                self.assertEqual(pixel, theme.qcolor(theme.BG))
                        if mask < 100:
                            self.assertNotEqual(actual.pixelColor(*points[0]), actual.pixelColor(*points[1]))
                        if args.screenshots and width == 3840 and mask == 35:
                            self.assertTrue(self.host.grab().save(str(args.screenshots/f'workspace-wallpaper-{mode}.png')))
                        self.assertTrue(pages.close_active())
                        APP.processEvents()
                        self.assertIsNone(pages.active)
                        restored = self.host.grab().toImage().copy(origin.x(), origin.y(), container.width(), container.height())
                        self.assertEqual(restored.pixelColor(*points[0]), behind.pixelColor(*points[0]))
        finally:
            reference.hide()
            view.close()
            layer.load('')
            theme.set_skin_active(False)

    def test_recording_page_saves_syncs_rejects_busy_and_unloads(self):
        view = self.owner.workspace = module.Workspace(self.owner)
        dialog = SettingsDialog(dict(DEFAULT_SETTINGS), {}, preview=True,
            plugin_manager=SimpleNamespace(plugins=[self.owner]))
        try:
            self.assertFalse(self.owner.settings_panels)
            page = dialog.recording_page
            panel, = tuple(self.owner.settings_panels)
            self.assertIs(panel.parentWidget().parentWidget(), page.sections)
            self.assertEqual(page.sections.entries[-1][0], 'plugin_ddcut_replay')
            self.assertEqual(page.recording_mode_layout.indexOf(panel.mode_row), 0)
            panel.mode.setCurrentIndex(panel.mode.findData('clip'))
            self.assertTrue(self.backend)
            self.assertEqual(self.owner.hub.preferences['capture_mode'], 'clip')
            self.assertFalse(hasattr(view, 'capture_switch'))
            self.owner.set_capture_mode('original')
            self.assertFalse(self.backend)
            self.assertEqual(panel.mode.currentData(), 'original')
            panel.mode.setCurrentIndex(panel.mode.findData('both'))
            self.assertEqual(self.owner.capture_mode, 'both')
            self.assertEqual(self.owner.hub.preferences['capture_mode'], 'both')
            panel.segment.setValue(3)
            panel.danmaku.setChecked(False)
            self.assertEqual(self.owner.clip_options()['segment_seconds'], 3)
            self.assertFalse(self.owner.clip_options()['danmaku'])
            self.owner.set_capture_mode('original')
            self.busy = True
            panel.mode.setCurrentIndex(panel.mode.findData('clip'))
            self.assertEqual(panel.mode.currentData(), 'original')
            self.assertIn('停止当前录制', panel.status.text())
            panel.mode.setCurrentIndex(panel.mode.findData('both'))
            self.assertEqual(panel.mode.currentData(), 'original')
            self.assertEqual(self.owner.capture_mode, 'original')
            page.reset()
            self.assertEqual(len(self.owner.settings_panels), 1)
            self.assertEqual(panel.mode.currentData(), 'original')
            APP.setStyleSheet(theme.qss())
            dialog.nav.setCurrentRow(3)
            dialog.setAttribute(Qt.WA_DontShowOnScreen)
            dialog.show()
            APP.processEvents()
            page.scroll.ensureWidgetVisible(panel)
            APP.processEvents()
            if args.screenshots:
                dialog.grab().save(str(args.screenshots/'recording-settings.png'))
            self.owner.service.close = lambda: None
            self.owner.preview_server.close = lambda: None
            self.owner.on_unload()
            self.assertFalse(panel.isEnabled())
            self.assertIsNone(panel.owner)
        finally:
            dialog.close()
            dialog.deleteLater()
            view.close()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
