"""Selected upstream fixes against the current candidate; no real accounts or media."""
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from check_bili_watchlater import APP, plugin_module
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget


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

    def test_dpi_notifications_coalesce_and_ignore_cooldown(self):
        tile = QWidget()
        self.plugin._bindings['fixture'] = SimpleNamespace(tile=tile)
        try:
            with patch.object(self.plugin, '_host_tiles', return_value=[tile]), \
                    patch.object(self.plugin, '_cover_pip_dpi') as cover, \
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
        with patch.object(self.plugin, '_pip_drag_active', return_value=True):
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


if __name__ == '__main__':
    unittest.main()
