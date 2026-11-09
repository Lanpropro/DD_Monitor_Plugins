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
import socketserver
import struct
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
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--core', type=Path)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QPoint, QProcess, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QToolButton, QVBoxLayout
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
from check_proxy_plugin.core import error_category, free_port
from check_proxy_plugin.node_list import NodeModel, NODE_ROLE, ACTIVE_ROLE
from check_proxy_plugin.panel import Panel
from check_proxy_plugin.latency import Latency
from check_proxy_plugin.profiles import Profile, load_profiles, save_profiles
from check_proxy_plugin.metadata import display_name
from check_proxy_plugin.regions import region_code, display_node_name, flag_icon
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
    def __init__(self, name=b'proxy', body=None, code=200, headers=None, ipv6=False):
        self.requests = []
        self.authorizations = []
        self.user_agents = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except ConnectionResetError:
                    pass  # The connection snapshot fixture closes its CONNECT socket.

            def do_CONNECT(self):
                owner.requests.append(('CONNECT ' + self.path, self.headers.get('Proxy-Authorization')))
                self.send_response(200, 'Connection established')
                self.end_headers()
                self.close_connection = False

            def do_GET(self):
                owner.authorizations.append(self.headers.get('Authorization'))
                owner.user_agents.append(self.headers.get('User-Agent', ''))
                owner.requests.append((self.path, self.headers.get('Proxy-Authorization')))
                payload = body(self.headers) if callable(body) else (body if body is not None else name)
                self.send_response(code)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(payload)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (ConnectionResetError, BrokenPipeError):
                    pass
            def log_message(self, *unused):
                pass
        class IPv6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        self.server = (IPv6Server(('::1', 0), Handler) if ipv6 else
                       ThreadingHTTPServer(('127.0.0.1', 0), Handler))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ConfigurationTests(unittest.TestCase):
    def test_profile_user_agents_migrate_and_roundtrip_independently(self):
        from check_proxy_plugin.configuration import DEFAULT_USER_AGENT
        with tempfile.TemporaryDirectory() as directory:
            options = Options(profile='https://fixture.invalid/sub', core=__file__)
            (Path(directory) / 'profiles.json').write_text(json.dumps([
                {'id': 'legacy', 'name': 'Legacy', 'source': 'url', 'profile': options.profile}]))
            profiles = load_profiles(directory, options)
            self.assertEqual(profiles[0].user_agent, DEFAULT_USER_AGENT)
            profiles = [replace(profiles[0], user_agent='clash-verge'),
                        Profile('other', 'Other', 'url', 'https://other.invalid/sub', user_agent='Custom/2.0')]
            save_profiles(directory, profiles)
            self.assertEqual(load_profiles(directory, options), profiles)
        for value in ('', 'abc\r\nInjected: yes', '中文', 'x' * 257):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Options(core=__file__, source='url', profile='https://fixture.invalid/sub', user_agent=value).validate()

    def test_profile_format_diagnostics_only_contain_counts(self):
        from check_proxy_plugin.profile_settings import merge_resolution
        profile = json.dumps({'proxies': [
            {'name': 'private-name', 'server': 'private.fixture.invalid', 'password': 'fixture-secret'},
            {'server': '127.0.0.1'}, {'server': '::1'}, {'server': ''}]}).encode()
        with self.assertLogs('ddm.proxy_control', level='INFO') as logs:
            merge_resolution(core_config(10001, 10002, 'fixture-key'), profile)
        text = '\n'.join(logs.output)
        self.assertIn('format=json nodes=4', text)
        for key in ('ipv4', 'ipv6', 'domain', 'missing'):
            self.assertIn(f"'{key}': 1", text)
        for private in ('private-name', 'private.fixture.invalid', 'fixture-secret'):
            self.assertNotIn(private, text)

    def test_yaml_resolution_and_anchors_are_preserved_without_importing_listeners(self):
        from check_proxy_plugin.profile_settings import merge_resolution
        config = core_config(10001, 10002, 'fixture-key')
        config['hosts'] = {'base.fixture.invalid': '::1'}
        profile = b'''resolution: &resolution
  enable: true
  nameserver: [udp://127.0.0.1:5353]
  listen: 0.0.0.0:53
  cache-max-size: 999999
  enhanced-mode: fake-ip
dns: *resolution
ipv6: true
hosts:
  fixture.invalid: &address [127.0.0.1]
  alias.fixture.invalid: *address
tun: {enable: true}
allow-lan: true
external-controller: 0.0.0.0:9090
'''
        fields, hosts = merge_resolution(config, profile)
        self.assertEqual((fields, hosts), (2, 2))
        self.assertEqual(config['hosts']['alias.fixture.invalid'], ['127.0.0.1'])
        self.assertEqual(config['hosts']['base.fixture.invalid'], '::1')
        self.assertEqual(config['dns']['proxy-server-nameserver'], ['udp://127.0.0.1:5353'])
        self.assertEqual(config['dns']['listen'], '')
        self.assertEqual(config['dns']['cache-max-size'], 256)
        self.assertEqual(config['dns']['enhanced-mode'], 'redir-host')
        self.assertTrue(config['ipv6'])
        self.assertTrue(config['dns']['ipv6'])
        self.assertFalse(config['tun']['enable'])
        self.assertFalse(config['allow-lan'])
        self.assertEqual(config['external-controller'], '127.0.0.1:10002')
        self.assertNotIn('yaml', sys.modules)

    def test_uri_subscriptions_and_invalid_dns_keep_credentials_out_of_errors(self):
        from check_proxy_plugin.profile_settings import merge_resolution
        for profile in (b'ss://fixture-secret@host.invalid:443', b'c3M6Ly9maXh0dXJl',
                        b'!!python/object/apply:os.system [not-executed]'):
            config = core_config(10001, 10002, 'fixture-key')
            self.assertEqual(merge_resolution(config, profile), (0, 0))
            self.assertNotIn('hosts', config)
        for profile in (b'dns: [fixture-secret]', b'hosts: &recursive {x: *recursive}'):
            with self.assertRaises(ValueError) as error:
                merge_resolution(core_config(10001, 10002, 'fixture-key'), profile)
            self.assertNotIn('fixture-secret', str(error.exception))

    def test_invalid_downloaded_resolution_config_releases_pending_session(self):
        service = Service(body=b'dns: [fixture-secret]\nproxies: []')
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            try:
                controller.apply(Options(core=__file__, source='url', profile=service.url + '/profile'))
                wait_for(lambda: not controller.working)
                self.assertIn('DNS', controller.message)
                self.assertNotIn('fixture-secret', controller.message)
                self.assertIsNone(controller.core.process)
                self.assertIsNone(controller.core.directory)
                self.assertFalse(controller.endpoint)
                self.assertFalse(list(Path(directory).glob('session-*')))
            finally:
                controller.stop(announce=False)
                controller.deleteLater()
                APP.processEvents()
                service.close()

    def test_dns_reasons_are_specific_and_do_not_misclassify_hostnames(self):
        from check_proxy_plugin.core import dns_reason
        for line, reason in (
            ('dns resolve failed: ipv6 disabled', 'ipv6-disabled'),
            ('dns resolve failed: ip version error', 'ip-version'),
            ('dial error: lookup private.invalid: no such host password=secret', 'nxdomain'),
            ('all DNS requests failed: context deadline exceeded', 'timeout'),
            ("dns resolve failed: couldn't find ip", 'no-address'),
        ):
            self.assertEqual(error_category(line), 'dns')
            self.assertEqual(dns_reason(line), reason)
            self.assertNotIn('secret', dns_reason(line))
        self.assertEqual(error_category('dial dnshost.private.invalid error: TLS handshake failed'), 'tls')

    def test_core_error_categories_never_return_raw_sensitive_details(self):
        for line, expected in (
            ('level=warning msg="dial error: lookup private.example: no such host secret=fixture-secret"', 'dns'),
            ('level=warning msg="error: x509 certificate failed for fixture-secret"', 'tls'),
            ('level=warning msg="error: target machine actively refused it"', 'refused'),
            ('level=info msg="configuration loaded"', ''),
        ):
            self.assertEqual(error_category(line), expected)
            self.assertNotIn('fixture-secret', error_category(line))
    def test_region_flags_follow_names_and_taiwan_always_uses_china(self):
        for name in ('台湾 01', '台灣 02', '台北 03', 'TW04', 'Taiwan 05', '🇹🇼 06', '🇺🇸台湾 07'):
            with self.subTest(name=name):
                self.assertEqual(region_code(name), 'cn')
        for name, code in (('香港 01', 'hk'), ('日本 02', 'jp'), ('SG03', 'sg'),
                           ('🇺🇸 US 01', 'us'), ('印度尼西亚', 'id'), ('UK 02', 'gb'),
                           ('自动选择', ''), ('剩余流量 52GB', '')):
            self.assertEqual(region_code(name), code)
        self.assertEqual(display_node_name('🇹🇼 台湾 01'), '台湾 01')
        self.assertFalse(flag_icon('cn').pixmap(24, 18).isNull())
    def test_provider_names_use_metadata_and_never_url_tokens(self):
        title = '示例服务商'
        encoded = 'base64:' + base64.b64encode(title.encode()).decode().rstrip('=')
        self.assertEqual(display_name('url', 'https://example.invalid/sub?token=fixture-secret', encoded), title)
        self.assertEqual(display_name('url', 'https://example.invalid/sub?token=fixture-secret'), 'example.invalid')
        self.assertEqual(display_name('url', 'https://example.invalid/sub', disposition=
                         "attachment; filename*=UTF-8''%E7%A4%BA%E4%BE%8B.yaml"), '示例')

    def test_empty_configuration_cannot_change_routing_or_start_core(self):
        with tempfile.TemporaryDirectory() as root:
            controller = Controller(root)
            original = {key: os.environ.get(key) for key in KEYS}
            try:
                controller.apply(Options(core=__file__))
                self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)
                self.assertIsNone(controller.core.process)
                self.assertFalse(controller.endpoint)
                self.assertFalse(controller.working)
            finally:
                controller.deleteLater()
                APP.processEvents()
    def test_invalid_subscriptions_are_rejected_without_echoing_credentials(self):
        for value in ('', 'file:///secret', 'http://host:70000',
                      'http://user:secret@host:80/path', 'http://host:80\r\nx:y'):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                subscription_url(value)
            self.assertNotIn('secret', str(error.exception))
        self.assertEqual(subscription_url('https://example.invalid/sub?key=fixture'),
                         'https://example.invalid/sub?key=fixture')

    def test_subscription_trims_outer_whitespace_but_explains_internal_whitespace(self):
        self.assertEqual(subscription_url(' \nhttps://example.invalid/sub?key=fixture\r\n'),
                         'https://example.invalid/sub?key=fixture')
        for whitespace in (' ', '\n', '\r\n', '\t'):
            with self.subTest(whitespace=whitespace), self.assertRaises(ValueError) as error:
                subscription_url('https://example.invalid/' + whitespace + '?token=fixture-secret')
            self.assertIn('空格或换行', str(error.exception))
            self.assertNotIn('fixture-secret', str(error.exception))

    def test_config_cannot_enable_tun_lan_or_unprotected_controller(self):
        config = core_config(10001, 10002, 'random-key')
        self.assertEqual(config['bind-address'], '127.0.0.1')
        self.assertFalse(config['allow-lan'])
        self.assertFalse(config['tun']['enable'])
        self.assertFalse(config['dns']['enable'])
        self.assertFalse(config['ipv6'])
        self.assertEqual(config['dns']['ipv6'], config['ipv6'])
        self.assertEqual(config['dns']['listen'], '')
        self.assertEqual(config['dns']['enhanced-mode'], 'redir-host')
        self.assertEqual(config['dns']['cache-max-size'], 256)
        self.assertNotIn('nameserver', config['dns'])
        self.assertNotIn('proxy-server-nameserver', config['dns'])
        self.assertEqual(config['external-controller'], '127.0.0.1:10002')
        self.assertEqual(config['secret'], 'random-key')
        self.assertFalse(config['proxy-providers']['subscription']['health-check']['enable'])
        self.assertIn('DOMAIN-SUFFIX,bilibili.com,DIRECT', config['rules'])
        self.assertEqual(config['rules'][-1], 'MATCH,DDM')

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

    def test_named_profiles_migrate_legacy_subscription_and_roundtrip_locally(self):
        with tempfile.TemporaryDirectory() as root:
            options = Options(profile='https://example.invalid/sub?token=fixture-secret', node='First')
            save_options(root, options)
            controller = Controller(root)
            try:
                self.assertEqual(len(controller.profiles), 1)
                legacy = controller.profiles[0]
                self.assertEqual(legacy.profile, options.profile)
                self.assertEqual(controller.options.profile_id, legacy.id)
                profiles = [legacy, Profile('second', '服务商 B', 'nodes', 'ss://fixture', 'Second')]
                save_profiles(root, profiles)
                self.assertEqual(load_profiles(root, controller.options), profiles)
                controller.set_auto_start(False)
                self.assertFalse(load_options(root).auto_start)
                self.assertIsNone(controller.core.process)
            finally:
                controller.stop(announce=False)
                controller.deleteLater()
                APP.processEvents()


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


class LatencyTests(unittest.TestCase):
    def setUp(self):
        self.latency = Latency()
        self.events = []
        self.latency.result.connect(lambda *values: self.events.append(values))
        self.active, self.peak = 0, 0
        self.lock = threading.Lock()
        self.auth = []
        self.queries = []
        self.details = []
        self.latency.detail.connect(lambda *values: self.details.append(values))
        owner = self
        class Api(BaseHTTPRequestHandler):
            def do_GET(self):
                with owner.lock:
                    owner.active += 1
                    owner.peak = max(owner.peak, owner.active)
                    owner.auth.append(self.headers.get('Authorization'))
                    owner.queries.append(parse_qs(urlsplit(self.path).query))
                try:
                    threading.Event().wait(.08)
                    failed = '/Bad/' in unquote(urlsplit(self.path).path)
                    body = json.dumps({'error': 'fixture-secret'} if failed else {'delay': 42}).encode()
                    self.send_response(503 if failed else 200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with owner.lock:
                        owner.active -= 1
            def log_message(self, *unused):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Api)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.latency.stop()
        self.latency.deleteLater()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        APP.processEvents()

    def test_latency_batch_is_authenticated_bounded_and_reports_real_results(self):
        self.latency.start(self.server.server_port, 'fixture-key', ['Good A', 'Good/B', 'Bad', 'Good D'])
        wait_for(lambda: not self.latency.testing)
        finished = [values for values in self.events if values[2] in ('ready', 'failed')]
        self.assertEqual(len(finished), 4)
        self.assertIn(('Bad', None, 'failed'), finished)
        self.assertIn(('Good/B', 42, 'ready'), finished)
        self.assertLessEqual(self.peak, 2)
        self.assertEqual(set(self.auth), {'Bearer fixture-key'})
        self.assertNotIn('fixture-secret', str(finished))
        self.assertEqual(self.queries[0]['url'], ['https://www.gstatic.com/generate_204'])
        self.assertEqual(self.queries[0]['timeout'], ['8000'])
        self.assertIn(('Bad', '检测失败（503）'), self.details)

    def test_cancel_stops_queued_requests_and_ignores_late_replies(self):
        self.latency.start(self.server.server_port, 'fixture-key', ['First', 'Second', 'Third'])
        self.assertLessEqual(len(self.latency.active), 2)
        self.latency.stop()
        QTest.qWait(150)
        self.assertFalse(self.latency.active)
        self.assertFalse(self.latency.pending)
        self.assertFalse(self.latency.testing)
        self.assertFalse(any(values[2] == 'ready' for values in self.events))


class ConnectionTests(unittest.TestCase):
    def test_snapshots_are_authenticated_bounded_and_only_requested_while_visible(self):
        from check_proxy_plugin.connections import ConnectionMonitor
        record = {'metadata': {'host': 'fixture.invalid', 'destinationPort': '443', 'network': 'tcp'},
                  'chains': ['First', 'DDM'], 'rule': 'Match', 'upload': 12, 'download': 1024}
        service = Service(body=json.dumps({'connections': [record] * 110}).encode())
        monitor = ConnectionMonitor(SimpleNamespace(port=service.server.server_port, secret='fixture-key'))
        rows, statuses = [], []
        monitor.snapshot.connect(lambda *values: rows.append(values))
        monitor.status.connect(statuses.append)
        try:
            monitor.set_running(True)
            QTest.qWait(40)
            self.assertFalse(service.requests)
            monitor.set_active(True)
            wait_for(lambda: any(total == 110 for _items, total in rows))
            items, total = rows[-1]
            self.assertEqual(len(items), 100)
            self.assertEqual(items[0][0], 'fixture.invalid:443')
            self.assertEqual(items[0][2], 'First → DDM')
            self.assertEqual(service.authorizations, ['Bearer fixture-key'])
            self.assertIn('Match', items[0][4])
            self.assertIn('前 100', statuses[-1])
            monitor.set_active(False)
            self.assertFalse(monitor.timer.isActive())
            self.assertIsNone(monitor.reply)
            # Late results cannot restore rows or restart polling.
            before = len(service.requests)
            monitor.refresh()
            QTest.qWait(40)
            self.assertEqual(len(service.requests), before)
        finally:
            monitor.set_active(False)
            monitor.deleteLater()
            APP.processEvents()
            service.close()

    def test_oversized_response_pauses_monitor_and_drops_raw_data(self):
        from check_proxy_plugin.connections import ConnectionMonitor, MAX_SNAPSHOT
        service = Service(body=b'x' * (MAX_SNAPSHOT + 8192))
        monitor = ConnectionMonitor(SimpleNamespace(port=service.server.server_port, secret='fixture-key'))
        statuses = []
        monitor.status.connect(statuses.append)
        try:
            monitor.set_running(True)
            monitor.set_active(True)
            wait_for(lambda: any('512 KiB' in message for message in statuses))
            self.assertFalse(monitor.timer.isActive())
            self.assertIsNone(monitor.reply)
            self.assertFalse(monitor.buffer)
        finally:
            monitor.set_active(False)
            monitor.deleteLater()
            APP.processEvents()
            service.close()

    def test_parser_excludes_urls_credentials_and_reports_bad_payload(self):
        from check_proxy_plugin.connections import connection_rows
        rows, total = connection_rows({'connections': [{'metadata': {
            'host': 'https://user:fixture-secret@host.invalid/path?token=private',
            'destinationPort': '443'}, 'chains': ['First'], 'rule': 'Match'}]})
        self.assertEqual(total, 1)
        self.assertNotIn('fixture-secret', str(rows))
        self.assertNotIn('token', str(rows))
        with self.assertRaises(ValueError):
            connection_rows({'connections': None})


class InterfaceTests(unittest.TestCase):
    def test_editor_keeps_compatibility_per_profile_and_new_profile_uses_generic_default(self):
        from check_proxy_plugin.configuration import DEFAULT_USER_AGENT
        panel = Panel(Options())
        try:
            panel.set_profiles([Profile('one', 'One', 'url', 'https://one.invalid', user_agent='clash-verge'),
                                Profile('two', 'Two', 'url', 'https://two.invalid', user_agent='Custom/2.0')], 'one')
            panel._edit_profile(0)
            self.assertEqual(panel.options().user_agent, 'clash-verge')
            panel._edit_profile(1)
            self.assertEqual(panel.options().user_agent, 'Custom/2.0')
            panel._new_profile()
            self.assertEqual(panel.options().user_agent, DEFAULT_USER_AGENT)
            panel.agent.setEditText('Provider Client/3.0')
            self.assertEqual(panel.options().user_agent, 'Provider Client/3.0')
            panel.set_busy(True)
            self.assertFalse(panel.agent.isEnabled())
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_connections_page_pauses_on_hide_and_retains_bounded_events(self):
        with tempfile.TemporaryDirectory() as root, patch.object(module.config, 'data_root', return_value=root):
            window = QWidget()
            window.resize(1100, 800)
            plugin = module.ProxyControl()
            try:
                plugin.on_load(SimpleNamespace(window=window, register_workspace_control=Mock()))
                window.show()
                plugin.toggle()
                monitor = plugin.controller.connections
                self.assertFalse(monitor.active)
                plugin.panel.navigation.setCurrentRow(3)
                self.assertTrue(monitor.active)
                self.assertFalse(monitor.timer.isActive())
                for index in range(80):
                    plugin.controller._status(f'Fixture event {index}')
                plugin.controller.core.diagnostic.emit('dns')
                self.assertEqual(len(plugin.controller.events), 64)
                self.assertIn('节点域名解析失败', plugin.panel.connections.events.toPlainText())
                plugin.popover.hide()
                self.assertFalse(monitor.active)
                self.assertIsNone(monitor.reply)
                self.assertEqual(len(plugin.controller.events), 64)
            finally:
                plugin.on_unload()
                window.deleteLater()
                APP.processEvents()

    def test_running_icon_has_a_distinct_silhouette_and_brand_matches(self):
        with tempfile.TemporaryDirectory() as root, patch.object(module.config, 'data_root', return_value=root):
            window = QWidget()
            plugin = module.ProxyControl()
            try:
                plugin.on_load(SimpleNamespace(window=window, register_workspace_control=Mock()))
                stopped = plugin.entry.icon().pixmap(28, 28).toImage()
                plugin.toggle()
                plugin.controller.runningChanged.emit(True)
                running = plugin.entry.icon().pixmap(28, 28).toImage()
                alpha = lambda image: sum(image.pixelColor(x, y).alpha() > 180
                                          for x in range(28) for y in range(28))
                self.assertGreater(alpha(running), alpha(stopped) * 1.4)
                self.assertEqual(plugin.panel.brand.pixmap().toImage(), running)
                self.assertIn('运行中', plugin.entry.toolTip())
            finally:
                plugin.on_unload()
                window.deleteLater()
                APP.processEvents()

    def test_rail_icon_distinguishes_open_and_running_and_clears_on_hide(self):
        from ddm import theme
        with tempfile.TemporaryDirectory() as root, patch.object(module.config, 'data_root', return_value=root):
            window = QWidget()
            window.resize(1100, 800)
            plugin = module.ProxyControl()
            try:
                plugin.on_load(SimpleNamespace(window=window, register_workspace_control=Mock()))
                def colors():
                    rendered = plugin.entry.icon().pixmap(24, 24).toImage()
                    return [rendered.pixelColor(x, y) for x in range(24) for y in range(24)
                            if rendered.pixelColor(x, y).alpha() > 180]
                def contains(color):
                    expected = theme.qcolor(color)
                    return any(abs(value.red() - expected.red()) < 4 and
                               abs(value.green() - expected.green()) < 4 and
                               abs(value.blue() - expected.blue()) < 4 for value in colors())
                self.assertTrue(contains(theme.TEXT2))
                window.show()
                plugin.entry.click()
                self.assertTrue(plugin.entry.isChecked())
                self.assertTrue(contains(theme.ACCENT))
                plugin.controller.runningChanged.emit(True)
                self.assertTrue(contains(theme.ACCENT))
                self.assertTrue(contains(theme.SUCCESS))
                plugin.popover.hide()
                self.assertFalse(plugin.entry.isChecked())
                self.assertTrue(contains(theme.SUCCESS))
                plugin.controller.runningChanged.emit(False)
                self.assertTrue(contains(theme.TEXT2))
            finally:
                plugin.on_unload()
                window.deleteLater()
                APP.processEvents()

    def test_modes_use_shared_segments_and_flag_display_preserves_node_identifiers(self):
        from ddm.ui import SegmentedControl, SelectControl, ToggleSwitch
        panel = Panel(Options())
        requests = []
        panel.routingRequested.connect(requests.append)
        try:
            self.assertIsInstance(panel.routing, SegmentedControl)
            self.assertIsInstance(panel.auto_start, ToggleSwitch)
            self.assertIsInstance(panel.quick_node, SelectControl)
            self.assertEqual(set(panel.routing.buttons), {'rule', 'global', 'direct'})
            panel.routing.buttons['global'].click()
            self.assertEqual(requests, ['global'])
            self.assertIn('全部经节点', panel.routing_hint.text())
            panel.set_routing('direct')
            self.assertEqual(requests, ['global'])
            self.assertEqual(panel.routing.value(), 'direct')
            panel.set_nodes(['🇹🇼 台湾 01', '🇯🇵 日本 02'], '🇹🇼 台湾 01')
            self.assertEqual(panel.model.records['🇹🇼 台湾 01']['region'], 'cn')
            self.assertEqual(panel.model.index(0).data(), '🇹🇼 台湾 01')
            self.assertEqual(panel.quick_node.itemData(0), '🇹🇼 台湾 01')
            self.assertNotIn('🇹🇼', panel.quick_node.itemText(0))
        finally:
            panel.deleteLater()
            APP.processEvents()
    def test_popover_renders_four_pages_and_clips_child_surfaces_in_both_host_themes(self):
        from ddm import theme
        previous = theme.mode()
        window = QWidget()
        window.resize(1000, 720)
        container = QWidget(window)
        container.setGeometry(window.rect())
        layout = QVBoxLayout(container)
        layout.addWidget(QWidget())
        panel = Panel(Options())
        anchor = QToolButton(window)
        popover = module.Popover(panel, window, anchor)
        try:
            panel.set_nodes(['日本 01', '香港 02'], '日本 01', {'日本 01': 'Shadowsocks'})
            panel.model.result('日本 01', 42, 'ready')
            window.show()
            popover.show()
            for mode in ('dark', 'light'):
                theme.apply(mode)
                APP.processEvents()
                self.assertFalse(popover.grab().isNull())
                self.assertIn(theme.SIDEBAR, panel.styleSheet())
                self.assertIn(theme.ACCENT, panel.styleSheet())
                self.assertFalse(popover.mask().contains(QPoint(0, 0)))
                self.assertTrue(popover.mask().contains(popover.rect().center()))
                self.assertTrue(popover.property('customControlSurface'))
                self.assertEqual(panel.pages.count(), 4)
                for page in range(4):
                    panel.navigation.setCurrentRow(page)
                    APP.processEvents()
                    self.assertFalse(popover.grab().isNull())
            panel.add_subscription.click()
            self.assertEqual(panel.navigation.currentRow(), 2)
            self.assertTrue(panel.location.isVisible())
            popover.hide()
        finally:
            popover.hide()
            window.deleteLater()
            APP.processEvents()
            theme.apply(previous)

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
                            apply.assert_called_once()
                            self.assertFalse(apply.call_args.kwargs['connect'])
                    self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)
                finally:
                    plugin.on_unload()
                    window.deleteLater()
                    APP.processEvents()

    def test_left_rail_toggles_popover_without_resizing_live_wall(self):
        with tempfile.TemporaryDirectory() as root, patch.object(module.config, 'data_root', return_value=root):
            window = QWidget()
            window.resize(1200, 900)
            container = window._content = QWidget(window)
            container.setGeometry(window.rect())
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            wall = QWidget(container)
            layout.addWidget(wall)
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
                self.assertFalse(panel.isWindow())
                self.assertIs(panel.window(), window)
                self.assertLessEqual(plugin.popover.width(), 660)
                self.assertLessEqual(plugin.popover.height(), 480)
                self.assertEqual(layout.getContentsMargins(), (0, 0, 0, 0))
                self.assertEqual(wall.x(), 0)
                self.assertTrue(wall.isVisible())
                self.assertFalse(hasattr(panel, 'address'))
                self.assertFalse(hasattr(panel, 'mode'))
                self.assertEqual(panel.navigation.currentRow(), 0)
                panel.navigation.setCurrentRow(2)
                self.assertTrue(panel.location.isVisible())
                panel.location.setText('retained')
                with patch.object(plugin.controller, 'stop') as stop:
                    button.click()
                    self.assertFalse(panel.isVisible())
                    self.assertEqual(layout.getContentsMargins(), (0, 0, 0, 0))
                    button.click()
                    self.assertEqual(panel.location.text(), 'retained')
                    panel.close_button.click()
                    button.click()
                    window.activateWindow()
                    panel.location.setFocus()
                    QTest.qWait(30)
                    QTest.keyClick(panel.location, Qt.Key_Escape)
                    self.assertFalse(plugin.popover.isVisible())
                    stop.assert_not_called()
                context.show_workspace.assert_not_called()
            finally:
                plugin.on_unload()
                window.deleteLater()
                APP.processEvents()

    def test_popover_stays_within_resized_window_and_preserves_layout_margins(self):
        with tempfile.TemporaryDirectory() as root, patch.object(module.config, 'data_root', return_value=root):
            window = QWidget()
            container = window._content = QWidget(window)
            container.setGeometry(0, 0, 1000, 700)
            layout = QVBoxLayout(container)
            layout.setContentsMargins(4, 5, 6, 7)
            layout.addWidget(QWidget())
            context = SimpleNamespace(window=window, register_workspace_control=Mock())
            plugin = module.ProxyControl()
            window.show()
            plugin.on_load(context)
            plugin.toggle()
            window.resize(500, 380)
            APP.processEvents()
            self.assertLessEqual(plugin.popover.height(), 364)
            self.assertLessEqual(plugin.popover.width(), 484)
            self.assertTrue(window.rect().contains(plugin.popover.geometry()))
            plugin.on_unload()
            self.assertEqual(layout.getContentsMargins(), (4, 5, 6, 7))
            window.deleteLater()
            APP.processEvents()

    def test_node_list_search_selection_and_delay_updates_do_not_create_row_widgets(self):
        panel = Panel(Options())
        panel.set_nodes(['香港 01', '日本 02', '日本 03'], '香港 01', {'日本 02': 'Shadowsocks'})
        panel.search.setText('日本')
        self.assertEqual(panel.model.rowCount(), 2)
        self.assertEqual(panel.model.data(panel.model.index(0)), '日本 02')
        panel.model.result('日本 02', 123, 'ready')
        self.assertEqual(panel.model.data(panel.model.index(0), NODE_ROLE)['delay'], 123)
        panel.model.select('日本 03')
        self.assertFalse(panel.model.data(panel.model.index(0), ACTIVE_ROLE))
        self.assertTrue(panel.model.data(panel.model.index(1), ACTIVE_ROLE))
        self.assertIsNone(panel.nodes.indexWidget(panel.model.index(0)))
        panel.search.setText('no-match')
        self.assertEqual(panel.model.rowCount(), 0)
        self.assertFalse(panel.test.isEnabled())
        panel.set_testing(True)
        self.assertTrue(panel.test.isEnabled())
        panel.set_testing(False)
        self.assertFalse(panel.test.isEnabled())
        panel.deleteLater()
        APP.processEvents()

    def test_import_confirmation_is_local_and_empty_start_only_guides_to_settings(self):
        panel = Panel(Options(core=__file__))
        requests = []
        panel.applyRequested.connect(requests.append)
        panel.show()
        try:
            panel.apply.click()
            self.assertEqual(panel.navigation.currentRow(), 2)
            self.assertFalse(requests)
            self.assertFalse(panel.apply.isVisible())
            panel.import_button.click()
            self.assertIn('HTTP(S)', panel.import_status.text())
            panel.location.setText(' https://example.invalid/sub?token=fixture ')
            self.assertNotIn('HTTP(S)', panel.status.text())
            self.assertIn('尚未确认', panel.import_status.text())
            QTest.keyClick(panel.location, Qt.Key_Return)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].profile, 'https://example.invalid/sub?token=fixture')
            self.assertEqual(requests[0].source, 'url')
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_import_progress_failure_retry_and_success_follow_controller_signal_order(self):
        panel = Panel(Options(core=__file__))
        panel.navigation.setCurrentRow(2)
        panel.location.setText('https://example.invalid/sub')
        requests = []
        panel.applyRequested.connect(requests.append)
        try:
            panel.import_button.click()
            # apply() first stops the old core before starting the new download.
            panel.set_nodes([], '')
            panel.set_busy(False)
            panel.set_busy(True)
            panel.set_status('正在更新订阅…')
            for widget in (panel.source, panel.location, panel.profile, panel.import_button, panel.runtime):
                self.assertFalse(widget.isEnabled())
            panel._confirm_import()
            self.assertEqual(len(requests), 1)
            self.assertEqual(panel.import_status.text(), '正在更新订阅…')
            panel.set_nodes([], '')
            panel.set_busy(False)
            panel.set_status('下载失败，请重试')
            self.assertEqual(panel.navigation.currentRow(), 2)
            self.assertEqual(panel.import_status.text(), '下载失败，请重试')
            self.assertEqual(panel.location.text(), 'https://example.invalid/sub')
            self.assertTrue(panel.import_button.isEnabled())
            self.assertEqual(panel.state.text(), '未启动')
            panel.import_button.click()
            panel.set_nodes([], '')
            panel.set_busy(False)
            panel.set_busy(True)
            panel.set_nodes(['First', 'Second'], 'First')
            panel.set_busy(False)
            panel.set_status('已导入 2 个节点，代理已启动')
            self.assertEqual(panel.navigation.currentRow(), 1)
            self.assertEqual(panel.count.text(), '2 个节点')
            self.assertIn('2 个节点', panel.import_status.text())
            self.assertFalse(panel._importing)
            self.assertEqual(panel.import_button.text(), '导入订阅并启动')
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_file_and_node_import_keep_the_correct_input_and_validation(self):
        panel = Panel(Options(core=__file__))
        requests = []
        panel.applyRequested.connect(requests.append)
        try:
            panel.source.setCurrentIndex(panel.source.findData('file'))
            panel.location.setText(__file__)
            panel.import_button.click()
            self.assertEqual(requests[-1].source, 'file')
            self.assertEqual(requests[-1].profile, __file__)
            panel.source.setCurrentIndex(panel.source.findData('nodes'))
            panel.profile.setPlainText('ss://first\nss://second')
            panel.import_button.click()
            self.assertEqual(requests[-1].source, 'nodes')
            self.assertEqual(requests[-1].profile, 'ss://first\nss://second')
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_popover_is_anchored_and_outside_click_retains_draft_without_stopping(self):
        window = QWidget()
        window.resize(1100, 800)
        anchor = QToolButton(window)
        anchor.setGeometry(12, 210, 30, 30)
        panel = Panel(Options())
        popover = module.Popover(panel, window, anchor)
        anchor.clicked.connect(lambda: popover.hide() if popover.isVisible() else popover.show())
        stops = []
        panel.stopRequested.connect(lambda: stops.append(True))
        window.show()
        try:
            anchor.click()
            self.assertEqual(popover.x(), anchor.geometry().right() + 9)
            self.assertEqual(popover.y(), anchor.y() - 8)
            self.assertFalse(popover.isWindow())
            panel.navigation.setCurrentRow(2)
            panel.location.setText('https://example.invalid/draft')
            QTest.mouseClick(panel.location, Qt.LeftButton)
            self.assertTrue(popover.isVisible())
            QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(1050, 750))
            self.assertFalse(popover.isVisible())
            anchor.click()
            self.assertEqual(panel.location.text(), 'https://example.invalid/draft')
            anchor.click()
            self.assertFalse(popover.isVisible())
            self.assertFalse(stops)
        finally:
            popover.hide()
            window.deleteLater()
            APP.processEvents()

    def test_node_cards_use_two_columns_and_one_column_in_narrow_windows(self):
        panel = Panel(Options())
        panel.resize(658, 478)
        panel.set_nodes([f'Node {number}' for number in range(12)], 'Node 0')
        panel.navigation.setCurrentRow(1)
        panel.show()
        try:
            QTest.qWait(30)
            first = panel.nodes.visualRect(panel.model.index(0))
            second = panel.nodes.visualRect(panel.model.index(1))
            self.assertEqual(first.y(), second.y())
            self.assertGreater(second.x(), first.x())
            panel.resize(380, 478)
            QTest.qWait(30)
            self.assertGreater(panel.nodes.visualRect(panel.model.index(1)).y(),
                               panel.nodes.visualRect(panel.model.index(0)).y())
            self.assertIsNone(panel.nodes.indexWidget(panel.model.index(0)))
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_overview_starts_saved_profile_and_keeps_unconfirmed_edits_separate(self):
        panel = Panel(Options(core=__file__, profile_id='a', profile_name='服务商 A',
                              profile='https://example.invalid/a'))
        profiles = [Profile('a', '服务商 A', 'url', 'https://example.invalid/a'),
                    Profile('b', '服务商 B', 'url', 'https://example.invalid/b')]
        requests, activations, changes = [], [], []
        panel.applyRequested.connect(requests.append)
        panel.profileRequested.connect(activations.append)
        panel.autoStartRequested.connect(changes.append)
        try:
            panel.set_profiles(profiles, 'a')
            panel.profiles_list.setCurrentRow(1)
            self.assertEqual(panel.options().profile_id, 'b')
            self.assertFalse(requests)
            panel.add_profile.click()
            panel.location.setText('https://example.invalid/unconfirmed')
            panel.apply.click()
            self.assertEqual(requests[-1].profile_id, 'a')
            self.assertEqual(requests[-1].profile, 'https://example.invalid/a')
            panel.quick_profile.setCurrentIndex(1)
            panel.activate_profile.click()
            self.assertEqual(activations, ['b'])
            panel.auto_start.click()
            self.assertEqual(changes, [False])
            panel.set_nodes(['First', 'Second'], 'First')
            panel.select_node('Second')
            self.assertEqual(panel.quick_node.currentText(), 'Second')
            self.assertEqual(panel.model.active, 'Second')
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_disabled_auto_start_does_not_launch_core_when_loading_plugin(self):
        with tempfile.TemporaryDirectory() as root, patch.object(module.config, 'data_root', return_value=root):
            save_options(Path(root) / 'utils/proxy_control', Options(enabled=True, auto_start=False,
                         profile='https://example.invalid/sub'))
            window = QWidget()
            plugin = module.ProxyControl()
            try:
                with patch.object(Controller, 'apply') as apply:
                    plugin.on_load(SimpleNamespace(window=window, register_workspace_control=Mock()))
                    apply.assert_called_once()
                    self.assertFalse(apply.call_args.kwargs['connect'])
                self.assertIsNone(plugin.controller.core.process)
            finally:
                plugin.on_unload()
                window.deleteLater()
                APP.processEvents()

    def test_configuration_cards_open_editor_and_add_new_profile_without_another_window(self):
        panel = Panel(Options(core=__file__))
        panel.resize(658, 478)
        panel.set_profiles([Profile('a', '服务商 A', 'url', 'https://example.invalid/a')], 'a')
        panel.navigation.setCurrentRow(2)
        panel.show()
        try:
            QTest.qWait(30)
            self.assertEqual(panel.title.text(), '配置')
            self.assertEqual(panel.configuration_views.currentIndex(), 0)
            self.assertFalse(panel.profiles_list.verticalScrollBar().isVisible())
            card = panel.profiles_list.visualItemRect(panel.profiles_list.item(0))
            QTest.mouseClick(panel.profiles_list.viewport(), Qt.LeftButton, pos=card.center())
            self.assertEqual(panel.configuration_views.currentIndex(), 1)
            self.assertTrue(panel.location.isVisible())
            self.assertEqual(panel.location.text(), 'https://example.invalid/a')
            self.assertIs(panel.location.window(), panel)
            panel.back_to_profiles.click()
            self.assertEqual(panel.configuration_views.currentIndex(), 0)
            panel.add_profile.click()
            self.assertEqual(panel.configuration_views.currentIndex(), 1)
            self.assertEqual(panel.options().profile_id, '')
            self.assertEqual(panel.location.text(), '')
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_profile_cards_scroll_internally_and_switch_is_keyboard_operable(self):
        panel = Panel(Options(core=__file__))
        panel.resize(658, 478)
        profiles = [Profile(str(i), f'服务商 {i}', 'url', f'https://example.invalid/{i}') for i in range(12)]
        panel.set_profiles(profiles, '0')
        panel.navigation.setCurrentRow(2)
        panel.show()
        changes = []
        panel.autoStartRequested.connect(changes.append)
        try:
            QTest.qWait(30)
            self.assertTrue(panel.profiles_list.verticalScrollBar().isVisible())
            first = panel.profiles_list.visualItemRect(panel.profiles_list.item(0))
            second = panel.profiles_list.visualItemRect(panel.profiles_list.item(1))
            self.assertEqual(first.y(), second.y())
            self.assertGreater(second.x(), first.x())
            panel.navigation.setCurrentRow(0)
            panel.auto_start.setFocus()
            QTest.keyClick(panel.auto_start, Qt.Key_Space)
            self.assertEqual(changes, [False])
            self.assertFalse(panel.auto_start.isChecked())
            panel.resize(380, 478)
            QTest.qWait(30)
            first_card, second_card = panel.dashboard_cards[:2]
            self.assertEqual(first_card.x(), second_card.x())
            self.assertGreater(second_card.y(), first_card.y())
        finally:
            panel.deleteLater()
            APP.processEvents()

    def test_cached_nodes_can_be_selected_while_service_is_stopped(self):
        with tempfile.TemporaryDirectory() as root, patch.object(module.config, 'data_root', return_value=root):
            directory = Path(root) / 'utils/proxy_control'
            save_options(directory, Options(core=__file__, profile_id='fixture', source='nodes', profile='fixture'))
            save_profiles(directory, [Profile('fixture', '服务商', 'nodes', 'fixture', 'First',
                                             {'First': 'Http', 'Second': 'Http'})])
            window = QWidget()
            plugin = module.ProxyControl()
            try:
                plugin.on_load(SimpleNamespace(window=window, register_workspace_control=Mock()))
                plugin.toggle()
                panel = plugin.panel
                self.assertEqual(panel.model.visible, ['First', 'Second'])
                self.assertEqual(panel.state.text(), '未启动')
                self.assertTrue(panel.test.isEnabled())
                panel.nodeRequested.emit('Second')
                self.assertEqual(panel.model.active, 'Second')
                self.assertEqual(load_options(directory).node, 'Second')
                self.assertIsNone(plugin.controller.core.process)
                self.assertFalse(plugin.controller.endpoint)
                panel.routingRequested.emit('global')
                self.assertEqual(panel.routing.value(), 'global')
            finally:
                plugin.on_unload()
                window.deleteLater()
                APP.processEvents()


@unittest.skipUnless(args.core, 'Pass --core for actual Mihomo process tests')
class RealCoreTests(unittest.TestCase):
    def test_subscription_requests_clash_format_before_loading_profile(self):
        profile = json.dumps({'proxies': [{'name': 'NegotiatedNode', 'type': 'http',
            'server': 'format.fixture.invalid', 'port': self.first.server.server_port}],
            'hosts': {'format.fixture.invalid': '127.0.0.1'}})
        def response(headers):
            return profile.encode() if headers.get('User-Agent', '') == 'clash-verge' else profile.replace(
                '"hosts": {"format.fixture.invalid": "127.0.0.1"}', '"hosts": {}').encode()
        subscription = Service(body=response)
        try:
            self.start(source='url', profile=subscription.url + '/profile', user_agent='clash-verge')
            self.assertEqual(requests.get('http://fixture.invalid/live', timeout=5).content, b'first')
            self.assertEqual(subscription.user_agents[-1], 'clash-verge')
        finally:
            subscription.close()

    def test_full_profile_hosts_are_used_for_node_resolution(self):
        profile = json.dumps({'proxies': [{'name': 'HostsNode', 'type': 'http',
            'server': 'hosts.fixture.invalid', 'port': self.first.server.server_port}],
            'hosts': {'hosts.fixture.invalid': '127.0.0.1'}})
        self.start(profile=profile)
        response = requests.get('http://fixture.invalid/live', timeout=7)
        self.assertEqual(response.content, b'first')

    def test_switching_service_profiles_restores_each_download_identity(self):
        subscription = Service(body=self.profile.encode())
        try:
            self.start(source='url', profile=subscription.url + '/one', profile_name='One', user_agent='clash-verge')
            first_id = self.controller.options.profile_id
            self.start(source='url', profile=subscription.url + '/two', profile_name='Two', user_agent='Custom/2.0')
            self.assertEqual(subscription.user_agents[-1], 'Custom/2.0')
            self.controller.activate_profile(first_id)
            wait_for(lambda: not self.controller.working, timeout=32)
            self.assertTrue(self.controller.endpoint, (self.controller.message, self.core_failure))
            self.assertEqual(subscription.user_agents, ['clash-verge', 'Custom/2.0', 'clash-verge'])
            self.assertEqual(self.controller.options.user_agent, 'clash-verge')
        finally:
            subscription.close()

    def test_connections_snapshot_shows_actual_core_route_and_stops_with_service(self):
        self.start()
        monitor = self.controller.connections
        snapshots = []
        monitor.snapshot.connect(lambda *values: snapshots.append(values))
        connection = socket.create_connection(('127.0.0.1', urlsplit(self.controller.endpoint).port), timeout=3)
        try:
            connection.sendall(b'CONNECT fixture.invalid:443 HTTP/1.1\r\nHost: fixture.invalid:443\r\n\r\n')
            self.assertIn(b'200', connection.recv(4096))
            monitor.set_active(True)
            wait_for(lambda: any(total > 0 for _rows, total in snapshots))
            row = next(row for row in snapshots[-1][0] if 'fixture.invalid' in row[0])
            self.assertIn('First', row[2])
            self.assertTrue(row[4])
            self.controller.stop()
            self.assertFalse(monitor.timer.isActive())
            self.assertIsNone(monitor.reply)
            self.assertFalse(snapshots[-1][0])
        finally:
            connection.close()

    def test_named_proxy_server_resolves_through_internal_dns_without_dns_listener(self):
        queries = []
        class DnsHandler(socketserver.BaseRequestHandler):
            def handle(self):
                data, server_socket = self.request
                offset, labels = 12, []
                while data[offset]:
                    length = data[offset]
                    labels.append(data[offset + 1:offset + 1 + length].decode())
                    offset += length + 1
                qtype = struct.unpack('!H', data[offset + 1:offset + 3])[0]
                queries.append('.'.join(labels))
                answer = (b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 30, 4) + b'\x7f\x00\x00\x01') if (
                    qtype == 1 and labels[0] == 'node') else b''
                if qtype == 28 and labels[0] == 'node6':
                    answer = b'\xc0\x0c' + struct.pack('!HHIH', 28, 1, 30, 16) + socket.inet_pton(socket.AF_INET6, '::1')
                header = data[:2] + struct.pack('!HHHHH', 0x8180, 1, int(bool(answer)), 0, 0)
                server_socket.sendto(header + data[12:offset + 5] + answer, self.client_address)
        server = socketserver.UDPServer(('127.0.0.1', 0), DnsHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        dns = {'enable': True, 'nameserver': [f'udp://127.0.0.1:{server.server_address[1]}']}
        profile = json.dumps({'proxies': [{'name': 'NamedNode', 'type': 'http', 'server': 'node.fixture.invalid',
                             'port': self.first.server.server_port}], 'dns': dns})
        try:
            self.start(profile=profile)
            with socket.create_connection(('127.0.0.1', urlsplit(self.controller.endpoint).port), timeout=.5):
                pass
            response = requests.get('http://fixture.invalid/live', timeout=5)
            self.assertEqual(response.content, b'first')
            self.assertIn('node.fixture.invalid', queries)
            import psutil
            connections = psutil.Process(self.controller.core.process.processId()).net_connections(kind='inet')
            self.assertFalse(any(connection.laddr.port in (53, 1053) for connection in connections))
            self.controller.stop()
            empty_profile = profile.replace('node.fixture.invalid', 'empty.fixture.invalid')
            self.start(profile=empty_profile)
            response = requests.get('http://fixture.invalid/live', timeout=5)
            self.assertEqual(response.status_code, 502)
            wait_for(lambda: self.controller.core.last_dns_reason == 'no-address')
            self.controller.stop()
            ipv6_service = Service(b'ipv6-only', ipv6=True)
            try:
                ipv6_profile = json.dumps({'proxies': [{'name': 'IPv6Only', 'type': 'http',
                    'server': 'node6.fixture.invalid', 'port': ipv6_service.server.server_port}],
                    'dns': dns, 'ipv6': True})
                self.start(profile=ipv6_profile)
                response = requests.get('http://fixture.invalid/live', timeout=5)
                self.assertEqual(response.content, b'ipv6-only')
                self.assertIn('node6.fixture.invalid', queries)
                self.controller.stop()
            finally:
                ipv6_service.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    def test_all_failed_latency_classifies_core_connection_error_without_enabling_proxy(self):
        profile = json.dumps({'proxies': [{'name': 'First', 'type': 'http',
                             'server': '127.0.0.1', 'port': free_port()}]})
        self.start(profile=profile)
        self.controller.stop()
        original = {key: os.environ.get(key) for key in KEYS}
        start = self.controller.latency.start
        def local_start(port, secret, names, **_options):
            start(port, secret, names, target='http://fixture.invalid/probe')
        with patch.object(self.controller.latency, 'start', side_effect=local_start), \
                self.assertLogs('ddm.proxy_control', level='INFO') as logs:
            self.controller.test_nodes(['First'])
            wait_for(lambda: self.controller.core.process is None and
                     '当前预选节点诊断' in self.controller.message, timeout=15)
        self.assertIn('连接被拒绝', self.controller.message)
        self.assertIn('不据此判定全部节点不可用', self.controller.message)
        self.assertFalse(self.controller.endpoint)
        self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)
        self.assertTrue(any('kind=refused' in line for line in logs.output))
        self.assertNotIn('fixture.invalid', str(logs.output))

    def test_diagnostic_probe_can_be_cancelled_without_late_restart(self):
        profile = json.dumps({'proxies': [{'name': 'First', 'type': 'http',
                             'server': '127.0.0.1', 'port': free_port()}]})
        self.start(profile=profile)
        self.controller.stop()
        cancelled = []
        def cancel_probe(_reply):
            cancelled.append(True)
            self.controller.cancel_tests()
        self.controller.probe.manager.finished.connect(cancel_probe)
        start = self.controller.latency.start
        def local_start(port, secret, names, **_options):
            start(port, secret, names, target='http://fixture.invalid/probe')
        with patch.object(self.controller.latency, 'start', side_effect=local_start):
            self.controller.test_nodes(['First'])
            wait_for(lambda: bool(cancelled))
        QTest.qWait(100)
        self.assertIsNone(self.controller.core.process)
        self.assertIsNone(self.controller.probe.reply)
        self.assertFalse(self.controller.working)
        self.assertFalse(self.controller.endpoint)
        self.assertIn('诊断已取消', self.controller.message)
    def test_latency_works_while_stopped_without_enabling_proxy_and_releases_core(self):
        self.start()
        self.controller.stop()
        original = {key: os.environ.get(key) for key in KEYS}
        results, running, testing = [], [], []
        self.controller.latency.result.connect(lambda *values: results.append(values))
        self.controller.runningChanged.connect(running.append)
        self.controller.testingChanged.connect(testing.append)
        start = self.controller.latency.start
        def local_start(port, secret, names, **_options):
            start(port, secret, names, target='http://fixture.invalid/probe')
        with patch.object(self.controller.latency, 'start', side_effect=local_start), \
                patch.object(self.controller.environment, 'apply') as apply:
            self.controller.test_nodes(['First', 'Second'])
            self.assertTrue(testing[-1])
            wait_for(lambda: any(value[2] == 'ready' for value in results) and
                     not self.controller.latency.testing and self.controller.core.process is None)
            apply.assert_not_called()
        self.assertEqual(len([value for value in results if value[2] == 'ready']), 2)
        self.assertNotIn(True, running)
        self.assertFalse(self.controller.endpoint)
        self.assertFalse(load_options(self.temp.name).enabled)
        self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)
        self.assertIn('2/2', self.controller.message)
        self.assertFalse(testing[-1])
        self.assertFalse(self.controller.timer.isActive())

    def test_offline_latency_can_be_cancelled_while_preparing_core(self):
        self.start()
        self.controller.stop()
        self.controller.test_nodes(['First', 'Second'])
        self.assertIsNotNone(self.controller._test_names)
        self.controller.cancel_tests()
        QTest.qWait(150)
        self.assertIsNone(self.controller._test_names)
        self.assertIsNone(self.controller.core.process)
        self.assertFalse(self.controller.working)
        self.assertFalse(self.controller.latency.testing)
        self.assertFalse(self.controller.endpoint)

    def test_offline_latency_cancellation_releases_core_and_keeps_completed_results(self):
        self.start()
        self.controller.stop()
        results, lists, cancelled = [], [], []
        self.controller.latency.result.connect(lambda *values: results.append(values))
        self.controller.nodes.connect(lambda *values: lists.append(values))
        def cancel_after_result(name, _delay, state):
            if name == 'First' and state == 'ready' and not cancelled:
                cancelled.append(len(lists))
                self.controller.cancel_tests()
        self.controller.latency.result.connect(cancel_after_result)
        start = self.controller.latency.start
        def local_start(port, secret, names, **_options):
            start(port, secret, names, target='http://fixture.invalid/probe')
        with patch.object(self.controller.latency, 'start', side_effect=local_start):
            self.controller.test_nodes(['First', 'Second'])
            wait_for(lambda: bool(cancelled))
        self.assertIsNone(self.controller.core.process)
        self.assertFalse(self.controller.endpoint)
        self.assertFalse(self.controller.latency.testing)
        self.assertEqual(len(lists), cancelled[0])
        self.assertFalse(any(value[0] == 'First' and value[2] == 'idle' for value in results))
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = Controller(self.temp.name)
        self.core_trace = []
        self.core_failure = {}
        core = self.controller.core
        read_output, failure = core._read_output, core._failure
        def capture_output(process):
            self.core_trace.append(bytes(process.peek(8192)).decode('utf-8', errors='replace'))
            self.core_trace[:] = self.core_trace[-8:]
            read_output(process)
        def capture_failure(process, message):
            self.core_failure = {'listener': core._listener_ready, 'types': core._types_loaded,
                                 'global': core._global_selected, 'nodes': len(core.nodes),
                                 'process': str(process.state()) if process else 'none',
                                 'output': ''.join(self.core_trace)[-4096:]}
            failure(process, message)
        core._read_output, core._failure = capture_output, capture_failure
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
        wait_for(lambda: bool(self.controller.endpoint) or not self.controller.working, timeout=32)
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

    def test_core_reports_protocol_and_serves_actual_latency_checks(self):
        self.start()
        self.assertEqual(self.controller.core.types['First'], 'Http')
        results = []
        self.controller.latency.result.connect(lambda *values: results.append(values))
        self.controller.latency.start(self.controller.core.port, self.controller.core.secret,
                                      ['First', 'Second'], target='http://fixture.invalid/probe')
        wait_for(lambda: not self.controller.latency.testing)
        successful = [values for values in results if values[2] == 'ready']
        self.assertEqual(len(successful), 2)
        self.assertTrue(all(type(values[1]) is int for values in successful))

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

    def test_catalog_download_names_provider_without_changing_network_and_retains_nodes(self):
        title = 'base64:' + base64.b64encode('服务商名称'.encode()).decode()
        subscription = Service(body=self.profile.encode(), headers={'profile-title': title})
        original = {key: os.environ.get(key) for key in KEYS}
        try:
            options = Options(core=str(args.core.resolve()), source='url', profile=subscription.url + '/sub?token=fixture')
            with patch.object(self.controller.environment, 'apply') as apply:
                self.controller.apply(options, connect=False)
                wait_for(lambda: not self.controller.working)
                apply.assert_not_called()
            self.assertFalse(self.controller.endpoint)
            self.assertIsNone(self.controller.core.process)
            self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)
            self.assertEqual(self.controller.options.profile_name, '服务商名称')
            self.assertEqual(self.controller.node_snapshot()[0], ['First', 'Second'])
            self.controller.select('Second')
            self.assertEqual(self.controller.node_snapshot()[1], 'Second')
            self.controller.apply(self.controller.options)
            wait_for(lambda: not self.controller.working, timeout=32)
            self.assertTrue(self.controller.endpoint, (self.controller.message, self.core_failure))
            self.assertEqual(self.controller.core.current, 'Second')
            self.controller.stop()
            self.assertEqual(self.controller.node_snapshot()[0], ['First', 'Second'])
            self.assertEqual(self.controller.node_snapshot()[1], 'Second')
            self.assertFalse(load_options(self.temp.name).enabled)
            self.assertEqual({key: os.environ.get(key) for key in KEYS}, original)
            restored = Controller(self.temp.name)
            try:
                self.assertEqual(restored.node_snapshot()[0], ['First', 'Second'])
                self.assertEqual(restored.node_snapshot()[1], 'Second')
                self.assertIsNone(restored.core.process)
            finally:
                restored.deleteLater()
                APP.processEvents()
            self.controller.apply(replace(self.controller.options, profile_name='手动命名'), connect=False)
            wait_for(lambda: not self.controller.working)
            self.assertEqual(self.controller.options.profile_name, '手动命名')
        finally:
            subscription.close()

    def test_rule_global_and_direct_modes_route_actual_requests_and_switch_without_restarting(self):
        from check_proxy_plugin.configuration import core_config as make_config
        def fixture_config(*args, **kwargs):
            config = make_config(*args, **kwargs)
            config['hosts'] = {'fixture.bilibili.com': '127.0.0.1'}
            return config
        target = Service(b'direct-target')
        try:
            with patch('check_proxy_plugin.core.core_config', side_effect=fixture_config):
                self.start()
            endpoint = self.controller.endpoint
            pid = self.controller.core.process.processId()
            domestic = f'http://fixture.bilibili.com:{target.server.server_port}/live'
            def content(url):
                return requests.get(url, proxies={'http': endpoint}, timeout=5).content
            self.assertEqual(content(domestic), b'direct-target')
            self.assertEqual(content('http://fixture.invalid/live'), b'first')
            self.controller.set_mode('global')
            wait_for(lambda: self.controller.options.routing == 'global')
            self.assertEqual(content(domestic), b'first')
            self.controller.set_mode('direct')
            wait_for(lambda: self.controller.options.routing == 'direct')
            self.assertEqual(content(domestic), b'direct-target')
            self.assertEqual(self.controller.core.process.processId(), pid)
            self.assertEqual(load_options(self.temp.name).routing, 'direct')
            secret = self.controller.core.secret
            self.controller.core.secret = 'invalid-fixture-secret'
            self.controller.set_mode('global')
            wait_for(lambda: self.controller.core.mode_reply is None)
            self.controller.core.secret = secret
            self.assertEqual(self.controller.options.routing, 'direct')
            self.assertEqual(self.controller.core.process.processId(), pid)
        finally:
            target.close()

    def test_panel_confirmation_downloads_subscription_and_opens_imported_nodes(self):
        subscription = Service(body=self.profile.encode())
        panel = Panel(Options(core=str(args.core.resolve())))
        panel.applyRequested.connect(self.controller.apply)
        panel.stopRequested.connect(self.controller.stop)
        self.controller.status.connect(panel.set_status)
        self.controller.busy.connect(panel.set_busy)
        self.controller.nodes.connect(panel.set_nodes)
        self.controller.profilesChanged.connect(panel.set_profiles)
        try:
            panel.navigation.setCurrentRow(2)
            panel.location.setText(subscription.url + '/subscription?token=fixture')
            panel.import_button.click()
            self.assertFalse(panel.location.isEnabled())
            wait_for(lambda: bool(self.controller.endpoint) or not self.controller.working)
            self.assertTrue(self.controller.endpoint, self.controller.message)
            self.assertEqual(panel.navigation.currentRow(), 1)
            self.assertEqual(panel.model.visible, ['First', 'Second'])
            self.assertIn('已导入 2 个节点', panel.status.text())
            self.assertEqual(panel.status.text(), panel.import_status.text())
            self.assertTrue(panel.location.isEnabled())
            self.assertEqual(requests.get('http://fixture.invalid/live', timeout=5).content, b'first')
        finally:
            self.controller.stop()
            panel.deleteLater()
            APP.processEvents()
            subscription.close()

    def test_named_provider_switch_preserves_nodes_and_releases_previous_core(self):
        self.start(profile_name='服务商 A')
        first_id = self.controller.options.profile_id
        self.controller.select('Second')
        wait_for(lambda: self.controller.core.current == 'Second')
        self.start(profile_name='服务商 B')
        second_pid = self.controller.core.process.processId()
        self.assertEqual(len(self.controller.profiles), 2)
        self.controller.activate_profile(first_id)
        wait_for(lambda: self.controller.core.current == 'Second' and not self.controller.working)
        self.assertEqual(self.controller.options.profile_name, '服务商 A')
        self.assertEqual(requests.get('http://fixture.invalid/live', timeout=5).content, b'second')
        import psutil
        self.assertFalse(psutil.pid_exists(second_pid))
        self.controller.set_auto_start(False)
        self.controller.stop()
        self.assertEqual(load_profiles(self.temp.name, self.controller.options), self.controller.profiles)
        self.assertFalse(load_options(self.temp.name).auto_start)

    def test_failed_provider_import_keeps_previous_saved_configuration(self):
        self.start(profile_name='服务商 A')
        saved = replace(self.controller.options)
        self.controller.apply(Options(core=str(args.core.resolve()), source='nodes',
                                      profile='<html>not nodes</html>', profile_name='无效服务商'))
        self.controller.core.deadline = time.monotonic() + 1
        wait_for(lambda: not self.controller.working, timeout=5)
        self.assertEqual(self.controller.options, saved)
        self.assertEqual(load_options(self.temp.name), saved)
        self.assertEqual(len(self.controller.profiles), 1)
        self.assertIsNone(self.controller.pending)
        self.assertIsNone(self.controller.core.process)

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
        while encrypted == mixed:
            encrypted = free_port()
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
            if self.server_process.state() == QProcess.NotRunning:
                self.fail(f'Encrypted fixture core exited before listening: {self.server_process.exitCode()}')
            try:
                with socket.create_connection(('127.0.0.1', mixed), timeout=0.1):
                    return True
            except OSError:
                return False
        wait_for(listening, timeout=32)
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
