"""Replay card clicks without network, accounts, native players or a host window."""
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from check_bili_watchlater import APP, models, plugin_module
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage
from ddm.pane_danmaku import PaneDanmaku

pages = importlib.import_module('ddm_plugin_bili_watchlater.page')


class TileFixture:
    """Use identity, like real Qt Tiles; equal empty room dicts are distinct cells."""
    def __init__(self, room):
        self.room = room


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

    def test_covers_and_titles_play_without_opening_browser(self):
        with patch.object(self.page, '_open_item') as browser:
            for mode in ('list', 'grid'):
                card = self.card(mode)
                for target in (card.cover, card.title_label):
                    self.handler.reset_mock()
                    QTest.mouseClick(target, Qt.LeftButton, pos=QPoint(5, 5))
                    self.handler.assert_called_once_with(self.item)
            browser.assert_not_called()

    def test_browser_icon_is_the_explicit_external_action(self):
        with patch.object(self.page, '_open_item') as browser:
            for mode in ('list', 'grid'):
                card = self.card(mode)
                QTest.mouseClick(card.open_button, Qt.LeftButton, pos=QPoint(5, 5))
            self.assertEqual(browser.call_count, 2)
        self.handler.assert_not_called()
        self.close.assert_not_called()

    def test_title_double_click_dispatches_once_and_closes(self):
        for mode in ('list', 'grid'):
            with self.subTest(mode=mode):
                self.handler.reset_mock()
                self.close.reset_mock()
                self.double_click(self.card(mode).title_label)
                self.handler.assert_called_once_with(self.item)
                self.close.assert_called_once()

    def test_grid_cover_keeps_natural_aspect_in_short_window(self):
        card = self.card('grid')
        card.fit_cell(310, 255)
        self.assertGreaterEqual(card.cover.height(), 150)
        self.assertAlmostEqual(card.cover.width() / card.cover.height(), 16 / 9, delta=.03)
        self.assertEqual(pages.grid_columns_for_width(932), 3)
        self.assertEqual(pages.grid_columns_for_width(1218), 4)

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
        self.playing = TileFixture({'room_id': self.item.bvid})
        self.empty = TileFixture({})
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

    def pane_layout(self):
        tiles = [TileFixture({'room_id': str(i + 1)}) for i in range(3)]
        tiles += [TileFixture({}) for _ in range(3)]
        wall = SimpleNamespace(tiles=tiles, layout_id='fixture', _danmaku_cell=None,
                               _cell_order=lambda: list(range(6)))
        wall.pane_danmaku = PaneDanmaku(wall, {})
        wall.pane_danmaku.settings['fixture'] = {3: tiles[0], 4: tiles[1]}
        self.plugin._host_window = lambda: SimpleNamespace(wall=wall)
        self.plugin._host_tiles = lambda: tiles
        return tiles

    def test_one_plus_five_skips_two_empty_chat_backing_tiles(self):
        tiles = self.pane_layout()
        with patch.object(self.plugin, '_start_vod_for_tile') as play:
            self.plugin._play_item_from_page(self.item)
        play.assert_called_once_with(tiles[5], self.item)
        self.assertIs(play.call_args.args[0], tiles[5])

    def test_target_converted_to_chat_uses_visible_empty_cell(self):
        tiles = self.pane_layout()
        self.plugin._pick_target_tile = tiles[3]
        with patch.object(self.plugin, '_start_vod_for_tile') as play:
            self.plugin._play_item_from_page(self.item)
        play.assert_called_once_with(tiles[5], self.item)
        self.assertIs(play.call_args.args[0], tiles[5])

    def test_missing_inline_capability_does_not_launch_browser(self):
        self.plugin._host_window = lambda: SimpleNamespace()
        with patch.object(self.plugin, '_open_url') as browser, \
                patch.object(self.plugin, '_notify_warning') as warning:
            self.plugin._start_vod_for_tile(self.empty, self.item)
        browser.assert_not_called()
        warning.assert_called_once()

    def test_chat_backing_tiles_do_not_count_as_free_video_cells(self):
        tiles = self.pane_layout()
        tiles[5].room = {'room_id': 'occupied'}
        with patch.object(self.plugin, '_start_vod_for_tile') as play, \
                patch.object(self.plugin, '_notify_warning') as warning:
            self.plugin._play_item_from_page(self.item)
        play.assert_not_called()
        warning.assert_called_once()


class CoverSchedulingTests(unittest.TestCase):
    def test_viewport_priority_discards_old_waiting_covers(self):
        covers = pages.CoverLoader()
        try:
            covers.get('https://i0.hdslb.com/bfs/archive/old.jpg')
            wanted = ['https://i0.hdslb.com/bfs/archive/visible.jpg',
                      'https://i0.hdslb.com/bfs/archive/prefetch.jpg']
            covers.prioritize(wanted)
            self.assertEqual(covers._pending, wanted)
            self.assertEqual(set(covers._timings), set(wanted))
        finally:
            covers.shutdown()

    def test_cover_timing_reports_stages_without_resource_url(self):
        covers = pages.CoverLoader()
        captured = []
        covers.diagnostic.connect(captured.append)
        url = 'https://i0.hdslb.com/bfs/archive/private-fixture.jpg'
        covers._decoding.add(url)
        covers._timings[url] = [1, 1.2, 1.5]
        try:
            image = QImage(16, 9, QImage.Format_RGB32)
            image.fill(Qt.black)
            with patch('time.monotonic', return_value=1.6):
                covers._decoded(url, image)
            self.assertIn('queue_ms=200 network_ms=300 decode_ms=100', captured[0])
            self.assertNotIn(url, captured[0])
            self.assertFalse(covers._timings)
        finally:
            covers.shutdown()


class EntryIconTests(unittest.TestCase):
    def test_hover_interruption_and_disabled_motion_reset(self):
        from ddm_plugin_bili_watchlater.entry_icon import WatchLaterButton
        button = WatchLaterButton()
        try:
            with patch('ddm.motion.enabled', return_value=True):
                button._feedback(entering=True)
                button._hover_animation.setCurrentTime(112)
                self.assertAlmostEqual(button._play_scale, 1.22)
                button._feedback()
                self.assertAlmostEqual(button._poses[0][1], 1.22)
            with patch('ddm.motion.enabled', return_value=False):
                button._feedback(entering=True)
            self.assertEqual(button._play_scale, 1)
            self.assertFalse(button.icon().pixmap(36, 36).isNull())
        finally:
            button.deleteLater()


if __name__ == '__main__':
    unittest.main()
