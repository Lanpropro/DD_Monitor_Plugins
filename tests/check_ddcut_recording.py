"""Real local growing recordings: host controls, indexes, seeking and export.

No real account, stream URL or installed plugin is loaded. A local file is read
at real-time speed only to verify functionality; this is not a performance test.
"""
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
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--format', choices=('ts', 'mp4', 'mkv'))
args, remainder = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMainWindow
from ddm.plugins import PluginContext, PluginManager
from ddm.recording import RecordingManager, RecordingSession, input_args
from ddm.capture_control import CaptureControl

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ddcut_recording_test', ROOT / 'plugins/DDCut_Replay/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
media = sys.modules[spec.name + '.media']
ui = sys.modules[spec.name + '.workspace']
APP = QApplication.instance() or QApplication([])
FFMPEG = shutil.which('ffmpeg')


def wait_for(condition, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if condition():
            return
        time.sleep(.025)
    raise AssertionError('Timed out waiting for a real media result')


class Tile:
    room = {'uname': "本地,录制'甲", 'room_id': 'offline', 'live': True}
    stream_headers = {}
    stream_codec = 'avc'

    def __init__(self, source):
        self.stream_url = str(source)

    def isVisible(self):
        return True


class RecordingTests(unittest.TestCase):
    def test_growing_timeline_seek_export_and_unload_preserve_recording(self):
        with tempfile.TemporaryDirectory(prefix='ddcut-growing-') as temp:
            root = Path(temp)
            source = root / 'local.mp4'
            subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30',
                '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '24',
                '-c:v', 'libx264', '-threads', '1', '-g', '30', '-c:a', 'aac', str(source)],
                check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            video_only = root / 'video-only.mp4'
            subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(source),
                '-c:v', 'copy', '-an', str(video_only)], check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            for fmt in ([args.format] if args.format else ('ts', 'mp4', 'mkv')):
                with self.subTest(format=fmt):
                    tile = Tile(video_only if fmt == 'ts' else source)
                    host = QMainWindow()
                    host.show()
                    host.settings = {'recording_dir': str(root / fmt), 'recording_format': fmt,
                                     'recording_codec': 'copy', 'recording_lock_quality': False,
                                     'recording_replay_enabled': False, 'ffmpeg_path': FFMPEG}
                    host.wall = SimpleNamespace(tiles=[tile])
                    host.recorder = RecordingManager(host.settings)
                    host.capture_control = CaptureControl(host)
                    host._pending_capture = {}
                    host._recording_enabled = lambda: True
                    host._start_capture = host.capture_control.start_capture
                    manager = PluginManager(plugins_dir=str(root / 'no-plugins'), enabled=[])
                    manager.window = host
                    context = PluginContext(manager, 'ddcut_replay')
                    plugin = module.DDCutReplay()
                    with patch.object(module.config, 'data_root', return_value=str(root / fmt)), \
                         patch('ddm.recording.input_args', side_effect=lambda url, headers: ['-re', *input_args(url, headers)]):
                        plugin.on_load(context)
                        try:
                            # This is the same control method wired to the main
                            # window's original tile recording signal.
                            host.capture_control.toggle_recording(tile)
                            session = host.recorder.sessions[tile]
                            self.assertIsInstance(session, module.ReplayRecordingSession)
                            wait_for(lambda: (session.update_review() or session.review_info()) is not None)
                            first = context.recording.live_recording(str(id(tile)), session.session_id)
                            self.assertEqual(first['format'], fmt)
                            self.assertIsNone(session.process.poll())
                            self.assertTrue(all(Path(p['path']).suffix == '.' + fmt for p in first['segments']))
                            self.assertLess(len(first['segments']), len(list(session.active_pattern.parent.glob('*.' + fmt))))

                            def null_preview(view, path):
                                view.stop()
                                import mpv
                                view.core = mpv.MPV(vo='null', ao='null', mute=True, keep_open=True,
                                    log_handler=lambda level, prefix, text: print(prefix, text) if level in ('error', 'fatal', 'warn') else None)
                                view.core.play(str(path))

                            with patch.object(ui.Preview, 'load', null_preview):
                                view = plugin.workspace = ui.Workspace(plugin)
                                view.show()
                                view.open_current_recording()
                                wait_for(lambda: view.preview.position() > .1)
                                self.assertTrue(view.preview.core.mute)
                                view.preview.core.pause = True
                                wait_for(lambda: (session.update_review() or session.review_info())['duration'] > first['duration'] + 1)
                                view.refresh_live()
                                self.assertGreater(view.slider.maximum(), int(first['duration'] * 1000))
                                view.seek_live_edge()
                                wait_for(lambda: view.resume_position is None)
                                self.assertGreater(view.preview.position(), first['duration'] - .5)
                                self.assertTrue(view.preview.core.pause)
                                self.assertTrue(view.preview.core.mute)
                                selected = dict(view.current)
                                end = selected['duration'] - .25
                                start = end - 1.5
                                target = root / f'{fmt}-clip.mp4'
                                job = plugin.hub.submit('growing-export', 'export', media.export_clip,
                                    FFMPEG, selected, start, end, 'precise', target, root / 'work')
                                wait_for(lambda: job.future.done())
                                self.assertEqual(job.state, 'done', job.message)
                                self.assertAlmostEqual(job.result['actual_duration'], 1.5, delta=.16)
                                if fmt == 'ts':
                                    first_clip = plugin.hub.submit('video-only-start', 'export', media.export_clip,
                                        FFMPEG, selected, .5, 2, 'precise', root / 'first-clip.mp4', root / 'work')
                                    wait_for(lambda: first_clip.future.done())
                                    self.assertEqual(first_clip.state, 'done', first_clip.message)
                                    self.assertAlmostEqual(first_clip.result['actual_duration'], 1.5, delta=.1)
                                self.assertIsNone(session.process.poll())
                                view.close()
                                self.assertFalse(session.stopping)
                                if fmt == 'ts':
                                    old_path = view.current['path']
                                    session.settings['recording_segment_minutes'] = 1
                                    session.output_started_at = time.monotonic() - 61
                                    host.recorder._poll()
                                    wait_for(lambda: session.chunk >= 2 and not session.rotating)
                                    wait_for(lambda: session.review_info() is not None)
                                    self.assertNotEqual(session.review_info()['path'], old_path)
                                    view.refresh_live()
                                    self.assertIsNone(view.live_source)
                                    self.assertEqual(view.current['path'], old_path)
                                    self.assertTrue(json.loads(Path(old_path).with_name('recording.json').read_text(encoding='utf-8'))['complete'])
                            plugin.on_unload()
                            self.assertIs(host.recorder.session_factory, RecordingSession)
                            self.assertFalse(session.stopping)
                            host.capture_control.toggle_recording(tile)
                            wait_for(lambda: tile not in host.recorder.sessions)
                            saved = json.loads(Path(first['path']).with_name('recording.json').read_text(encoding='utf-8'))
                            self.assertTrue(saved['complete'])
                            self.assertGreater(saved['duration'], first['duration'])
                            self.assertTrue(all(Path(p['path']).is_file() for p in saved['segments']))
                            self.assertTrue(media.scan_directory(media.Job('scan', 'scan'), root / fmt))
                        finally:
                            host.recorder.shutdown()
                            if plugin.timer and plugin.timer.isActive():
                                plugin.on_unload()
                            host.close()

    def test_av1_copy_keeps_codec_and_uses_compatible_container(self):
        settings = {'recording_format': 'ts', 'recording_codec': 'copy'}
        session = module.ReplayRecordingSession(Tile('unused'), settings, True, Path('unused'), 'av1')
        self.assertEqual(session.settings['recording_codec'], 'copy')
        self.assertEqual(session.extension, 'mp4')
        self.assertEqual(settings['recording_format'], 'ts')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remainder])
