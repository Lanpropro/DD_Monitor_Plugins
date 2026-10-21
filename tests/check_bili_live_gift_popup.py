"""Real Qt gift/quantity actions and complete pointer-close semantics."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from bili_live_support import bili
from bili_live_fixtures import room_payload,guard_payload,upgrade_payload
from PySide6.QtCore import QEvent,QPoint,Qt,QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication,QPushButton,QMessageBox
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
    def upgrade_card(self):
        catalog=normalize_catalog(upgrade_payload(),'90001')
        self.popup.set_state(replace(self.state,tabs=catalog.tabs))
        card=self.popup._cards[0]
        self.point(card.mapToGlobal(card.rect().center()))
        return card

    def test_upgrade_first_click_opens_picker_second_click_sends_base_once(self):
        card=self.upgrade_card();QTest.mouseClick(card,Qt.LeftButton)
        self.assertEqual(self.sent,[])
        self.assertTrue(self.popup.quantity_popup.isVisible())
        QTest.mouseClick(card,Qt.LeftButton)
        self.assertEqual(self.sent,[('main:35638',1)])
        self.assertFalse(self.popup.quantity_popup.isVisible())

    def test_upgrade_selection_and_send_use_selected_gift_and_own_quantities(self):
        card=self.upgrade_card();card.click();q=self.popup.quantity_popup
        self.assertTrue(q.isVisible(),'single-count grouped gift must open its choices')
        for key,count,total in [('main:35638:35641',1,'10'),
                                ('main:35638:35640',1,'15'),
                                ('main:35638:35639',10,'150')]:
            with self.subTest(key=key):
                if not q.isVisible():card.click()
                QTest.mouseClick(q.variant_buttons[key],Qt.LeftButton)
                self.assertEqual(self.sent,[])
                q.edit.setText(str(count))
                self.assertIn(total,q.total.text())
                QTest.mouseClick(q.send_button,Qt.LeftButton)
                self.assertEqual(self.sent,[(key,count)])
                self.sent.clear()

    def test_upgrade_lock_and_quantity_reset_do_not_leak_into_ordinary_gifts(self):
        data=upgrade_payload()
        data['gift_data']['room_gift_list']['gold_list'][0]['upgrade_gift'][0].update(
            locked=True,lock_tip='需要解锁')
        self.popup.set_state(replace(self.state,tabs=normalize_catalog(data,'90001').tabs))
        card=self.popup._cards[0];card.click();q=self.popup.quantity_popup
        self.assertTrue(q.isVisible())
        locked=q.variant_buttons['main:35638:35641']
        self.assertFalse(locked.isEnabled());self.assertIn('需要解锁',locked.toolTip())
        locked.click();self.assertEqual(q.item.gift_id,35638)
        q.variant_buttons['main:35638:35639'].click();q.edit.setText('10')
        q.variant_buttons['main:35638:35640'].click()
        self.assertEqual(q.edit.text(),'1');self.assertTrue(q.edit.isReadOnly())
        self.assertEqual(self.sent,[])
        self.popup.set_state(self.state)
        self.card('main:1').click()
        self.assertTrue(q.isVisible());self.assertFalse(q.variants.isVisible())
        self.assertEqual(q.variant_buttons,{})

    def test_upgrade_send_is_blocked_by_wallet_busy_and_context_changes(self):
        card=self.upgrade_card();card.click();q=self.popup.quantity_popup
        self.assertTrue(q.isVisible())
        q.variant_buttons['main:35638:35640'].click()
        self.popup.set_state(replace(self.popup.state,wallet=1000))
        self.assertFalse(q.send_button.isEnabled())
        self.popup.set_state(replace(self.popup.state,sending=True))
        self.assertTrue(all(not b.isEnabled() for b in q.variant_buttons.values()))
        q.send_button.click();self.assertEqual(self.sent,[])
        self.popup.set_state(GiftPanelState())
        self.assertFalse(q.isVisible())
        self.popup._request_send('main:35638:35640',1)
        self.assertEqual(self.sent,[])

    def test_guard_gift_confirms_price_and_terms_before_emitting_send(self):
        item=normalize_catalog(guard_payload(),'90001').tabs[0].items[0]
        self.popup.set_state(replace(self.state,tabs=(GiftTab('main','礼物',(item,)),),wallet=19998000))
        card=self.popup._cards[0]
        self.assertTrue(card.isEnabled())
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.Cancel):card.click()
        self.assertEqual(self.sent,[])
        seen=[]
        def accept(dialog):
            seen.append(dialog.text()+dialog.informativeText())
            return QMessageBox.Ok
        with patch.object(QMessageBox,'exec',new=accept):card.click()
        self.assertEqual(self.sent,[(item.key,1)])
        self.assertIn('舰长一号',seen[0]);self.assertIn('1980',seen[0])
        self.assertIn('service-agreement',seen[0]);self.assertIn('live-protocol',seen[0]);self.assertIn('lp-protocol',seen[0])

    def test_guard_confirmation_cannot_send_after_context_reset(self):
        item=normalize_catalog(guard_payload(),'90001').tabs[0].items[0]
        self.popup.set_state(replace(self.state,tabs=(GiftTab('main','礼物',(item,)),),wallet=19998000))
        self.assertTrue(self.popup._cards[0].isEnabled())
        def reset(dialog):
            self.popup.set_state(GiftPanelState())
            return QMessageBox.Ok
        with patch.object(QMessageBox,'exec',new=reset):self.popup._cards[0].click()
        self.assertEqual(self.popup.state.tabs,())
        self.assertEqual(self.sent,[])

    def test_real_guard_dialog_click_keeps_picker_open_and_emits_once(self):
        item=normalize_catalog(guard_payload(),'90001').tabs[0].items[0]
        self.popup.set_state(replace(self.state,tabs=(GiftTab('main','礼物',(item,)),),wallet=19998000))
        observed=[]
        def accept():
            dialog=APP.activeModalWidget()
            observed.append(isinstance(dialog,QMessageBox))
            if dialog:QTest.mouseClick(dialog.button(QMessageBox.Ok),Qt.LeftButton)
        QTimer.singleShot(20,accept)
        self.popup._cards[0].click()
        self.assertEqual(observed,[True]);self.assertTrue(self.popup.isVisible())
        self.assertEqual(self.sent,[(item.key,1)])

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
        APP.sendPostedEvents(None,QEvent.DeferredDelete)
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

    def test_retained_unconfirmed_balance_is_visible_but_cannot_buy(self):
        self.card('main:1').click();q=self.popup.quantity_popup
        self.popup.set_state(replace(self.state,wallet_valid=False))
        self.assertNotIn('未知',self.popup.wallet_label.text())
        self.assertFalse(self.card('main:1').isEnabled())
        self.assertFalse(q.send_button.isEnabled())
        self.popup._request_send('main:1',1)
        self.assertEqual(self.sent,[])
        self.popup.set_state(replace(self.state,wallet_valid=True))
        self.assertTrue(self.card('main:1').isEnabled());self.assertTrue(q.send_button.isEnabled())

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
        self.popup.open_at(self.button)
        self.point(self.outside)
        QTest.qWait(220)
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
        other=QPushButton();other.resize(60,28)
        # Native placement can overlap the picker at fractional DPI. Make this
        # an actual outside click, independent of Windows' default position.
        other.move(screen.bottomRight()-QPoint(80,40));other.show();APP.processEvents()
        QTest.mouseClick(other,Qt.LeftButton)
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
