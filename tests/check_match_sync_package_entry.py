"""Install the real ZIP and verify entry routing and staged upgrades offline."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--scenario', choices=('fresh', 'upgrade'))
    args = parser.parse_args()
    if args.scenario is None:
        # Each installation starts its own Qt process, as the application does.
        for scenario in ('fresh', 'upgrade'):
            command = [sys.executable, str(args.host / 'dev/checks/bootstrap.py'),
                       str(Path(__file__).resolve()), *sys.argv[1:], '--scenario', scenario]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', timeout=60)
            print(result.stdout, end='')
            assert result.returncode == 0, scenario
            assert 'Traceback' not in result.stdout, result.stdout
        return
    sys.path.insert(0, str(args.host.resolve()))
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget
    from ddm import config, theme
    from ddm.online import version_key
    from ddm.plugin_updates import apply_pending, pending_versions, stage
    from ddm.plugins import PluginManager
    from ddm.widgets import Sidebar

    app = QApplication.instance() or QApplication([])

    def settle():
        for _ in range(5):
            app.processEvents()

    def wait_for(predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            settle()
            time.sleep(.01)
        assert predicate(), 'Plugin mode switch did not finish'

    with zipfile.ZipFile(args.archive) as package:
        assert package.testzip() is None
        manifest = json.loads(package.read('match_sync/plugin.json'))
        version = manifest['version']
        assert version_key(version) > version_key('1.6')
        assert all(name.startswith('match_sync/') for name in package.namelist())

    upgrade = args.scenario == 'upgrade'
    with tempfile.TemporaryDirectory(prefix='ddm-match-entry-') as directory, \
            patch.object(config, 'REPO', directory), patch.object(config, 'DATA_ROOT', directory):
        host = QWidget()
        host.setAttribute(Qt.WA_DontShowOnScreen)
        host.setStyleSheet(theme.qss())
        host.settings, host.rooms, host.players = dict(config.DEFAULT_SETTINGS), [], {}
        box = QHBoxLayout(host)
        host.sidebar = Sidebar([], auto_compact=False)
        box.addWidget(host.sidebar)
        host._content = QWidget(host)
        box.addWidget(host._content, 1)
        content = QVBoxLayout(host._content)
        host.wall = QLabel('Offline wall', host._content)
        host.wall.tiles = []
        content.addWidget(host.wall)
        manager = PluginManager(host, str(Path(directory) / 'plugins'))
        try:
            if upgrade:
                manager.install_zip(str(args.previous))
                sentinel = Path(manager.plugins_dir) / 'match_sync/user-data.txt'
                sentinel.write_text('preserve user data', encoding='utf-8')
                manager.plugin_settings['match_sync'] = {'offline_sentinel': True}
                stage(manager, str(args.archive), 'match_sync', version)
                assert pending_versions(manager) == {'match_sync': version}
                assert manager.catalog()[0]['version'] == '1.6.1'
                apply_pending(manager)
                assert sentinel.read_text(encoding='utf-8') == 'preserve user data'
                assert manager.plugin_settings['match_sync']['offline_sentinel']
                assert not pending_versions(manager)
            else:
                manager.install_zip(str(args.archive))
            manager.load()
            assert not manager.skipped and len(manager.plugins) == 1
            plugin = manager.plugins[0]
            assert plugin.version == version
            host.resize(1180, 800)
            host.show()
            settle()
            panel = host.sidebar.workspace_controls
            identity = ('match_sync', 'entry')
            assert identity in panel.entries and plugin.entry.isVisible()
            assert plugin.entry.parentWidget() is panel.row
            panel.toggle_popup()
            panel.search.setText('比赛二路')
            settle()
            assert not panel.empty.isVisible()
            panel._rows[identity][1].click()
            wait_for(lambda: plugin.viewer is not None and plugin.viewer.isVisible())
            assert not plugin.viewer.isWindow() and plugin.viewer.parentWidget() is host._content
            assert manager._workspace_pages.active[0] == 'match_sync'
            assert host.wall.isHidden() and host.sidebar.layout_button.isVisible()
            plugin.viewer.close()
            settle()
            assert host.wall.isVisible() and not plugin.button.isChecked()
            assert manager._workspace_pages.active is None
            for side in ('top', 'left'):
                host.sidebar.set_side(side)
                settle()
                assert identity in panel.entries
                panel.toggle_popup()
                settle()
                assert panel._rows[identity][1].isVisible()
                panel.popup.hide()
            manager.unload()
            assert not panel.entries and not panel._rows
            print('PASS:', 'upgrade 1.6.1 -> ' + version if upgrade else 'fresh install ' + version,
                  'search, launch real empty viewer, return, orientation and unload')
        finally:
            if not manager._unloaded:
                manager.unload()
            host.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            settle()


if __name__ == '__main__':
    main()
