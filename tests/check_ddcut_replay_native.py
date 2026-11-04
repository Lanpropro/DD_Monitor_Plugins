"""Screen-off Windows host/package/libmpv integration using silent local media."""
import argparse
from contextlib import ExitStack
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
parser.add_argument('--screenshots', type=Path)
args = parser.parse_args()
sys.path[:0] = [str(args.host.resolve()), str(args.host.resolve() / 'dev')]
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['DDM_NO_SAVE'] = '1'
os.environ['PATH'] = str(args.host.resolve()) + os.pathsep + os.environ['PATH']

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from ddm.app import MainWindow
from ddm.config import DEFAULT_SETTINGS
from ddm.plugins import PluginContext, PluginManager
from selfcheck_recording import isolated_window_tasks
from unittest.mock import patch
from ddm import config, images
from ddm.recording import input_args

app = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Native integration timed out')


with tempfile.TemporaryDirectory(prefix='ddcut-native-') as directory, isolated_window_tasks(), ExitStack() as cleanup:
    root = Path(directory)
    cleanup.enter_context(patch.object(images, 'REPO', str(root)))
    source = root / 'local.mp4'
    subprocess.run([shutil.which('ffmpeg'), '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30', '-t', '18',
        '-c:v', 'libx264', '-threads', '1', '-g', '30', str(source)], check=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    window = MainWindow([], [], layout_id='1x1', state={'settings': {
        **DEFAULT_SETTINGS, 'close_behavior': 'exit', 'recording_dir': directory}})
    cleanup.callback(window.close)
    window.setAttribute(Qt.WA_ShowWithoutActivating)
    window.move(-30000, -30000)
    window.show()
    manager = PluginManager(window=window, plugins_dir=str(root / 'plugins'), enabled=['ddcut_replay'])
    assert manager.install_zip(str(args.package.resolve())) == 'ddcut_replay'
    plugin = manager._load_module('ddcut_replay', str(root / 'plugins/ddcut_replay/plugin.py'))
    context = PluginContext(manager, 'ddcut_replay')
    with patch.object(config, 'data_root', return_value=str(root)):
        plugin.on_load(context)
        plugin.set_capture_mode('clip')
        manager.plugins.append(plugin)
        ui = sys.modules[type(plugin).__module__ + '.workspace']
        Workspace = ui.Workspace
        view = Workspace(plugin)
        plugin.workspace = view
        view.setAttribute(Qt.WA_ShowWithoutActivating)
        view.move(-30000, -30000)
        assert context.show_workspace(view)
        assert not view.isWindow()
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
        view.show_library()
        view.category.setCurrentIndex(1)
        app.processEvents()
        old_core = view.preview.core
        card = view.library.visualItemRect(view.library.item(0))
        QTest.mouseClick(view.library.viewport(), Qt.LeftButton, pos=card.center())
        wait_for(lambda: (plugin.poll() or True) and view.preview.core is not old_core)
        wait_for(lambda: view.preview.position() > .1)
        card_core = view.preview.core
        view.refresh_library()
        app.processEvents()
        assert view.preview.core is card_core and view.review_panel.isVisible()
        view.inpoint.setValue(.5)
        view.outpoint.setValue(2)
        assert view.export_button.isEnabled()
        assert view.tabs.count() == 1 and view.review_page is view.library_page
        view.edit_button.click()
        app.processEvents()
        assert view.tools.isVisible()
        view.edit_button.click()
        assert view.tools.isHidden()
        view.play_selection()
        wait_for(lambda: not view.selection_playing and view.preview.core.pause)
        assert abs(view.preview.position() - 2) < .12
        original_core = view.preview.core
        view.toggle_tasks(True)
        app.processEvents()
        assert view.task_panel.isVisible() and view.preview.core is original_core
        view.tabs.setCurrentWidget(view.library_page)
        app.processEvents()
        assert view.task_panel.isVisible()
        view.toggle_tasks(False)
        view.tabs.setCurrentWidget(view.review_page)
        app.processEvents()
        assert view.task_panel.isHidden() and view.review_panel.isVisible()
        assert view.tabs.width() >= view.content.width() - 8, (view.tabs.geometry(), view.content.geometry())
        if args.screenshots:
            from ddm import theme
            args.screenshots.mkdir(parents=True, exist_ok=True)
            window.resize(1440, 900)
            for mode in ('dark', 'light'):
                theme.apply(mode)
                window.setStyleSheet(theme.qss())
                app.processEvents()
                window.grab().save(str(args.screenshots / f'ddcut-software-{mode}.png'))
                view.toggle_tasks(True)
                app.processEvents()
                window.grab().save(str(args.screenshots / f'ddcut-software-tasks-{mode}.png'))
                view.toggle_tasks(False)
                view.tabs.setCurrentWidget(view.library_page)
                app.processEvents()
                window.grab().save(str(args.screenshots / f'ddcut-software-library-{mode}.png'))
                view.tabs.setCurrentWidget(view.review_page)
        view.close()
        assert view.preview.core is None and not plugin.hub.closed
        tile = window.wall.tiles[0]
        for cached in (Path(images.room_cover_path('offline')), Path(images.room_avatar_path('offline'))):
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(picture, cached)
        tile.set_room({'uname': '本地录制', 'title': '本地媒体回看验证', 'platform': 'bilibili', 'room_id': 'offline', 'live': False})
        tile.room['live'] = True
        tile.stream_url, tile.stream_headers, tile.stream_codec = str(source), {}, 'avc'
        window.settings.update(recording_lock_quality=False, recording_format='ts', recording_replay_enabled=False)
        with patch('ddm.recording.input_args', side_effect=lambda url, headers: ['-re', *input_args(url, headers)]):
            tile.recording_button.click()
            session = window.recorder.sessions[tile]
            plugin.open_for(tile)
            wait_for(lambda: (plugin.poll() or True) and view.live_source is not None and view.current and view.current['path'] != str(source))
            wait_for(lambda: view.preview.position() > .1)
            first_duration = view.current['duration']
            wait_for(lambda: (plugin.poll() or True) and view.current['duration'] > first_duration + 1)
            assert session.process.poll() is None and view.preview.core.mute
            manager.emit('danmaku.message', room_id='offline', message={'kind': 'danmaku', 'text': 'local highlight check'})
            session.update_review()
            chat = Path(session.review_info()['danmaku_path'])
            assert 'local highlight check' in chat.read_text(encoding='utf-8')
            assert all('wall_start' in part for part in session.review_info()['segments'])
            view.show_highlights()
            assert view.tools.currentIndex() == 2 and view.tools.isVisible()
            view.edit_button.click()
            original_core = view.preview.core
            view.tabs.setCurrentWidget(view.library_page)
            view.category.setCurrentIndex(0)
            app.processEvents()
            row = view.sources.visualItemRect(view.sources.item(0))
            assert row.height() >= 40
            assert view.sources.font().pixelSize() == ui.theme.FONT_BODY
            if args.screenshots:
                from ddm import theme
                for mode in ('dark', 'light'):
                    theme.apply(mode)
                    window.setStyleSheet(theme.qss())
                    app.processEvents()
                    window.grab().save(str(args.screenshots / f'ddcut-software-recording-list-{mode}.png'))
            view.tabs.setCurrentWidget(view.review_page)
            assert view.preview.core is original_core
            view.preview.core.pause = True
            view.seek_live_edge()
            wait_for(lambda: view.resume_position is None)
            wait_for(lambda: view.preview.position() > first_duration - .5)
            view.preview.core.command('screenshot-to-file', str(picture), 'video')
            image = QImage(str(picture))
            assert image.width() == 320 and image.height() == 180
            assert image.pixelColor(20, 20) != image.pixelColor(280, 20)
            view.close()
            assert not session.stopping and session.process.poll() is None
            tile.recording_button.click()
            wait_for(lambda: tile not in window.recorder.sessions)
        assert context.show_workspace(view)
        manager.unload()
        assert plugin.hub.closed and not window.recorder._part_leases
    window.close()
    app.processEvents()
print('PASS: installed ZIP, original recording button, growing timeline, native libmpv picture/seek/pause/mute, close/reopen/unload; no foreground activation')
