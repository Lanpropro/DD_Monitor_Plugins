"""Selected upstream fixes against the current candidate; no real accounts or media."""
import importlib
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from check_bili_watchlater import APP, plugin_module
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QThread, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QKeySequence, QPainter, QPixmap, QShortcut, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea, QWidget
from shiboken6 import isValid

pages = importlib.import_module('ddm_plugin_bili_watchlater.page')
widgets = importlib.import_module('ddm_plugin_bili_watchlater.widgets')
models = importlib.import_module('ddm_plugin_bili_watchlater.models')


class PlaybackMergeTests(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.BiliWatchLaterPlugin()

    def test_repeated_relative_seek_uses_player_position(self):
        position = [80.0]
        def seek(command, delta, mode):
            self.assertEqual((command, mode), ('seek', 'relative+exact'))
            position[0] += delta
        player = SimpleNamespace(player=SimpleNamespace(command=Mock(side_effect=seek)))
        binding = SimpleNamespace(progress=10, duration=200)
        with patch.object(self.plugin, '_player_for_binding', return_value=player):
            for _ in range(3):
                self.plugin._seek_binding_relative(binding, 5)
        self.assertEqual(position[0], 95)

    def test_seek_without_command_uses_clamped_absolute_fallback(self):
        player = SimpleNamespace(_set=Mock())
        binding = SimpleNamespace(progress=95, duration=100)
        with patch.object(self.plugin, '_player_for_binding', return_value=player):
            self.plugin._seek_binding_relative(binding, 10)
            player._set.assert_called_once_with('time-pos', 100)

    def test_key_filter_dispatches_relative_seek(self):
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
        binding, tile = SimpleNamespace(progress=10), object()
        with patch.object(self.plugin, '_vod_key_action', return_value=('right', tile, binding)), \
                patch.object(self.plugin, '_claim_key', return_value=True), \
                patch.object(self.plugin.playback, 'seek_binding_relative') as relative:
            self.assertTrue(self.plugin._handle_vod_key(event))
        relative.assert_called_once_with(binding, 5)

    def test_host_shortcut_has_priority_without_broadcasting_to_other_windows(self):
        host, floating = QWidget(), QWidget()
        host.shortcuts = {'video_danmaku': 'B'}
        shortcut = QShortcut(QKeySequence('B'), host)
        shortcut.setContext(Qt.WindowShortcut)
        host._window_shortcuts = [shortcut]
        tile = QWidget(floating)
        binding = SimpleNamespace(state='playing')
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_B, Qt.NoModifier)
        try:
            with patch.object(self.plugin.host, 'window', return_value=host), \
                    patch.object(self.plugin, '_key_window_ok', return_value=True), \
                    patch.object(self.plugin, '_active_vod_tile', return_value=tile), \
                    patch.object(self.plugin, '_binding_for_tile', return_value=binding):
                self.plugin._ensure_danmaku_shortcut()
                self.assertEqual(len(host.findChildren(QShortcut)), 1)
                self.assertIsNone(self.plugin._vod_key_action(event, host))
                self.assertEqual(self.plugin._vod_key_action(event, tile), ('danmaku', tile, binding))
                with patch.object(self.plugin, '_toggle_danmaku') as toggle:
                    self.plugin._handle_vod_key(event, tile)
                toggle.assert_called_once_with(tile, binding)
        finally:
            host.deleteLater()
            floating.deleteLater()

    def test_dpi_notifications_coalesce_and_ignore_cooldown(self):
        tile = QWidget()
        self.plugin._bindings['fixture'] = SimpleNamespace(tile=tile)
        try:
            with patch.object(self.plugin.host, 'tiles', return_value=[tile]), \
                    patch.object(self.plugin.pip_dpi, 'cover_pip_dpi') as cover, \
                    patch.object(self.plugin, '_queue_tile_sync') as sync:
                for _ in range(100):
                    self.plugin._on_dpi_event(tile)
                self.assertTrue(self.plugin._dpi_pending)
                QTest.qWait(170)
                cover.assert_called_once_with(tile)
                sync.assert_called_once_with(tile)
                for _ in range(100):
                    self.plugin._on_dpi_event(tile)
                self.assertFalse(self.plugin._dpi_pending)
                self.assertFalse(self.plugin._dpi_flush_timer.isActive())
        finally:
            if self.plugin._dpi_flush_timer:
                self.plugin._dpi_flush_timer.stop()
                self.plugin._dpi_flush_timer.deleteLater()
            tile.deleteLater()

    def test_drag_dismisses_covers_and_unload_cancels_pending_batch(self):
        tile, cover = QWidget(), QWidget()
        self.plugin._bindings['fixture'] = SimpleNamespace(tile=tile)
        self.plugin._dpi_covers[id(tile)] = cover
        cover.show()
        self.plugin._on_dpi_event(tile)
        with patch.object(self.plugin.pip_dpi, 'pip_drag_active', return_value=True):
            self.plugin._on_dpi_event(tile)
        self.assertTrue(cover.isHidden())
        self.assertFalse(self.plugin._dpi_pending)
        self.assertFalse(self.plugin._dpi_flush_timer.isActive())
        self.plugin._bindings.clear()
        self.plugin.on_unload()
        self.assertIsNone(self.plugin._dpi_flush_timer)
        tile.deleteLater()


class LayeredAlphaTests(unittest.TestCase):
    @unittest.skipUnless(APP.platformName() == 'windows', 'native Windows alpha surface')
    def test_native_alpha_upload_does_not_conflict_with_host_signatures(self):
        from ddm.playback_badges import PauseIndicator
        surface = importlib.import_module('ddm_plugin_bili_watchlater.tile_vod')
        host = PauseIndicator()
        widget = QWidget()
        widget.resize(48, 48)
        widget.setAttribute(Qt.WA_NativeWindow)
        host.start()
        widget.show()
        APP.processEvents()
        try:
            host._paint_layered()
            image = QImage(48, 48, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.white)
            self.assertTrue(surface._push_layered_alpha(widget, image))
            host._paint_layered()
            self.assertTrue(host._alpha_uploaded)
        finally:
            host.close()
            widget.close()
            host.deleteLater()
            widget.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)


class ScrollingMergeTests(unittest.TestCase):
    def test_wheel_batch_clamp_reverse_flush_and_disposal(self):
        from ddm_plugin_bili_watchlater.wheel_scroll import WheelScrollAccumulator
        area, content = QScrollArea(), QWidget()
        content.setMinimumHeight(3000)
        area.setWidget(content)
        area.resize(400, 300)
        area.show()
        APP.processEvents()
        wheel = WheelScrollAccumulator(area)
        bar = area.verticalScrollBar()
        try:
            bar.setValue(100)
            wheel._pending = 60
            wheel._scan()
            self.assertEqual((bar.value(), wheel.pending()), (160, 0))
            wheel._pending = 500
            wheel._scan()
            self.assertEqual((bar.value(), wheel.pending()), (400, 260))
            event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 30), QPoint(0, 120),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            wheel.eventFilter(area.viewport(), event)
            self.assertEqual(wheel.pending(), -30)
            wheel.flush()
            self.assertEqual(bar.value(), 370)
            wheel._pending = 100000
            wheel.flush()
            self.assertEqual(bar.value(), bar.maximum())
            wheel.shutdown()
            wheel.shutdown()
            self.assertFalse(wheel.is_scanning())
        finally:
            wheel.shutdown()
            area.close()
            area.deleteLater()

    def test_cover_cache_matches_existing_painting_and_updates(self):
        cover = widgets.CoverWidget()
        item = models.WatchLaterItem(aid=1, bvid='fixture', duration=600, progress=90)
        pixmap = QPixmap(320, 180)
        pixmap.fill(Qt.darkBlue)
        cover.set_item(item, pixmap)
        try:
            for dpr in (1, 1.5, 2):
                with patch.object(cover, 'devicePixelRatioF', return_value=dpr):
                    cached = cover._composited_cover()
                    self.assertIs(cached, cover._composited_cover())
                    direct = QImage(cached.size(), QImage.Format_ARGB32_Premultiplied)
                    direct.setDevicePixelRatio(dpr)
                    direct.fill(Qt.transparent)
                    painter = QPainter(direct)
                    try:
                        cover._paint_content(painter)
                    finally:
                        painter.end()
                    self.assertEqual(cached.toImage().convertToFormat(direct.format()), direct)
            before = cover._composited_cover().cacheKey()
            item.progress = 300
            self.assertNotEqual(cover._composited_cover().cacheKey(), before)
            cover.set_pixmap(None)
            self.assertIsNone(cover._render_cache)
        finally:
            cover.deleteLater()

    def page(self):
        page = pages.WatchLaterPage(SimpleNamespace(credentials=lambda _platform: None))
        page._geometry_settings = Mock()
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.shutdown)
        return page

    def test_pagination_is_throttled_not_restarted_by_scroll(self):
        page = self.page()
        page._view_mode = 'grid'
        page._visible = True
        page._render_total, page._render_limit = 64, 16
        page.stack.setCurrentWidget(page.grid_scroll)
        bar = page.grid_scroll.verticalScrollBar()
        bar.blockSignals(True)
        bar.setRange(0, 500)
        bar.setValue(500)
        bar.blockSignals(False)

        page._maybe_grow_page()
        first = page._grow_settle_timer.remainingTime()
        QTest.qWait(20)
        page._maybe_grow_page()
        self.assertLess(page._grow_settle_timer.remainingTime(), first)
        page._grow_settle_timer.stop()

    def test_remove_keeps_expanded_page_and_inactive_view(self):
        page = self.page()
        page._view_mode = 'grid'
        page._items = [models.WatchLaterItem(aid=i+1, bvid=f'fixture{i}', title=f'Video {i}') for i in range(50)]
        page._items_version += 1
        page._render()
        page._render_limit = 32
        page._render(keep_position=True)
        page._items.pop(0)
        page._items_version += 1
        with patch.object(page, '_prune_inactive_view') as prune:
            page._render_after_remove()
        self.assertEqual(page._render_limit, 32)
        self.assertEqual(len(page._cards), 32)
        prune.assert_not_called()

    def test_cover_retention_band_does_not_release_nearby_rows(self):
        page = self.page()
        page._view_mode, page._visible, page._grid_columns = 'grid', True, 3
        page.stack.setCurrentWidget(page.grid_scroll)
        rows = [Mock() for _ in range(60)]
        for index, row in enumerate(rows):
            row.geometry.return_value = QRect(0, index // 3 * 200, 280, 196)
            row.item.cover_url, row.item.bvid = '', ''
        page._cards, page._cover_views = rows, set(rows[:3])
        bar = page.grid_scroll.verticalScrollBar()
        bar.blockSignals(True)
        bar.setRange(0, 4000)
        with patch.object(page, '_danmaku_provider', return_value=None):
            bar.setValue(600)
            page._refetch_visible_covers()
            rows[0].cover.set_pixmap.assert_not_called()
            bar.setValue(2000)
            page._refetch_visible_covers()
            rows[0].cover.set_pixmap.assert_called_once_with(None)
        bar.blockSignals(False)


class WaitingRequest(QThread):
    completed = Signal(int, int, object)
    failed = Signal(int, int, object)

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.started_run = threading.Event()
        self.cookies = {'SESSDATA': 'fixture-only'}
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        self.started_run.set()
        self.gate.wait(3)
        self.completed.emit(1, 1, [])


class WorkerLifecycleTests(unittest.TestCase):
    def test_request_snapshots_clear_on_success_error_and_cancellation(self):
        workers = importlib.import_module('ddm_plugin_bili_watchlater.workers')
        for outcome in ('success', 'error', 'cancelled'):
            with self.subTest(outcome=outcome):
                worker = workers.ListRequest({'SESSDATA': 'fixture-only'}, 1, 1)
                with patch.object(workers, 'BiliWatchLaterAPI') as factory:
                    if outcome == 'error':
                        factory.return_value.list_all.side_effect = ValueError('fixture')
                    elif outcome == 'cancelled':
                        worker.cancel()
                    worker.run()
                    factory.return_value.close.assert_called_once()
                self.assertEqual(worker.cookies, {})
                worker.deleteLater()

    def test_worker_start_failure_also_releases_snapshot(self):
        workers = importlib.import_module('ddm_plugin_bili_watchlater.workers')
        page = pages.WatchLaterPage(SimpleNamespace(credentials=lambda _platform: None))
        page._geometry_settings = Mock()
        worker = workers.ListRequest({'SESSDATA': 'fixture-only'}, 1, 1)
        try:
            with patch.object(worker, 'start', side_effect=RuntimeError('fixture')):
                page._track(worker)
            self.assertNotIn(worker, page._requests)
            self.assertEqual(worker.cookies, {})
        finally:
            page.shutdown()
            page.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def page_with_request(self):
        page = pages.WatchLaterPage(SimpleNamespace(credentials=lambda _platform: None))
        page._geometry_settings = Mock()
        worker = WaitingRequest()
        receiver = Mock()
        worker.completed.connect(receiver)
        worker.failed.connect(receiver)
        page._track(worker)
        self.assertTrue(worker.started_run.wait(1))
        self.addCleanup(self.finish_request, page, worker)
        return page, worker, receiver

    def finish_request(self, page, worker):
        if isValid(worker):
            worker.gate.set()
            worker.wait(1000)
        if isValid(page):
            page.shutdown()
            page.deleteLater()
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_page_destroyed_during_request_releases_worker_and_snapshot(self):
        retirement = importlib.import_module('ddm_plugin_bili_watchlater.request_retirement')
        page, worker, receiver = self.page_with_request()
        page.shutdown()
        self.assertIn(worker, retirement._retirement.pending)
        self.assertTrue(worker.cancelled)
        page.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(page))
        worker.gate.set()
        self.assertTrue(worker.wait(1000))
        QTest.qWait(20)
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertNotIn(worker, retirement._retirement.pending)
        self.assertEqual(worker.cookies, {})
        self.assertFalse(isValid(worker))
        receiver.assert_not_called()

    def test_application_quit_drains_detached_request(self):
        retirement = importlib.import_module('ddm_plugin_bili_watchlater.request_retirement')
        page, worker, receiver = self.page_with_request()
        page.shutdown()
        release = threading.Timer(.03, worker.gate.set)
        release.start()
        try:
            APP.aboutToQuit.emit()
        finally:
            release.join()
        self.assertEqual(retirement._retirement.pending, set())
        self.assertFalse(worker.isRunning())
        self.assertEqual(worker.cookies, {})
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        receiver.assert_not_called()

    def test_normal_completion_cleans_page_tracking_and_snapshot(self):
        page, worker, receiver = self.page_with_request()
        worker.gate.set()
        worker.wait(1000)
        QTest.qWait(20)
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertNotIn(worker, page._requests)
        self.assertEqual(worker.cookies, {})
        self.assertFalse(isValid(worker))
        receiver.assert_called_once()

    def test_completion_between_timeout_and_signal_connection_is_reclaimed(self):
        retirement = importlib.import_module('ddm_plugin_bili_watchlater.request_retirement')
        class Completed(QObject):
            finished = Signal()
        worker = Completed()
        worker.cancel = Mock()
        worker.wait = Mock(side_effect=[False, True])
        worker.isFinished = lambda: True
        worker.cookies = {'SESSDATA': 'fixture-only'}
        registry = retirement.RetiredPageRequests(APP)
        registry.retire(worker)
        self.assertEqual(registry.pending, set())
        self.assertEqual(worker.cookies, {})
        registry.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(worker))


if __name__ == '__main__':
    unittest.main()
