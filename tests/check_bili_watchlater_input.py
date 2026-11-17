"""Input routing and Qt lifetime contracts; no accounts, media or external network."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from check_bili_watchlater import APP,plugin_module
from ddm_plugin_bili_watchlater import playback_input
from PySide6.QtCore import QEvent,QPointF,Qt,Signal
from PySide6.QtGui import QKeyEvent,QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit,QWidget


class Tile(QWidget):
    pauseToggled=Signal(object)
    fullscreenRequested=Signal(object)
    def __init__(self):
        super().__init__()
        self.room={'watchlater_bvid':'fixture'}
        self.video=QWidget(self)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.plugin=plugin_module.BiliWatchLaterPlugin()
        self.input=self.plugin.input
        self.tile=Tile()
        self.binding=SimpleNamespace(tile=self.tile,bvid='fixture',state='playing')
        self.plugin._bindings['fixture']=self.binding
        self.pause=Mock()
        self.fullscreen=Mock()
        self.tile.pauseToggled.connect(self.pause)
        self.tile.fullscreenRequested.connect(self.fullscreen)

    def tearDown(self):
        # on_unload normally releases media; this fixture contains no media objects.
        self.plugin._bindings.clear()
        self.plugin.on_unload()
        self.tile.deleteLater()
        APP.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_one_state_owner_and_legacy_filter_class_identity(self):
        self.assertIs(plugin_module._VodKeyFilter,playback_input._VodKeyFilter)
        self.assertIs(plugin_module._PipClickFilter,playback_input._PipClickFilter)
        self.assertIs(self.plugin._click_timers,self.input.click_timers)
        pressed={int(Qt.Key_Space)}
        self.plugin._key_down=pressed
        self.assertIs(self.input.key_down,pressed)
        self.assertNotIn('_key_down',self.plugin.__dict__)
        self.assertNotIn('_click_timers',self.plugin.__dict__)

    def test_single_click_waits_250ms_then_pauses_once(self):
        self.plugin._on_vod_click(self.tile,self.binding)
        timer=self.plugin._click_timers[id(self.tile)]
        self.assertIsNone(timer.parent())
        self.assertEqual(timer.interval(),250)
        self.assertTrue(timer.isSingleShot())
        self.pause.assert_not_called()
        QTest.qWait(300)
        self.pause.assert_called_once_with(self.tile.room)
        self.assertEqual(self.input.click_timers,{})

    def test_double_click_cancels_pause_and_requests_fullscreen_once(self):
        self.plugin._on_vod_click(self.tile,self.binding)
        timer=self.input.click_timers[id(self.tile)]
        self.plugin._on_vod_click(self.tile,self.binding)
        self.assertFalse(timer.isActive())
        self.fullscreen.assert_called_once_with(self.tile)
        QTest.qWait(300)
        self.pause.assert_not_called()

    def test_late_click_ignores_replaced_binding_and_shutdown(self):
        self.plugin._bindings['fixture']=SimpleNamespace(tile=self.tile)
        self.plugin._single_vod_click(self.tile,self.binding)
        self.pause.assert_not_called()
        self.plugin._bindings['fixture']=self.binding
        self.plugin._shutting_down=True
        self.plugin._single_vod_click(self.tile,self.binding)
        self.pause.assert_not_called()
        self.plugin._shutting_down=False

    def test_key_filter_claims_press_release_without_button_click(self):
        key_filter=playback_input._VodKeyFilter(self.plugin)
        action=('space',self.tile,self.binding)
        with patch.object(self.input,'vod_key_action',return_value=action):
            override=QKeyEvent(QEvent.ShortcutOverride,Qt.Key_Space,Qt.NoModifier)
            self.assertTrue(key_filter.eventFilter(self.tile,override))
            press=QKeyEvent(QEvent.KeyPress,Qt.Key_Space,Qt.NoModifier)
            self.assertTrue(key_filter.eventFilter(self.tile,press))
            self.assertIn(int(Qt.Key_Space),self.input.key_down)
            self.pause.assert_called_once_with(self.tile.room)
            release=QKeyEvent(QEvent.KeyRelease,Qt.Key_Space,Qt.NoModifier)
            self.assertTrue(key_filter.eventFilter(self.tile,release))
            self.assertNotIn(int(Qt.Key_Space),self.input.key_down)
            self.assertFalse(key_filter.eventFilter(self.tile,release))

    def test_editor_focus_preserves_space_and_cursor_keys(self):
        edit=QLineEdit()
        self.addCleanup(edit.deleteLater)
        with patch.object(self.input,'key_window_ok',return_value=True), \
                patch.object(playback_input.QApplication,'focusWidget',return_value=edit):
            for key in (Qt.Key_Space,Qt.Key_Left,Qt.Key_Right):
                event=QKeyEvent(QEvent.KeyPress,key,Qt.NoModifier)
                self.assertIsNone(self.plugin._vod_key_action(event,edit))

    def mouse(self,kind,x,buttons):
        return QMouseEvent(kind,QPointF(x,10),QPointF(x,10),
                           Qt.LeftButton if kind!=QEvent.MouseMove else Qt.NoButton,
                           buttons,Qt.NoModifier)

    def test_pip_click_and_drag_both_release_host_mouse_gesture(self):
        click_filter=playback_input._PipClickFilter(self.plugin)
        self.assertFalse(click_filter.eventFilter(self.tile.video,self.mouse(QEvent.MouseButtonPress,10,Qt.LeftButton)))
        self.assertFalse(click_filter.eventFilter(self.tile.video,self.mouse(QEvent.MouseButtonRelease,10,Qt.NoButton)))
        APP.processEvents()
        self.pause.assert_called_once_with(self.tile.room)
        self.pause.reset_mock()
        self.assertFalse(click_filter.eventFilter(self.tile.video,self.mouse(QEvent.MouseButtonPress,10,Qt.LeftButton)))
        moved=10+APP.startDragDistance()+5
        self.assertFalse(click_filter.eventFilter(self.tile.video,self.mouse(QEvent.MouseMove,moved,Qt.LeftButton)))
        self.assertIsNone(click_filter._press)
        self.assertFalse(click_filter.eventFilter(self.tile.video,self.mouse(QEvent.MouseButtonRelease,moved,Qt.NoButton)))
        APP.processEvents()
        self.pause.assert_not_called()

    def test_shortcut_parent_and_unload_cancel_timer_and_filters(self):
        main,floating=QWidget(),QWidget()
        self.addCleanup(main.deleteLater)
        self.addCleanup(floating.deleteLater)
        with patch.object(self.plugin.host,'window',return_value=main):
            self.plugin._ensure_space_shortcut()
            self.plugin._ensure_seek_shortcuts()
        self.assertIs(self.input.space_shortcut.parent(),main)
        self.plugin._sync_shortcut_hosts(floating)
        self.assertIs(self.input.space_shortcut.parent(),floating)
        self.assertIs(self.input.seek_left.parent(),floating)
        self.assertIsNone(self.input.danmaku_shortcut)
        self.plugin._bump_key_filter()
        self.assertIsNone(self.input.key_filter.parent())
        self.plugin._on_vod_click(self.tile,self.binding)
        timer=self.input.click_timers[id(self.tile)]
        self.input.key_down.add(int(Qt.Key_Space))
        self.plugin._bindings.clear()
        self.plugin.on_unload()
        self.assertFalse(timer.isActive())
        self.assertEqual(self.input.click_timers,{})
        self.assertIsNone(self.input.key_filter)
        self.assertIsNone(self.input.space_shortcut)
        self.assertIsNone(self.input.seek_left)
        self.assertEqual(self.input.key_down,set())
        QTest.qWait(300)
        self.pause.assert_not_called()

    def test_pip_transition_reinstalls_key_filter_before_click_filter(self):
        floating=object()
        window=SimpleNamespace(picture_in_picture=SimpleNamespace(active=lambda:True,floating=floating))
        app=Mock()
        with patch.object(self.plugin.host,'window',return_value=window), \
                patch.object(playback_input.QApplication,'instance',return_value=app):
            self.plugin._sync_pip_filter()
            self.assertEqual([call[0] for call in app.method_calls],
                             ['removeEventFilter','installEventFilter','installEventFilter'])
            self.assertIs(app.method_calls[1].args[0],self.input.key_filter)
            self.assertIs(app.method_calls[2].args[0],self.input.pip_filter)
            app.reset_mock()
            self.plugin._sync_pip_filter()
            app.installEventFilter.assert_not_called()
            self.plugin._bindings.clear()
            self.plugin._sync_pip_filter()
            self.assertIsNone(self.input.pip_filter)


if __name__=='__main__':unittest.main()
