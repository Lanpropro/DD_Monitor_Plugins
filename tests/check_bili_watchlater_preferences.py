"""Preference behavior and real loopback cache handoff; never contact Bilibili."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import requests

from check_bili_watchlater import APP, models, plugin_module, streams
from check_bili_watchlater_interactions import TileFixture, pages
from ddm_plugin_bili_watchlater.preferences import DEFAULTS, read_preferences
from ddm_plugin_bili_watchlater.warm_media import Prefix, PrefixSession, PreparedMedia, fetch_prefix
from ddm_plugin_bili_watchlater.next_prefetch import NextPrefetch
from PySide6.QtCore import QEvent, QObject, QPoint, QRunnable, Signal
from PySide6.QtGui import QPixmap


def context(values):
    return SimpleNamespace(credentials=lambda _: None, setting=lambda key, default: values.get(key, default),
                           set_setting=lambda key, value: values.__setitem__(key, value))


class PreferenceTests(unittest.TestCase):
    def page(self, values=None):
        page = pages.WatchLaterPage(context(values if values is not None else {}))
        page._geometry_settings = Mock()
        self.addCleanup(lambda: (page.shutdown(), page.deleteLater(), APP.sendPostedEvents(None, QEvent.DeferredDelete)))
        return page

    @contextmanager
    def shown(self, page):
        with patch.object(page, '_load'), patch.object(page, '_cache_fresh', return_value=True), \
                patch.object(page, '_apply_saved_geometry', return_value=True), patch.object(page, '_start_entrance'):
            page.resize(980, 660)
            page.show()
            APP.processEvents()
            yield

    def test_new_defaults_and_prefetch_dependency(self):
        values = read_preferences(None)
        self.assertTrue(values['auto_play'])
        self.assertTrue(values['video_preview'])
        self.assertTrue(values['remember_state'])
        self.assertFalse(values['prefetch_next'])
        self.assertFalse(read_preferences(context(dict(prefetch_next=True, autoplay=False)))['prefetch_next'])
        self.assertTrue(read_preferences(context(dict(prefetch_next=True, autoplay=True)))['prefetch_next'])

    def test_settings_disable_and_clear_prefetch_when_continuation_is_off(self):
        page = self.page()
        page._open_settings()
        dialog = page._settings_dialog
        self.assertFalse(dialog.prefetch_next.isEnabled())
        dialog.autoplay.setChecked(True)
        self.assertTrue(dialog.prefetch_next.isEnabled())
        dialog.prefetch_next.setChecked(True)
        dialog.autoplay.setChecked(False)
        self.assertFalse(dialog.prefetch_next.isEnabled())
        self.assertFalse(dialog.prefetch_next.isChecked())

    def test_close_reopen_and_new_page_restore_deep_anchor_and_offset(self):
        values = {}
        page = self.page(values)
        items = [models.WatchLaterItem(aid=i+1, bvid=f'fixture{i}', title=f'视频{i}', view_count=0, danmaku_count=0)
                 for i in range(100)]
        page._items, page._loaded_once = items, True
        with self.shown(page):
            page._on_view_mode_changed('list')
            page._render_limit = 100
            page._render(keep_position=True)
            APP.processEvents()
            page._restore_anchor(items[65].key)
            bar = page.list_scroll.verticalScrollBar()
            bar.setValue(bar.value() + 9)
            page.hide()
            saved = values['browse_state']
            self.assertEqual(saved['key'], items[65].key)
            self.assertEqual(saved['offset'], 9)
            page.show()
            APP.processEvents()
            self.assertEqual(page._visible_anchor_key(), saved['key'])
            self.assertEqual(page.list_scroll.verticalScrollBar().value() - page._rows_by_key[saved['key']].y(), 9)
        second = self.page(values)
        second._items, second._loaded_once = items, True
        with self.shown(second):
            self.assertEqual(second._view_mode, 'list')
            self.assertEqual(second._visible_anchor_key(), saved['key'])

    def test_retention_off_starts_at_top_and_account_change_clears_saved_state(self):
        values = dict(remember_state=False, browse_state=dict(key='old', value=999, view='list', query='old'))
        page = self.page(values)
        page.browse_state.prepare()
        self.assertEqual(page._view_mode, 'grid')
        self.assertEqual(page.search.text(), '')
        self.assertIsNone(page.browse_state.pending)
        page.on_account_changed(2)
        self.assertEqual(values['browse_state'], {})

    def test_saved_filters_from_another_account_are_cleared_before_render(self):
        values = dict(browse_state=dict(account='old', query='no-match', filter='watched', view='list'))
        page = self.page(values)
        page._cookies = {'DedeUserID': 'new'}
        page._items = [models.WatchLaterItem(aid=1, bvid='fixture', title='video')]
        page._loaded_once = True
        page.browse_state.prepare()
        page._render()
        self.assertEqual(page.search.text(), '')
        self.assertEqual(page.filter_combo.currentData(), 'all')
        self.assertEqual(page._render_total, 1)
        self.assertEqual(values['browse_state'], {})

    def test_user_filter_change_discards_pending_scroll_restore(self):
        page = self.page()
        page.browse_state.pending = dict(key='old', value=999)
        page.sort_combo.setCurrentIndex(1)
        self.assertIsNone(page.browse_state.pending)

    def test_auto_play_off_enters_paused_and_does_not_leak_muted_state(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        tile = TileFixture({})
        tile.muted = False
        tile.set_paused = Mock()
        player = Mock()
        def play_on(*args, **kwargs):
            self.assertTrue(tile.muted)
        plugin.host.window = lambda: SimpleNamespace(_play_on=play_on, _match_sync_active=False)
        plugin.host.player = lambda _: player
        self.assertTrue(plugin._play_on_host(tile, 'https://media.invalid/video', {}, start_paused=True))
        tile.set_paused.assert_called_once_with(True)
        player.set_paused.assert_called_once_with(True)
        player.set_muted.assert_called_once_with(False)
        self.assertFalse(tile.muted)

    def test_new_binding_respects_auto_play_setting(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        plugin.context = context(dict(auto_play=False))
        tile = TileFixture({})
        plugin.host.window = lambda: SimpleNamespace(_play_on=Mock())
        plugin._resolve_cell_from_tile = lambda _: object()
        plugin.host.tiles = lambda: [tile]
        item = models.WatchLaterItem(aid=1, bvid='fixture')
        with patch.object(plugin.playback, 'start_resolution') as resolve, patch.object(plugin.playback, 'ensure_tick_timer'):
            plugin._start_vod_for_tile(tile, item)
        self.assertTrue(resolve.call_args.args[0].resume_paused)

    def test_card_preview_gate_release_and_original_cover_preserved(self):
        from ddm_plugin_bili_watchlater import card_preview
        from ddm_plugin_bili_watchlater.widgets import CoverWidget
        class Provider(QObject):
            preview_ready = Signal()
            def __init__(self, _bvid, _cid, parent):
                super().__init__(parent)
                self._pool = SimpleNamespace(activeThreadCount=lambda: 0)
                self.calls = 0
            def frame_at(self, seconds):
                self.calls += 1
                image = QPixmap(40, 22)
                image.fill()
                return image, seconds
            def release(self):
                pass
        page = self.page()
        page._visible = True
        cover = CoverWidget()
        self.addCleanup(lambda: (cover.close(), cover.deleteLater()))
        original = QPixmap(40, 22)
        original.fill()
        cover.set_item(models.WatchLaterItem(aid=1, bvid='fixture', cid=123, duration=60), original)
        cover.show()
        page.card_preview.watch(cover)
        with patch.object(card_preview, 'VodPreviewProvider', Provider):
            page.preferences['video_preview'] = False
            APP.sendEvent(cover, QEvent(QEvent.Enter))
            self.assertFalse(page.card_preview.delay.isActive())
            page.preferences['video_preview'] = True
            APP.sendEvent(cover, QEvent(QEvent.Enter))
            page.card_preview.delay.stop()
            page.card_preview.begin()
            self.assertGreater(page.card_preview.provider.calls, 0)
            self.assertIsNotNone(cover._preview_frame)
            APP.sendEvent(cover, QEvent(QEvent.Leave))
            self.assertIsNone(cover._preview_frame)
            self.assertEqual(cover._pixmap.cacheKey(), original.cacheKey())

    def test_preview_retirement_keeps_running_pool_alive_without_waiting_on_page(self):
        from ddm_plugin_bili_watchlater import card_preview
        from ddm_plugin_bili_watchlater.vod_preview import VodPreviewProvider
        started, finish = threading.Event(), threading.Event()
        class Task(QRunnable):
            def run(self):
                started.set()
                finish.wait(3)
        provider = VodPreviewProvider('fixture', 1)
        provider._pool.start(Task())
        try:
            self.assertTrue(started.wait(1))
            card_preview.retire_preview(provider)
            self.assertIn(provider, card_preview._retirement.providers)
            self.assertEqual(provider.state(), 'released')
        finally:
            finish.set()
            provider._pool.waitForDone(1000)
            card_preview._retirement.collect()
        self.assertNotIn(provider, card_preview._retirement.providers)


@contextmanager
def media_server():
    data = bytes(range(256)) * 64
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            raw = self.headers.get('Range', '')
            seen.append(raw)
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', raw)
            start = int(match[1]) if match else 0
            end = min(int(match[2]), len(data) - 1) if match and match[2] else len(data) - 1
            self.send_response(206 if match else 200)
            if match:
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(end - start + 1))
            self.end_headers()
            self.wfile.write(data[start:end+1])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/media.mp4', data, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def session():
    result = requests.Session()
    result.trust_env = False
    return result


class PrefetchTests(unittest.TestCase):
    def test_cancel_running_prefetch_keeps_worker_alive_until_finished(self):
        from ddm_plugin_bili_watchlater import request_retirement
        entered, finish = threading.Event(), threading.Event()
        def resolve(*args, **kwargs):
            entered.set()
            finish.wait(3)
            return streams.VodStream(url='https://unused.invalid/video')
        manager = NextPrefetch(APP)
        try:
            with patch('ddm_plugin_bili_watchlater.vod_stream.resolve_vod', side_effect=resolve):
                manager.prepare(1, (1, 'fixture', 0, 0), {'SESSDATA': 'fixture'}, 1)
                worker = next(iter(manager.workers))
                cookies = worker.cookies
                self.assertTrue(entered.wait(1))
                manager.clear()
                self.assertEqual(manager.entries, {})
                self.assertEqual(manager.workers, set())
                self.assertIn(worker, request_retirement._retirement.pending)
                self.assertTrue(worker.cancelled.is_set())
                finish.set()
                self.assertTrue(worker.wait(1000))
                APP.processEvents()
                self.assertEqual(cookies, {})
                self.assertNotIn(worker, request_retirement._retirement.pending)
        finally:
            finish.set()
            manager.clear()
            manager.deleteLater()

    def test_real_prefetch_worker_clears_credentials_and_retires_after_dash_buffers(self):
        with media_server() as (url, data, seen):
            stream = streams.VodStream(url=url, is_dash=True, audio_url=url + '?audio', cid=1)
            manager = NextPrefetch(APP)
            try:
                with patch('ddm_plugin_bili_watchlater.vod_stream.resolve_vod', return_value=stream):
                    manager.prepare(1, (1, 'fixture', 0, 0), {'SESSDATA': 'fixture'}, 1)
                    worker = next(iter(manager.workers))
                    cookies = worker.cookies
                    self.assertTrue(worker.wait(3000))
                    APP.processEvents()
                    self.assertEqual(cookies, {})
                    self.assertEqual(manager.workers, set())
                    prepared = manager.take(1, (1, 'fixture', 0, 0))
                    self.assertEqual(prepared.video.data, data)
                    self.assertEqual(prepared.audio.data, data)
            finally:
                manager.clear()
                manager.deleteLater()

    def test_bounded_prefix_and_cached_range_do_not_fetch_upstream_twice(self):
        with media_server() as (url, data, seen), session() as network:
            prefix = fetch_prefix(network, url, {}, 256, lambda: False)
            self.assertEqual(prefix.data, data[:256])
            self.assertEqual(len(prefix.data), 256)
            proxy_session = PrefixSession(prefix, session)
            try:
                response = proxy_session.get(url, headers={'Range': 'bytes=0-127'}, stream=True, timeout=2)
                self.assertEqual(b''.join(response.iter_content()), data[:128])
                self.assertEqual(seen, ['bytes=0-255'])
                response.close()
            finally:
                proxy_session.close()

    def test_cached_prefix_stitches_contiguous_tail_and_seek_passes_through(self):
        with media_server() as (url, data, seen):
            cached = Prefix(url, data[:256], len(data), 'video/mp4')
            proxy_session = PrefixSession(cached, session)
            try:
                with proxy_session.get(url, headers={'Range': 'bytes=128-511'}, stream=True, timeout=2) as response:
                    self.assertEqual(response.headers['Content-Range'], f'bytes 128-511/{len(data)}')
                    self.assertEqual(b''.join(response.iter_content(64)), data[128:512])
                self.assertEqual(seen, ['bytes=256-511'])
                with proxy_session.get(url, headers={'Range': 'bytes=1024-1151'}, stream=True, timeout=2) as response:
                    self.assertEqual(response.content, data[1024:1152])
                self.assertEqual(seen[-1], 'bytes=1024-1151')
            finally:
                proxy_session.close()

    def test_actual_host_proxy_serves_cached_video_bytes_and_releases_lease(self):
        with media_server() as (url, data, seen):
            prepared = PreparedMedia(streams.VodStream(url=url), Prefix(url, data[:256], len(data), 'video/mp4'))
            stream, lease = prepared.activate()
            try:
                with session() as client:
                    response = client.get(stream.url, headers={'Range': 'bytes=0-127'}, timeout=3)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, data[:128])
                self.assertEqual(seen, [])
            finally:
                lease.close()
            self.assertEqual(lease.proxies, [])

    def test_expired_preload_rejected_and_failed_key_not_retried(self):
        prepared = PreparedMedia(streams.VodStream(url='https://media.invalid/video'), Prefix('', b'x', 1, 'video/mp4'))
        prepared.created -= 121
        with self.assertRaises(ValueError):
            prepared.activate()
        manager = NextPrefetch(APP)
        try:
            with patch('ddm_plugin_bili_watchlater.next_prefetch.PrefetchRequest.start') as start:
                key = (1, 'fixture', 0, 0)
                manager.prepare(1, key, {'SESSDATA': 'fixture'}, 1)
                token = manager.entries[1]['token']
                manager._completed(token, None)
                manager.prepare(1, key, {}, 1)
                self.assertEqual(start.call_count, 1)
                self.assertIsNone(manager.take(1, (2, 'fixture', 0, 0)))
                self.assertIsNone(manager.take(1, key))
                self.assertEqual(manager.entries, {})
                self.assertEqual(manager.workers, set())
        finally:
            manager.clear()
            manager.deleteLater()

    def test_prefetch_near_end_selects_next_part_and_setting_off_clears(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        plugin.context = context(dict(autoplay=True, prefetch_next=True))
        plugin._account_generation = 2
        plugin.playback.cookie_snapshot = lambda: {}
        plugin._next_prefetch = Mock()
        binding = SimpleNamespace(item=SimpleNamespace(parts=2, bvid='current', cid=1), bvid='current',
                                  part=0, quality=80, state='playing', duration=100, progress=40)
        plugin._maybe_prefetch(binding)
        plugin._next_prefetch.prepare.assert_not_called()
        binding.progress = 80
        plugin._maybe_prefetch(binding)
        plugin._next_prefetch.prepare.assert_called_once_with(id(binding), (2, 'current', 1, 80), {}, 0)
        plugin._preferences_changed(dict(prefetch_next=False, video_preview=False))
        plugin._next_prefetch.clear.assert_called_once()

    def test_warm_handoff_skips_resolver_and_expired_data_falls_back(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        plugin.playback.cookie_snapshot = lambda: {}
        plugin._account_generation = 1
        item = models.WatchLaterItem(aid=1, bvid='fixture')
        binding = plugin_module._VodBinding(item, object(), TileFixture({}))
        plugin._bindings[item.bvid] = binding
        prepared = Mock()
        stream, lease = Mock(), Mock()
        prepared.activate.return_value = (stream, lease)
        with patch.object(plugin.playback, 'on_vod_completed') as inject, \
                patch('ddm_plugin_bili_watchlater.workers.VodResolveRequest') as request:
            plugin._start_resolution(binding, start=0, prepared=prepared)
            inject.assert_called_once_with(binding.request_id, 1, stream)
            request.assert_not_called()
            plugin._cancel_binding_media(binding)
            lease.close.assert_called_once()
            prepared.activate.side_effect = ValueError('expired')
            inject.reset_mock()
            plugin._start_resolution(binding, start=0, prepared=prepared)
            inject.assert_not_called()
            request.return_value.start.assert_called_once()
        plugin._vod_requests.clear()

    def test_account_change_and_shutdown_clear_pending_cache(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        coordinator = Mock()
        plugin._next_prefetch = coordinator
        plugin.on_event(plugin_module.api.EVENT_ACCOUNT_CHANGED, {'platform': 'bilibili'})
        coordinator.clear.assert_called_once()
        plugin.on_unload()
        self.assertEqual(coordinator.clear.call_count, 2)
        coordinator.deleteLater.assert_called_once()
        self.assertIsNone(plugin._next_prefetch)


if __name__ == '__main__':
    unittest.main()
