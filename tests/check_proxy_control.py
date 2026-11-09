"""Proxy regression using temporary state and loopback services; no real accounts."""
import argparse
import base64
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import wave
from unittest.mock import Mock, patch
from urllib.request import getproxies

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--core', type=Path)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
import requests

spec = importlib.util.spec_from_file_location('check_proxy_plugin', ROOT / 'plugins/proxy_control/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from check_proxy_plugin.configuration import Options, core_config, load_options, save_options, subscription_url
from check_proxy_plugin.environment import Environment, KEYS
from check_proxy_plugin.controller import Controller
from check_proxy_plugin.network import Download
from check_proxy_plugin.prepare_core import install, ASSET
from check_proxy_plugin.core import free_port
from ddm.global_danmaku import request_proxy
from ddm.hls_proxy import HlsProxy

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    if not predicate():
        raise AssertionError('Timed out waiting for local proxy operation')


class Service:
    def __init__(self, name=b'proxy', body=None, code=200):
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_CONNECT(self):
                owner.requests.append(('CONNECT ' + self.path, self.headers.get('Proxy-Authorization')))
                self.send_response(200, 'Connection established')
                self.end_headers()
                self.close_connection = False

            def do_GET(self):
                owner.requests.append((self.path, self.headers.get('Proxy-Authorization')))
                payload = body if body is not None else name
                self.send_response(code)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (ConnectionResetError, BrokenPipeError):
                    pass
            def log_message(self, *unused):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ConfigurationTests(unittest.TestCase):
    def test_invalid_subscriptions_are_rejected_without_echoing_credentials(self):
        for value in ('', 'file:///secret', 'http://host:70000',
                      'http://user:secret@host:80/path', 'http://host:80\r\nx:y'):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                subscription_url(value)
            self.assertNotIn('secret', str(error.exception))
        self.assertEqual(subscription_url('https://example.invalid/sub?key=fixture'),
                         'https://example.invalid/sub?key=fixture')

    def test_config_cannot_enable_tun_lan_or_unprotected_controller(self):
        config = core_config(10001, 10002, 'random-key')
        self.assertEqual(config['bind-address'], '127.0.0.1')
        self.assertFalse(config['allow-lan'])
        self.assertFalse(config['tun']['enable'])
        self.assertEqual(config['external-controller'], '127.0.0.1:10002')
        self.assertEqual(config['secret'], 'random-key')
        self.assertFalse(config['proxy-providers']['subscription']['health-check']['enable'])
        self.assertEqual(config['rules'], ['MATCH,DDM'])

    def test_legacy_modes_preserve_subscription_and_remove_manual_proxy_settings(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'preferences.json'
            for mode in ('external', 'system', 'direct', 'managed'):
                with self.subTest(mode=mode):
                    path.write_text(json.dumps({'mode': mode, 'address': 'socks5://user:secret@localhost:7890',
                                                'core': 'fixture-core', 'source': 'nodes',
                                                'profile': 'fixture-nodes', 'node': 'First'}))
                    controller = Controller(root)
                    try:
                        self.assertEqual(controller.options.enabled, mode == 'managed')
                        self.assertEqual(controller.options.profile, 'fixture-nodes')
                        self.assertEqual(controller.options.node, 'First')
                        saved = json.loads(path.read_text())
                        self.assertNotIn('mode', saved)
                        self.assertNotIn('address', saved)
                        self.assertNotIn('secret', path.read_text())
                        self.assertIsNone(controller.core.process)
                    finally:
                        controller.stop(announce=False)
                        controller.deleteLater()
                        APP.processEvents()

    def test_private_preferences_roundtrip_and_broken_input_falls_back(self):
        with tempfile.TemporaryDirectory() as root:
            options = Options(enabled=True, profile='https://example.invalid/?token=fixture')
            save_options(root, options)
            self.assertEqual(load_options(root), options)
            for invalid in ('broken', '[]', 'null'):
                (Path(root) / 'preferences.json').write_text(invalid)
                self.assertEqual(load_options(root), Options())

    def test_core_installer_rejects_unverified_archive(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / 'untrusted.zip'
            archive.write_bytes(b'not an official binary')
            with self.assertRaises(ValueError):
                install(archive, Path(root) / 'core.exe')
            self.assertFalse((Path(root) / 'core.exe').exists())


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.environment = Environment()
        self.temporary = tempfile.TemporaryDirectory()
        self.controller = Controller(self.temporary.name)
        self.service = Service()

    def tearDown(self):
        self.controller.stop()
        self.controller.deleteLater()
        APP.processEvents()
        self.service.close()
        self.environment.restore()
        self.temporary.cleanup()

    def test_http_proxy_reaches_requests_urllib_host_relay_and_websocket_resolver(self):
        from urllib.request import urlopen
        self.controller.environment.apply(self.service.url)
        self.assertIsNone(self.controller.core.process)
        self.assertEqual(request_proxy('wss://example.invalid/ws'), self.service.url)
        self.assertEqual(requests.get('http://example.invalid/live', timeout=3).content, b'proxy')
        with urlopen('http://example.invalid/urllib', timeout=3) as response:
            self.assertEqual(response.read(), b'proxy')
        relay = HlsProxy('http://example.invalid/stream', {}, request_proxy('http://example.invalid/stream'))
        try:
            self.assertEqual(requests.get(relay.url, timeout=3).content, b'proxy')
        finally:
            relay.stop()
            relay._thread.join(timeout=2)
        self.assertTrue(any(path == 'http://example.invalid/stream' for path, _auth in self.service.requests))

    def test_stop_restores_original_environment_exactly(self):
        original = {key: os.environ.get(key) for key in KEYS}
        self.controller.environment.apply(self.service.url)
        self.assertIsNone(request_proxy(self.service.url))
        self.assertEqual(requests.get(self.service.url, timeout=3).content, b'proxy')
        self.controller.stop()
        self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)

    def test_invalid_edit_does_not_stop_an_existing_proxy(self):
        self.controller.environment.apply(self.service.url)
        self.controller.apply(Options(profile='bad value'))
        self.assertEqual(getproxies()['http'], self.service.url)

    def test_core_children_never_inherit_proxy_loops(self):
        self.controller.environment.apply(self.service.url)
        self.assertFalse(set(KEYS).intersection(self.controller.environment.child_environment()))

    def test_native_mpv_and_ffmpeg_children_use_the_selected_http_proxy(self):
        from ddm.player import mpv, EMBEDDED_OPTIONS
        output = io.BytesIO()
        with wave.open(output, 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 16000)
        service = Service(body=output.getvalue())
        player = None
        try:
            self.controller.environment.apply(service.url)
            player = mpv.MPV(vo='null', ao='null', **EMBEDDED_OPTIONS)
            player.play('http://fixture.invalid/mpv.wav')
            wait_for(lambda: any('mpv.wav' in path for path, _auth in service.requests), timeout=6)
            ffmpeg = shutil.which('ffmpeg')
            if ffmpeg:
                result = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-rw_timeout', '3000000',
                                         '-i', 'http://fixture.invalid/ffmpeg.wav', '-f', 'null', '-'],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6)
                self.assertEqual(result.returncode, 0)
                self.assertTrue(any('ffmpeg.wav' in path for path, _auth in service.requests))
        finally:
            if player is not None:
                player.terminate()
            service.close()

    def test_failed_download_is_sanitized_and_size_is_bounded(self):
        download = Download()
        results = []
        download.finished.connect(lambda path, error: results.append((path, error)))
        download.start(self.service.url + '/?token=fixture-secret', Path(self.temporary.name) / 'profile', 2)
        wait_for(lambda: bool(results))
        self.assertIsNone(results[0][0])
        self.assertNotIn('fixture-secret', results[0][1])
        self.assertFalse((Path(self.temporary.name) / 'profile.download').exists())
        download.cancel()
        download.deleteLater()

    def test_cancel_download_ignores_late_result(self):
        download = Download()
        done = Mock()
        download.finished.connect(done)
        download.start(self.service.url, Path(self.temporary.name) / 'cancel', 1024)
        download.cancel()
        QTest.qWait(100)
        done.assert_not_called()
        download.deleteLater()


class InterfaceTests(unittest.TestCase):
    def test_legacy_startup_only_resumes_previously_managed_subscriptions(self):
        for mode in ('external', 'system', 'direct', 'managed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root, \
                    patch.object(module.config, 'data_root', return_value=root):
                directory = Path(root) / 'utils/proxy_control'
                directory.mkdir(parents=True)
                (directory / 'preferences.json').write_text(json.dumps({
                    'mode': mode, 'address': 'http://user:fixture-secret@127.0.0.1:7890',
                    'source': 'nodes', 'profile': 'fixture-nodes', 'node': 'First'}))
                window = QWidget()
                context = SimpleNamespace(window=window, register_workspace_control=Mock())
                plugin = module.ProxyControl()
                original = {key: os.environ.get(key) for key in KEYS}
                try:
                    with patch.object(Controller, 'apply') as apply:
                        plugin.on_load(context)
                        if mode == 'managed':
                            apply.assert_called_once()
                            self.assertEqual(apply.call_args.args[0].profile, 'fixture-nodes')
                        else:
                            apply.assert_not_called()
                    self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)
                finally:
                    plugin.on_unload()
                    window.deleteLater()
                    APP.processEvents()

    def test_left_rail_toggles_auxiliary_window_without_replacing_live_wall(self):
        with tempfile.TemporaryDirectory() as root, patch.object(module.config, 'data_root', return_value=root):
            window = QWidget()
            window.resize(1200, 900)
            wall = QWidget(window)
            wall.resize(1100, 800)
            window.show()
            wall.show()
            entries = []
            context = SimpleNamespace(window=window,
                                      register_workspace_control=lambda *args, **kwargs: entries.append(args),
                                      show_workspace=Mock(side_effect=AssertionError('Must keep video wall')))
            plugin = module.ProxyControl()
            try:
                plugin.on_load(context)
                button = entries[0][1]
                self.assertFalse(button.icon().isNull())
                button.click()
                panel = plugin.panel
                self.assertTrue(panel.isVisible())
                self.assertFalse(panel.isModal())
                self.assertTrue(wall.isVisible())
                self.assertFalse(hasattr(panel, 'address'))
                self.assertFalse(hasattr(panel, 'mode'))
                self.assertTrue(panel.managed.isVisible())
                self.assertTrue(panel.runtime.isVisible())
                panel.profile.setPlainText('retained')
                with patch.object(plugin.controller, 'stop') as stop:
                    button.click()
                    self.assertFalse(panel.isVisible())
                    button.click()
                    self.assertEqual(panel.profile.toPlainText(), 'retained')
                    panel.reject()
                    stop.assert_not_called()
                context.show_workspace.assert_not_called()
            finally:
                plugin.on_unload()
                window.deleteLater()
                APP.processEvents()


@unittest.skipUnless(args.core, 'Pass --core for actual Mihomo process tests')
class RealCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = Controller(self.temp.name)
        self.server_process = None
        self.first = Service(b'first')
        self.second = Service(b'second')
        self.profile = json.dumps({'tun': {'enable': True}, 'allow-lan': True,
                                   'proxies': [dict(name=name, type='http', server='127.0.0.1', port=service.server.server_port)
                                               for name, service in [('First', self.first), ('Second', self.second)]]})

    def tearDown(self):
        self.controller.stop()
        if self.server_process is not None:
            self.server_process.kill()
            self.server_process.waitForFinished(1000)
            self.server_process.deleteLater()
        self.controller.deleteLater()
        APP.processEvents()
        self.first.close()
        self.second.close()
        self.temp.cleanup()

    def start(self, **changes):
        options = Options(enabled=True, core=str(args.core.resolve()), source='nodes', profile=self.profile)
        self.controller.apply(replace(options, **changes))
        wait_for(lambda: bool(self.controller.endpoint) or not self.controller.working)
        self.assertTrue(self.controller.endpoint, self.controller.message)

    def test_managed_profile_select_authentication_and_real_traffic(self):
        self.start()
        self.assertEqual(requests.get('http://fixture.invalid/live', timeout=5).content, b'first')
        port = self.controller.core.port
        response = requests.get(f'http://127.0.0.1:{port}/proxies', timeout=3)
        self.assertEqual(response.status_code, 401)
        self.controller.select('Second')
        wait_for(lambda: self.controller.core.current == 'Second')
        self.assertEqual(requests.get('http://fixture.invalid/live', timeout=5).content, b'second')
        pid = self.controller.core.process.processId()
        directory = Path(self.controller.core.directory.name)
        self.controller.stop()
        import psutil
        self.assertFalse(psutil.pid_exists(pid))
        self.assertFalse(directory.exists())

    def test_subscription_download_uses_provider_nodes_without_importing_tun(self):
        subscription = Service(body=self.profile.encode())
        try:
            self.start(source='url', profile=subscription.url + '/subscription?token=fixture')
            self.assertEqual(requests.get('http://fixture.invalid/live', timeout=5).content, b'first')
            config = json.loads((Path(self.controller.core.directory.name) / 'config.json').read_text())
            self.assertFalse(config['tun']['enable'])
            self.assertFalse(config['allow-lan'])
            self.assertTrue(subscription.requests)
        finally:
            subscription.close()

    def test_stop_releases_core_and_persists_disabled_state(self):
        original = {key: os.environ.get(key) for key in KEYS}
        self.start()
        process = self.controller.core.process
        self.controller.stop()
        self.assertEqual(process.state(), QProcess.NotRunning)
        self.assertIsNone(self.controller.core.process)
        self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)
        self.assertFalse(load_options(self.temp.name).enabled)
        self.assertEqual(load_options(self.temp.name).profile, self.profile)

    def test_invalid_profile_does_not_claim_a_working_proxy(self):
        self.controller.apply(Options(enabled=True, core=str(args.core.resolve()),
                                      source='nodes', profile='<html>not nodes</html>'))
        self.controller.core.deadline = time.monotonic() + 1
        wait_for(lambda: bool(self.controller.endpoint) or not self.controller.working, timeout=5)
        self.assertFalse(self.controller.endpoint)
        self.assertIsNone(self.controller.core.process)

    def server_core(self):
        mixed, encrypted = free_port(), free_port()
        directory = Path(self.temp.name) / 'fixture-server'
        directory.mkdir()
        config = {'mixed-port': mixed, 'bind-address': '127.0.0.1', 'allow-lan': False,
                  'log-level': 'silent', 'rules': ['MATCH,DIRECT'], 'dns': {'enable': False},
                  'listeners': [{'name': 'EncryptedFixture', 'type': 'shadowsocks',
                                 'listen': '127.0.0.1', 'port': encrypted,
                                 'cipher': 'aes-128-gcm', 'password': 'fixture-password'}]}
        path = directory / 'config.json'
        path.write_text(json.dumps(config))
        self.server_process = QProcess()
        self.server_process.setStandardOutputFile(QProcess.nullDevice())
        self.server_process.setStandardErrorFile(QProcess.nullDevice())
        self.server_process.start(str(args.core.resolve()), ['-d', str(directory), '-f', str(path)])
        self.assertTrue(self.server_process.waitForStarted(3000))
        def listening():
            try:
                with socket.create_connection(('127.0.0.1', mixed), timeout=0.1):
                    return True
            except OSError:
                return False
        wait_for(listening)
        return mixed, encrypted

    def test_raw_shadowsocks_node_link_really_connects_encrypted_fixture(self):
        _mixed, encrypted = self.server_core()
        userinfo = base64.urlsafe_b64encode(b'aes-128-gcm:fixture-password').decode().rstrip('=')
        self.start(profile=f'ss://{userinfo}@127.0.0.1:{encrypted}#EncryptedFixture')
        self.assertIn('EncryptedFixture', self.controller.core.nodes)
        response = requests.get(self.first.url + '/encrypted-test',
                                proxies={'http': self.controller.endpoint}, timeout=5)
        self.assertEqual(response.content, b'first')

    def test_base64_subscription_file_loads_real_nodes(self):
        _mixed, encrypted = self.server_core()
        userinfo = base64.urlsafe_b64encode(b'aes-128-gcm:fixture-password').decode().rstrip('=')
        node = f'ss://{userinfo}@127.0.0.1:{encrypted}#FileFixture'
        path = Path(self.temp.name) / 'subscription.txt'
        path.write_bytes(base64.b64encode(node.encode()))
        self.start(source='file', profile=str(path))
        self.assertIn('FileFixture', self.controller.core.nodes)
        response = requests.get(self.first.url + '/file-test',
                                proxies={'http': self.controller.endpoint}, timeout=5)
        self.assertEqual(response.content, b'first')

    def test_prepare_download_verifies_and_installs_the_actual_official_archive(self):
        archive = args.core.resolve().parent / ASSET
        if not archive.is_file():
            self.skipTest('Official archive is not beside the provided core')
        service = Service(body=archive.read_bytes())
        prepared = []
        self.controller.prepared.connect(prepared.append)
        try:
            with patch('check_proxy_plugin.controller.URL', service.url + '/core.zip'):
                self.controller.prepare()
                wait_for(lambda: bool(prepared) or not self.controller.working, timeout=15)
            self.assertTrue(prepared, self.controller.message)
            result = subprocess.run([prepared[0], '-v'], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertIn(b'v1.19.33', result.stdout)
            self.assertIsNone(self.controller.installer)
        finally:
            service.close()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
