"""Native Windows/VLC synthetic checks; requires QT_QPA_PLATFORM=windows."""
import ctypes
from ctypes import wintypes
import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from bili_live_support import HOST,ROOT
from bili_live_fixtures import room_payload
from PySide6.QtCore import QPoint,Qt,QEvent,QTimer,QEventLoop,QAbstractNativeEventFilter
from PySide6.QtGui import QCursor,QInputMethodEvent,QMouseEvent,QMovie
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ddm import plugins as host_api,theme
from ddm.widgets import Tile
from ddm.player import TilePlayer
from ddm.recording import ffmpeg_path

APP=QApplication.instance() or QApplication([])
assert APP.platformName()=='windows','native test requires Windows Qt'
def settle(predicate,seconds=5):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        APP.processEvents()
        if predicate():return
        QTest.qWait(10)
    raise AssertionError('native state did not settle')


def check_native_move_loop(owner,popup,anchor):
    # Exercise the real Windows modal move loop on our synthetic window. Key
    # messages target only this HWND; no global input injection is needed.
    user32=ctypes.windll.user32
    user32.PostMessageW.argtypes=(wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
    user32.PostMessageW.restype=wintypes.BOOL
    hwnd=int(owner.winId())
    quantity=getattr(popup,'quantity_popup',None)
    followers=[popup]+([quantity] if quantity is not None and quantity.isVisible() else [])
    handles={int(w.winId()):type(w).__name__ for w in followers}
    loop=QEventLoop();rows=[];inside=False
    class NativeMoves(QAbstractNativeEventFilter):
        def nativeEventFilter(self,event_type,message):
            nonlocal inside
            native=wintypes.MSG.from_address(int(message))
            if native.hWnd==hwnd and native.message==0x231:
                inside=True;rows.append((time.perf_counter(),'enter'))
            elif native.hWnd==hwnd and native.message==0x232:
                rows.append((time.perf_counter(),'exit'));inside=False
                loop.quit()
            elif inside and native.message==3:
                if native.hWnd==hwnd:rows.append((time.perf_counter(),'owner'))
                elif native.hWnd in handles:rows.append((time.perf_counter(),handles[native.hWnd]))
            return False,0
    observer=NativeMoves();APP.installNativeEventFilter(observer)
    start=QTimer();start.setSingleShot(True)
    timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
    outside=QPoint(owner.x()+20,owner.y()-10)
    APP.sendEvent(owner,QMouseEvent(QEvent.NonClientAreaMouseButtonPress,
        QPoint(20,-10),outside,Qt.LeftButton,Qt.LeftButton,Qt.NoModifier))
    def actions():
        time.sleep(.08)
        for index in range(40):
            user32.PostMessageW(hwnd,0x100,0x27 if index<20 else 0x25,0)
            time.sleep(.004)
        time.sleep(.05)
        user32.PostMessageW(hwnd,0x100,0x0D,0)
    sender=threading.Thread(target=actions,daemon=True)
    def begin():
        sender.start();user32.PostMessageW(hwnd,0x112,0xF010,0)
    start.timeout.connect(begin)
    try:
        start.start(1);timeout.start(3000);loop.exec()
    finally:
        timeout.stop();start.stop();APP.removeNativeEventFilter(observer)
        if sender.ident is not None:sender.join(1)
        APP.sendEvent(owner,QMouseEvent(QEvent.NonClientAreaMouseButtonRelease,
            QPoint(20,-10),outside,Qt.LeftButton,Qt.NoButton,Qt.NoModifier))
    APP.processEvents()
    names=[name for _,name in rows]
    assert names.count('enter')==1 and names.count('exit')==1,names
    assert names.count('owner')>=10,names
    for follower in followers:
        label=type(follower).__name__
        assert names.count(label)>=5,('popup did not follow during native modal move',label,names)
        assert follower.isVisible(),label
    screen=anchor.screen().availableGeometry()
    expected=max(screen.left(),min(anchor.mapToGlobal(QPoint(anchor.width()//2,0)).x()-popup.width()//2,
                                  screen.right()-popup.width()+1))
    assert abs(popup.x()-expected)<=1,(popup.x(),expected)
    owner_times=[t for t,name in rows if name=='owner']
    timings=[]
    for follower in followers:
        label=type(follower).__name__
        delays=sorted((t-max(p for p in owner_times if p<=t))*1000 for t,name in rows
                      if name==label and any(p<=t for p in owner_times))
        timings.append(f'{label} updates={len(delays)}, p95={delays[int(len(delays)*.95)]:.2f}ms, max={max(delays):.2f}ms')
    print(f'PASS: actual native move loop owner={len(owner_times)}; '+ '; '.join(timings),flush=True)


def main():
    with tempfile.TemporaryDirectory() as root:
        root=Path(root);manager=host_api.PluginManager(plugins_dir=str(root/'plugins'),enabled=[])
        manager.install_zip(str(ROOT/'dist/bili_live-1.0.zip'))
        values={'SESSDATA':'synthetic','bili_jct':'synthetic','buvid3':'synthetic'}
        manager.register_credentials('bilibili',lambda:values,identity_keys=('SESSDATA','bili_jct'))
        for name in tuple(sys.modules):
            if name.startswith('ddm_plugin_bili_live.'):del sys.modules[name]
        manager.load();entry=manager.plugins[0]
        api=importlib.import_module('ddm_plugin_bili_live.gift_api')
        media=importlib.import_module('ddm_plugin_bili_live.gift_media')
        popup_module=importlib.import_module('ddm_plugin_bili_live.gift_popup')
        workers=importlib.import_module('ddm_plugin_bili_live.workers')
        tile=Tile({'room_id':'90001','uname':'Synthetic native','live':False})
        tile.move(100,400);tile.resize(900,500);manager.attach_tile(tile);tile.show();APP.processEvents()
        composer=tile._plugin_controls.entries[0].control.widget
        video=root/'fixture.mp4'
        subprocess.run([ffmpeg_path(),'-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
            'color=c=blue:s=320x180:r=25','-t','30','-c:v','mpeg4',str(video)],check=True)
        gif=root/'fixture.gif';gif.write_bytes(b'GIF89a'+bytes.fromhex('01000100800000000000ffffff')+
            bytes.fromhex('21f904000a0000002c0000000001000100000202440100')*2+b';')
        player=TilePlayer(tile.video,tile);player.play(str(video));tile.set_video_active(True)
        catalog=api.normalize_catalog(room_payload(),'90001')
        user32=ctypes.windll.user32
        user32.ChildWindowFromPointEx.argtypes=[wintypes.HWND,wintypes.POINT,wintypes.UINT]
        user32.ChildWindowFromPointEx.restype=wintypes.HWND
        user32.MapWindowPoints.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.POINTER(wintypes.POINT),wintypes.UINT]
        user32.IsChild.argtypes=[wintypes.HWND,wintypes.HWND]
        user32.IsChild.restype=wintypes.BOOL
        def native_hit(widget,owner):
            hwnd=int(owner.winId());point=widget.mapTo(owner,widget.rect().center());scale=owner.devicePixelRatioF()
            point=wintypes.POINT(round(point.x()*scale),round(point.y()*scale))
            for _ in range(20):
                child=user32.ChildWindowFromPointEx(hwnd,point,3)
                if not child or child==hwnd:return hwnd
                user32.MapWindowPoints(hwnd,child,ctypes.byref(point),1);hwnd=child
            raise AssertionError('native recursion')
        try:
            settle(lambda:player.player.get_time()>0)
            with patch.object(api,'get_catalog',return_value=catalog),patch.object(api,'get_wallet',return_value=99999),                    patch.object(api,'get_bag',return_value=api.GiftTab('bag','背包',())),                    patch.object(media,'_fetch',return_value=str(gif)):
                for mode in ('dark','light'):
                    theme.apply(mode);APP.setStyleSheet(theme.qss())
                    for width in (900,360,200):
                        tile.resize(width,400);APP.processEvents();tile.raise_overlays()
                        assert composer.edit.width()>=24,(width,composer.edit.width())
                        target=native_hit(composer.gift_button,tile);bottom=int(tile.bottom.winId())
                        assert target==bottom or user32.IsChild(bottom,target),'VLC blocked gift entry'
                        composer.open_gifts();settle(lambda:not composer.gift_controller.state.loading)
                        popup=composer.gift_popup
                        settle(lambda:any(c.image.movie for c in popup._cards))
                        assert popup.screen().availableGeometry().contains(popup.geometry())
                        card=next(c for c in popup._cards if c.item.key=='main:1')
                        QTest.mouseClick(card,Qt.LeftButton);settle(lambda:popup.quantity_popup.isVisible())
                        quantity=popup.quantity_popup
                        assert quantity.screen().availableGeometry().contains(quantity.geometry())
                        if width==900:
                            if mode=='dark':check_native_move_loop(tile,popup,composer.gift_button)
                            title_point=QPoint(tile.x()+20,tile.y()-10)
                            APP.sendEvent(tile,QMouseEvent(QEvent.NonClientAreaMouseButtonPress,
                                QPoint(20,-10),title_point,Qt.LeftButton,Qt.LeftButton,Qt.NoModifier))
                            for _ in range(24):
                                before,quantity_before=popup.pos(),quantity.pos()
                                anchor_before=composer.gift_button.mapToGlobal(QPoint())
                                tile.move(tile.pos()+QPoint(2,1));APP.processEvents()
                                delta=composer.gift_button.mapToGlobal(QPoint())-anchor_before
                                # Native physical pixels round at fractional DPR.
                                for label,actual in (('gift',popup.pos()-before),('quantity',quantity.pos()-quantity_before)):
                                    error=actual-delta
                                    assert abs(error.x())<=1 and abs(error.y())<=1,(label,actual,delta)
                                assert popup.isVisible() and quantity.isVisible()
                            APP.sendEvent(tile,QMouseEvent(QEvent.NonClientAreaMouseButtonRelease,
                                QPoint(20,-10),title_point,Qt.LeftButton,Qt.NoButton,Qt.NoModifier))
                        QCursor.setPos(popup.mapToGlobal(popup.rect().center()))
                        popup.mark_sent(api.GiftResult('main:1',1,{}))
                        QCursor.setPos(QPoint(popup.x()+5,popup.y()-2));QTest.qWait(50)
                        QCursor.setPos(quantity.mapToGlobal(quantity.rect().center()));QTest.qWait(170)
                        assert popup.isVisible() and quantity.isVisible()
                        popup.hide();assert all(c.image.movie is None or c.image.movie.state()!=QMovie.Running for c in popup._cards)
                    if mode=='dark':
                        tile.resize(900,400);APP.processEvents()
                        emote_api=importlib.import_module('ddm_plugin_bili_live.composer').bili
                        with patch.object(emote_api,'get_emoticons',return_value=[]):
                            composer.open_emoticons()
                            settle(lambda:composer.popup is not None and not workers._active_requests)
                            check_native_move_loop(tile,composer.popup,composer.emote_button)
                            composer.popup.hide()
                    tile.setProperty('fullscreen',True);tile._layout_areas();APP.processEvents()
                    geometry=tile.video.geometry();composer.edit.setFocus();APP.processEvents()
                    assert tile.plugin_interaction_active(),'editor focus not protected'
                    APP.sendEvent(composer.edit,QInputMethodEvent('pinyin',[]))
                    composer.edit.setText('draft');QTest.keyClick(composer.edit,Qt.Key_Return)
                    assert not composer.busy,'IME confirmation sent text'
                    APP.sendEvent(composer.edit,QInputMethodEvent('',[]));composer.edit.clearFocus()
                    tile.setProperty('fullscreen',False);tile._layout_areas()
                print(f'PASS: native VLC gift hit targets, dark/light 900/360/200, continuous owner/picker following, GIFs, gap, focus/IME; scale={tile.devicePixelRatioF()}',flush=True)
        finally:
            manager.unload();settle(lambda:not workers._active_requests)
            player.release();tile.close();tile.deleteLater();APP.sendPostedEvents(None,QEvent.DeferredDelete)

if __name__=='__main__':
    main()
