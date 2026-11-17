"""Actual Qt workspace foundation and host recording settings regression."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

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
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QPalette
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

    def test_first_choice_can_be_deferred_and_persisted(self):
        from ddcut_settings_check.recording_settings import CaptureModePrompt
        prompt = CaptureModePrompt(self.owner, self.host)
        prompt.reject()
        self.assertNotIn('capture_mode', self.owner.hub.preferences)
        prompt = CaptureModePrompt(self.owner, self.host)
        prompt.mode.setCurrentIndex(prompt.mode.findData('both'))
        prompt.apply()
        self.assertEqual(self.owner.hub.preferences['capture_mode'], 'both')
        self.assertEqual(prompt.result(), QDialog.DialogCode.Accepted)
        prompt.deleteLater()

    def test_workspace_covers_wallpaper_in_both_themes_and_restores(self):
        container = QWidget()
        colors = container.palette()
        colors.setColor(QPalette.Window, QColor('#ff00ff'))
        container.setPalette(colors)
        container.setAutoFillBackground(True)
        self.host.setCentralWidget(container)
        self.host.resize(1180, 760)
        self.host.show()
        pages = WorkspacePages(container)
        view = self.owner.workspace = module.Workspace(self.owner)
        for mode in ('dark', 'light'):
            theme.apply(mode)
            self.host.setStyleSheet(theme.qss())
            pages.show_page('ddcut_replay', view)
            APP.processEvents()
            picture = view.grab().toImage()
            for x, y in ((3, 3), (3, picture.height()-4), (picture.width()-4, 3)):
                self.assertEqual(picture.pixelColor(x, y), theme.qcolor(theme.BG))
            if args.screenshots:
                args.screenshots.mkdir(parents=True, exist_ok=True)
                self.host.grab().save(str(args.screenshots/f'workspace-{mode}.png'))
            self.assertTrue(pages.close_active())
            self.assertIsNone(pages.active)

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
            self.assertEqual(view.capture_switch.currentData(), 'clip')
            view.capture_switch.setCurrentIndex(view.capture_switch.findData('original'))
            self.assertFalse(self.backend)
            self.assertEqual(panel.mode.currentData(), 'original')
            panel.mode.setCurrentIndex(panel.mode.findData('both'))
            self.assertEqual(view.capture_switch.currentData(), 'both')
            self.assertEqual(self.owner.hub.preferences['capture_mode'], 'both')
            panel.segment.setValue(3)
            panel.danmaku.setChecked(False)
            self.assertEqual(self.owner.clip_options()['segment_seconds'], 3)
            self.assertFalse(self.owner.clip_options()['danmaku'])
            view.capture_switch.setCurrentIndex(view.capture_switch.findData('original'))
            self.busy = True
            panel.mode.setCurrentIndex(panel.mode.findData('clip'))
            self.assertEqual(panel.mode.currentData(), 'original')
            self.assertIn('停止当前录制', panel.status.text())
            panel.mode.setCurrentIndex(panel.mode.findData('both'))
            self.assertEqual(panel.mode.currentData(), 'original')
            self.assertEqual(view.capture_switch.currentData(), 'original')
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
