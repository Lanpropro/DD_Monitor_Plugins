"""Offline shutdown/startup snapshots and live autosave/button regressions."""
from copy import deepcopy
from types import SimpleNamespace
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from check_bili_watchlater import APP, models, plugin_module
from check_bili_watchlater_interactions import TileFixture
from check_bili_watchlater_preferences import context
from ddm_plugin_bili_watchlater.playback_session import KEY, seconds
from ddm_plugin_bili_watchlater.button_feedback import StableActionButton
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFocusEvent


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.values = dict(resume_session=True)
        self.tiles = [TileFixture({}) for _ in range(6)]
        self.chats = {3: None, 4: None}
        self.cells = dict(enumerate(self.tiles))
        panes = SimpleNamespace(bindings=lambda: self.chats, cell_tiles=lambda: self.cells,
            cell_for_tile=lambda tile: next((cell for cell, candidate in self.cells.items() if candidate is tile), None))
        self.window = SimpleNamespace(wall=SimpleNamespace(layout_id='corner', pane_danmaku=panes, tiles=self.tiles), _play_on=Mock())
        self.plugin = self.make_plugin()
        self.tile = self.tiles[5]
        self.item = models.WatchLaterItem(aid=1, bvid='BV1xx411c7mD', title='恢复视频', videos=3, cid=7, duration=900)
        self.binding = plugin_module._VodBinding(self.item, object(), self.tile)
        self.binding.part, self.binding.progress, self.binding.duration = 1, 50., 900.
        self.binding.quality, self.binding.state = 80, 'paused'
        self.binding.prev_room = {'room_id': '123', 'uname': '主播'}
        self.tile.room = dict(room_id=self.item.bvid, watchlater_bvid=self.item.bvid)
        self.tile.paused = True
        self.plugin._bindings[self.item.bvid] = self.binding

    def make_plugin(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        plugin.context = context(self.values)
        plugin.host.window = lambda: self.window
        plugin.playback.cookie_snapshot = lambda: {'DedeUserID': 'fixture-account', 'SESSDATA': 'fixture-secret'}
        plugin._resolve_cell_from_tile = lambda tile: tile
        plugin.host.player = lambda tile: None
        plugin._notify_warning = Mock()
        plugin._log = Mock()
        return plugin

    def saved(self):
        self.plugin.playback_session.capture()
        self.tile.room = dict(self.binding.prev_room)
        return deepcopy(self.values[KEY])

    def test_real_host_cookie_contract_without_dedeuserid_can_save_and_restore(self):
        from ddm import bili,plugins as api
        manager=api.PluginManager(plugins_dir='unused',enabled=[])
        with patch.object(bili,'SESSION_DATA','fixture-host-session'), \
                patch.object(bili,'BILI_JCT','fixture-host-csrf'), \
                patch.object(bili,'BUVID3','fixture-host-device'):
            manager.register_credentials('bilibili',bili._cookies,identity_keys=('SESSDATA','bili_jct'))
            self.plugin.context.credentials=manager.credentials
            del self.plugin.playback.cookie_snapshot
            self.assertNotIn('DedeUserID',self.plugin._cookie_snapshot())
            self.plugin.playback_session.capture()
            saved=deepcopy(self.values[KEY])
            self.assertEqual(saved['videos'][0]['progress'],50.)
            serialized=json.dumps(saved)
            for value in ('fixture-host-session','fixture-host-csrf','fixture-host-device'):
                self.assertNotIn(value,serialized)
            self.tile.room=dict(self.binding.prev_room)
            second=self.make_plugin()
            second.context.credentials=manager.credentials
            del second.playback.cookie_snapshot
            with patch.object(second,'_start_vod_for_tile') as start:
                second.playback_session.restore()
                self.assertEqual(start.call_args.kwargs['resume']['progress'],50.)
                self.assertIs(start.call_args.args[0],self.tile)

    def test_changed_host_login_session_never_restores_another_login(self):
        self.plugin.playback.cookie_snapshot=lambda:{'SESSDATA':'fixture-old-login'}
        self.plugin.playback_session.capture()
        self.assertTrue(self.values[KEY])
        self.tile.room=dict(self.binding.prev_room)
        second=self.make_plugin()
        second.playback.cookie_snapshot=lambda:{'SESSDATA':'fixture-other-login'}
        with patch.object(second,'_start_vod_for_tile') as start:
            second.playback_session.restore()
        start.assert_not_called()
        self.assertEqual(self.values[KEY],{})

    def test_shutdown_snapshot_and_new_instance_restore_exact_cell_part_and_progress(self):
        self.plugin.host.player = lambda _: SimpleNamespace(_prop=lambda key, default: 123.5 if key == 'time-pos' else default)
        saved = self.saved()
        self.assertEqual(saved['videos'][0]['progress'], 123.5)
        self.assertNotIn('fixture-secret', json.dumps(saved))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plugin-only.json'
            path.write_text(json.dumps(self.values), encoding='utf-8')
            self.values = json.loads(path.read_text(encoding='utf-8'))
        second = self.make_plugin()
        with patch.object(second, '_start_vod_for_tile') as start:
            second.playback_session.restore()
            tile, item = start.call_args.args
            self.assertIs(tile, self.tile)
            self.assertEqual(item.bvid, self.item.bvid)
            self.assertEqual(item.parts, 3)
            self.assertEqual(start.call_args.kwargs['resume'], dict(part=1, progress=123.5, quality=80, paused=True))
            second.playback_session.restore()
            self.assertEqual(start.call_count, 1)
        self.assertEqual(self.values[KEY], {})

    def test_snapshot_restores_owner_without_waiting_for_network(self):
        self.item.owner_name = 'Fixture UP'
        self.item.owner_mid = 123
        self.item.owner_face = 'https://i0.hdslb.com/bfs/face/fixture.jpg'
        saved = self.saved()
        self.assertEqual(saved['videos'][0]['owner_name'], 'Fixture UP')
        second = self.make_plugin()
        with patch.object(second, '_start_vod_for_tile') as start:
            second.playback_session.restore()
        item = start.call_args.args[1]
        self.assertEqual((item.owner_name, item.owner_mid, item.owner_face),
                         (self.item.owner_name, self.item.owner_mid, self.item.owner_face))

    def test_real_binding_resume_uses_saved_part_and_seek_and_respects_auto_play(self):
        second = self.make_plugin()
        self.values['auto_play'] = False
        self.tile.room = {}
        self.tile.set_room = lambda room: setattr(self.tile, 'room', dict(room or {}))
        resume = dict(part=2, progress=321.25, quality=64, paused=False)
        with patch.object(second.playback, 'start_resolution') as resolve, patch.object(second.playback, 'ensure_tick_timer'):
            second._start_vod_for_tile(self.tile, self.item, resume=resume)
            binding = second._bindings[self.item.bvid]
            self.assertEqual((binding.part, binding.quality, binding.progress), (2, 64, 321.25))
            self.assertTrue(binding.resume_paused)
            resolve.assert_called_once_with(binding, start=321.25)

    def test_exit_during_restore_keeps_saved_progress_instead_of_initial_zero(self):
        self.binding.pending_seek = self.binding.progress = 123.
        player = Mock()
        player._prop.side_effect = lambda key, default=None: 0. if key == 'time-pos' else default
        self.plugin._player_for_binding = lambda _: player
        self.plugin.playback_session.capture()
        self.assertEqual(self.values[KEY]['videos'][0]['progress'], 123.)

    def test_capture_happens_before_closing_cleanup_and_duplicate_close_does_not_erase_it(self):
        def unload():
            self.plugin._bindings.clear()
            self.plugin._shutting_down = True
        with patch.object(self.plugin, 'on_unload', side_effect=unload):
            self.plugin.on_event(plugin_module.api.EVENT_CLOSING, None)
            saved = deepcopy(self.values[KEY])
            self.assertEqual(saved['videos'][0]['progress'], 50.)
            self.plugin.on_event(plugin_module.api.EVENT_CLOSING, None)
            self.assertEqual(self.values[KEY], saved)

    def test_changed_account_layout_occupied_target_and_chat_cell_do_not_get_replaced(self):
        saved = self.saved()
        for change in ('account', 'layout', 'occupied', 'chat'):
            self.values[KEY] = deepcopy(saved)
            self.window.wall.layout_id = 'corner'
            self.tile.room = {'room_id': '123'}
            self.chats = {3: None, 4: None}
            second = self.make_plugin()
            if change == 'account':
                second._cookie_snapshot = lambda: {'DedeUserID': 'another-account'}
            elif change == 'layout':
                self.window.wall.layout_id = '1x1'
            elif change == 'occupied':
                self.tile.room = {'room_id': 'new-live'}
            else:
                self.chats[5] = None
            with patch.object(second, '_start_vod_for_tile') as start:
                second.playback_session.restore()
                start.assert_not_called()

    def test_invalid_records_duplicates_and_nonfinite_progress_are_bounded(self):
        saved = self.saved()
        record = saved['videos'][0]
        record['progress'] = float('nan')
        saved['videos'] = [None, dict(record, cell=-1), dict(record, bvid='invalid'), record, record]
        self.values[KEY] = saved
        second = self.make_plugin()
        with patch.object(second, '_start_vod_for_tile') as start:
            second.playback_session.restore()
            self.assertEqual(start.call_count, 1)
            self.assertEqual(start.call_args.kwargs['resume']['progress'], 0.)
        self.assertEqual(seconds(float('inf')), 0.)

    def test_capture_uses_current_cell_and_preserves_pending_seek_if_player_not_ready(self):
        self.cells[1], self.cells[5] = self.cells[5], self.cells[1]
        self.plugin.host.player = lambda _: SimpleNamespace(_prop=lambda *_: None)
        self.plugin.playback_session.capture()
        self.assertEqual(self.values[KEY]['videos'][0]['cell'], 1)
        self.assertEqual(self.values[KEY]['videos'][0]['progress'], 50.)

    def test_off_ended_replaced_and_account_change_clear_recovery(self):
        self.saved()
        self.values['resume_session'] = False
        self.plugin.playback_session.capture()
        self.assertEqual(self.values[KEY], {})
        self.values['resume_session'] = True
        for state in ('ended', 'replaced'):
            self.binding.state = 'ended' if state == 'ended' else 'playing'
            if state == 'replaced':
                self.tile.room = {'room_id': 'another-live'}
            self.plugin.playback_session.capture()
            self.assertEqual(self.values[KEY], {})
        self.values[KEY] = {'fixture': 'old'}
        self.plugin._bindings.clear()
        self.plugin.on_event(plugin_module.api.EVENT_ACCOUNT_CHANGED, {'platform': 'bilibili'})
        self.assertEqual(self.values[KEY], {})

    def test_startup_waits_for_registered_cells_and_stops_retrying_after_unload(self):
        self.saved()
        second = self.make_plugin()
        second._resolve_cell_from_tile = lambda _: None
        with patch('ddm_plugin_bili_watchlater.playback_session.QTimer.singleShot') as timer:
            second.playback_session.restore()
            timer.assert_called_once_with(400, second.playback_session.restore)
            self.assertTrue(self.values[KEY])
            second._shutting_down = True
            second.playback_session.restore()
            self.assertEqual(timer.call_count, 1)


class AcceptanceTraceTests(unittest.TestCase):
    def test_first_tile_show_restores_synchronously_and_started_is_a_fallback(self):
        from ddm_plugin_bili_watchlater.tile_presentation import _TileEventFilter
        from PySide6.QtWidgets import QWidget
        plugin = plugin_module.BiliWatchLaterPlugin()
        tile = QWidget()
        tile.room = {}
        plugin._sanitize_stale_rooms = Mock()
        def restore():
            plugin.playback_session.finished = True
        plugin.playback_session.restore = Mock(side_effect=restore)
        watcher = _TileEventFilter(plugin)
        try:
            watcher.eventFilter(tile, QEvent(QEvent.Show))
            plugin._sanitize_stale_rooms.assert_called_once()
            plugin.playback_session.restore.assert_called_once()
            watcher.eventFilter(tile, QEvent(QEvent.Show))
            plugin.playback_session.restore.assert_called_once()
            plugin.on_event(plugin_module.api.EVENT_STARTED, {})
            self.assertEqual(plugin.playback_session.restore.call_count, 2)
        finally:
            tile.deleteLater()
            watcher.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_stale_sanitizer_leaves_a_restored_binding_intact(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        tile = Mock(room={'watchlater_bvid': 'BV1xx411c7mD'})
        tile.findChildren.return_value = []
        plugin._host_tiles = lambda: [tile]
        plugin._bindings['BV1xx411c7mD'] = SimpleNamespace(tile=tile)
        plugin._stop_host_tile = Mock()
        plugin._sanitize_stale_rooms()
        plugin._stop_host_tile.assert_not_called()
        tile.set_room.assert_not_called()

    def test_lift_trace_defers_file_io_and_caps_samples_without_identifiers(self):
        from ddm_plugin_bili_watchlater import acceptance_trace as trace
        from PySide6.QtWidgets import QWidget
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trace.jsonl'
            view=QWidget()
            probe=trace.LiftTrace(view)
            with patch.object(trace,'trace_path',return_value=path),patch.object(trace,'_remaining',80):
                for _ in range(10):
                    probe.press()
                    probe.begin()
                    probe.mark('snapshot_ms')
                    probe.mark('host_start_ms')
                    probe.end()
                self.assertFalse(path.exists())
                APP.processEvents()
                rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                self.assertEqual(len(rows),8)
                self.assertTrue(all(row['phase']=='drag_lift' for row in rows))
                self.assertTrue(all(set(row)=={'phase','time','attempt','press_to_drag_ms','snapshot_ms','host_start_ms','total_ms','loop_return_ms'} for row in rows))
            view.deleteLater()
            APP.sendPostedEvents(None,QEvent.DeferredDelete)


class ButtonTests(unittest.TestCase):
    def test_settings_reuse_one_page_and_write_only_changed_keys(self):
        from ddm_plugin_bili_watchlater.page import WatchLaterPage
        values, writes = {}, []
        ctx = context(values)
        def save(key, value):
            writes.append((key, value))
            values[key] = value
        ctx.set_setting = save
        page = WatchLaterPage(ctx)
        page._geometry_settings = Mock()
        try:
            page._open_settings()
            panel = page._settings_dialog
            panel.auto_play.setChecked(False)
            self.assertEqual(writes, [('auto_play', False)])
            page._back_to_browser()
            page._open_settings()
            self.assertIs(page._settings_dialog, panel)
            self.assertFalse(panel.auto_play.isChecked())
            self.assertEqual(len(writes), 1)
            panel.auto_play.setChecked(True)
            self.assertEqual(writes[-1], ('auto_play', True))
            self.assertEqual(len(writes), 2)
        finally:
            page.shutdown()
            page.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_functional_autosave_and_return_do_not_reapply_styles_or_rebuild_grid(self):
        from ddm_plugin_bili_watchlater.page import WatchLaterPage
        page = WatchLaterPage(context({}))
        page._geometry_settings = Mock()
        try:
            page._open_settings()
            with patch.object(page, 'setStyleSheet') as styles, patch.object(page, '_fit_grid') as fit, patch.object(page, '_log') as log:
                page._settings_dialog.autoplay.setChecked(True)
                page._back_to_browser()
                styles.assert_not_called()
                fit.assert_not_called()
                records = [call.args[0] for call in log.call_args_list if call.args[0].startswith('settings change')]
                self.assertEqual([record.split('phase=')[1].split()[0] for record in records], ['persist', 'view', 'playback'])
                self.assertTrue(all('keys=autoplay ' in record for record in records))
        finally:
            page.shutdown()
            page.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_capsule_corners_survive_press_disable_and_style_change(self):
        from ddm_plugin_bili_watchlater.page_shell import BrowserWindow
        root = BrowserWindow()
        root.resize(200, 100)
        button = StableActionButton('恢复默认', root)
        button.setGeometry(10, 10, 130, 38)
        root.show()
        APP.processEvents()
        try:
            for primary in (False, True):
                button.setObjectName('PrimaryButton' if primary else 'IconButton')
                for down, enabled in ((False, True), (True, True), (False, False)):
                    button.setEnabled(enabled)
                    button.setDown(down)
                    root.setStyleSheet('QPushButton {background: magenta; border-radius: 0px;}')
                    APP.processEvents()
                    image = button.grab().toImage()
                    corner = image.pixelColor(1, 1)
                    center = image.pixelColor(image.width() // 2, 2)
                    self.assertNotEqual(center, corner)
                    self.assertNotEqual(corner, QColor('magenta'))
        finally:
            root.close()
            root.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_only_keyboard_focus_draws_a_focus_outline(self):
        from ddm_plugin_bili_watchlater.page_panels import GalleryToolButton
        button = GalleryToolButton()
        try:
            button.show()
            button.setFocus(Qt.ActiveWindowFocusReason)
            APP.processEvents()
            self.assertFalse(button.focus_outline())
            button.setFocus(Qt.TabFocusReason)
            APP.sendEvent(button, QFocusEvent(QEvent.FocusIn, Qt.TabFocusReason))
            self.assertTrue(button.focus_outline())
            APP.sendEvent(button, QFocusEvent(QEvent.FocusIn, Qt.MouseFocusReason))
            self.assertFalse(button.focus_outline())
        finally:
            button.close()
            button.deleteLater()


if __name__ == '__main__':
    unittest.main()
