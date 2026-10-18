"""Regression coverage for anchored pickers, quantities, loading and Qt warnings."""
from dataclasses import replace
import gc
import tempfile
import sys
import threading
import time
import unittest
import shiboken6
from unittest.mock import patch
from bili_live_support import bili
from bili_live_fixtures import room_payload
from PySide6.QtCore import QEvent, QPoint, Qt, qInstallMessageHandler
from PySide6.QtGui import QCursor, QImage, QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget
from ddm import images, theme
from ddm_plugin_bili_live.emoticons import EmoticonPopup
from ddm_plugin_bili_live.gift_api import normalize_catalog, GiftTab
from ddm_plugin_bili_live.gift_controller import GiftPanelState
from ddm_plugin_bili_live.gift_popup import GiftPopup
from ddm_plugin_bili_live.gift_media import GiftImageLabel
from ddm_plugin_bili_live.workers import _active_requests

APP = QApplication.instance() or QApplication([])

def settle(predicate, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        APP.processEvents()
        if predicate(): return
        QTest.qWait(5)
    raise AssertionError('UI state did not settle')

class PickerUXTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = QWidget(); self.host.setGeometry(100, 450, 400, 250)
        row = QVBoxLayout(self.host)
        self.anchor = QPushButton('picker'); row.addWidget(self.anchor)
        self.host.show(); APP.processEvents()
        self.gift = GiftPopup(self.host, cache_dir=self.temp.name)
        self.emote = EmoticonPopup(self.host)
        self.media = patch.object(self.gift.media, 'request'); self.media.start()
        self.item = normalize_catalog(room_payload(), '90001').tabs[0].items[1]
        self.other = replace(self.item, key='other')
        self.state = GiftPanelState(tabs=(GiftTab('main','礼物',(self.item,self.other)),),wallet=99999)
        self.gift.set_state(self.state)

    def tearDown(self):
        self.gift.shutdown(); self.emote.hide(); self.host.close()
        settle(lambda: not _active_requests)
        self.gift.deleteLater(); self.emote.deleteLater(); self.host.deleteLater()
        APP.sendPostedEvents(None,QEvent.DeferredDelete)
        self.media.stop(); self.temp.cleanup()

    def test_both_pickers_center_above_button_and_follow_host(self):
        for popup in (self.emote,self.gift):
            with self.subTest(popup=type(popup).__name__):
                popup.open_at(self.anchor); APP.processEvents()
                anchor = self.anchor.mapToGlobal(self.anchor.rect().center())
                self.assertLessEqual(abs(popup.geometry().center().x()-anchor.x()),2)
                before = popup.pos(); self.host.move(self.host.pos()+QPoint(30,20))
                APP.processEvents()
                self.assertEqual(popup.pos()-before,QPoint(30,20))
                self.assertTrue(popup.isVisible()); popup.hide()

    def test_same_window_type_and_outside_click_reaches_target(self):
        self.assertEqual(self.emote.windowType(),self.gift.windowType())
        other=QPushButton('outside'); clicked=[];other.clicked.connect(lambda:clicked.append(True));other.show()
        try:
            for popup in (self.emote,self.gift):
                popup.open_at(self.anchor);QTest.mouseClick(other,Qt.LeftButton)
                self.assertFalse(popup.isVisible())
            self.assertEqual(len(clicked),2)
        finally: other.close()

    def test_catalog_arriving_after_bag_selects_gifts_and_hides_empty_status(self):
        self.gift.set_state(replace(self.state,tabs=(GiftTab('bag','背包',()),)))
        self.gift.open_at(self.anchor)
        self.gift.set_state(replace(self.state,tabs=self.state.tabs+(GiftTab('bag','背包',()),)))
        self.assertEqual(self.gift.tabs.currentIndex(),0)
        self.assertTrue(self.gift.status.isHidden())

    def test_quantity_click_twice_sends_one_and_leave_for_other_card_closes(self):
        self.gift.open_at(self.anchor);APP.processEvents()
        card=self.gift._cards[0]; other=self.gift._cards[1];q=self.gift.quantity_popup
        sent=[];self.gift.sendRequested.connect(lambda *x:sent.append(x))
        QTest.mouseMove(card,card.rect().center());QTest.qWait(50)
        self.assertFalse(q.isVisible(),'hover must not open quantities')
        card.click();self.assertTrue(q.isVisible());self.assertEqual(sent,[])
        self.assertEqual(q.edit.text(),'1');self.assertLessEqual(q.width(),180)
        self.assertNotIn('1',[b.text() for b in q.options.findChildren(QPushButton)])
        card.click();self.assertEqual(sent,[(self.item.key,1)]);self.assertFalse(q.isVisible())
        card.click();self.assertTrue(q.isVisible())
        QCursor.setPos(other.mapToGlobal(other.rect().center()))
        settle(lambda:not q.isVisible())

    def test_send_status_shares_balance_row_and_success_is_quiet(self):
        self.gift.open_at(self.anchor)
        self.gift.set_state(replace(self.state,sending=True,feedback='正在赠送…'))
        self.assertIn('正在赠送',self.gift.wallet_label.text())
        self.assertTrue(self.gift.status.isHidden())
        self.gift.set_state(replace(self.state,wallet=99899))
        self.assertNotIn('正在赠送',self.gift.wallet_label.text())
        self.gift.set_state(replace(self.state,feedback='网络失败'))
        self.assertIn('网络失败',self.gift.wallet_label.text())

    def test_empty_gift_image_never_opens_a_file(self):
        warnings=[];previous=qInstallMessageHandler(lambda kind,ctx,msg:warnings.append(msg))
        try:
            image=GiftImageLabel();image.set_file('');image.set_file('');image.deleteLater()
            APP.sendPostedEvents(None,QEvent.DeferredDelete)
        finally:qInstallMessageHandler(previous)
        self.assertFalse(any('No file name specified' in x for x in warnings),warnings)

    def test_shutdown_cancels_layout_callbacks_before_native_widget_destruction(self):
        errors=[]
        popup=GiftPopup(self.host,cache_dir=self.temp.name)
        with patch.object(sys,'excepthook',side_effect=lambda *x:errors.append(x)):
            popup.set_state(self.state)
            popup.shutdown();popup.deleteLater()
            APP.sendPostedEvents(None,QEvent.DeferredDelete);APP.processEvents()
        self.assertEqual(errors,[],'queued image layout callback reached a deleted popup')

    def test_titlebar_drag_keeps_gift_and_quantity_open_after_send(self):
        self.gift.open_at(self.anchor);APP.processEvents()
        card=self.gift._cards[0];QCursor.setPos(card.mapToGlobal(card.rect().center()));card.click()
        self.gift._armed=True
        outside=QPoint(self.host.x()+20,self.host.y()-15)
        QCursor.setPos(outside)
        APP.sendEvent(self.host,QMouseEvent(QEvent.NonClientAreaMouseButtonPress,
            QPoint(20,-15),outside,Qt.LeftButton,Qt.LeftButton,Qt.NoModifier))
        self.host.move(self.host.pos()+QPoint(30,10));QTest.qWait(250)
        self.assertTrue(self.gift.isVisible())
        self.assertTrue(self.gift.quantity_popup.isVisible())
        APP.sendEvent(self.host,QMouseEvent(QEvent.NonClientAreaMouseButtonRelease,
            QPoint(20,-15),outside,Qt.LeftButton,Qt.NoButton,Qt.NoModifier))

    def test_shutdown_deletes_pickers_owned_by_host_window(self):
        for popup in (EmoticonPopup(self.host),GiftPopup(self.host,cache_dir=self.temp.name)):
            popup.open_at(self.anchor);popup.shutdown()
            APP.sendPostedEvents(None,QEvent.DeferredDelete)
            self.assertFalse(shiboken6.isValid(popup),'removed tile left a popup owned by MainWindow')

    def test_owner_destruction_without_shutdown_has_no_stale_event_callbacks(self):
        for picker in (EmoticonPopup, GiftPopup):
            with self.subTest(picker=picker.__name__):
                owner = QWidget()
                anchor = QPushButton('open', owner)
                popup = picker(owner)
                owner.show()
                popup.open_at(anchor)
                APP.processEvents()
                errors = []
                with patch.object(sys, 'excepthook', side_effect=lambda *args: errors.append(str(args[1]))):
                    try:
                        shiboken6.delete(owner)
                    except RuntimeError as error:
                        errors.append(str(error))
                    APP.processEvents()
                self.assertFalse(shiboken6.isValid(popup))
                self.assertEqual(errors, [], 'application filter reached a deleted picker or owner')

    def test_garbage_collected_pickers_do_not_receive_stale_application_events(self):
        errors = []
        def orphan_picker():
            owner = QWidget()
            anchor = QPushButton('open', owner)
            popup = EmoticonPopup(owner)
            popup.open_at(anchor)
            popup.close()
            owner.close()
        with patch.object(sys, 'excepthook', side_effect=lambda *args: errors.append(str(args[1]))):
            for _ in range(10):
                orphan_picker()
                gc.collect()
                APP.processEvents()
        self.assertEqual(errors, [], 'GC cleared a picker before its application filter was removed')

    def test_quantity_actions_keep_usable_height_in_application_theme(self):
        previous=APP.styleSheet()
        try:
            APP.setStyleSheet(theme.qss())
            self.gift.open_at(self.anchor);self.gift._cards[0].click();APP.processEvents()
            q=self.gift.quantity_popup
            for b in (*q.options.findChildren(QPushButton),q.send_button):
                self.assertGreaterEqual(b.height(),24)
        finally:APP.setStyleSheet(previous)

    def test_quantity_defaults_to_one_despite_service_option_order(self):
        q=self.gift.quantity_popup
        for counts,custom,want in (((10,1,99),False,'1'),((10,99),True,'1'),((10,99),False,'10')):
            with self.subTest(counts=counts,custom=custom):
                q.set_item(replace(self.item,counts=counts,allow_custom=custom),999999)
                self.assertEqual(q.edit.text(),want)

    def test_long_send_failure_cannot_expand_panel_off_screen(self):
        previous=APP.styleSheet()
        message='发送请求超时，结果可能未确认，请确认后再试'
        try:
            APP.setStyleSheet(theme.qss())
            self.gift.open_at(self.anchor)
            self.gift.set_state(replace(self.state,feedback=message*3));APP.processEvents()
            self.assertLessEqual(self.gift.width(),400)
            self.assertLessEqual(self.gift.height(),340)
            self.assertTrue(self.anchor.screen().availableGeometry().contains(self.gift.geometry()))
        finally:APP.setStyleSheet(previous)

if __name__=='__main__':unittest.main()
