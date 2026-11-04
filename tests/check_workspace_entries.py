"""Actual sidebar entries and plugin click routing; isolated, offscreen and no media."""
import argparse
import importlib
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--screenshots', type=Path)
args = parser.parse_args()
sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
os.environ['DDM_NO_SAVE'] = '1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget
from ddm import config, theme
from ddm.plugins import PluginContext, PluginManager
from ddm.widgets import Sidebar

app = QApplication.instance() or QApplication([])
for font in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/segoeui.ttf'):
    if Path(font).is_file():
        QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Microsoft YaHei', 10))


def settle():
    for _ in range(6):
        app.processEvents()


class EmptyViewer(QDialog):
    def __init__(self, context, _sources, _room):
        super().__init__(context.window)
        self.rows = {}
        self.running = False

    def embed(self, parent):
        self.setParent(parent, Qt.Widget)


record_module = importlib.import_module('plugins.DDCut_Replay.plugin')
match_module = importlib.import_module('plugins.match_sync.plugin')
with tempfile.TemporaryDirectory(prefix='ddm-workspace-ui-') as directory, \
        patch.object(config, 'DATA_ROOT', directory), patch.object(config, 'REPO', directory), \
        patch.object(match_module, 'Viewer', EmptyViewer):
    host = QMainWindow()
    host.setAttribute(Qt.WA_DontShowOnScreen)
    central = QWidget(host)
    host.setCentralWidget(central)
    box = QHBoxLayout(central)
    box.setContentsMargins(0, 0, 0, 0)
    sidebar = host.sidebar = Sidebar([
        {'room_id': 'offline-demo-a', 'uname': '赛事直播 · 示例', 'title': '本地布局示例', 'live': False},
        {'room_id': 'offline-demo-b', 'uname': '常看的主播 · 示例', 'title': '本地布局示例', 'live': False},
    ], auto_compact=False)
    box.addWidget(sidebar)
    content = host._content = QWidget(central)
    QVBoxLayout(content).addWidget(QLabel('画面墙 · 本地布局示例', alignment=Qt.AlignCenter))
    box.addWidget(content, 1)
    host.settings = {'recording_dir': directory}
    host.wall = SimpleNamespace(tiles=[])
    host.recorder = SimpleNamespace(exports=[])
    host.capture_control = Mock()
    manager = PluginManager(window=host, plugins_dir=str(Path(directory) / 'plugins'), enabled=[])
    record = record_module.DDCutReplay()
    match = match_module.MatchSyncPlugin()
    record.on_load(PluginContext(manager, 'ddcut_replay'))
    match.on_load(PluginContext(manager, 'match_sync'))
    manager.plugins.extend([record, match])
    host.resize(1180, 800)
    host.show()
    settle()
    panel = sidebar.workspace_controls
    assert record.toolbar is None, 'A second global toolbar must not remain on the new host'
    assert record.entry.parentWidget() is panel.row and match.entry.parentWidget() is panel.row
    assert [panel.buttons.itemAt(i).widget() for i in range(panel.buttons.count())] == [record.entry, match.entry]
    record.entry.click()
    settle()
    assert record.workspace is not None and record.workspace.isVisible()
    record.workspace.close()
    assert not record.hub.closed
    match.button.trigger()
    deadline = time.monotonic() + 2
    while match.viewer is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert match.viewer is not None and match.entry._mode
    assert sidebar.layout_button.isVisible(), 'Workspace mode must not take over the original footer'
    match.viewer.close()
    settle()
    assert not match.button.isChecked()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
        for mode in ('dark', 'light'):
            theme.apply(mode)
            host.setStyleSheet(theme.qss())
            settle()
            sidebar.grab().save(str(args.screenshots / f'workspace-sidebar-{mode}.png'))
            host.grab().save(str(args.screenshots / f'workspace-window-{mode}.png'))
        theme.apply('dark')
        host.setStyleSheet(theme.qss())
        sidebar.footer.set_minimized(True, animate=False)
        settle()
        assert sidebar.footer.body.isHidden() and sidebar.footer.restore_button.isVisible()
        host.grab().save(str(args.screenshots / 'workspace-window-minimized.png'))
        sidebar.footer.restore_button.click()
        sidebar.footer.animation.setCurrentTime(sidebar.footer.animation.duration())
        settle()
        assert not sidebar.footer.minimized
        sidebar.set_collapsed(True, animate=False)
        settle()
        sidebar.grab().save(str(args.screenshots / 'workspace-sidebar-collapsed.png'))
        sidebar.set_collapsed(False, animate=False)
        sidebar.set_side('top')
        settle()
        sidebar.grab().save(str(args.screenshots / 'workspace-sidebar-portrait.png'))
        sidebar.set_side('left')
    manager.unload()
    settle()
    assert not panel.entries and panel.isHidden()
    assert record.hub.closed
    host.close()
    host.deleteLater()
    settle()
print('PASS: both real plugin entries, original footer, recording window, match toggle, theme layout and unload')
