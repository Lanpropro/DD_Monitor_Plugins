"""Owner recovery, late metadata guards and wall marks independent of selection."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from check_bili_watchlater import APP, models, plugin_module, streams, http_api
from check_bili_watchlater_order import OrderTests
from ddm_plugin_bili_watchlater import workers
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from ddm import theme


class OwnerTests(unittest.TestCase):
    def item(self):
        return models.WatchLaterItem(aid=1, bvid='BV1xx411c7mD', title='Fixture',
            owner_mid=123, owner_name='Fixture UP', owner_face='https://i0.hdslb.com/face.jpg')

    def test_view_metadata_uses_existing_normalizer_and_keeps_count_contract(self):
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200, json=lambda: {
            'code': 0, 'data': {'aid': 1, 'bvid': self.item().bvid,
            'title': 'Fixture', 'owner': {'name': 'Fixture UP', 'mid': 123,
            'face': '//i0.hdslb.com/face.jpg'}, 'stat': {'view': 20, 'danmaku': 10}}})
        result = http_api.video_stat(self.item().bvid, session=session, with_view=True)
        self.assertEqual((result.view, result.danmaku), (20, 10))
        self.assertEqual(result.item.owner_name, 'Fixture UP')
        self.assertEqual(result.item.owner_face, self.item().owner_face)
        self.assertEqual(http_api.video_stat(self.item().bvid, session=session), 10)

    def test_missing_owner_worker_hands_off_stream_before_optional_metadata(self):
        item = self.item()
        worker = workers.VodResolveRequest({}, 2, 3, item.bvid)
        worker.load_metadata = True
        events = []
        worker.completed.connect(lambda *_: events.append('play'))
        worker.metadata_ready.connect(lambda *args: events.append(args[2]))
        def details(*_, **__):
            self.assertEqual(events, ['play'])
            return SimpleNamespace(item=item)
        with patch.object(streams, 'resolve_vod', return_value=Mock()), \
                patch.object(http_api, 'video_stat', side_effect=details):
            worker.run()
        self.assertEqual(events, ['play', item])
        worker.deleteLater()

    def test_optional_failure_or_cancellation_does_not_fail_playback(self):
        for action in ('failure', 'cancel', 'known'):
            with self.subTest(action=action):
                worker = workers.VodResolveRequest({}, 0, 1, self.item().bvid)
                worker.load_metadata = action != 'known'
                play, failed, metadata = Mock(), Mock(), Mock()
                worker.completed.connect(play)
                worker.failed.connect(failed)
                worker.metadata_ready.connect(metadata)
                if action == 'cancel':
                    worker.completed.connect(lambda *_: worker.cancel())
                with patch.object(streams, 'resolve_vod', return_value=Mock()), \
                        patch.object(http_api, 'video_stat', side_effect=RuntimeError) as fetch:
                    worker.run()
                play.assert_called_once()
                failed.assert_not_called()
                metadata.assert_not_called()
                self.assertEqual(fetch.call_count, int(action == 'failure'))
                worker.deleteLater()

    def binding(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        item = self.item()
        restored = models.WatchLaterItem(aid=1, bvid=item.bvid, videos=2)
        tile = Mock(room={'watchlater_bvid': item.bvid})
        binding = plugin_module._VodBinding(restored, object(), tile)
        binding.part, binding.progress, binding.state, binding.request_id = 1, 123., 'paused', 3
        plugin._bindings[item.bvid] = binding
        plugin._host_tiles = lambda: [tile]
        return plugin, binding, tile, item

    def test_late_metadata_updates_existing_tile_without_restarting_or_seeking(self):
        plugin, binding, tile, item = self.binding()
        item.videos = 2
        with patch.object(plugin, '_play_on_host') as play:
            plugin.playback.on_item_metadata(3, 0, item)
        self.assertIs(binding.item, item)
        self.assertEqual((binding.part, binding.progress, binding.state), (1, 123., 'paused'))
        tile.set_uname_title.assert_called_once_with(item.owner_name, 'Fixture  P2')
        self.assertEqual(tile.room['face'], item.owner_face)
        play.assert_not_called()

    def test_late_metadata_rejects_old_generation_request_and_newer_list_metadata(self):
        for change in ('generation', 'request', 'known', 'replaced', 'destroyed'):
            with self.subTest(change=change):
                plugin, binding, tile, item = self.binding()
                if change == 'generation': plugin.playback.advance_generation()
                if change == 'request': binding.request_id = 4
                if change == 'known': binding.item = self.item()
                if change == 'replaced': tile.room = {'room_id': 'live-fixture'}
                if change == 'destroyed': plugin._host_tiles = lambda: []
                plugin.playback.on_item_metadata(3, 0, item)
                tile.set_uname_title.assert_not_called()

    def test_canonical_list_refresh_replaces_restored_owner(self):
        plugin, binding, tile, item = self.binding()
        plugin.page = SimpleNamespace(_cookies={})
        plugin.watch_order = Mock()
        plugin.order_sidebar = None
        plugin._watch_order_changed = Mock()
        plugin._watch_items_changed([item])
        self.assertIs(binding.item, item)
        tile.set_uname_title.assert_called_once()

    def test_wall_marks_follow_actual_video_rooms_and_clear_after_removal(self):
        plugin, binding, tile, _ = self.binding()
        plugin.order_sidebar = SimpleNamespace(list=Mock())
        plugin._playback_tiles = lambda: [tile]
        plugin._sync_order_wall_marks()
        plugin.order_sidebar.list.set_on_wall.assert_called_with({binding.bvid})
        tile.room = {'room_id': 'live-fixture'}
        plugin._sync_order_wall_marks()
        plugin.order_sidebar.list.set_on_wall.assert_called_with(set())
        tile.room = {'watchlater_bvid': binding.bvid}
        plugin._bindings.clear()
        plugin._sync_order_wall_marks()
        plugin.order_sidebar.list.set_on_wall.assert_called_with(set())


class WallMarkTests(unittest.TestCase):
    setUp = OrderTests.setUp
    page = OrderTests.page
    def test_wall_mark_is_independent_of_selection_and_clears_without_model_reset(self):
        from ddm_plugin_bili_watchlater.order_sidebar import OrderSidebar
        page = self.page()
        panel = OrderSidebar(page, self.order, Mock())
        try:
            with patch.object(page, 'refresh_from_wall'), patch.object(page.covers, '_pump'):
                panel.resize(300, 500)
                panel.show()
                APP.processEvents()
                view = panel.list
                view.setCurrentIndex(view.model().index(1, 0))
                resets, changes = [], []
                view.model().modelReset.connect(lambda: resets.append(True))
                view.model().dataChanged.connect(lambda *args: changes.append(args))
                view.set_on_wall({'video0'})
                self.assertEqual(view.currentIndex().row(), 1)
                self.assertIn('已入墙', view.model().index(0, 0).data(Qt.AccessibleTextRole))
                rect = view.visualRect(view.model().index(0, 0))
                image = view.viewport().grab().toImage()
                dpr = image.devicePixelRatio()
                marker = image.pixelColor(round(dpr), round((rect.top()+4+(rect.height()-12)/2)*dpr))
                self.assertEqual(marker, QColor(theme.ACCENT))
                view.set_on_wall({'video0'})
                self.assertEqual(len(changes), 1)
                view.set_on_wall(set())
                self.assertNotIn('已入墙', view.model().index(0, 0).data(Qt.AccessibleTextRole))
                self.assertEqual(len(changes), 2)
                self.assertFalse(resets)
        finally:
            panel.close()
            panel.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(OwnerTests))
    suite.addTest(WallMarkTests('test_wall_mark_is_independent_of_selection_and_clears_without_model_reset'))
    raise SystemExit(not unittest.TextTestRunner().run(suite).wasSuccessful())
