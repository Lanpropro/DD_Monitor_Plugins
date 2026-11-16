"""Offline watch-later regressions against the requested host, without real accounts."""
import argparse
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--archive', type=Path)
args, rest = parser.parse_known_args()
sys.argv = [sys.argv[0], *rest]
ROOT = Path(__file__).resolve().parents[1]
HOST = args.host.resolve()
ARCHIVE = args.archive.resolve() if args.archive else None
sys.path.insert(0, str(HOST))
os.chdir(HOST)
os.environ['DDM_NO_SAVE'] = '1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication, QLineEdit
from ddm import plugins as api
from ddm.app import MainWindow

APP = QApplication.instance() or QApplication([])
installation = tempfile.TemporaryDirectory() if ARCHIVE else None
PLUGIN_DIR = Path(installation.name) if installation else ROOT / 'plugins'
loader = api.PluginManager(plugins_dir=str(PLUGIN_DIR), enabled=[])
if ARCHIVE:
    assert loader.install_zip(str(ARCHIVE)) == 'bili_watchlater'
    assert not loader.plugins
entry = loader._load_module('bili_watchlater', str(PLUGIN_DIR / 'bili_watchlater/plugin.py'))
plugin_module = sys.modules[type(entry).__module__]
models = importlib.import_module('ddm_plugin_bili_watchlater.models')
streams = importlib.import_module('ddm_plugin_bili_watchlater.vod_stream')
http_api = importlib.import_module('ddm_plugin_bili_watchlater.bili_api')


class ProtocolTests(unittest.TestCase):
    def test_headless_load_and_unload(self):
        manager = api.PluginManager(plugins_dir=str(PLUGIN_DIR), enabled=['bili_watchlater'])
        manager.load()
        self.assertEqual([p.context.name for p in manager.plugins], ['bili_watchlater'])
        self.assertFalse([reason for key, reason in manager.skipped if key == 'bili_watchlater'])
        manager.unload()

    def test_risk_response_stops_list_fallback(self):
        for code in (-352, -401, -403):
            with self.subTest(code=code):
                session = Mock()
                session.get.return_value = SimpleNamespace(
                    status_code=200, json=lambda: {'code': code, 'message': 'blocked'})
                with self.assertRaises(http_api.BiliAPIError):
                    http_api.BiliWatchLaterAPI(session).list_all({'SESSDATA': 'fixture'})
                self.assertEqual(session.get.call_count, 1)

    def test_risk_response_stops_stream_fallback(self):
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200,
                                                   json=lambda: {'code': -352})
        with self.assertRaises(streams.VodStreamError):
            streams.resolve_vod('BV1xx411c7mD', cid=101, session=session)
        self.assertEqual(session.get.call_count, 1)

    def test_voucher_stops_stream_fallback(self):
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200,
            json=lambda: {'code': 0, 'data': {'v_voucher': 'fixture'}})
        with self.assertRaises(streams.VodStreamError):
            streams.resolve_vod('BV1xx411c7mD', cid=101, session=session)
        self.assertEqual(session.get.call_count, 1)

    def test_second_part_durl_uses_second_cid(self):
        session = Mock()
        def get(url, **kwargs):
            payload = ({'code': 0, 'data': [{'cid': 101}, {'cid': 202}]}
                       if url == streams.PAGELIST_ENDPOINT else
                       {'code': 0, 'data': {'durl': [{'url': 'https://a.bilivideo.com/v.mp4'}]}})
            return SimpleNamespace(status_code=200, json=lambda: payload)
        session.get.side_effect = get
        result = streams.resolve_vod('BV1xx411c7mD', page=1, session=session)
        self.assertEqual(result.cid, 202)
        self.assertEqual(session.get.call_args.kwargs['params']['cid'], 202)

    def test_second_part_web_fallback_has_page_parameter(self):
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200, text=(
            '<script>window.__playinfo__={"data":{"durl":'
            '[{"url":"https://a.bilivideo.com/v.mp4"}]}}</script>'))
        streams._strategy_playinfo(session, 'BV1xx411c7mD', {}, None, page=1)
        self.assertEqual(session.get.call_args.kwargs['params'], {'p': 2})


class HostTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(api, 'DEFAULT_PLUGINS_DIR', str(PLUGIN_DIR)),
                        patch('ddm.config.save'),
                        patch('ddm.account_store.AccountStore.load_account', return_value={})]
        for name in ('refresh_account', 'refresh_status', 'refresh_stats',
                     'load_room_avatars', '_check_startup_plugins', 'sync_danmaku',
                     'stop_danmaku'):
            self.patches.append(patch.object(MainWindow, name))
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        with patch('ddm.app.QTimer.singleShot'):
            self.window = MainWindow([], [{'room_id': '90001', 'uname': 'Fixture', 'live': False}],
                layout_id='1x1', state={'sessdata': 'fixture', 'bili_jct': 'fixture-csrf',
                'buvid3': 'fixture-device', 'plugins_enabled': ['bili_watchlater'],
                'settings': {'auto_update': False}})
        self.window.resize(1000, 600)
        self.window.show()
        APP.processEvents()
        self.plugin = next(p for p in self.window.plugins.plugins
                           if p.context.name == 'bili_watchlater')
        self.tile = self.window.wall.tiles[0]
        self.item = models.WatchLaterItem(bvid='BV1xx411c7mD', aid=1, cid=101, videos=2)

    def tearDown(self):
        self.window.close()
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def binding(self, state='playing'):
        cell = self.plugin._resolve_cell_from_tile(self.tile)
        self.assertIsNotNone(cell)
        binding = plugin_module._VodBinding(self.item, cell, self.tile)
        binding.state = state
        binding.prev_room = dict(self.tile.room)
        self.plugin._bindings[binding.bvid] = binding
        return binding

    def test_real_host_controls_and_page(self):
        keys = {e.key for e in self.tile._plugin_controls.entries if e.owner == 'bili_watchlater'}
        self.assertEqual(keys, {'watchlater', 'watchlater_progress', 'watchlater_subtitle'})
        self.assertIn(self.tile, self.plugin._cell_tiles.values())
        self.assertIsNotNone(self.plugin._ensure_page())

    def test_host_player_injection_and_restore(self):
        binding = self.binding()
        original = dict(self.tile.room)
        stream = streams.VodStream(url='https://a.bilivideo.com/v.mp4', cid=101,
                                   quality=80, qn=80, accept_quality=(80,))
        workers = importlib.import_module('ddm_plugin_bili_watchlater.workers')
        player = Mock()
        # Exercise real StreamPlayback; replace only the media engine and HTTP.
        with patch('ddm.app.TilePlayer', return_value=player), \
                patch.object(workers.DanmakuRequest, 'start'):
            self.plugin._inject_playback(binding, stream, 0)
        self.assertIs(self.window.players[self.tile], player)
        self.assertEqual(player.play.call_args.args[0], stream.url)
        self.assertEqual(self.tile.stream_headers['Referer'], 'https://www.bilibili.com/')
        self.assertEqual(self.tile.room['watchlater_bvid'], binding.bvid)
        self.assertFalse(self.tile.room['live'])
        self.assertFalse(self.tile._recording_available)
        self.plugin._release_binding(binding, restart=False)
        self.assertEqual(self.tile.room['room_id'], original['room_id'])
        self.assertNotIn(self.tile, self.window.players)
        self.assertEqual(self.plugin._bindings, {})

    def test_avatar_loader_uses_async_cover_lifecycle(self):
        avatars = importlib.import_module('ddm_plugin_bili_watchlater.avatars')
        pixmap = QPixmap(8, 8)
        pixmap.fill(Qt.black)
        with patch.object(avatars.CoverLoader, 'get', return_value=pixmap), \
                patch.object(avatars.CoverLoader, 'shutdown') as shutdown:
            provider = avatars.AvatarLoader()
            delivered = []
            provider.ready.connect(lambda bvid, image: delivered.append((bvid, image.size())))
            provider.get('fixture-video', 'https://i0.hdslb.com/bfs/face/test.jpg')
            self.assertEqual(delivered, [('fixture-video', pixmap.size())])
            provider.shutdown()
            provider.get('late', 'https://i0.hdslb.com/bfs/face/test.jpg')
            self.assertEqual(len(delivered), 1)
            shutdown.assert_called_once()
            provider.deleteLater()

    def test_qt6_keyboard_modifiers(self):
        binding = self.binding()
        with patch.object(self.plugin, '_key_window_ok', return_value=True), \
                patch.object(self.plugin, '_active_vod_tile', return_value=self.tile):
            self.tile.pause_button.setFocus()
            APP.processEvents()
            for key, action in ((Qt.Key_Space, 'space'), (Qt.Key_Left, 'left'),
                                (Qt.Key_Right, 'right'), (Qt.Key_B, 'danmaku')):
                event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)
                result = self.plugin._vod_key_action(event, self.tile)
                self.assertIsNotNone(result)
                self.assertEqual(result, (action, self.tile, binding))
            edit = QLineEdit(self.window)
            edit.show()
            edit.setFocus()
            APP.processEvents()
            self.assertIsNone(self.plugin._vod_key_action(
                QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier), edit))

    def test_cancelled_resolution_does_not_restore_over_replacement(self):
        binding = self.binding('resolving')
        self.tile.set_room({'room_id': '90002', 'uname': 'Replacement', 'live': False})
        with patch.object(self.plugin, '_stop_host_tile') as stop:
            self.plugin._release_binding(binding, restart=False)
        self.assertEqual(self.tile.room['room_id'], '90002')
        stop.assert_not_called()

    def test_detach_rejects_late_danmaku_for_same_video(self):
        old = self.binding()
        workers = importlib.import_module('ddm_plugin_bili_watchlater.workers')
        with patch.object(workers.DanmakuRequest, 'start'):
            self.plugin._start_danmaku_fetch(old, {})
        request_id = self.plugin._dm_seq
        self.plugin._detach_binding(old)
        new = self.binding()
        self.plugin._on_danmaku_completed(request_id, [(1.0, 1, '#ffffff', 'stale')])
        self.assertIsNone(new.danmaku)

    def test_next_part_discards_previous_media(self):
        binding = self.binding('ended')
        binding.danmaku = [(1.0, 1, '#ffffff', 'P1')]
        binding.preview = Mock()
        preview = binding.preview
        with tempfile.TemporaryDirectory() as directory:
            subtitle = Path(directory) / 'p1.srt'
            subtitle.write_text('P1', encoding='utf-8')
            binding.subtitles = [('中文', str(subtitle))]
            binding.subtitle_on = binding.subtitle_loaded = True
            with patch.object(self.plugin, '_start_resolution'):
                self.plugin._next_binding(binding)
            self.assertFalse(subtitle.exists())
        self.assertEqual(binding.part, 1)
        self.assertEqual(binding.cid, 0)
        self.assertIsNone(binding.preview)
        preview.release.assert_called_once()
        self.assertIsNone(binding.danmaku)
        self.assertEqual(binding.subtitles, [])


if __name__ == '__main__':
    unittest.main()
