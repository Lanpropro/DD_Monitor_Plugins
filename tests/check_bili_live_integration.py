"""Install the real ZIP and exercise real host tile/account/fullscreen lifecycle."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from bili_live_support import HOST,ROOT,session,bili
from bili_live_fixtures import room_payload
import shiboken6
from PySide6.QtCore import QEvent,Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ddm import plugins as host_api,theme
from ddm.app import MainWindow

APP=QApplication.instance() or QApplication([])
def settle(predicate):
    end=time.monotonic()+4
    while time.monotonic()<end:
        APP.processEvents()
        if predicate():return
        QTest.qWait(5)
    raise AssertionError('installed integration did not settle')

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)/'plugins';self.directory.mkdir()
        installer=host_api.PluginManager(plugins_dir=str(self.directory),enabled=[])
        archive=ROOT/'dist/bili_live-1.0.zip'
        self.assertTrue(archive.is_file())
        installer.install_zip(str(archive));self.assertEqual(installer.plugins,[])
        self.patches=[patch.object(host_api,'DEFAULT_PLUGINS_DIR',str(self.directory)),
            patch('ddm.config.save'),patch.object(MainWindow,'refresh_account'),
            patch.object(MainWindow,'sync_danmaku'),patch.object(MainWindow,'stop_danmaku')]
        for p in self.patches:p.start()
        self.window=self.new_window(['bili_live'])
        self.window.resize(1000,600);self.window.show();APP.processEvents()
        self.entry=next(p for p in self.window.plugins.plugins if p.context.name=='bili_live')
        self.api=importlib.import_module('ddm_plugin_bili_live.gift_api')
        self.workers=importlib.import_module('ddm_plugin_bili_live.workers')
        self.emotes=importlib.import_module('ddm_plugin_bili_live.composer').bili
        self.catalog=self.api.normalize_catalog(room_payload(),'90001')
        self.reads=[patch.object(self.api,'get_catalog',return_value=self.catalog),
            patch.object(self.api,'get_wallet',return_value=999999),
            patch.object(self.api,'get_bag',return_value=self.api.GiftTab('bag','背包',())),
            patch.object(self.api,'prepare_target',return_value=self.api.GiftTarget('90001',123,456))]
        for p in self.reads:p.start()
    def new_window(self,enabled):
        for name in tuple(sys.modules):
            if name.startswith('ddm_plugin_bili_live.'):
                del sys.modules[name]
        with patch('ddm.app.QTimer.singleShot'):
            return MainWindow([], [{'room_id':'90001','uname':'Synthetic','live':False}],layout_id='1x1',state={
                'sessdata':'fake','bili_jct':'fake-csrf','buvid3':'device-test','plugins_enabled':enabled,
                'settings':{'auto_update':False}})
    def tearDown(self):
        self.window.close();APP.processEvents();APP.sendPostedEvents(None,QEvent.DeferredDelete)
        for p in reversed(self.reads):p.stop()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def composer(self,tile=None):
        tile=tile or self.window.wall.tiles[0]
        return next(e.control.widget for e in tile._plugin_controls.entries if e.owner=='bili_live')
    def test_gift_row_lazy_shared_state_and_drafts(self):
        composer=self.composer()
        self.assertIsNone(composer.gift_controller)
        composer.edit.setText('draft remains')
        composer.open_gifts();settle(lambda:not composer.gift_controller.state.loading)
        self.assertEqual([composer.layout().itemAt(i).widget() for i in range(4)],
            [composer.edit,composer.emote_button,composer.gift_button,composer.send_button])
        for button in (composer.emote_button,composer.gift_button):
            self.assertEqual((button.width(),button.height()),(28,28))
        with patch.object(self.api,'send_gift',return_value=self.api.GiftResult('main:1',1,{})):
            composer.gift_controller.send('main:1',1);settle(lambda:not self.entry.coordinator.busy)
        self.assertEqual(composer.edit.text(),'draft remains')
        with patch.object(self.emotes,'get_emoticons',return_value=[]):
            composer.open_emoticons();settle(lambda:composer.popup is not None)
            self.assertFalse(composer.gift_popup.isVisible())
            composer.open_gifts();self.assertFalse(composer.popup.isVisible())
    def test_existing_new_empty_room_swap_and_fullscreen_same_control(self):
        tile=self.window.wall.tiles[0];composer=self.composer(tile)
        self.window.wall.set_layout('2x2');empty=next(t for t in self.window.wall.tiles if not t.room.get('room_id'));self.assertFalse(self.composer(empty).isVisible())
        empty.set_room({'room_id':'90002'});self.assertTrue(self.composer(empty).supported)
        composer.edit.setText('old');tile.set_room({'room_id':'90003'})
        self.assertEqual(composer.edit.text(),'')
        tile.set_room({'room_id':'douyu:1','platform':'douyu'});self.assertFalse(composer.isVisible())
        tile.set_room({'room_id':'90001'});self.assertIs(self.composer(tile),composer)
        for mode in ('dark','light'):
            theme.apply(mode);self.window.wall.set_fullscreen_tile(tile);APP.processEvents()
            self.assertIs(self.composer(tile),composer)
            self.window.wall.set_fullscreen_tile(None);APP.processEvents()
        self.window._wire_tile(tile);self.assertEqual(len(tile._plugin_controls.entries),1)
    def test_200_360_900_geometry_no_overlap(self):
        tile=self.window.wall.tiles[0];composer=self.composer()
        # Standalone tile geometry avoids the wall's responsive layout overriding requested widths.
        tile.setParent(None);tile.show()
        for width in (900,360,200):
            tile.resize(width,300);APP.processEvents()
            self.assertGreaterEqual(composer.edit.width(),24,(width,composer.width(),composer.edit.width()))
            self.assertTrue(tile.volume_button.isVisible());self.assertTrue(tile.fullscreen_button.isVisible())
            self.assertLess(tile.fullscreen_button.geometry().right(),width)
            self.assertLessEqual(composer.parentWidget().geometry().right(),tile.danmaku_button.x())
        tile.setParent(self.window.wall);self.window.wall.relayout(force=True)
    def test_shared_account_device_publication_and_logout_close_views(self):
        composer=self.composer();composer.open_gifts();settle(lambda:not composer.gift_controller.state.loading)
        generation=self.entry.session.snapshot().generation
        from ddm import bili as host_bili
        host_bili.set_login_cookies('fake','fake-csrf','')
        self.entry.session.publish_device(generation,'new-device')
        self.assertEqual(self.window.current_state()['buvid3'],'new-device')
        self.assertEqual(self.entry.session.snapshot().generation,generation)
        self.window.logout('bilibili');APP.processEvents()
        self.assertFalse(composer.gift_popup.isVisible())
        self.assertIsNone(composer.gift_controller.state.wallet)
        self.assertNotIn('SESSDATA',self.entry.session.snapshot().values)
    def test_two_tiles_hide_remove_inflight_exit(self):
        a=self.composer();self.window.wall.set_layout('2x2');tile=next(t for t in self.window.wall.tiles if not t.room.get('room_id'));tile.set_room({'room_id':'90002'});b=self.composer(tile)
        a.open_gifts();b.open_gifts();settle(lambda:not a.gift_controller.state.loading and not b.gift_controller.state.loading)
        self.assertIs(a.gift_controller.coordinator,b.gift_controller.coordinator)
        entered,release=threading.Event(),threading.Event()
        def send(*args,**kw):entered.set();release.wait(3);return self.api.GiftResult('main:1',1,{})
        with patch.object(self.api,'send_gift',side_effect=send) as post:
            a.gift_controller.send('main:1',1);settle(entered.is_set)
            a.hide();self.window.wall._drop_tile(self.window.wall.tiles[0]);APP.processEvents()
            b.gift_controller.send('main:1',1);self.assertEqual(post.call_count,1)
            release.set();settle(lambda:not self.entry.coordinator.busy)
        self.assertTrue(not shiboken6.isValid(a.gift_popup) or not a.gift_popup.isVisible())
    def test_disabled_removed_restart_has_no_ui(self):
        self.window.close();APP.processEvents()
        other=self.new_window([])
        try:self.assertEqual(other.wall.tiles[0]._plugin_controls.entries,[])
        finally:other.close()
        manager=host_api.PluginManager(plugins_dir=str(self.directory),enabled=[])
        manager.remove_plugin('bili_live')
        other=self.new_window(['bili_live'])
        try:
            self.assertEqual(other.plugins.plugins,[])
            self.assertEqual(other.wall.tiles[0]._plugin_controls.entries,[])
        finally:other.close()

if __name__=='__main__':unittest.main()
