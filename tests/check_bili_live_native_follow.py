"""Native HWND following while Qt geometry events are still queued."""
import ctypes
from ctypes import wintypes
import tempfile
import unittest
from unittest.mock import patch
from bili_live_support import HOST
from bili_live_fixtures import room_payload
from PySide6.QtCore import QEvent,QPoint,Qt
from PySide6.QtWidgets import QApplication,QPushButton,QVBoxLayout,QWidget
from PySide6.QtTest import QTest
from ddm_plugin_bili_live.emoticons import EmoticonPopup
from ddm_plugin_bili_live.gift_api import GiftTab,normalize_catalog
from ddm_plugin_bili_live.gift_controller import GiftPanelState
from ddm_plugin_bili_live.gift_popup import GiftPopup

APP=QApplication.instance() or QApplication([])

@unittest.skipUnless(APP.platformName()=='windows','requires native Windows HWNDs')
class NativeFollowTests(unittest.TestCase):
    def setUp(self):
        self.user32=ctypes.WinDLL('user32',use_last_error=True)
        self.user32.GetWindowRect.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.RECT))
        self.user32.SetWindowPos.argtypes=(wintypes.HWND,wintypes.HWND,ctypes.c_int,
            ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT)
        self.user32.SendMessageW.argtypes=(wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
        self.user32.SendMessageW.restype=ctypes.c_ssize_t
        self.temp=tempfile.TemporaryDirectory()
        self.owner=QWidget();QVBoxLayout(self.owner).addWidget(QPushButton('anchor'))
        self.anchor=self.owner.findChild(QPushButton)
        self.owner.setGeometry(180,430,500,220);self.owner.show();APP.processEvents()
        self.emote=EmoticonPopup(self.owner)
        self.gift=GiftPopup(self.owner,cache_dir=self.temp.name)
        self.media=patch.object(self.gift.media,'request');self.media.start()
        item=normalize_catalog(room_payload(),'90001').tabs[0].items[1]
        self.gift.set_state(GiftPanelState(tabs=(GiftTab('main','礼物',(item,)),),wallet=99999))
    def tearDown(self):
        self.user32.SendMessageW(int(self.owner.winId()),0x232,0,0)
        self.emote.shutdown();self.gift.shutdown();self.owner.close();self.owner.deleteLater()
        APP.processEvents();APP.sendPostedEvents(None,QEvent.DeferredDelete)
        self.media.stop();self.temp.cleanup()
    def rect(self,widget):
        result=wintypes.RECT()
        self.assertTrue(self.user32.GetWindowRect(int(widget.winId()),ctypes.byref(result)))
        return result.left,result.top,result.right,result.bottom
    def move_native(self,widget,x,y):
        self.assertTrue(self.user32.SetWindowPos(int(widget.winId()),None,x,y,0,0,0x15))
    def assert_native_position(self,actual,expected,message=''):
        # Qt rounds each logical coordinate at fractional device scales.
        self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)),1,message)
    def start_drag(self):self.user32.SendMessageW(int(self.owner.winId()),0x231,0,0)
    def open_picker(self,popup,quantity=False):
        popup.open_at(self.anchor);APP.processEvents()
        if quantity:
            popup._quantity(popup._cards[0]);APP.processEvents()
        return [popup]+([popup.quantity_popup] if quantity else [])
    def test_first_and_continuous_native_moves_follow_before_qt_can_dispatch(self):
        for popup in (self.emote,self.gift):
            with self.subTest(popup=type(popup).__name__):
                followers=self.open_picker(popup,quantity=popup is self.gift)
                origin=self.rect(self.owner);initial=[self.rect(f) for f in followers]
                self.start_drag()
                try:
                    for dx,dy in ((45,12),(85,24),(35,8),(0,0)):
                        self.move_native(self.owner,origin[0]+dx,origin[1]+dy)
                        # No processEvents: Windows already displays the owner.
                        for follower,before in zip(followers,initial):
                            actual=self.rect(follower)
                            self.assert_native_position(actual[:2],(before[0]+dx,before[1]+dy),
                                f'{type(follower).__name__} waited for queued Qt geometry')
                    positions=[self.rect(f) for f in followers]
                    APP.processEvents()
                    for follower,position in zip(followers,positions):
                        self.assert_native_position(self.rect(follower),position,
                                                    'Qt dispatch made a native follower jump again')
                finally:
                    self.user32.SendMessageW(int(self.owner.winId()),0x232,0,0)
                    popup.hide();self.move_native(self.owner,*origin[:2]);APP.processEvents()
    def test_native_drag_does_not_reopen_hidden_pickers(self):
        for popup in (self.emote,self.gift):
            self.open_picker(popup);popup.hide();APP.processEvents()
            before=self.rect(popup);origin=self.rect(self.owner);self.start_drag()
            self.move_native(self.owner,origin[0]+40,origin[1]+10)
            self.assertEqual(self.rect(popup),before);self.assertFalse(popup.isVisible())
            APP.processEvents()

    def test_native_edge_clamp_returns_to_original_anchor_without_drift(self):
        popup=self.emote;self.open_picker(popup)
        original=self.rect(popup);origin=self.rect(self.owner)
        work=self.owner.screen().availableGeometry();scale=self.owner.devicePixelRatioF()
        self.start_drag()
        for delta in (-1200,1200,0):
            self.move_native(self.owner,origin[0]+delta,origin[1])
            rect=self.rect(popup)
            self.assertGreaterEqual(rect[0],round(work.left()*scale))
            self.assertLessEqual(rect[2],round((work.right()+1)*scale))
        self.assertEqual(self.rect(popup),original)

    def test_native_quantity_stays_with_gift_when_parent_clamps_at_edge(self):
        followers=self.open_picker(self.gift,quantity=True)
        original=[self.rect(f) for f in followers];origin=self.rect(self.owner)
        offset=original[1][0]-original[0][0];self.start_drag()
        self.move_native(self.owner,origin[0]-1200,origin[1])
        self.assertEqual(self.rect(followers[1])[0]-self.rect(followers[0])[0],offset,
                         'quantity window detached from the clamped gift picker')

    def test_native_follow_does_not_move_for_an_unrelated_window(self):
        self.open_picker(self.emote);before=self.rect(self.emote)
        other=QWidget();other.setGeometry(600,500,200,100);other.show();APP.processEvents()
        try:
            self.user32.SendMessageW(int(other.winId()),0x231,0,0)
            self.move_native(other,650,520)
            self.assertEqual(self.rect(self.emote),before)
        finally:other.close();other.deleteLater();APP.processEvents()

    def test_reopening_picker_rebases_native_follow_after_owner_moves(self):
        self.open_picker(self.emote);self.emote.hide()
        owner=self.rect(self.owner);self.move_native(self.owner,owner[0]+50,owner[1]+20)
        APP.processEvents();self.open_picker(self.emote)
        before=self.rect(self.emote);owner=self.rect(self.owner);self.start_drag()
        self.move_native(self.owner,owner[0]+45,owner[1]+12)
        self.assert_native_position(self.rect(self.emote)[:2],(before[0]+45,before[1]+12))

    def test_top_edge_placement_does_not_jump_when_native_drag_ends(self):
        self.open_picker(self.emote);origin=self.rect(self.owner);self.start_drag()
        self.move_native(self.owner,origin[0],origin[1]-400)
        during=self.rect(self.emote)
        self.user32.SendMessageW(int(self.owner.winId()),0x232,0,0);APP.processEvents()
        self.assert_native_position(self.rect(self.emote),during,
                         'native clamping disagreed with normal above/below placement')

    def test_interactive_resize_keeps_using_updated_layout_anchor(self):
        self.open_picker(self.emote);origin=self.rect(self.owner);self.start_drag()
        self.assertTrue(self.user32.SetWindowPos(int(self.owner.winId()),None,origin[0],origin[1],
                                                origin[2]-origin[0]+120,origin[3]-origin[1]+60,0x14))
        # Resize deliberately retains normal coalesced descendant layout.
        QTest.qWait(15)
        screen=self.anchor.screen().availableGeometry()
        expected=max(screen.left(),min(
            self.anchor.mapToGlobal(QPoint(self.anchor.width()//2,0)).x()-self.emote.width()//2,
            screen.right()-self.emote.width()+1))
        self.assertLessEqual(abs(self.emote.x()-expected),1,'native drag froze placement during resize')

if __name__=='__main__':unittest.main()
