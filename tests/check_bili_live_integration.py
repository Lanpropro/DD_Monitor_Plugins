"""Install the real ZIP and exercise real host tile/account/fullscreen lifecycle."""
import importlib
import json
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch
from bili_live_support import HOST,ROOT,session,bili
from bili_live_fixtures import room_payload
import shiboken6
from PySide6.QtCore import QEvent,QPoint,QObject,Qt,qInstallMessageHandler
from PySide6.QtGui import QColor,QCursor,QImage,QRegion
from PySide6.QtWidgets import QApplication,QLineEdit,QToolButton
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
        self.qt_warnings=[]
        self.previous_handler=qInstallMessageHandler(lambda kind,ctx,msg:self.qt_warnings.append(msg))
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)/'plugins';self.directory.mkdir()
        installer=host_api.PluginManager(plugins_dir=str(self.directory),enabled=[])
        archive=ROOT/'dist/bili_live-1.0.zip'
        self.assertTrue(archive.is_file())
        installer.install_zip(str(archive));self.assertEqual(installer.plugins,[])
        self.patches=[patch.object(host_api,'DEFAULT_PLUGINS_DIR',str(self.directory)),
            patch('ddm.config.save'),patch.object(MainWindow,'refresh_account'),
            patch.object(MainWindow,'refresh_status'),patch.object(MainWindow,'refresh_stats'),
            patch.object(MainWindow,'load_room_avatars'),patch.object(MainWindow,'_check_startup_plugins'),
            patch.object(MainWindow,'sync_danmaku'),patch.object(MainWindow,'stop_danmaku')]
        for p in self.patches:p.start()
        self.window=self.new_window(['bili_live'])
        self._window_closed=False
        self.window.resize(1000,600);self.window.show();APP.processEvents()
        self.entry=next(p for p in self.window.plugins.plugins if p.context.name=='bili_live')
        self.api=importlib.import_module('ddm_plugin_bili_live.gift_api')
        self.workers=importlib.import_module('ddm_plugin_bili_live.workers')
        self.emotes=importlib.import_module('ddm_plugin_bili_live.composer').bili
        self.catalog=self.api.normalize_catalog(room_payload(),'90001')
        media=importlib.import_module('ddm_plugin_bili_live.gift_media')
        image=QImage(32,32,QImage.Format_ARGB32);image.fill(QColor('#00a1d6'))
        image_path=Path(self.temp.name)/'fixture.png';self.assertTrue(image.save(str(image_path)))
        self.reads=[patch.object(media,'_fetch',return_value=str(image_path)),
            patch.object(self.api,'get_catalog',return_value=self.catalog),
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
        if not self._window_closed:self.window.close()
        APP.processEvents();APP.sendPostedEvents(None,QEvent.DeferredDelete)
        for p in reversed(self.reads):p.stop()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
        qInstallMessageHandler(self.previous_handler)
        relevant=[m for m in self.qt_warnings if any(x in m for x in (
            'No file name specified','must be a top level window','QThreadStorage:'))]
        self.assertEqual(relevant,[],'Qt warnings during installed plugin lifecycle')
    def composer(self,tile=None):
        tile=tile or self.window.wall.tiles[0]
        return next(e.control.widget for e in tile._plugin_controls.entries if e.owner=='bili_live')
    def test_zip_integrity_and_tracked_source_bytes(self):
        with zipfile.ZipFile(ROOT/'dist/bili_live-1.0.zip') as archive:
            self.assertIsNone(archive.testzip())
            names=archive.namelist()
            self.assertEqual(len(names),len(set(names)))
            self.assertTrue(all(name.startswith('bili_live/') for name in names))
            manifest=json.loads(archive.read('bili_live/plugin.json'))
            self.assertEqual((manifest['id'],manifest['version']),('bili_live','1.0'))
            self.assertEqual(archive.read('bili_live/LICENSE'),(ROOT/'LICENSE').read_bytes())
            for name in names:
                source=ROOT/'plugins'/name
                if source.is_file():self.assertEqual(archive.read(name),source.read_bytes(),name)
    def test_zip_upgrade_preserves_local_data(self):
        from ddm.plugin_updates import stage,apply_pending,pending_versions
        old_zip=Path(self.temp.name)/'bili-live-old.zip'
        current=ROOT/'dist/bili_live-1.0.zip'
        with zipfile.ZipFile(current) as source,zipfile.ZipFile(old_zip,'w') as target:
            for name in source.namelist():
                data=source.read(name)
                if name=='bili_live/plugin.json':
                    manifest=json.loads(data);manifest['version']='0.9'
                    data=json.dumps(manifest).encode('utf-8')
                target.writestr(name,data)
        manager=host_api.PluginManager(plugins_dir=str(Path(self.temp.name)/'upgrade'),enabled=[])
        manager.install_zip(str(old_zip))
        folder=Path(manager.plugins_dir)/'bili_live'
        sentinel=folder/'user-data.txt';sentinel.write_bytes(b'preserve local data')
        stage(manager,str(current),'bili_live','1.0')
        self.assertEqual(pending_versions(manager),{'bili_live':'1.0'})
        self.assertEqual(host_api.read_manifest(str(folder),'bili_live')['version'],'0.9')
        apply_pending(manager)
        self.assertEqual(pending_versions(manager),{})
        self.assertEqual(host_api.read_manifest(str(folder),'bili_live')['version'],'1.0')
        self.assertEqual(sentinel.read_bytes(),b'preserve local data')
        self.assertEqual((folder/'plugin.py').read_bytes(),(ROOT/'plugins/bili_live/plugin.py').read_bytes())
    def test_gift_row_lazy_shared_state_and_drafts(self):
        composer=self.composer()
        self.assertIsNone(composer.gift_controller)
        composer.edit.setText('draft remains')
        composer.open_gifts();settle(lambda:not composer.gift_controller.state.loading)
        self.assertEqual([composer.layout().itemAt(i).widget() for i in range(4)],
            [composer.edit,composer.emote_button,composer.gift_button,composer.send_button])
        for button in (composer.emote_button,composer.gift_button):
            self.assertEqual((button.width(),button.height()),(24,24))
        release=threading.Event()
        def refresh_wallet(**kw):
            release.wait(3);return 999800
        try:
            with patch.object(self.api,'send_gift',return_value=self.api.GiftResult('main:1',1,{})),patch.object(self.api,'get_wallet',side_effect=refresh_wallet):
                composer.gift_controller.send('main:1',1)
                settle(lambda:not self.entry.coordinator.busy)
                self.assertEqual(composer.gift_controller.state.wallet,999894)
                self.assertEqual(composer.gift_popup.wallet_label.text(),'剩余电池：9998.94')
                self.assertEqual(composer.gift_controller.state.feedback,'')
                release.set();settle(lambda:not composer.gift_controller.state.loading)
                self.assertEqual(composer.gift_popup.wallet_label.text(),'剩余电池：9998')
        finally:release.set()
        self.assertEqual(composer.edit.text(),'draft remains')
        with patch.object(self.emotes,'get_emoticons',return_value=[]):
            composer.open_emoticons();settle(lambda:composer.popup is not None)
            self.assertFalse(composer.gift_popup.isVisible())
            composer.open_gifts();self.assertFalse(composer.popup.isVisible())
    def test_rapid_pointer_sweeps_across_tiles_keep_both_pickers_at_their_anchors(self):
        self.window.resize(1900,1000);APP.processEvents()
        self.window.wall.set_layout('2x2')
        tiles=self.window.wall.tiles
        for index,tile in enumerate(tiles):tile.set_room({'room_id':str(90001+index),'uname':f'Tile {index}'})
        composer=self.composer(tiles[0])
        composer.open_gifts();settle(lambda:not composer.gift_controller.state.loading)
        composer.gift_popup.hide()
        with patch.object(self.emotes,'get_emoticons',return_value=[]):
            composer.open_emoticons();settle(lambda:composer.popup is not None)
        composer.popup.hide()
        for popup,button in ((composer.gift_popup,composer.gift_button),(composer.popup,composer.emote_button)):
            tiles[0].set_controls_visible(True);popup.open_at(button);APP.processEvents()
            for step in range(36):
                target=tiles[step%len(tiles)]
                QCursor.setPos(target.mapToGlobal(target.rect().center()))
                APP.processEvents()
                target.set_controls_visible(True)
                QCursor.setPos(popup.mapToGlobal(popup.rect().center()))
                APP.processEvents();QTest.qWait(2)
                anchor=button.mapToGlobal(QPoint(button.width()//2,0))
                screen=button.screen().availableGeometry()
                expected=max(screen.left(),min(anchor.x()-popup.width()//2,screen.right()-popup.width()+1))
                self.assertLessEqual(abs(popup.x()-expected),1,(type(popup).__name__,step,popup.x(),expected))
                self.assertTrue(popup.isVisible())
            popup.hide()
    def test_installed_pickers_follow_during_each_mainwindow_move(self):
        self.addCleanup(QCursor.setPos,QCursor.pos())
        composer=self.composer()
        image=QImage(32,32,QImage.Format_ARGB32);image.fill(QColor('#00a1d6'))
        image_path=Path(self.temp.name)/'picker.png';self.assertTrue(image.save(str(image_path)))
        media=importlib.import_module('ddm_plugin_bili_live.gift_media')
        emoticons=importlib.import_module('ddm_plugin_bili_live.emoticons')
        item=self.catalog.tabs[0].items[1]
        items=tuple(replace(item,key=f'follow:{i}',name=f'Gift {i}') for i in range(80))
        catalog=replace(self.catalog,tabs=(self.api.GiftTab('main','礼物',items),))
        packages=[{'pkg_type':3,'pkg_name':'跟随回归','emoticons':[
            {'emoji':f'[emote-{i}]','perm':1,'emoticon_unique':f'emote_{i}',
             'url':f'https://example.invalid/follow-{i}.png'} for i in range(80)]}]
        for local_patch in (patch.object(self.api,'get_catalog',return_value=catalog),
                patch.object(media,'_fetch',return_value=str(image_path)),
                patch.object(emoticons,'_load_image',return_value=image),
                patch.object(self.emotes,'get_emoticons',return_value=packages)):
            local_patch.start();self.addCleanup(local_patch.stop)
        composer.open_gifts();settle(lambda:not composer.gift_controller.state.loading)
        settle(lambda:any(c.image.pixmap() and not c.image.pixmap().isNull()
                         for c in composer.gift_popup._cards))
        self.assertEqual(len(composer.gift_popup._cards),80)
        composer.gift_popup.hide()
        composer.open_emoticons()
        settle(lambda:composer.popup is not None and
               len(composer.popup.scroll.findChildren(QToolButton))==80)
        settle(lambda:any(not b.icon().isNull() for b in composer.popup.scroll.findChildren(QToolButton)))
        composer.popup.hide()
        self.window.move(100,250);APP.processEvents()
        class OwnerFrames(QObject):
            def __init__(self,button,popup):
                super().__init__();self.button,self.popup=button,popup;self.errors=[]
            def eventFilter(self,watched,event):
                if event.type()==QEvent.Move:
                    screen=self.button.screen().availableGeometry()
                    expected=max(screen.left(),min(
                        self.button.mapToGlobal(QPoint(self.button.width()//2,0)).x()-self.popup.width()//2,
                        screen.right()-self.popup.width()+1))
                    self.errors.append(abs(self.popup.x()-expected))
                return False
        for popup,button in ((composer.popup,composer.emote_button),(composer.gift_popup,composer.gift_button)):
            with self.subTest(popup=type(popup).__name__):
                popup.open_at(button);APP.processEvents()
                observer=OwnerFrames(button,popup);self.window.installEventFilter(observer)
                before=self.window.pos()
                try:
                    for step in range(1,41):
                        self.window.move(before+QPoint(step*2,step%2))
                        # Observe before a queued position update can hide lag.
                        APP.processEvents()
                    self.assertEqual(len(observer.errors),40)
                    self.assertLessEqual(max(observer.errors),1,
                        'installed picker followed one event behind the actual MainWindow')
                finally:
                    self.window.removeEventFilter(observer)
                    popup.hide();self.window.move(before);APP.processEvents()
        if APP.platformName()=='windows':
            import ctypes
            from ctypes import wintypes
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.GetWindowRect.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.RECT))
            user32.SetWindowPos.argtypes=(wintypes.HWND,wintypes.HWND,ctypes.c_int,
                ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT)
            user32.SendMessageW.argtypes=(wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
            def native_rect(widget):
                rect=wintypes.RECT();self.assertTrue(user32.GetWindowRect(int(widget.winId()),ctypes.byref(rect)))
                return rect.left,rect.top,rect.right,rect.bottom
            owner=int(self.window.winId())
            for popup,button in ((composer.popup,composer.emote_button),(composer.gift_popup,composer.gift_button)):
                with self.subTest(native_picker=type(popup).__name__):
                    popup.open_at(button);APP.processEvents()
                    followers=[popup]
                    if popup is composer.gift_popup:
                        popup._quantity(popup._cards[0])
                        # Keep the auto-hiding quantity picker open while checking
                        # native following; the desktop cursor may be elsewhere.
                        quantity=popup.quantity_popup
                        QCursor.setPos(quantity.mapToGlobal(quantity.rect().center()))
                        APP.processEvents();self.assertTrue(quantity.isVisible())
                        followers.append(quantity)
                    origin=native_rect(self.window);initial=[native_rect(f) for f in followers]
                    user32.SendMessageW(owner,0x231,0,0)
                    try:
                        for dx,dy in ((45,12),(90,24),(30,8),(0,0)):
                            self.assertTrue(user32.SetWindowPos(owner,None,origin[0]+dx,origin[1]+dy,0,0,0x15))
                            for follower,old in zip(followers,initial):
                                expected=(old[0]+dx,old[1]+dy)
                                self.assertLessEqual(max(abs(a-b) for a,b in zip(native_rect(follower)[:2],expected)),1,
                                                     'installed native follower waited for Qt')
                        final=[native_rect(f) for f in followers];APP.processEvents()
                        for follower,position in zip(followers,final):
                            self.assertLessEqual(max(abs(a-b) for a,b in zip(native_rect(follower),position)),1)
                    finally:
                        user32.SendMessageW(owner,0x232,0,0);popup.hide()
                        user32.SetWindowPos(owner,None,*origin[:2],0,0,0x15);APP.processEvents()

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
    def test_centered_bounded_group_and_narrow_host_actions(self):
        tile=self.window.wall.tiles[0];composer=self.composer()
        self.assertEqual(tile.bottom.findChildren(QLineEdit),[composer.edit])
        # Fix the tile size without reparenting its native video surface.
        # The wall may otherwise override requested test widths.
        composer.edit.setText('draft survives resize')
        try:
            for width in (900,360,200,1600,900):
                tile.setFixedSize(width,300);APP.processEvents()
                region=tile._plugin_controls.widget
                self.assertEqual(region.isVisible(),width>=900)
                for button in (tile.pause_button,tile.volume_button,tile.danmaku_button,
                               tile.danmaku_settings_button,tile.recording_button,tile.fullscreen_button):
                    self.assertTrue(button.isVisible())
                    self.assertTrue(tile.bottom.rect().contains(button.geometry()))
                if width>=900:
                    self.assertEqual(region.width(),min(520,max(300,int(width*.45))))
                    self.assertLessEqual(abs(region.geometry().center().x()-tile.bottom.width()/2),2)
                    self.assertLess(region.geometry().right(),tile.danmaku_button.x())
                    self.assertGreaterEqual(composer.edit.width(),24)
                else:
                    self.assertTrue(tile.volume_button.compact_popup)
                self.assertEqual(composer.edit.text(),'draft survives resize')
        finally:
            tile.setMinimumSize(0,0);tile.setMaximumSize(16777215,16777215)
            self.window.wall.relayout(force=True)
    def test_composer_border_fits_real_bottom_bar_in_normal_and_fullscreen_tiles(self):
        # The normal bar leaves 27px after its margins. A 28px editor loses
        # its bottom border even though a standalone composer looks correct.
        tile=self.window.wall.tiles[0];composer=self.composer()
        self.window.resize(1100,600);APP.processEvents()
        try:
            for mode in ('dark','light'):
                theme.apply(mode);APP.setStyleSheet(theme.qss())
                for fullscreen in (False,True):
                    self.window.wall.set_fullscreen_tile(tile if fullscreen else None)
                    with self.subTest(mode=mode,fullscreen=fullscreen):
                        tile.setFixedSize(900,350);tile.set_controls_visible(True)
                        APP.processEvents()
                        self.assertTrue(composer.isVisible())
                        for widget in (composer.edit,composer.emote_button,
                                       composer.gift_button,composer.send_button):
                            self.assertTrue(QRegion(widget.rect()).subtracted(widget.visibleRegion()).isEmpty(),
                                f'{widget.accessibleName() or widget.text()} is clipped: '
                                f'{widget.rect()} vs {widget.visibleRegion().boundingRect()}')
                            bounds=widget.rect().translated(widget.mapTo(tile.bottom,QPoint()))
                            self.assertTrue(tile.bottom.rect().contains(bounds))
        finally:
            self.window.wall.set_fullscreen_tile(None)
            tile.setMinimumSize(0,0);tile.setMaximumSize(16777215,16777215)
            self.window.wall.relayout(force=True);theme.apply('dark')

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
        self.window.resize(1900,1000);APP.processEvents()
        a=self.composer();self.window.wall.set_layout('2x2');tile=next(t for t in self.window.wall.tiles if not t.room.get('room_id'));tile.set_room({'room_id':'90002'});b=self.composer(tile)
        a.edit.setText('first draft');b.edit.setText('second draft')
        self.assertEqual((a.edit.text(),b.edit.text()),('first draft','second draft'))
        tile.set_room({'room_id':'90003'})
        self.assertEqual((a.edit.text(),b.edit.text()),('first draft',''))
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
        # Do not unload it again after new_window replaces its module namespace.
        self._window_closed=True
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
