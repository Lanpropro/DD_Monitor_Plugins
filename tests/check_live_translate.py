"""Offline protocol, local HTTP, Qt ownership and process lifecycle regression."""
import argparse
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
import wave
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QByteArray, QProcess, Qt
from PySide6.QtWidgets import QApplication, QWidget
from ddm.plugins import StreamSource

spec = importlib.util.spec_from_file_location('check_translate_plugin', ROOT / 'plugins/live_translate/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from check_translate_plugin.options import (Options, REAZON_FILES, QWEN_FILES, audio_arguments,
                                            with_bundled_defaults)
from check_translate_plugin.protocol import AudioGate, HEADER, MAX_FRAME, MAX_PENDING, read_frame
from check_translate_plugin.session import Session
from check_translate_plugin.translation import TranslationSlot, translate
from check_translate_plugin.local_translation import LocalTranslation, server_arguments
from check_translate_plugin.audio_diagnostics import AudioDiagnostics, failure_message
from check_translate_plugin.embedded_recognition import EmbeddedRecognition
from check_translate_plugin.host_process import HostProcess
from check_translate_plugin.secret_store import available as secret_available, protect

APP = QApplication.instance() or QApplication([])


class ProcessGroupingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    @unittest.skipUnless(os.name == 'nt', 'Windows process naming only')
    def test_host_named_wrapper_uses_and_removes_the_launch_image(self):
        target = self.root / 'helper.exe'
        target.write_bytes(b'MZ fixture')
        dependency = self.root / 'helper.dll'
        dependency.write_bytes(b'DLL fixture')
        group = HostProcess(target, name='DD监控室CE.exe', include_dlls=True)
        executable = group.prepare()
        self.assertEqual(executable.name, 'DD监控室CE.exe')
        self.assertEqual(executable.read_bytes(), target.read_bytes())
        self.assertEqual((group.directory / 'helper.dll').read_bytes(), dependency.read_bytes())
        directory = group.directory
        group.cleanup()
        self.assertFalse(directory.exists())

class Models(unittest.TestCase):
    def test_handoff_example_matches_the_current_options_schema_without_credentials(self):
        path = ROOT / 'docs/handoffs/live-translate/options.example.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        options = Options(**data)
        self.assertEqual(asdict(options), data)
        self.assertEqual(options.translation_mode, 'local')
        self.assertEqual(options.local_model.rsplit('/', 1)[-1], 'Qwen3-0.6B-Q8_0.gguf')
        self.assertEqual(options.local_server.rsplit('/', 1)[-1], 'llama-server.exe')
        self.assertEqual(options.endpoint, '')
        self.assertNotIn('api_key', data)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        for name in [*REAZON_FILES.values(), 'silero_vad.onnx']:
            (self.root / name).write_bytes(b'test fixture')
        self.options = Options(model_dir=str(self.root), python=sys.executable, translation_mode='original')

    def tearDown(self):
        self.directory.cleanup()

    def test_complete_model_files_are_required(self):
        self.options.validate()
        (self.root / 'tokens.txt').unlink()
        with self.assertRaises(ValueError):
            self.options.validate()

    def test_qwen_requires_tokenizer_as_well_as_weights(self):
        for name in QWEN_FILES.values():
            (self.root / name).touch()
        qwen = replace(self.options, engine='qwen3_asr')
        with self.assertRaises(ValueError):
            qwen.files()
        (self.root / 'tokenizer').mkdir()
        for name in ('vocab.json', 'merges.txt', 'tokenizer_config.json'):
            (self.root / 'tokenizer' / name).touch()
        qwen.validate()

    def test_endpoint_credentials_and_unsupported_language_are_rejected(self):
        for options in [replace(self.options, endpoint='file:///tmp/model'),
                        replace(self.options, endpoint='https://user:secret@example.com/v1'),
                        replace(self.options, endpoint='https://example.com/v1?key=secret'),
                        replace(self.options, source_language='韩语')]:
            options = replace(options, translation_mode='api')
            with self.subTest(options=options), self.assertRaises(ValueError):
                options.validate()
        self.assertNotIn('api_key', asdict(self.options))

    def test_local_translation_requires_real_artifact_paths_and_gguf_header(self):
        model = self.root / 'translation.gguf'
        options = replace(self.options, translation_mode='local', local_server=sys.executable,
                          local_model=str(model))
        with self.assertRaises(ValueError):
            options.validate()
        model.write_bytes(b'not a GGUF')
        with self.assertRaises(ValueError):
            options.validate()
        model.write_bytes(b'GGUF test fixture')
        options.validate()
        replace(options, translation_mode='api', endpoint='https://example.com/v1').validate()

    def test_complete_bundled_artifacts_fill_only_missing_or_stale_paths(self):
        vendor = self.root / 'vendor'
        python = vendor / 'python' / 'python.exe'
        model_dir = vendor / 'models' / 'reazon-ja-en'
        local_model = vendor / 'models' / 'Qwen3-0.6B-Q8_0.gguf'
        local_server = vendor / 'llama' / 'b12211' / 'llama-server.exe'
        for path in (python, local_model, local_server):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        model_dir.mkdir(parents=True)
        for name in (*REAZON_FILES.values(), 'silero_vad.onnx'):
            (model_dir / name).touch()
        (self.root / 'custom.gguf').touch()
        settings = with_bundled_defaults(
            {'python': str(self.root / 'missing-python.exe'), 'translation_mode': 'api',
             'local_model': str(self.root / 'custom.gguf')}, self.root)
        self.assertEqual(settings['python'], str(python))
        self.assertEqual(settings['model_dir'], str(model_dir))
        self.assertEqual(settings['local_server'], str(local_server))
        self.assertEqual(settings['local_model'], str(self.root / 'custom.gguf'))
        self.assertEqual(settings['translation_mode'], 'api')
        self.assertEqual(with_bundled_defaults({}, self.root)['engine'], 'reazon')

    def test_host_headers_are_preserved_without_extra_referer(self):
        headers = {'User-Agent': 'test agent', 'Authorization': 'test-token'}
        command = audio_arguments('https://example.com/live.m3u8', headers)
        self.assertIn('Authorization: test-token\r\n', command[command.index('-headers') + 1])
        self.assertNotIn('Referer', command[command.index('-headers') + 1])
        self.assertEqual(headers, {'User-Agent': 'test agent', 'Authorization': 'test-token'})
        with self.assertRaises(ValueError):
            audio_arguments('https://example.com/live', {'Referer': 'x\r\nInjected: y'})


class ProtocolTests(unittest.TestCase):
    def test_audio_errors_are_bounded_classified_and_never_export_credentials(self):
        diagnostic = AudioDiagnostics()
        diagnostic.feed_error(b'x' * 20000)
        diagnostic.feed_error(b' https://private.example/?key=secret Server returned 40')
        diagnostic.feed_error(b'3 Forbidden Cookie: secret')
        self.assertLessEqual(len(diagnostic._tail), 8192)
        self.assertEqual(diagnostic.reason(), 'http_403')
        message = failure_message(diagnostic.reason(), 1)
        self.assertIn('403', message)
        self.assertNotIn('secret', message)
        diagnostic = AudioDiagnostics()
        diagnostic.feed_error(b'Connection timed out')
        self.assertEqual(diagnostic.reason(), 'timeout')

    def test_overload_is_bounded_and_next_delivered_frame_resets_vad(self):
        gate = AudioGate()
        self.assertIsNone(gate.frame(b'\0' * MAX_FRAME, MAX_PENDING))
        self.assertEqual(gate.dropped_bytes, MAX_FRAME)
        audio, reset = read_frame(io.BytesIO(gate.frame(b'\0\0', 0)))
        self.assertTrue(reset)
        self.assertEqual(audio, b'\0\0')
        self.assertFalse(read_frame(io.BytesIO(gate.frame(b'\0\0', 0)))[1])

    def test_bad_or_truncated_frames_do_not_allocate_unbounded_buffers(self):
        with self.assertRaises(ValueError):
            read_frame(io.BytesIO(HEADER.pack(MAX_FRAME + 2, False)))
        with self.assertRaises(EOFError):
            read_frame(io.BytesIO(HEADER.pack(4, False) + b'\0\0'))
        with self.assertRaises(ValueError):
            read_frame(io.BytesIO(HEADER.pack(1, False) + b'\0'))
        self.assertIsNone(read_frame(io.BytesIO()))


class TranslationTests(unittest.TestCase):
    def test_real_local_http_protocol_sends_text_only(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, self.headers.get('Authorization'),
                                 json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                body = json.dumps({'choices': [{'message': {'content': '<think>hidden</think>你好'}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *unused):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            options = Options(endpoint=f'http://127.0.0.1:{server.server_port}/v1', translation_mode='api')
            result, warning = translate('こんにちは', options, 'test-key')
            self.assertEqual((result, warning), ('你好', ''))
            self.assertEqual(received[0][0:2], ('/v1/chat/completions', 'Bearer test-key'))
            body = received[0][2]
            self.assertEqual(body['messages'][1]['content'], 'こんにちは')
            self.assertFalse(body['stream'])
            self.assertNotIn('audio', body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_absolute_wait_limit_keeps_only_one_slow_request(self):
        release = threading.Event()
        calls = []
        def slow(text, options, key):
            calls.append(text)
            release.wait(2)
            return 'later', ''
        slot = TranslationSlot(Options(endpoint='http://localhost/v1', timeout=0.2, translation_mode='api'), operation=slow)
        try:
            self.assertEqual(slot.run('first')[0], '')
            for _ in range(10):
                self.assertEqual(slot.run('next')[0], '')
            self.assertEqual(calls, ['first'])
        finally:
            release.set()
            slot.future.result(timeout=2)
            slot.close()

    def test_failed_response_does_not_expose_credentials_or_body(self):
        def fail(*unused, **kwargs):
            raise OSError('secret-token https://secret.example.com/private')
        text, warning = translate('test', Options(endpoint='https://example.com/v1'), opener=fail)
        self.assertEqual(text, '')
        self.assertNotIn('secret', warning)

    def test_original_only_never_submits_translation_even_with_a_saved_endpoint(self):
        operation = Mock()
        slot = TranslationSlot(Options(translation_mode='original', endpoint='https://example.com/v1'), operation=operation)
        try:
            self.assertEqual(slot.run('speech')[0], '')
            operation.assert_not_called()
        finally:
            slot.close()

    def test_local_runtime_is_cpu_only_loopback_and_bounded(self):
        arguments = server_arguments(Options(local_model='small.gguf', threads=2), 12345)
        for option, value in [('--host', '127.0.0.1'), ('-ngl', '0'), ('-c', '1024'), ('-np', '1')]:
            self.assertEqual(arguments[arguments.index(option) + 1], value)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.window = QWidget()
        self.settings = {}
        self.entries = []
        self.show_workspace = Mock(side_effect=AssertionError('Translation must not replace the video wall'))
        self.context = SimpleNamespace(
            window=self.window, setting=lambda key, default: self.settings.get(key, default),
            set_setting=lambda key, value: self.settings.update({key: value}),
            register_workspace_control=lambda *args, **kwargs: self.entries.append((args, kwargs)) or True,
            show_workspace=self.show_workspace, log=Mock())
        self.plugin = module.LiveTranslate()
        self.plugin.on_load(self.context)

    def tearDown(self):
        self.plugin.on_unload()
        self.window.deleteLater()
        APP.processEvents()

    def test_load_is_lazy_and_stream_snapshots_are_independent(self):
        self.assertIsNone(self.plugin.session.worker)
        tile = QWidget(self.window)
        source = StreamSource('1', 'https://example.com/live', headers={'Referer': 'test'})
        payload = {'tile': tile, 'source': source}
        self.plugin.on_event('stream.resolved', payload)
        source.headers['Referer'] = 'changed'
        self.assertEqual(self.plugin.sources[tile].headers, {'Referer': 'test'})
        self.plugin.open(tile)
        self.assertEqual(self.plugin.panel.sources.count(), 1)
        self.plugin.on_event('tile.removed', {'tile': tile})
        self.assertEqual(self.plugin.panel.sources.count(), 0)

    def test_workspace_entry_has_an_icon_for_the_left_rail(self):
        self.assertEqual(len(self.entries), 1)
        arguments, settings = self.entries[0]
        self.assertEqual(arguments[0], 'translate')
        button = arguments[1]
        self.assertEqual(button.text(), '实时翻译')
        self.assertFalse(button.icon().isNull())
        self.assertFalse(button.icon().pixmap(18, 18).isNull())

    def test_configuration_is_an_auxiliary_window_and_keeps_video_visible(self):
        self.window.resize(1200, 900)
        wall = QWidget(self.window)
        wall.setGeometry(20, 20, 1100, 800)
        self.window.show()
        wall.show()
        before = wall.geometry()
        self.plugin.open()
        APP.processEvents()
        panel = self.plugin.panel
        self.assertTrue(panel.isWindow())
        self.assertTrue(panel.windowFlags() & Qt.Tool)
        self.assertFalse(panel.isModal())
        self.assertTrue(wall.isVisible())
        self.assertEqual(wall.geometry(), before)
        self.assertLess(panel.width(), self.window.width())
        self.assertLess(panel.height(), self.window.height())
        self.show_workspace.assert_not_called()

    def test_left_icon_toggles_and_reuses_configuration(self):
        self.window.show()
        button = self.entries[0][0][1]
        button.click()
        panel = self.plugin.panel
        self.assertTrue(panel.isVisible())
        panel.model.setText('retained-model')
        button.click()
        self.assertFalse(panel.isVisible())
        button.click()
        self.assertIs(self.plugin.panel, panel)
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.model.text(), 'retained-model')
        panel.close()
        self.assertFalse(panel.isVisible())
        button.click()
        self.assertTrue(panel.isVisible())

    def test_hiding_configuration_does_not_stop_subtitles_or_session(self):
        self.window.show()
        button = self.entries[0][0][1]
        button.click()
        self.plugin._caption('original', 'translation')
        with patch.object(self.plugin.session, 'stop') as stop:
            button.click()
            self.assertFalse(self.plugin.panel.isVisible())
            self.assertTrue(self.plugin.captions.isVisible())
            button.click()
            self.plugin.panel.reject()
            self.assertFalse(self.plugin.panel.isVisible())
            self.assertTrue(self.plugin.captions.isVisible())
            stop.assert_not_called()

    def test_caption_prefers_host_overlay_and_clears_it_on_stop(self):
        tile = QWidget(self.window)
        calls = []
        self.plugin.session.tile = lambda: tile
        self.context.set_tile_caption = (
            lambda target, original, translated='', *, style=None, visible=True:
            calls.append((target, original, translated, style, visible)) or True)
        self.context.clear_tile_caption = lambda target: calls.append(('clear', target)) or True
        self.plugin._caption('原文', '译文')
        self.assertEqual(calls[0][0:3], (tile, '原文', '译文'))
        self.assertFalse(self.plugin.captions is not None and self.plugin.captions.isVisible())
        self.plugin.stop()
        self.assertEqual(calls[1], ('clear', tile))

    def test_caption_falls_back_when_the_host_has_no_caption_api(self):
        self.plugin._caption('原文', '译文')
        self.assertTrue(self.plugin.captions.isVisible())

    def test_stream_switch_replaces_the_active_source_without_stopping(self):
        tile = QWidget(self.window)
        self.plugin.session.tile = lambda: tile
        source = StreamSource('1', 'https://example.com/new.flv')
        with patch.object(self.plugin.session, 'switch_source', return_value=True) as switch, \
                patch.object(self.plugin.session, 'stop') as stop:
            self.plugin.on_event('stream.resolved', {'tile': tile, 'source': source})
            switch.assert_called_once()
            stop.assert_not_called()
        self.assertEqual(self.plugin.sources[tile].url, source.url)

    def test_session_source_switch_keeps_the_recognition_owner_and_replaces_audio(self):
        session = self.plugin.session
        first = StreamSource('1', 'https://example.com/first.flv')
        second = StreamSource('1', 'https://example.com/second.flv')
        with patch.object(Options, 'validate'), \
                patch.object(session, '_build_source', return_value=(None, ['-i', 'first'])), \
                patch.object(session, '_start_recognition') as start:
            session.start(Options(translation_mode='api',
                                  endpoint='https://example.com/v1'),
                          'key', first, self.window, 'ffmpeg')
            start.assert_called_once()
        session.worker = Mock()
        session._recognition_is_ready = True
        with patch.object(session, '_build_source', return_value=(None, ['-i', 'second'])), \
                patch.object(session, '_restart_audio') as restart:
            self.assertTrue(session.switch_source(second))
            restart.assert_called_once_with(reset_source=True)
        self.assertEqual(session._arguments, ['-i', 'second'])
        session.stop()

    @unittest.skipUnless(secret_available(), 'Windows DPAPI only')
    def test_api_key_is_encrypted_and_loaded_after_panel_reopen(self):
        self.window.show()
        saved = protect('sk-local-test')
        self.settings['api_key_dpapi'] = saved
        self.plugin.open()
        self.assertEqual(self.plugin.panel.key.text(), 'sk-local-test')
        self.assertNotIn('sk-local-test', json.dumps(self.settings))
        self.plugin.panel.key.setText('sk-new-test')
        self.plugin._save_api_key()
        self.assertNotIn('sk-new-test', json.dumps(self.settings))
        self.plugin.panel.key.clear()
        self.plugin._save_api_key()
        self.assertEqual(self.settings['api_key_dpapi'], '')

    def test_room_menu_selects_source_even_when_configuration_is_open(self):
        self.window.show()
        tiles = [QWidget(self.window), QWidget(self.window)]
        for i, tile in enumerate(tiles):
            self.plugin.on_event('stream.resolved', {
                'tile': tile, 'source': StreamSource(str(i), 'https://example.com/live')})
        self.plugin.open(tiles[0])
        self.plugin.tile_actions(tiles[1])[0][1]()
        self.assertTrue(self.plugin.panel.isVisible())
        self.assertIs(self.plugin.panel.sources.currentData()(), tiles[1])

    def test_translation_mode_shows_only_its_own_fields_and_keeps_values(self):
        self.window.show()
        self.plugin.open()
        panel = self.plugin.panel
        self.assertEqual(panel.translation_mode.currentData(), 'local')
        self.assertTrue(panel.local_group.isVisible())
        self.assertFalse(panel.api_group.isVisible())
        panel.local_model.setText('my-local-model.gguf')
        panel.translation_mode.setCurrentIndex(panel.translation_mode.findData('api'))
        self.assertFalse(panel.local_group.isVisible())
        self.assertTrue(panel.api_group.isVisible())
        panel.endpoint.setText('https://example.com/v1')
        panel.translation_mode.setCurrentIndex(panel.translation_mode.findData('local'))
        self.assertEqual(panel.options().local_model, 'my-local-model.gguf')
        self.assertEqual(panel.options().endpoint, 'https://example.com/v1')

    def test_local_translation_is_owned_and_late_ready_cannot_restart_stopped_session(self):
        session = self.plugin.session
        local = LocalTranslation(session)
        local.api_key = 'local-session-key'
        source = StreamSource('1', 'https://example.com/live')
        with patch.object(Options, 'validate'), \
                patch('check_translate_plugin.session.LocalTranslation', return_value=local), \
                patch.object(local, 'start') as start, patch.object(local, 'stop') as stop, \
                patch.object(session, '_start_recognition') as worker:
            session.start(Options(translation_mode='local'), 'api-secret', source, self.window, 'ffmpeg')
            start.assert_called_once()
            worker.assert_not_called()
            local.ready.emit('http://127.0.0.1:12345/v1')
            options, key, _ffmpeg, _arguments = worker.call_args.args
            self.assertEqual(options.endpoint, 'http://127.0.0.1:12345/v1')
            self.assertEqual(key, 'local-session-key')
            session.stop()
            stop.assert_called_once()
            local.ready.emit('http://127.0.0.1:12345/v1')
            self.assertEqual(worker.call_count, 1)

    def test_api_and_original_modes_do_not_start_the_local_model(self):
        session = self.plugin.session
        source = StreamSource('1', 'https://example.com/live')
        for mode in ('api', 'original'):
            with self.subTest(mode=mode), patch.object(Options, 'validate'), \
                    patch('check_translate_plugin.session.LocalTranslation') as local, \
                    patch.object(session, '_start_recognition') as worker:
                options = Options(translation_mode=mode, endpoint='https://example.com/v1')
                session.start(options, 'api-secret', source, self.window, 'ffmpeg')
                local.assert_not_called()
                self.assertIsNone(session.local)
                self.assertEqual(worker.call_args.args[1], 'api-secret' if mode == 'api' else '')
                session.stop()

    def test_overseas_transport_uses_host_proxy_and_is_owned_by_session(self):
        session = self.plugin.session
        relay = SimpleNamespace(url='http://127.0.0.1:12345/live.m3u8', stop=Mock())
        for platform in ('youtube', 'twitch'):
            source = StreamSource('1', 'https://example.com/live.m3u8', platform=platform,
                                  headers={'Authorization': 'fixture-secret'})
            with self.subTest(platform=platform), patch.object(Options, 'validate'), \
                    patch('ddm.global_danmaku.request_proxy', return_value='http://proxy.invalid:8080') as proxy, \
                    patch('ddm.hls_proxy.HlsProxy', return_value=relay) as transport, \
                    patch.object(session, '_start_recognition') as worker:
                session.start(Options(translation_mode='original'), '', source, self.window, 'ffmpeg')
                proxy.assert_called_once_with(source.url)
                transport.assert_called_once_with(source.url, source.headers, 'http://proxy.invalid:8080')
                arguments = worker.call_args.args[-1]
                self.assertEqual(arguments[arguments.index('-i') + 1], relay.url)
                self.assertEqual(arguments[arguments.index('-rw_timeout') + 1], '30000000')
                self.assertEqual(arguments[arguments.index('-http_proxy') + 1], '')
                self.assertNotIn('-headers', arguments)
                self.assertNotIn(source.url, arguments)
                session.stop()
                self.assertIsNone(session._relay)
        self.assertEqual(relay.stop.call_count, 2)

    def test_actual_ffmpeg_reads_audio_through_explicit_host_proxy(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('FFmpeg is not on PATH')
        output = io.BytesIO()
        with wave.open(output, 'wb') as recording:
            recording.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            recording.writeframes(b'\0\0' * 16000)
        body, received = output.getvalue(), []
        class Proxy(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *unused):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        session = self.plugin.session
        source = StreamSource('fixture', 'http://unreachable.invalid/live.wav', platform='youtube',
                              headers={'Authorization': 'fixture-secret'})
        relay = None
        try:
            with patch.object(Options, 'validate'), patch.object(session, '_start_recognition') as worker, \
                    patch('ddm.global_danmaku.request_proxy', return_value=f'http://127.0.0.1:{server.server_port}'):
                session.start(Options(translation_mode='original'), '', source, self.window, ffmpeg)
                relay = session._relay
                arguments = worker.call_args.args[-1]
                # A child-process proxy must not divert the loopback relay connection.
                environment = dict(os.environ, http_proxy='http://127.0.0.1:1')
                result = subprocess.run([ffmpeg, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env=environment, timeout=15)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(len(result.stdout), 32000)
                self.assertIn((source.url, 'fixture-secret'), received)
        finally:
            session.stop()
            if relay:
                relay._thread.join(timeout=2)
                self.assertFalse(relay._thread.is_alive())
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_audio_failure_records_safe_reason_and_stale_exit_cannot_stop_new_session(self):
        session = self.plugin.session
        self.plugin.open()
        audio = session.audio = QProcess(session)
        session._audio_started = time.monotonic()
        session._audio_diagnostics.feed_error(b'https://private.example/?key=secret Server returned 403 Forbidden')
        statuses = []
        session.status.connect(statuses.append)
        session._audio_finished(audio, 1, QProcess.NormalExit)
        record = session.samples[-1]
        self.assertEqual(record['event'], 'audio_exit')
        self.assertEqual(record['reason'], 'http_403')
        self.assertEqual(record['received_pcm_bytes'], 0)
        self.assertIn('403', statuses[-1])
        self.assertNotIn('secret', json.dumps(record))
        self.assertNotIn('secret', str(self.context.log.call_args))
        new_audio = session.audio = QProcess(session)
        session._audio_finished(audio, 1, QProcess.NormalExit)
        self.assertIs(session.audio, new_audio)
        session.stop()

    def test_stale_recognition_event_cannot_display_a_caption(self):
        session = self.plugin.session
        events = []
        session.caption.connect(lambda *args: events.append(args))
        worker = Mock()
        worker.source_epoch = 2
        session.worker = worker
        session._recognition_original(worker, 1, 'stale')
        self.assertEqual(events, [])
        session._recognition_original(worker, 2, 'current')
        self.assertEqual(events, [('current', '')])
        session._recognition_caption(worker, 1, {'text': 'stale', 'translated': '旧源'})
        self.assertEqual(events, [('current', '')])
        session.worker = None

    def test_start_uses_embedded_recognition_and_does_not_spawn_a_python_process(self):
        session = self.plugin.session
        worker = Mock()
        worker.ready = Mock()
        worker.original = Mock()
        worker.caption = Mock()
        worker.failed = Mock()
        with patch('check_translate_plugin.session.EmbeddedRecognition', return_value=worker), \
                patch('check_translate_plugin.session.HostProcess') as helper:
            session._start_recognition(Options(translation_mode='original'), '', 'ffmpeg', [])
        self.assertIs(session.worker, worker)
        worker.start.assert_called_once_with()
        self.assertIsNone(session.audio)
        helper.assert_called_once_with('ffmpeg', include_dlls=True)
        with patch.object(session, '_start_audio') as start:
            session._recognition_ready(worker)
            start.assert_called_once_with()

    def test_metrics_history_is_bounded_and_contains_no_transcript(self):
        session = self.plugin.session
        for i in range(600):
            session._record({'event': 'resources', 'sample': i})
        self.assertEqual(len(session.samples), 300)
        self.assertEqual(session.samples[0]['sample'], 300)

    def test_stop_reaps_ffmpeg_local_translation_and_recognition_thread(self):
        session = self.plugin.session
        local = session.local = LocalTranslation(session)
        worker = session.worker = EmbeddedRecognition(Options(translation_mode='original'), parent=session)
        processes = [QProcess(session), local.process]
        try:
            for process in processes:
                process.start(sys.executable, ['-c', 'import time; time.sleep(30)'])
                self.assertTrue(process.waitForStarted(3000))
            session.audio = processes[0]
            session.stop()
            self.assertTrue(all(process.state() == QProcess.NotRunning for process in processes))
            self.assertIsNone(session.worker)
            self.assertIsNone(session.audio)
            self.assertIsNone(session.local)
            self.assertFalse(worker.is_alive)
        finally:
            for process in processes:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(1000)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
