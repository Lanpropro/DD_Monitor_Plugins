"""Spatial placement, plugin settings, loading feedback and layout regressions."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from check_bili_watchlater import APP, models, plugin_module
from check_bili_watchlater_interactions import TileFixture, pages
from ddm_plugin_bili_watchlater.placement_layout import PlacementCell, PlacementSnapshot
from ddm_plugin_bili_watchlater.preferences import read_preferences
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest


class PageControlsTests(unittest.TestCase):
    def setUp(self):
        self.values = {}
        self.context = SimpleNamespace(credentials=lambda _: None,
            setting=lambda key, default: self.values.get(key, default),
            set_setting=lambda key, value: self.values.__setitem__(key, value))
        self.page = pages.WatchLaterPage(self.context)
        self.page._geometry_settings = Mock()
        self.tiles = [TileFixture({'room_id': str(i + 1), 'uname': f'主播{i+1}'}) for i in range(3)]
        self.tiles += [TileFixture({}) for _ in range(3)]
        self.snapshot = PlacementSnapshot('1+5', 16 / 9, [
            PlacementCell(i, QRectF((i % 3) / 3, (i // 3) / 2, 1/3, 1/2),
                None if i in (3, 4) else tile, f'主播{i+1}' if i < 3 else '弹幕' if i in (3, 4) else '空视频格',
                'video' if i < 3 else 'chat' if i in (3, 4) else 'empty', str(i))
            for i, tile in enumerate(self.tiles)])
        self.handler = Mock()
        self.options = [(cell.target, cell.title, cell.kind == 'video') for cell in self.snapshot.cells if cell.target]
        self.page.set_placement_handlers(lambda: self.options, self.handler, lambda: self.snapshot)
        self.item = models.WatchLaterItem(aid=1, bvid='BV1xx411c7mD', title='测试视频', view_count=0, danmaku_count=0)

    def tearDown(self):
        self.page.shutdown()
        self.page.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def card(self, mode='grid'):
        if mode == 'grid':
            self.page._rebuild_cards([self.item])
            card = self.page._cards[0]
        else:
            card = self.page._make_row(self.item)
        card.resize(500, 320)
        return card

    def test_cover_and_placement_buttons_open_map_without_playing(self):
        for mode in ('list', 'grid'):
            card = self.card(mode)
            for button in (card.cover, card.title_label, card.play_button):
                QTest.mouseClick(button, Qt.LeftButton, pos=QPoint(5, 5))
                picker = self.page._placement_picker
                self.assertIsNotNone(picker)
                self.assertEqual(len(picker.map.buttons), 6)
                self.handler.assert_not_called()
                picker.reject()

    def test_empty_cell_places_and_occupied_cell_replaces_the_clicked_target(self):
        for index in (5, 0):
            self.page._select_item(self.item)
            picker = self.page._placement_picker
            QTest.mouseClick(picker.map.buttons[index], Qt.LeftButton)
            self.handler.assert_called_with(self.item, self.tiles[index])
            self.assertIsNone(self.page._placement_picker)
        self.assertEqual(self.handler.call_count, 2)

    def test_chat_cells_are_visible_but_cannot_dispatch_placement(self):
        self.page._select_item(self.item)
        picker = self.page._placement_picker
        for index in (3, 4):
            self.assertFalse(picker.map.buttons[index].isEnabled())
            QTest.mouseClick(picker.map.buttons[index], Qt.LeftButton)
        self.handler.assert_not_called()

    def test_double_selection_reuses_one_picker(self):
        self.page._select_item(self.item)
        picker = self.page._placement_picker
        self.page._select_item(self.item)
        self.assertIs(self.page._placement_picker, picker)
        self.handler.assert_not_called()

    def test_changed_layout_or_room_requires_a_fresh_click(self):
        self.page._select_item(self.item)
        picker = self.page._placement_picker
        cells = [replace(cell) for cell in self.snapshot.cells]
        cells[0] = replace(cells[0], identity='new-room', title='新主播')
        self.snapshot = replace(self.snapshot, cells=cells)
        self.page._commit_placement(self.item, picker, 0)
        self.handler.assert_not_called()
        self.assertIn('变化', picker.hint.text())
        self.page._commit_placement(self.item, picker, 0)
        self.handler.assert_called_once_with(self.item, self.tiles[0])

    def test_single_delete_icon_keeps_confirmation_and_multiselect_does_not_place(self):
        self.page._has_session = self.page._has_csrf = True
        with patch.object(self.page, '_confirm_remove') as confirm:
            for mode in ('grid', 'list'):
                card = self.card(mode)
                card.set_remove_available(True)
                QTest.mouseClick(card.remove_button, Qt.LeftButton)
            self.assertEqual(confirm.call_count, 2)
        self.page._on_multi_delete_clicked()
        card = self.card()
        card.set_multi_mode(True, False)
        QTest.mouseClick(card.cover, Qt.LeftButton)
        self.assertIsNone(self.page._placement_picker)
        self.handler.assert_not_called()

    def test_settings_persist_only_plugin_preferences_and_apply_columns(self):
        self.page._open_settings()
        dialog = self.page._settings_dialog
        dialog.columns.setCurrentIndex(dialog.columns.findData(4))
        self.assertFalse(dialog.card_size.isEnabled())
        dialog.autoplay.setChecked(True)
        self.assertEqual(self.values, dict(grid_columns=4, autoplay=True))
        self.assertIs(self.page._inline_panel, dialog)
        self.card()
        self.page._fit_grid()
        self.assertEqual(self.page._grid_columns, 4)
        self.assertTrue(read_preferences(self.context)['autoplay'])

    def test_settings_autosave_survives_return_without_saving_unchanged_defaults(self):
        self.page._open_settings()
        self.page._settings_dialog.autoplay.setChecked(True)
        self.page._settings_dialog.reject()
        self.assertEqual(self.values, dict(autoplay=True))
        self.assertTrue(read_preferences(self.context)['autoplay'])

    def test_fixed_six_columns_fit_narrow_window_with_compact_actions(self):
        self.page._items = [models.WatchLaterItem(aid=i+1, bvid=f'fixture{i}', title='测试', view_count=0, danmaku_count=0)
                            for i in range(6)]
        self.page._loaded_once = True
        self.page.preferences['grid_columns'] = 6
        with patch.object(self.page, '_load'), patch.object(self.page, '_cache_fresh', return_value=True), \
                patch.object(self.page, '_apply_saved_geometry', return_value=True), patch.object(self.page, '_start_entrance'):
            self.page.resize(720, 660)
            self.page.show()
            self.page._render()
            APP.processEvents()
            self.page._fit_grid()
            APP.processEvents()
            viewport = self.page.grid_scroll.viewport()
            for card in self.page._cards:
                self.assertTrue(card.play_button._compact)
                self.assertLessEqual(card.mapTo(viewport, QPoint(card.width(), 0)).x(), viewport.width())
                self.assertTrue(card.rect().contains(card.remove_button.geometry()))

    def test_toolbar_stays_fixed_and_confirmation_buttons_are_in_footer(self):
        self.page._items = [self.item]
        self.page._loaded_once = True
        self.page._has_session = self.page._has_csrf = True
        with patch.object(self.page, '_load'), patch.object(self.page, '_cache_fresh', return_value=True), \
                patch.object(self.page, '_apply_saved_geometry', return_value=True), patch.object(self.page, '_start_entrance'):
            self.page.show()
            self.page._render()
            for width in (720, 980, 1320):
                self.page.resize(width, 660)
                self.page._cancel_multiselect()
                APP.processEvents()
                controls = (self.page.sort_combo, self.page.view_switch, self.page.settings_button, self.page.fab_top)
                before = [control.mapTo(self.page, QPoint()) for control in controls]
                self.page._on_multi_delete_clicked()
                APP.processEvents()
                self.assertEqual(before, [control.mapTo(self.page, QPoint()) for control in controls])
                self.assertIs(self.page.multi_confirm_button.parentWidget(), self.page.fab_bar)
                self.assertIs(self.page.multi_cancel_button.parentWidget(), self.page.fab_bar)
                self.assertEqual(self.page.multi_delete_button.text(), '')
                self.assertEqual(self.page.fab_top.text(), '')

    def test_refresh_spin_stops_on_completion_hide_and_reduced_motion(self):
        from ddm_plugin_bili_watchlater.page_panels import BusyRefreshButton
        button = BusyRefreshButton(self.page)
        button.setParent(None)
        self.addCleanup(button.deleteLater)
        button.show()
        with patch('ddm.motion.enabled', return_value=True):
            button.set_busy(True)
            self.assertEqual(button.spin.state(), QAbstractAnimation.Running)
            button.spin.setCurrentTime(250)
            self.assertAlmostEqual(button.angle, 90.)
            self.assertEqual(button.text(), '')
            button.set_busy(False)
            self.assertEqual(button.angle, 0)
            self.assertEqual(button.spin.state(), QAbstractAnimation.Stopped)
            button.set_busy(True)
            button.hide()
            self.assertEqual(button.spin.state(), QAbstractAnimation.Stopped)
        with patch('ddm.motion.enabled', return_value=False):
            button.set_busy(True)
            self.assertEqual(button.spin.state(), QAbstractAnimation.Stopped)
            self.assertEqual(button.angle, 0)
        button.close()

    def test_empty_state_text_has_enough_height_for_wrapping(self):
        from ddm_plugin_bili_watchlater.widgets import StatusView
        view = StatusView()
        try:
            for size in (10, 14, 20):
                view.message.setFont(QFont(APP.font().family(), size))
                view.resize(650, 400)
                view.set_state('没有匹配的视频', '试试更换关键词，或清除当前筛选条件。' * 3, primary='清除搜索/筛选')
                view.show()
                APP.processEvents()
                self.assertGreaterEqual(view.message.height(), view.message.heightForWidth(view.message.width()))
                self.assertLess(view.title.geometry().bottom(), view.message.y())
                self.assertLess(view.message.geometry().bottom(), view.primary_button.y())
                height = view.content.height()
                for _ in range(5):
                    view._fit_text()
                self.assertEqual(view.content.height(), height)
        finally:
            view.close()
            view.deleteLater()


class AutoplayTests(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.BiliWatchLaterPlugin()
        self.enabled = True
        self.plugin.context = SimpleNamespace(setting=lambda key, default: self.enabled if key == 'autoplay' else default)
        self.tile, self.player = Mock(), Mock()
        self.binding = SimpleNamespace(bvid='fixture', state='playing', ended=False, strip=None, card=Mock())
        self.plugin._bindings['fixture'] = self.binding
        for name in ('_cancel_host_retries', '_hide_pause_overlay', '_sync_position_badge', '_sync_subtitle_control'):
            setattr(self.plugin, name, Mock())
        self.plugin._has_next = Mock(return_value=True)
        self.plugin._next_binding = Mock()

    def test_eof_queues_exactly_one_advance(self):
        self.plugin._enter_ended(self.binding, self.tile, self.player)
        self.plugin._enter_ended(self.binding, self.tile, self.player)
        APP.processEvents()
        self.plugin._next_binding.assert_called_once_with(self.binding)

    def test_default_off_and_last_item_do_not_advance(self):
        self.enabled = False
        self.plugin._enter_ended(self.binding, self.tile, self.player)
        APP.processEvents()
        self.plugin._next_binding.assert_not_called()
        self.enabled = True
        self.binding.state = 'playing'
        self.plugin._has_next.return_value = False
        self.plugin._enter_ended(self.binding, self.tile, self.player)
        APP.processEvents()
        self.plugin._next_binding.assert_not_called()

    def test_replaced_binding_shutdown_and_setting_change_cancel_queued_advance(self):
        for action in ('replace', 'shutdown', 'disable'):
            self.binding.state = 'playing'
            self.plugin._shutting_down = False
            self.plugin._bindings['fixture'] = self.binding
            self.enabled = True
            self.plugin._enter_ended(self.binding, self.tile, self.player)
            if action == 'replace':
                self.plugin._bindings.clear()
            elif action == 'shutdown':
                self.plugin._shutting_down = True
            else:
                self.enabled = False
            APP.processEvents()
        self.plugin._next_binding.assert_not_called()

    def test_next_path_uses_next_part_before_next_video_in_the_same_tile(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        item = SimpleNamespace(parts=2)
        target = TileFixture({})
        binding = SimpleNamespace(item=item, bvid='first', part=0, quality=0, tile=target, preview=None, subtitles=[])
        plugin._host_tiles = lambda: [target]
        with patch.object(plugin, '_cancel_binding_media'), patch.object(plugin, '_start_resolution') as resolve, \
                patch.object(plugin, '_next_item_for') as next_item:
            plugin._next_binding(binding)
            self.assertEqual(binding.part, 1)
            resolve.assert_called_once_with(binding, start=0.)
            next_item.assert_not_called()
        next_video = models.WatchLaterItem(aid=2, bvid='second')
        with patch.object(plugin, '_next_item_for', return_value=next_video), \
                patch.object(plugin, '_start_vod_for_tile') as play:
            plugin._next_binding(binding)
            play.assert_called_once_with(target, next_video)


class LayoutSnapshotTests(unittest.TestCase):
    def test_one_plus_five_includes_content_and_locks_two_chat_cells(self):
        from ddm.pane_danmaku import PaneDanmaku
        from ddm import layouts, resize_layout
        from ddm_plugin_bili_watchlater.placement_layout import snapshot_wall
        tiles = [TileFixture({'room_id': str(i), 'uname': f'主播{i}'}) for i in range(3)]
        tiles += [TileFixture({}) for _ in range(3)]
        wall = SimpleNamespace(tiles=tiles, layout_id='corner', _danmaku_cell=None, _cell_order=lambda: list(range(6)))
        wall.pane_danmaku = PaneDanmaku(wall, {})
        wall.pane_danmaku.settings['corner'] = {3: tiles[0], 4: tiles[1]}
        candidates = tiles[:3] + [tiles[5]]
        snapshot = snapshot_wall(wall, candidates, lambda _: None, lambda _: None)
        self.assertEqual(len(snapshot.cells), 6)
        self.assertEqual([cell.index for cell in snapshot.cells if cell.target is None], [3, 4])
        self.assertEqual(snapshot.cells[0].title, '主播0')
        self.assertIs(snapshot.cells[5].target, tiles[5])
        self.assertEqual(snapshot.cells[5].kind, 'empty')
        tree = resize_layout.build_partition(layouts.BY_ID['corner']['spec'][2])
        weights = resize_layout.validated_weights({}, tree)
        wall.layout_control = SimpleNamespace(border_model=lambda: (tree, weights, {}, lambda _: QSize(), None))
        wall._partitions = {'corner': tree}
        wall.width, wall.height = lambda: 1200, lambda: 700
        wall.grid = SimpleNamespace(spacing=lambda: 8)
        snapshot = snapshot_wall(wall, candidates, lambda _: None, lambda _: None)
        self.assertAlmostEqual(snapshot.aspect, 12 / 7)
        for cell in snapshot.cells:
            self.assertTrue(QRectF(0, 0, 1, 1).contains(cell.rect))


if __name__ == '__main__':
    unittest.main()
