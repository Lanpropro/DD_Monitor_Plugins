"""Real local capture: exclusive modes, archive identity, artwork and original files."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['DDM_NO_SAVE'] = '1'
from PySide6.QtWidgets import QApplication, QMainWindow
from ddm import config, images
from ddm.plugins import PluginContext, PluginManager
from ddm.recording import RecordingManager, RecordingSession, input_args

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ddcut_modes', root / 'plugins/DDCut_Replay/plugin.py')
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)
media = sys.modules[spec.name + '.media']
ui = sys.modules[spec.name + '.workspace']
app = QApplication.instance() or QApplication([])
ffmpeg = shutil.which('ffmpeg')


def until(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('Local recording did not complete')


with tempfile.TemporaryDirectory(prefix='ddcut-modes-') as directory:
    data = Path(directory)
    source = data / 'source.mp4'
    subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
        '-i', 'testsrc2=size=320x180:rate=30', '-t', '20', '-c:v', 'libx264',
        '-threads', '1', '-g', '30', str(source)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    cover = data / 'cover.png'
    subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(source),
        '-frames:v', '1', str(cover)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

    class Tile:
        room = {'platform': 'bilibili', 'room_id': '123456', 'uname': '本地主播', 'title': '本地直播标题', 'live': True}
        stream_url, stream_headers, stream_codec = str(source), {}, 'avc'

        def isVisible(self):
            return True

    tile = Tile()
    host = QMainWindow()
    host.settings = {'recording_dir': str(data / 'recordings'), 'recording_format': 'mp4',
                     'recording_codec': 'copy', 'recording_replay_enabled': True, 'recording_min_free_mb': 256}
    host.wall = SimpleNamespace(tiles=[tile])
    host.capture_control = SimpleNamespace()
    host.recorder = RecordingManager(host.settings)
    manager = PluginManager(plugins_dir=str(data / 'plugins'), enabled=[])
    manager.window = host
    notices = []
    host.recorder.noticeWithRoom.connect(lambda message, room: notices.append(message))
    with patch.object(config, 'data_root', return_value=str(data)), patch.object(images, 'REPO', str(data)), \
         patch('ddm.recording.input_args', side_effect=lambda url, headers: ['-re', *input_args(url, headers)]):
        for path in (Path(images.room_cover_path('123456')), Path(images.room_avatar_path('123456'))):
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cover, path)
        plugin = plugin_module.DDCutReplay()
        plugin.on_load(PluginContext(manager, 'ddcut_replay'))
        try:
            assert plugin.capture_mode == 'original' and host.recorder.supports_replay
            plugin.set_capture_mode('clip')
            assert not host.recorder.supports_replay
            assert not host.recorder.start(tile, recording=False)
            assert host.recorder.start(tile, recording=True)
            session = host.recorder.sessions[tile]
            until(lambda: session.review_info() is not None)
            try:
                plugin.set_capture_mode('original')
            except RuntimeError:
                pass
            else:
                raise AssertionError('Mode changed during recording')
            assert not host.recorder.save_replay(tile)
            info = session.review_info()
            assert all(value in Path(info['path']).name for value in ('哔哩哔哩', '本地主播', '本地直播标题', '123456'))
            assert Path(info['cover_path']).is_file() and Path(info['avatar_path']).is_file()
            host.recorder.stop(tile)
            until(lambda: tile not in host.recorder.sessions)
            assert not list((data / 'recordings/replays').glob('*'))
            assert not list((data / 'recordings').glob('*.mp4'))
            assert any(message.startswith('已保存切片素材：') for message in notices)
            assert not any('已保存即时回放' in message for message in notices)
            saved = json.loads(Path(info['path']).with_name('recording.json').read_text(encoding='utf-8'))
            assert saved['complete'] and saved['ended_at'] >= saved['created_at']
            assert '#EXT-X-ENDLIST' in Path(saved['path']).read_text(encoding='utf-8')

            tile.room.pop('platform')  # Bilibili rooms also arrive as numeric IDs without a platform field.
            plugin.set_capture_mode('original')
            assert host.recorder.session_factory is RecordingSession and host.recorder.supports_replay
            assert host.recorder.start(tile, recording=True)
            time.sleep(2.5)
            host.recorder.stop(tile)
            until(lambda: tile not in host.recorder.sessions and not host.recorder.exports)
            originals = list((data / 'recordings').glob('*.mp4'))
            replays = list((data / 'recordings/replays').glob('*.mp4'))
            assert len(originals) == len(replays) == 1
            for output in originals + replays:
                assert all(value in output.name for value in ('本地主播', 'bilibili', '本地直播标题', '123456'))
                assert media.probe(media.Job('probe', 'test'), ffmpeg, output, data / 'work')['duration'] > 1
                metadata = json.loads(output.with_suffix(output.suffix + '.recording.json').read_text(encoding='utf-8'))
                assert metadata['room']['title'] == '本地直播标题' and Path(metadata['cover_path']).is_file()
            entries = media.scan_directory(media.Job('scan', 'scan'), data / 'recordings')
            assert len(entries) == 3 and all(entry['room']['uname'] == '本地主播' for entry in entries)
            view = ui.Workspace(plugin)
            plugin.workspace = view
            plugin.hub.entries = entries
            view.category.setCurrentIndex(1)
            assert view.library.topLevelItemCount() == 3
            card = view.library.itemWidget(view.library.topLevelItem(0), 0)
            assert card.image.pixmap() is not None and not card.image.pixmap().isNull()
            with patch.object(ui.Preview, 'load'):
                view.review({**entries[0], **media.probe(media.Job('probe', 'test'), ffmpeg, entries[0]['path'], data / 'work')})
                assert view.tabs.count() == 1 and not view.review_panel.isHidden()
                assert view.review_page is view.library_page
            plugin.on_unload()
            plugin = plugin_module.DDCutReplay()
            plugin.on_load(PluginContext(manager, 'ddcut_replay'))
            assert plugin.capture_mode == 'original' and host.recorder.session_factory is RecordingSession
        finally:
            plugin.on_unload()
            host.recorder.shutdown()
            host.close()
print('PASS: exclusive recording modes, original video + replay, clip-only archive, readable names/notifications, metadata/artwork, inline review and mode persistence')
