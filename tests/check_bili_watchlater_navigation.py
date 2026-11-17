"""Native single-window navigation, keyboard return and settings presentation."""
from contextlib import contextmanager
import unittest
from unittest.mock import patch
import check_bili_watchlater_page_controls as controls
from check_bili_watchlater import APP, models
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame, QPushButton
from ddm import theme
from ddm.ui import SettingsSurface
from ddm_plugin_bili_watchlater.page_shell import background_color, card_color
from ddm_plugin_bili_watchlater.preferences import DEFAULTS


class NavigationTests(unittest.TestCase):
    setUp = controls.PageControlsTests.setUp
    tearDown = controls.PageControlsTests.tearDown

    @contextmanager
    def shown(self):
        with patch.object(self.page, '_load'), patch.object(self.page, '_cache_fresh', return_value=True), \
                patch.object(self.page, '_apply_saved_geometry', return_value=True), patch.object(self.page, '_start_entrance'):
            self.page.resize(980, 700)
            self.page.show()
            self.page.activateWindow()
            APP.processEvents()
            yield

    def test_main_window_has_custom_chrome_and_keeps_one_native_handle(self):
        with self.shown():
            page = self.page
            self.assertTrue(page.windowFlags() & Qt.FramelessWindowHint)
            self.assertFalse(page.windowFlags() & Qt.WindowMinimizeButtonHint)
            self.assertTrue(page.testAttribute(Qt.WA_TranslucentBackground))
            self.assertTrue(page.window_header.breadcrumb.isHidden())
            handle = page.winId()
            for open_panel, field in ((page._open_settings, '_settings_dialog'),
                                      (lambda: page._select_item(self.item), '_placement_picker')):
                open_panel()
                panel = getattr(page, field)
                self.assertFalse(panel.isWindow())
                self.assertTrue(page.window_header.breadcrumb.isVisible())
                self.assertIn('稍后再看  ›  ', page.window_header.breadcrumb.text())
                self.assertIs(panel.window(), page)
                self.assertEqual(page.winId(), handle)
                self.assertIs(page.navigation.currentWidget(), panel)
                self.assertFalse(page.browser_view.isVisible())
                self.assertFalse(page._refresh_shortcut.isEnabled())
                page._schedule_visible_covers()
                self.assertFalse(page._cover_scroll_timer.isActive())
                panel.reject()
                self.assertIs(page.navigation.currentWidget(), page.browser_view)
                self.assertTrue(page.window_header.breadcrumb.isHidden())
                self.assertEqual(page.winId(), handle)
                self.assertTrue(page._refresh_shortcut.isEnabled())

    def test_back_and_escape_keep_autosaved_settings_without_closing_browser(self):
        with self.shown():
            self.page._open_settings()
            self.page._settings_dialog.autoplay.setChecked(True)
            QTest.mouseClick(self.page.window_header.back, Qt.LeftButton)
            self.assertIsNone(self.page._settings_dialog)
            self.assertEqual(self.values, dict(autoplay=True))
            self.assertTrue(self.page.isVisible())
            self.page._select_item(self.item)
            self.page.window_header.back.setFocus()
            QTest.keyClick(self.page.window_header.back, Qt.Key_Escape)
            self.assertIsNone(self.page._placement_picker)
            self.handler.assert_not_called()
            self.assertFalse(self.page.window_header.back.isVisible())

    def test_back_preserves_deep_list_scroll_search_and_sort(self):
        page = self.page
        page._items = [models.WatchLaterItem(aid=i+1, bvid=f'fixture{i}', title=f'视频{i}',
                                           view_count=0, danmaku_count=0) for i in range(100)]
        page._loaded_once = True
        with self.shown():
            page.search.setText('视频')
            page._search_timer.stop()
            page.sort_combo.setCurrentIndex(1)
            page._on_view_mode_changed('list')
            page._render_limit = 100
            page._render(keep_position=True)
            APP.processEvents()
            bar = page.list_scroll.verticalScrollBar()
            bar.setValue(bar.maximum() // 2)
            value, key = bar.value(), page._visible_anchor_key()
            page._open_settings()
            page._settings_dialog.auto_play.setChecked(False)
            page._back_to_browser()
            APP.processEvents()
            self.assertEqual(page._view_mode, 'list')
            self.assertEqual(page.search.text(), '视频')
            self.assertEqual(page.sort_combo.currentIndex(), 1)
            self.assertEqual(bar.value(), value)
            self.assertEqual(page._visible_anchor_key(), key)

    def test_opening_other_detail_releases_previous_and_never_places(self):
        with self.shown():
            self.page._open_settings()
            old = self.page._settings_dialog
            self.page._select_item(self.item)
            self.assertTrue(old._finished)
            self.assertIsNone(self.page._settings_dialog)
            self.assertEqual(self.page.navigation.count(), 3)
            self.page._open_settings()
            self.assertIsNone(self.page._placement_picker)
            self.assertEqual(self.page.navigation.count(), 2)
            self.handler.assert_not_called()

    def test_hiding_browser_releases_details_and_reopens_list(self):
        with self.shown():
            for open_panel in (self.page._open_settings, lambda: self.page._select_item(self.item)):
                open_panel()
                self.page.hide()
                self.assertIsNone(self.page._settings_dialog)
                self.assertIsNone(self.page._placement_picker)
                self.assertIsNone(self.page._inline_panel)
                self.assertEqual(self.page.navigation.count(), 2)
                self.page.show()
                APP.processEvents()
                self.assertIs(self.page.navigation.currentWidget(), self.page.browser_view)

    def test_no_reset_or_apply_button_and_retains_prefetch_dependency(self):
        self.page._open_settings()
        panel = self.page._settings_dialog
        self.assertFalse(any(button.text() in ('恢复默认', '应用', '取消') for button in panel.findChildren(QPushButton)))
        panel.autoplay.setChecked(True)
        panel.prefetch_next.setChecked(True)
        panel.autoplay.setChecked(False)
        self.assertFalse(panel.prefetch_next.isEnabled())
        self.assertFalse(panel.prefetch_next.isChecked())
        self.assertEqual(controls.read_preferences(self.context), DEFAULTS)

    def test_flat_material_tracks_host_settings_in_both_themes(self):
        from ddm.settings_material import BASE_COLORS
        original = theme.is_light()
        try:
            for mode in ('dark', 'light'):
                theme.apply(mode)
                self.assertEqual(background_color(), BASE_COLORS[theme.is_light()]['background'])
                self.assertEqual(card_color(), BASE_COLORS[theme.is_light()]['card'])
                self.page._open_settings()
                panel = self.page._settings_dialog
                self.assertTrue(panel.window().property('settingsGlassContent'))
                self.assertEqual(len(panel.findChildren(SettingsSurface)), 3)
                self.assertEqual(len(panel.findChildren(QFrame, 'WatchLaterSettingsDivider')), 8)
                panel.reject()
        finally:
            theme.apply('light' if original else 'dark')

    def test_window_header_maximize_restore_and_close(self):
        with self.shown():
            header = self.page.window_header
            with patch.object(self.page, 'showMaximized') as maximize, \
                    patch.object(self.page, 'showNormal') as restore:
                header.toggle_maximize()
                maximize.assert_called_once()
                with patch.object(self.page, 'isMaximized', return_value=True):
                    header.toggle_maximize()
                restore.assert_called_once()
            self.page._open_settings()
            QTest.mouseClick(header.close_button, Qt.LeftButton)
            self.assertFalse(self.page.isVisible())
            self.assertIsNone(self.page._settings_dialog)

    def test_edges_and_corners_intercept_child_pages_and_dispatch_native_resize(self):
        with self.shown():
            page = self.page
            for open_panel in (lambda: None, page._open_settings, lambda: page._select_item(self.item)):
                open_panel()
                APP.processEvents()
                for handle in page._resize_handles:
                    point = handle.mapTo(page, handle.rect().center())
                    self.assertIs(page.childAt(point), handle)
                    self.assertTrue(handle.isVisible())
                    with patch.object(QWindow, 'startSystemResize', return_value=True) as resize:
                        QTest.mouseClick(handle, Qt.LeftButton)
                        resize.assert_called_once_with(handle.edges)
                page._back_to_browser()
            self.assertEqual(page._resize_handles[0].cursor().shape(), Qt.SizeHorCursor)
            self.assertEqual(page._resize_handles[2].cursor().shape(), Qt.SizeVerCursor)
            self.assertEqual(page._resize_handles[-1].cursor().shape(), Qt.SizeFDiagCursor)

    def test_resize_handles_follow_geometry_and_hide_when_maximized_or_embedded(self):
        with self.shown():
            page = self.page
            page.resize(860, 620)
            APP.processEvents()
            corner = page._resize_handles[-1]
            self.assertEqual(corner.geometry().bottomRight(), QPoint(859, 619))
            with patch.object(page, 'isMaximized', return_value=True):
                page._sync_resize_handles()
                self.assertTrue(all(handle.isHidden() for handle in page._resize_handles))
            with patch.object(page, 'isWindow', return_value=False):
                page._sync_resize_handles()
                self.assertTrue(all(handle.isHidden() for handle in page._resize_handles))
            page._sync_resize_handles()
            self.assertTrue(all(handle.isVisible() for handle in page._resize_handles))

    def test_resized_dimensions_restore_using_existing_geometry_store(self):
        store = {}
        from types import SimpleNamespace
        self.page._geometry_settings = SimpleNamespace(value=lambda key:store.get(key),
            setValue=lambda key,value:store.__setitem__(key,value))
        with self.shown():
            self.page.resize(850, 610)
            APP.processEvents()
            self.page._save_geometry_now()
            self.page.resize(980, 700)
            from ddm_plugin_bili_watchlater.page import WatchLaterPage
            self.assertTrue(WatchLaterPage._apply_saved_geometry(self.page))
            self.assertEqual((self.page.width(), self.page.height()), (850, 610))

    def test_inline_delete_confirmation_requires_explicit_accept_and_rejects_old_account(self):
        self.page._has_session = self.page._has_csrf = True
        with self.shown(), patch.object(self.page, '_begin_remove') as remove:
            self.page._confirm_remove(self.item)
            panel = self.page._inline_panel
            self.assertFalse(panel.isWindow())
            self.assertIs(panel.window(), self.page)
            self.assertIs(panel.focusProxy(), panel.cancel_button)
            self.page._escape_navigation()
            remove.assert_not_called()
            self.page._confirm_remove(self.item)
            self.page._inline_panel.accept()
            remove.assert_called_once_with(self.item)
            remove.reset_mock()
            self.page._confirm_remove(self.item)
            self.page._generation += 1
            self.page._inline_panel.accept()
            remove.assert_not_called()
            self.assertEqual(self.page.navigation.count(), 1)

    def test_close_cancels_inline_clear_confirmation(self):
        self.page._has_session = self.page._has_csrf = True
        self.page._items = [self.item]
        with self.shown(), patch.object(self.page, '_begin_clear') as clear:
            self.page._confirm_clear()
            self.assertIsNotNone(self.page._inline_panel)
            self.page.close()
            self.assertIsNone(self.page._inline_panel)
            clear.assert_not_called()

    def test_detail_pages_fit_existing_narrow_window_without_forcing_resize(self):
        with self.shown():
            self.page.resize(720, 520)
            APP.processEvents()
            self.assertEqual(self.page.width(), 720)
            for open_panel in (self.page._open_settings, lambda: self.page._select_item(self.item)):
                open_panel()
                APP.processEvents()
                self.assertEqual(self.page.width(), 720)
                self.assertEqual(self.page.height(), 520)
                panel = self.page._inline_panel
                self.assertLessEqual(panel.width(), 720)
                self.page._back_to_browser()

    def test_settings_trace_is_bounded_and_contains_only_numeric_timings(self):
        with self.shown(), patch.object(self.page, '_log') as log:
            for _ in range(4):
                self.page._open_settings()
                APP.processEvents()
                self.page._back_to_browser()
            records = [call.args[0] for call in log.call_args_list]
            self.assertEqual(sum('phase=construct ' in record for record in records), 1)
            self.assertEqual(sum('phase=reuse ' in record for record in records), 2)
            self.assertTrue(all(record.startswith('settings timing phase=') for record in records))
            self.assertTrue(all('elapsed_ms=' in record and 'stage_ms=' in record for record in records))


if __name__ == '__main__':
    unittest.main()
