"""Replay card clicks without network, accounts, native players or a host window."""
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from check_bili_watchlater import APP, models, plugin_module
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
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
        self.target = TileFixture({})
        self.targets = [(self.target, '第 1 格 · 空格', False)]
        self.page.set_placement_handlers(lambda: self.targets, self.handler)
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

    def test_list_card_double_click_only_selects(self):
        self.double_click(self.card('list'))
        self.handler.assert_not_called()
        self.close.assert_not_called()
        self.assertEqual(self.page._selected_key, self.item.key)

    def test_list_cover_double_click_only_selects(self):
        self.double_click(self.card('list').cover)
        self.handler.assert_not_called()
        self.close.assert_not_called()

    def test_grid_card_double_click_only_selects(self):
        self.double_click(self.card('grid'))
        self.handler.assert_not_called()
        self.close.assert_not_called()

    def test_grid_cover_double_click_only_selects(self):
        self.double_click(self.card('grid').cover)
        self.handler.assert_not_called()
        self.close.assert_not_called()

    def test_grid_cover_single_click_only_selects(self):
        QTest.mouseClick(self.card('grid').cover, Qt.LeftButton, pos=QPoint(5, 5))
        self.handler.assert_not_called()
        self.assertEqual(self.page._selected_key, self.item.key)
        self.close.assert_not_called()

    def test_covers_and_titles_select_without_playing_or_opening_browser(self):
        with patch.object(self.page, '_open_item') as browser:
            for mode in ('list', 'grid'):
                card = self.card(mode)
                for target in (card.cover, card.title_label):
                    self.handler.reset_mock()
                    QTest.mouseClick(target, Qt.LeftButton, pos=QPoint(5, 5))
                    self.handler.assert_not_called()
                    self.assertEqual(self.page._selected_key, self.item.key)
            browser.assert_not_called()

    def test_browser_icon_is_the_explicit_external_action(self):
        with patch.object(self.page, '_open_item') as browser:
            for mode in ('list', 'grid'):
                card = self.card(mode)
                QTest.mouseClick(card.open_button, Qt.LeftButton, pos=QPoint(5, 5))
            self.assertEqual(browser.call_count, 2)
        self.handler.assert_not_called()
        self.close.assert_not_called()

    def test_title_double_click_neither_plays_nor_closes(self):
        for mode in ('list', 'grid'):
            with self.subTest(mode=mode):
                self.handler.reset_mock()
                self.close.reset_mock()
                self.double_click(self.card(mode).title_label)
                self.handler.assert_not_called()
                self.close.assert_not_called()

    def test_grid_cover_keeps_natural_aspect_in_short_window(self):
        card = self.card('grid')
        card.fit_cell(310, 315)
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

    def test_explicit_button_places_into_selected_empty_cell(self):
        for mode in ('list', 'grid'):
            card = self.card(mode)
            self.assertEqual(card.play_button.text(), '置入视频')
            self.handler.reset_mock()
            QTest.mouseClick(card.play_button, Qt.LeftButton)
            self.handler.assert_called_once_with(self.item, self.target)

    def test_legacy_occupied_destination_still_reaches_the_same_target(self):
        card = self.card('grid')
        self.targets[:] = [(self.target, '第 1 格 · 主播', True)]
        self.page.refresh_targets()
        self.assertEqual(card.play_button.text(), '置入视频')
        QTest.mouseClick(card.play_button, Qt.LeftButton)
        self.handler.assert_called_once_with(self.item, self.target)

    def test_removed_target_does_not_silently_place_into_another_cell(self):
        card = self.card('grid')
        other = TileFixture({})
        self.targets[:] = [(other, '第 2 格 · 空格', False)]
        QTest.mouseClick(card.play_button, Qt.LeftButton)
        self.handler.assert_not_called()
        self.assertIsNone(self.page.placement.target())
        self.assertTrue(card.play_button.isEnabled())  # The spatial picker can offer the remaining cell.

    def test_no_target_disables_placement(self):
        self.targets.clear()
        self.page.refresh_targets()
        card = self.card('grid')
        self.assertFalse(card.play_button.isEnabled())
        self.page._play_item(self.item)
        self.handler.assert_not_called()

    def test_target_preference_and_initial_empty_selection(self):
        occupied = TileFixture({'room_id': 'fixture'})
        self.targets.insert(0, (occupied, '第 2 格 · 主播', True))
        self.page.refresh_targets()
        self.assertIs(self.page.placement.target(), self.target)
        self.page.refresh_targets(occupied)
        self.assertIs(self.page.placement.target(), occupied)
        self.assertEqual(self.page.placement.action_text(), '替换视频')

    def test_shared_controls_and_custom_gallery_preserve_page_regions(self):
        from ddm.ui import ActionButton, SegmentedControl, SelectControl, SettingsSurface, TitleLabel
        from ddm_plugin_bili_watchlater.page_panels import PlacementButton, ThumbnailSurface
        self.assertIsInstance(self.page.title_label, TitleLabel)
        self.assertNotIsInstance(self.page.browse_tools, SettingsSurface)
        self.assertIsInstance(self.page.filter_combo, SelectControl)
        self.assertIsInstance(self.page.sort_combo, SelectControl)
        self.assertIsInstance(self.page.filter_tabs, SegmentedControl)
        self.assertEqual(self.page.fab_refresh.text(), '')
        self.assertGreaterEqual(self.page.header_layout.indexOf(self.page.fab_refresh), 0)
        self.assertEqual(self.page.column.body_layout.indexOf(self.page.fab_bar),
                         self.page.column.body_layout.count() - 1)
        for mode in ('grid', 'list'):
            card = self.card(mode)
            self.assertIsInstance(card, ThumbnailSurface)
            self.assertIsInstance(card.play_button, PlacementButton)

    def test_grid_button_fits_below_natural_cover_at_supported_widths(self):
        card = self.card('grid')
        for width in (280, 310, 420):
            card.resize(width, 400)
            card.fit_cell(width, round(width * 9 / 16) + 140)
            card.layout().activate()
            self.assertAlmostEqual(card.cover.width() / card.cover.height(), 16 / 9, delta=.03)
            self.assertIs(card.play_button.parentWidget(), card.uploader_row)
            self.assertTrue(card.uploader_row.rect().contains(card.play_button.geometry()))

    def test_filter_segments_use_existing_filter_state_in_both_directions(self):
        self.page.filter_tabs.setValue('unfinished')
        self.assertEqual(self.page.filter_combo.currentData(), 'unfinished')
        self.page.filter_combo.setCurrentIndex(self.page.filter_combo.findData('finished'))
        self.assertTrue(self.page.filter_tabs.buttons['finished'].isChecked())
        self.page._clear_filters()
        self.assertTrue(self.page.filter_tabs.buttons['all'].isChecked())

    def test_two_line_titles_preserve_unicode_and_plain_text(self):
        from ddm_plugin_bili_watchlater.page_panels import two_line_title
        from PySide6.QtGui import QFontMetrics
        text = '🌟看星星<b>保留纯文本</b> —— 一起看看遥远的夜空和宇宙' * 3
        label = self.card('grid').title_label
        label.set_full_text(text)
        label.resize(230, 50)
        label._apply_elide()
        self.assertEqual(label.full_text(), text)
        self.assertEqual(label.textFormat(), Qt.PlainText)
        lines = label.text().split('\n')
        self.assertEqual(len(lines), 2)
        self.assertTrue(label.text().startswith('🌟'))
        metrics = QFontMetrics(label.font())
        self.assertTrue(all(metrics.horizontalAdvance(line) <= 230 for line in lines))
        emoji_line = two_line_title('🌟abc XYZ', label.font(), metrics.horizontalAdvance('🌟abc') + 2)
        self.assertEqual(emoji_line.replace('\n', '').replace(' ', ''), '🌟abcXYZ')

    def test_multiselect_mark_is_above_the_cover_and_does_not_intercept_clicks(self):
        for mode in ('list', 'grid'):
            card = self.card(mode)
            card.set_multi_mode(True, True)
            mark = card._selection_mark
            self.assertIs(mark.parentWidget(), card.cover)
            self.assertTrue(mark.testAttribute(Qt.WA_TransparentForMouseEvents))
            self.assertFalse(mark.isHidden())
            self.assertTrue(mark.selected)
            card.set_multi_mode(False)
            self.assertTrue(mark.isHidden())

    def test_cached_context_menu_refreshes_placement_label_and_availability(self):
        from ddm_plugin_bili_watchlater.widgets import refresh_item_context_menu
        card = self.card('grid')
        menu = card._context_menu()
        card.set_placement_action('替换视频', True)
        refresh_item_context_menu(menu, card)
        self.assertEqual(menu.wl_play_action.text(), '替换视频')
        card.set_placement_action('置入视频', False)
        refresh_item_context_menu(menu, card)
        self.assertFalse(menu.wl_play_action.isEnabled())

    def test_owner_and_statistics_update_without_losing_the_item_title(self):
        card = self.card('grid')
        replacement = models.WatchLaterItem(aid=2, bvid='BV1yy411c7mD',
            title='新的测试标题', owner_name='新的 UP 主', view_count=12345, danmaku_count=456)
        card.update_item(replacement)
        self.assertEqual(card.owner_label.full_text(), '新的 UP 主')
        self.assertEqual(card.title_label.full_text(), replacement.title)
        self.assertEqual(card.cover._item.view_count, 12345)
        card.set_danmaku_count(789)
        self.assertEqual(card.cover._item.danmaku_count, 789)

    def test_visible_controls_fit_normal_and_multiselect_at_minimum_width(self):
        self.item.view_count = self.item.danmaku_count = 0
        self.page._items = [self.item]
        self.page._loaded_once = True
        self.page._has_session = self.page._has_csrf = True
        with patch.object(self.page, '_load'), patch.object(self.page, '_cache_fresh', return_value=True), \
                patch.object(self.page, '_apply_saved_geometry', return_value=True), \
                patch.object(self.page, '_start_entrance'):
            self.page.show()
            self.page._render()
            for width in (720, 980, 1320):
                for multi in (False, True):
                    with self.subTest(width=width, multi=multi):
                        self.page.resize(width, 660)
                        self.page._cancel_multiselect()
                        if multi:
                            self.page._on_multi_delete_clicked()
                        APP.processEvents()
                        for widget in (self.page.search, self.page.filter_tabs, self.page.sort_combo,
                                       self.page.multi_delete_button, self.page.multi_cancel_button,
                                       self.page.view_switch, self.page.placement.combo, self.page.fab_refresh):
                            if widget.isVisible():
                                rect = QRect(widget.mapTo(self.page, QPoint()), widget.size())
                                self.assertTrue(self.page.rect().contains(rect), repr(widget))
            self.page.set_pick_target('第 1 格')
            APP.processEvents()
            self.assertIs(self.page.pick_banner.parentWidget(), self.page.browse_tools)
            self.assertGreater(self.page.pick_banner.mapTo(self.page, QPoint()).y(), self.page.search.y())


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

    def test_selector_lists_visible_cells_with_occupancy(self):
        tiles = self.pane_layout()
        options = self.plugin._placement_targets()
        self.assertEqual([option[0] for option in options], tiles[:3] + [tiles[5]])
        self.assertEqual([option[2] for option in options], [True, True, True, False])
        self.assertEqual(options[-1][1], '第 6 格 · 空格')

    def test_explicit_placement_replaces_occupied_target(self):
        with patch.object(self.plugin, '_start_vod_for_tile') as play:
            self.plugin._place_item_from_page(self.item, self.playing)
        play.assert_called_once_with(self.playing, self.item)

    def test_explicit_stale_target_never_falls_back_to_empty_cell(self):
        tiles = self.pane_layout()
        with patch.object(self.plugin, '_start_vod_for_tile') as play, \
                patch.object(self.plugin, '_notify_warning') as warning:
            self.plugin._place_item_from_page(self.item, tiles[3])
        play.assert_not_called()
        warning.assert_called_once()

    def test_cell_context_placement_closes_after_explicit_action(self):
        self.plugin.page = Mock()
        self.plugin._pick_target_tile = self.playing
        with patch.object(self.plugin, '_start_vod_for_tile') as play:
            self.plugin._place_item_from_page(self.item, self.playing)
        play.assert_called_once_with(self.playing, self.item)
        self.plugin.page.close.assert_called_once()
        self.assertIsNone(self.plugin._pick_target_tile)


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


class BottomBarLayoutTests(unittest.TestCase):
    def setUp(self):
        from ddm.tile_widgets import Tile
        from ddm.plugin_controls import TileControls
        self.plugin = plugin_module.BiliWatchLaterPlugin()
        self.tile = Tile({'room_id': 'BV1xx411c7mD', 'live': False})
        self.tile.set_recording_available(False)
        self.controls = TileControls(self.tile)
        self.tile._plugin_controls = self.controls
        self.controls.add('bili_watchlater', 'progress', self.plugin._make_progress_control, 61, 0)
        self.controls.add('bili_watchlater', 'subtitle', self.plugin._make_subtitle_control, 59, 1)
        self.progress = self.plugin._bar_progress[id(self.tile)]['widget']
        self.subtitle = self.plugin._subtitle_controls[id(self.tile)]['widget']
        self.progress.show()
        self.subtitle.show()
        self.tile.show()

    def tearDown(self):
        self.controls.shutdown()
        self.tile.close()
        self.tile.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_progress_and_subtitle_share_allocated_width_without_overlap(self):
        for width in (520, 800, 1100, 520):
            with self.subTest(width=width):
                self.tile.resize(width, 400)
                self.controls.invalidate()
                APP.processEvents()
                self.plugin._fit_vod_bottom_bar(self.tile)
                APP.processEvents()
                visible = [entry.wrapper for entry in self.controls.entries if not entry.wrapper.isHidden()]
                self.assertLessEqual(sum(widget.width() for widget in visible), self.controls.widget.width())
                if not self.progress.parentWidget().isHidden():
                    self.assertTrue(self.progress.rect().contains(self.progress.duration_label.geometry()))
                    right = self.progress.duration_label.mapTo(self.controls.widget, QPoint(self.progress.duration_label.width(), 0)).x()
                    subtitle_left = self.subtitle.mapTo(self.controls.widget, QPoint()).x()
                    if not self.subtitle.parentWidget().isHidden():
                        self.assertLessEqual(right, subtitle_left)

    def test_progress_width_declares_both_clocks_and_seek_handle(self):
        entry = next(entry for entry in self.controls.entries if entry.key == 'progress')
        self.assertGreaterEqual(entry.control.minimum_width, self.progress.minimumSizeHint().width())

    def test_narrow_tile_uses_line_then_restores_full_bar_when_widened(self):
        binding = SimpleNamespace(tile=self.tile, state='playing', line=Mock(), strip=None)
        with patch.object(self.plugin, '_binding_for_tile', return_value=binding), \
                patch.object(self.plugin, '_sync_position_badge'), \
                patch.object(self.plugin, '_sync_shortcut_hosts'):
            for width, visible in ((300, False), (1100, True), (300, False), (1100, True)):
                self.tile.resize(width, 400)
                self.controls.invalidate()
                APP.processEvents()
                self.plugin._sync_fullscreen_state(self.tile)
                self.assertEqual(self.plugin._bar_progress[id(self.tile)]['visible'], visible)
                binding.line.setVisible.assert_called_with(not visible)


if __name__ == '__main__':
    unittest.main()
