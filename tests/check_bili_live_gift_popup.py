"""Real Qt gift/quantity actions and complete pointer-close semantics."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from bili_live_support import bili
from bili_live_fixtures import room_payload
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication,QPushButton
from PySide6.QtTest import QTest
from ddm_plugin_bili_live.gift_api import normalize_catalog,GiftTab,GiftResult
from ddm_plugin_bili_live.gift_controller import GiftPanelState
from ddm_plugin_bili_live.gift_popup import GiftPopup,GiftCard,GiftButton

APP=QApplication.instance() or QApplication([])
def settle(predicate,seconds=2):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        APP.processEvents()
        if predicate():return
        QTest.qWait(10)
    raise AssertionError('popup state not reached')

class PopupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.button=GiftButton();self.button.move(300,400);self.button.show()
        self.popup=GiftPopup(cache_dir=self.temp.name)
        self.item=normalize_catalog(room_payload(),'90001').tabs[0].items[1]
        self.single=replace(self.item,key='single',counts=(1,),allow_custom=False)
        self.state=GiftPanelState(tabs=(GiftTab('main','礼物',(self.single,self.item)),),wallet=99999)
        self.sent=[];self.popup.sendRequested.connect(lambda *x:self.sent.append(x))
        # Network-free media fixtures: cards remain readable on failed images.
        self.no_media=patch.object(self.popup.media,'request');self.no_media.start()
        self.popup.set_state(self.state);self.popup.open_at(self.button);APP.processEvents()
        self.inside=self.popup.mapToGlobal(self.popup.rect().center())
        self.outside=QPoint(self.popup.geometry().right()+100,self.popup.geometry().bottom()+100)
    def tearDown(self):
        self.popup.shutdown();self.popup.deleteLater();self.button.close()
        APP.processEvents();self.no_media.stop();self.temp.cleanup()
    def card(self,key):return next(c for c in self.popup.findChildren(GiftCard) if c.item.key==key)
    def point(self,p):
        QCursor.setPos(p);APP.processEvents()
    def test_one_click_and_quantity_click_exactly_once(self):
        self.card('single').click();self.assertEqual(self.sent,[('single',1)])
        self.sent.clear();self.card('main:1').click();APP.processEvents()
        quantity=self.popup.quantity_popup
        self.assertTrue(quantity.isVisible());self.assertEqual(self.sent,[])
        quantity.edit.setText('10');quantity.send_button.click()
        self.assertEqual(self.sent,[('main:1',10)])
    def test_invalid_counts_and_unknown_balance_disable_paid_send(self):
        self.card('main:1').click();q=self.popup.quantity_popup
        for value in ('','0','1.5','-1','501','999999'):
            q.edit.setText(value);self.assertFalse(q.send_button.isEnabled(),value)
        q.edit.setText('1');self.assertTrue(q.send_button.isEnabled())
        self.popup.set_state(replace(self.state,wallet=None,wallet_error='offline'))
        self.assertFalse(q.send_button.isEnabled());self.assertIn('未知',self.popup.wallet_label.text())
    def test_browsing_outside_stays_open(self):
        self.point(self.outside);QTest.qWait(220)
        self.assertTrue(self.popup.isVisible())
    def test_success_inside_then_outside_closes_both(self):
        self.card('main:1').click();self.point(self.inside)
        self.popup.mark_sent(GiftResult('main:1',1,{}));QTest.qWait(220)
        self.assertTrue(self.popup.isVisible())
        self.point(self.outside);settle(lambda:not self.popup.isVisible())
        self.assertFalse(self.popup.quantity_popup.isVisible())
    def test_response_arrives_outside_closes(self):
        self.point(self.outside);self.popup.mark_sent(GiftResult('single',1,{}))
        settle(lambda:not self.popup.isVisible())
    def test_second_send_inflight_defers_close(self):
        self.point(self.inside);self.popup.mark_sent(GiftResult('single',1,{}))
        self.popup.set_state(replace(self.state,sending=True));self.point(self.outside)
        QTest.qWait(240);self.assertTrue(self.popup.isVisible())
        self.popup.set_state(replace(self.state,sending=False,feedback='failed'))
        settle(lambda:not self.popup.isVisible())
    def test_failure_does_not_arm_and_reopen_resets_latch(self):
        self.popup.set_state(replace(self.state,feedback='failed'))
        self.point(self.outside);QTest.qWait(220);self.assertTrue(self.popup.isVisible())
        self.popup.mark_sent(GiftResult('single',1,{}));settle(lambda:not self.popup.isVisible())
        self.popup.open_at(self.button);self.point(self.outside);QTest.qWait(220)
        self.assertTrue(self.popup.isVisible())
    def test_quantity_gap_transition_and_hidden_completion(self):
        self.card('main:1').click();q=self.popup.quantity_popup
        self.point(self.inside);self.popup.mark_sent(GiftResult('single',1,{}))
        self.point(QPoint(self.popup.x()+10,self.popup.y()-2));QTest.qWait(50)
        self.point(q.mapToGlobal(q.rect().center()));QTest.qWait(180)
        self.assertTrue(self.popup.isVisible());self.assertTrue(q.isVisible())
        self.popup.hide();self.popup.mark_sent(GiftResult('single',1,{}));QTest.qWait(180)
        self.assertFalse(self.popup.isVisible());self.assertFalse(q.isVisible())
    def test_escape_outside_click_toggle_and_screen_bounds(self):
        screen=self.button.screen().availableGeometry()
        self.assertTrue(screen.contains(self.popup.geometry()))
        self.assertEqual(self.popup.columns,5)
        self.assertLessEqual(self.popup.width(),400);self.assertLessEqual(self.popup.height(),340)
        self.popup.open_at(self.button);self.assertFalse(self.popup.isVisible())
        self.popup.open_at(self.button);QTest.keyClick(self.popup,Qt.Key_Escape)
        self.assertFalse(self.popup.isVisible())
        self.popup.open_at(self.button)
        other=QPushButton();other.show();QTest.mouseClick(other,Qt.LeftButton)
        self.assertFalse(self.popup.isVisible());other.close()
    def test_independent_errors_bag_stock_and_unsupported_remain_readable(self):
        bag=replace(self.single,key='bag:702',source='bag',bag_id=702,stock=1,expires_at=int(time.time())+3600)
        locked=replace(self.single,key='locked',disabled_reason='special flow')
        state=replace(self.state,tabs=(GiftTab('bag','背包',(bag,locked)),),wallet=None,
            catalog_error='catalog offline',wallet_error='wallet offline')
        self.popup.set_state(state);APP.processEvents()
        self.assertTrue(self.card('bag:702').isEnabled());self.assertFalse(self.card('locked').isEnabled())
        self.assertIn('1',self.card('bag:702').toolTip())
        self.assertIn('catalog offline',self.popup.status.text())
        self.card('bag:702').click();self.assertEqual(self.sent,[('bag:702',1)])

if __name__=='__main__':unittest.main()
