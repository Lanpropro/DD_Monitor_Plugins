"""Replay card clicks without network, accounts, native players or a host window."""
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from check_bili_watchlater import APP, models, plugin_module
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest

pages = importlib.import_module('ddm_plugin_bili_watchlater.page')


class CardPlaybackTests(unittest.TestCase):
    def setUp(self):
        context = SimpleNamespace(credentials=lambda _platform: None)
        self.page = pages.WatchLaterPage(context)
        self.page._geometry_settings = Mock()  # Never read or write real QSettings.
        self.handler = Mock()
        self.page.set_play_handler(self.handler)
        self.closed = patch.object(self.page, 'close')
        self.close = self.closed.start()
        self.item = models.WatchLaterItem(aid=1, bvid='BV1xx411c7mD', title='Fixture')

    def tearDown(self):
        self.closed.stop()
        self.page.shutdown()
        self.page.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def card(self, mode):
        if mode == 'list':
            card = self.page._make_row(self.item)
        else:
            self.page._rebuild_cards([self.item])
            card = self.page._cards[0]
        card.resize(500, 220)
        card.cover.resize(180, 100)
        return card

    def double_click(self, widget):
        QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(5, 5))
        QTest.mouseDClick(widget, Qt.LeftButton, pos=QPoint(5, 5))
        QTest.mouseRelease(widget, Qt.LeftButton, pos=QPoint(5, 5))

    def test_list_card_double_click_dispatches_once(self):
        self.double_click(self.card('list'))
        self.handler.assert_called_once_with(self.item)
        self.close.assert_called_once()

    def test_list_cover_double_click_dispatches_once(self):
        self.double_click(self.card('list').cover)
        self.handler.assert_called_once_with(self.item)
        self.close.assert_called_once()

    def test_grid_card_double_click_dispatches_once(self):
        self.double_click(self.card('grid'))
        self.handler.assert_called_once_with(self.item)
        self.close.assert_called_once()

    def test_grid_cover_double_click_dispatches_once(self):
        self.double_click(self.card('grid').cover)
        self.handler.assert_called_once_with(self.item)
        self.close.assert_called_once()

    def test_grid_cover_single_click_still_dispatches_immediately(self):
        QTest.mouseClick(self.card('grid').cover, Qt.LeftButton, pos=QPoint(5, 5))
        self.handler.assert_called_once_with(self.item)
        self.close.assert_not_called()

    def test_multiselect_double_click_does_not_play_or_close(self):
        for mode in ('list', 'grid'):
            with self.subTest(mode=mode):
                card = self.card(mode)
                card.set_multi_mode(True, False)
                self.double_click(card.cover)
        self.handler.assert_not_called()
        self.close.assert_not_called()

    def test_explicit_second_play_is_not_swallowed_by_a_debounce(self):
        card = self.card('list')
        card._play()
        card._play()
        self.assertEqual(self.handler.call_count, 2)


class PlaybackDestinationTests(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.BiliWatchLaterPlugin()
        self.item = models.WatchLaterItem(aid=1, bvid='BV1xx411c7mD')
        self.playing = SimpleNamespace(room={'room_id': self.item.bvid})
        self.empty = SimpleNamespace(room={})
        self.plugin._host_tiles = lambda: [self.playing, self.empty]
        self.plugin._bindings[self.item.bvid] = SimpleNamespace(tile=self.playing)

    def test_list_reselect_uses_existing_video_tile(self):
        with patch.object(self.plugin, '_start_vod_for_tile') as play:
            self.plugin._play_item_from_page(self.item)
        play.assert_called_once_with(self.playing, self.item)

    def test_explicit_target_keeps_priority_over_existing_tile(self):
        self.plugin._pick_target_tile = self.empty
        with patch.object(self.plugin, '_start_vod_for_tile') as play:
            self.plugin._play_item_from_page(self.item)
        play.assert_called_once_with(self.empty, self.item)

    def test_new_video_uses_first_empty_tile(self):
        item = models.WatchLaterItem(aid=2, bvid='BV1yy411c7mD')
        with patch.object(self.plugin, '_start_vod_for_tile') as play:
            self.plugin._play_item_from_page(item)
        play.assert_called_once_with(self.empty, item)


if __name__ == '__main__':
    unittest.main()
