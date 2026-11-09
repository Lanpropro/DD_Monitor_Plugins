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
parser.add_argument('--format', choices=('ts', 'mp4', 'mkv', 'av1'))
args, remainder = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['PATH'] = str(args.host.resolve()) + os.pathsep + os.environ['PATH']
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
            av1 = root / 'av1.mp4'
            if args.format in (None, 'av1'):
                subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10', '-t', '18',
                    '-c:v', 'libaom-av1', '-cpu-used', '8', '-threads', '2', '-g', '10', str(av1)],
                    check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            for fmt in ([args.format] if args.format else ('ts', 'mp4', 'mkv', 'av1')):
                with self.subTest(format=fmt):
                    tile = Tile(av1 if fmt == 'av1' else video_only if fmt == 'ts' else source)
                    if fmt == 'av1':
                        tile.stream_codec = 'av1'
                    host = QMainWindow()
                    host.show()
                    host.settings = {'recording_dir': str(root / fmt), 'recording_format': 'mp4' if fmt == 'av1' else fmt,
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
                        plugin.set_capture_mode('clip')
                        try:
                            # This is the same control method wired to the main
                            # window's original tile recording signal.
                            host.capture_control.toggle_recording(tile)
                            session = host.recorder.sessions[tile]
                            self.assertIsInstance(session, module.ReplayRecordingSession)
                            wait_for(lambda: (session.update_review() or session.review_info()) is not None)
                            first = context.recording.live_recording(str(id(tile)), session.session_id)
                            self.assertEqual(first['format'], 'fmp4' if fmt == 'av1' else 'ts')
                            self.assertEqual(first['index_format'], 'hls')
                            self.assertEqual(session.settings['recording_codec'], 'copy')
                            live_text = Path(first['path']).read_text(encoding='utf-8')
                            self.assertIn('#EXT-X-PLAYLIST-TYPE:EVENT', live_text)
                            self.assertNotIn('#EXT-X-ENDLIST', live_text)
                            self.assertIsNone(session.process.poll())
                            self.assertTrue(all(Path(p['path']).is_file() and not p['path'].endswith('.tmp') for p in first['segments']))
                            if fmt == 'av1':
                                self.assertIn('#EXT-X-MAP:', live_text)
                                self.assertTrue(all(Path(p['init']).is_file() for p in first['segments']))

                            def null_preview(view, path):
                                view.stop()
                                import mpv
                                view.core = mpv.MPV(vo='null', ao='null', mute=True, keep_open=True, **ui.preview_options(path),
                                    log_handler=lambda level, prefix, text: print(prefix, text) if level in ('error', 'fatal', 'warn') else None)
                                view.core.play(view.source_url(path))

                            with patch.object(ui.Preview, 'load', null_preview):
                                view = plugin.workspace = ui.Workspace(plugin)
                                view.show()
                                view.open_current_recording()
                                try:
                                    wait_for(lambda: view.preview.position() > .1)
                                except AssertionError as error:
                                    raise AssertionError(dict(status=view.status.text(),sources=len(view.sources_data),
                                        page=view.library_review.indexOf(view.library_review.currentWidget()),
                                        core=bool(view.preview.core),path=(view.current or {}).get('path'))) from error
                                original_core = view.preview.core
                                self.assertTrue(view.preview.core.mute)
                                view.preview.core.pause = True
                                wait_for(lambda: (session.update_review() or session.review_info())['duration'] > first['duration'] + 1)
                                view.refresh_live()
                                self.assertGreater(view.timeline.duration, first['duration'])
                                view.seek_live_edge()
                                wait_for(lambda: view.preview.position() > first['duration'] - .5)
                                self.assertIs(view.preview.core, original_core)
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
                                host.settings['recording_replay_enabled'] = True
                                self.assertFalse(host.recorder.save_replay(tile))
                                self.assertFalse(list((root / fmt / 'replays').glob('*')))
                                if fmt == 'av1':
                                    snapshot = context.recording.snapshot(str(id(tile)), session.session_id)
                                    snap_job = plugin.hub.submit('HLS snapshot', 'snapshot', media.prepare_snapshot,
                                        FFMPEG, snapshot, root / 'snapshot', root / 'work')
                                    wait_for(lambda: snap_job.future.done())
                                    self.assertEqual(snap_job.state, 'done', snap_job.message)
                                    context.recording.release(snapshot['lease'])
                                view.close()
                                self.assertFalse(session.stopping)
                                if fmt in ('ts', 'av1'):
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
                                    self.assertFalse(host.recorder.save_replay(tile))
                                    self.assertFalse(list((root / fmt / 'replays').glob('*')))
                            plugin.on_unload()
                            self.assertIs(host.recorder.session_factory, RecordingSession)
                            self.assertFalse(session.stopping)
                            host.capture_control.toggle_recording(tile)
                            wait_for(lambda: tile not in host.recorder.sessions)
                            saved = json.loads(Path(first['path']).with_name('recording.json').read_text(encoding='utf-8'))
                            self.assertTrue(saved['complete'])
                            vod_text = Path(first['path']).read_text(encoding='utf-8')
                            self.assertIn('#EXT-X-PLAYLIST-TYPE:VOD', vod_text)
                            self.assertIn('#EXT-X-ENDLIST', vod_text)
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
