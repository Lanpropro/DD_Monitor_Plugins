"""Screen-off Windows host/package/libmpv integration using silent local media."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--package', type=Path, required=True)
args = parser.parse_args()
sys.path[:0] = [str(args.host.resolve()), str(args.host.resolve() / 'dev')]
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['DDM_NO_SAVE'] = '1'
os.environ['PATH'] = str(args.host.resolve()) + os.pathsep + os.environ['PATH']

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from ddm.app import MainWindow
from ddm.config import DEFAULT_SETTINGS
from ddm.plugins import PluginContext, PluginManager
from selfcheck_recording import isolated_window_tasks
from unittest.mock import patch
from ddm import config

app = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Native integration timed out')


with tempfile.TemporaryDirectory(prefix='ddcut-native-') as directory, isolated_window_tasks():
    root = Path(directory)
    source = root / 'local.mp4'
    subprocess.run([shutil.which('ffmpeg'), '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30', '-t', '5',
        '-c:v', 'libx264', '-threads', '1', '-g', '30', str(source)], check=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    window = MainWindow([], [], layout_id='1x1', state={'settings': {
        **DEFAULT_SETTINGS, 'close_behavior': 'exit', 'recording_dir': directory}})
    window.setAttribute(Qt.WA_ShowWithoutActivating)
    window.move(-30000, -30000)
    window.show()
    manager = PluginManager(window=window, plugins_dir=str(root / 'plugins'), enabled=['ddcut_replay'])
    assert manager.install_zip(str(args.package.resolve())) == 'ddcut_replay'
    plugin = manager._load_module('ddcut_replay', str(root / 'plugins/ddcut_replay/plugin.py'))
    context = PluginContext(manager, 'ddcut_replay')
    with patch.object(config, 'data_root', return_value=str(root)):
        plugin.on_load(context)
        manager.plugins.append(plugin)
        Workspace = sys.modules[type(plugin).__module__ + '.workspace'].Workspace
        view = Workspace(plugin)
        plugin.workspace = view
        view.setAttribute(Qt.WA_ShowWithoutActivating)
        view.move(-30000, -30000)
        view.show()
        view.load_entry({'path': str(source), 'title': '本地静音验证', 'created_at': time.time()})
        wait_for(lambda: (plugin.poll() or True) and view.preview.core is not None)
        wait_for(lambda: view.preview.position() > .1)
        assert view.preview.core.mute
        view.preview.seek(2)
        wait_for(lambda: view.preview.position() >= 1.9)
        view.toggle_play()
        assert view.preview.core.pause
        picture = root / 'native-frame.png'
        view.preview.core.command('screenshot-to-file', str(picture), 'video')
        image = QImage(str(picture))
        assert image.width() == 320 and image.height() == 180
        assert image.pixelColor(20, 20) != image.pixelColor(280, 20)
        view.inpoint.setValue(.5)
        view.outpoint.setValue(2)
        assert view.export_button.isEnabled()
        view.close()
        assert view.preview.core is None and not plugin.hub.closed
        view.show()
        manager.unload()
        assert plugin.hub.closed and not window.recorder._part_leases
    window.close()
    app.processEvents()
print('PASS: installed ZIP, real host, native libmpv picture/seek/pause/mute, close/reopen/unload; no foreground activation')
