"""Composer-to-notice routing uses the failed request's room and login status."""
import threading
import time
import unittest
from unittest.mock import patch,Mock
from bili_live_support import session,bili
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ddm_plugin_bili_live.composer import DanmakuComposer
APP=QApplication.instance() or QApplication([])

def settle(predicate):
    end=time.monotonic()+3
    while time.monotonic()<end:
        APP.processEvents()
        if predicate():return
        QTest.qWait(5)
    raise AssertionError('notice did not arrive')

class Notices:
    def __init__(self):self.calls=[];self.closed=False
    def error(self,message,**context):self.calls.append((message,context));return True
    def close(self):self.closed=True

class FeedbackTests(unittest.TestCase):
    def setUp(self):
        media=patch('ddm_plugin_bili_live.gift_media.GiftMediaLoader.request')
        media.start();self.addCleanup(media.stop)
        session.set_login_cookies('fake','csrf','device-test');self.notices=Notices()
        self.widget=DanmakuComposer(session,notifier=self.notices)
        self.widget.set_room('101',room={'room_id':'101','uname':'测试主播','title':'直播间标题'})
        self.widget.show();APP.processEvents();self.release=threading.Event()
    def tearDown(self):
        self.release.set();self.widget.shutdown();self.widget.deleteLater()
        from ddm_plugin_bili_live.workers import _active_requests
        settle(lambda:not _active_requests)
        APP.sendPostedEvents(None,QEvent.DeferredDelete);APP.processEvents()
    def test_send_failure_routes_room_and_retains_draft_without_duplicate_tooltip(self):
        self.widget.edit.setText('保留草稿')
        with patch.object(bili,'send_danmaku',side_effect=ValueError('发送频率过快')):
            self.widget.send_text();settle(lambda:not self.widget.busy)
        self.assertEqual(self.widget.edit.text(),'保留草稿')
        self.assertEqual(len(self.notices.calls),1)
        error,context=self.notices.calls[0]
        self.assertIn('发送频率',str(error))
        self.assertEqual(context['room']['room_id'],'101');self.assertEqual(context['room']['uname'],'测试主播')
        self.assertFalse(self.widget.feedback.isVisible())
    def test_missing_credentials_and_opaque_server_login_error_offer_relogin(self):
        session.set_login_cookies('','');self.widget.send_text()
        self.widget.edit.setText('需要登录');self.widget.send_text()
        self.assertTrue(self.notices.calls[-1][1]['login_required'])
        session.set_login_cookies('fake','csrf')
        with patch.object(bili.requests,'get',return_value=Mock(status_code=200,
                json=lambda:{'code':-101,'message':'opaque account error'})):
            self.widget.send_text();settle(lambda:not self.widget.busy)
        error,context=self.notices.calls[-1]
        self.assertTrue(context['login_required'])
    def test_stale_send_failure_does_not_notify_the_new_room(self):
        entered=threading.Event()
        def send(*args,**kwargs):entered.set();self.release.wait(3);raise ValueError('old room')
        self.widget.edit.setText('草稿')
        with patch.object(bili,'send_danmaku',side_effect=send):
            self.widget.send_text();settle(entered.is_set)
            self.widget.set_room('202',room={'room_id':'202','uname':'新主播'})
            self.release.set()
            from ddm_plugin_bili_live.workers import _active_requests
            settle(lambda:not _active_requests)
        self.assertEqual(self.notices.calls,[])
    def test_catalog_and_gift_failures_route_to_notifications(self):
        with patch.object(bili,'get_emoticons',side_effect=ValueError('读取目录失败')):
            self.widget.open_emoticons();settle(lambda:self.widget._catalog_pending is None)
        self.assertIn('读取目录失败',str(self.notices.calls[-1][0]))
        from ddm_plugin_bili_live import gift_api as gifts
        with patch.object(gifts,'get_catalog',side_effect=ValueError('礼物目录不可用')), \
             patch.object(gifts,'get_wallet',return_value=1000), \
             patch.object(gifts,'get_bag',return_value=gifts.GiftTab('bag','背包',())):
            self.widget.open_gifts();settle(lambda:not self.widget.gift_controller.state.loading)
        self.assertTrue(any('礼物目录不可用' in str(error) for error,_ in self.notices.calls))
        self.widget._gift_error(bili.BiliAPIError('账号未登录',-101))
        self.assertEqual(self.notices.calls[-1][1]['room']['room_id'],'101')
    def test_opaque_gift_auth_errors_keep_login_action_for_reads_and_send(self):
        from ddm_plugin_bili_live import gift_api as gifts
        from bili_live_fixtures import room_payload
        failure=bili.BiliAPIError('opaque account rejection',-101)
        with patch.object(gifts,'get_catalog',side_effect=failure), \
             patch.object(gifts,'get_wallet',return_value=99999), \
             patch.object(gifts,'get_bag',return_value=gifts.GiftTab('bag','背包',())):
            self.widget.open_gifts();settle(lambda:not self.widget.gift_controller.state.loading)
        self.assertEqual(len(self.notices.calls),1)
        self.assertTrue(self.notices.calls[0][1]['login_required'])
        self.notices.calls.clear()
        controller=self.widget.gift_controller;controller.set_active(False)
        from dataclasses import replace
        controller.state=replace(controller.state,tabs=gifts.normalize_catalog(room_payload(),'101').tabs)
        with patch.object(gifts,'prepare_target',side_effect=failure):
            controller.send('main:1',1);settle(lambda:bool(self.notices.calls))
        self.assertEqual(len(self.notices.calls),1)
        self.assertTrue(self.notices.calls[0][1]['login_required'])

    def test_send_timeout_notice_keeps_uncertain_result_guidance(self):
        import requests
        self.widget.edit.setText('不要重复发送')
        with patch.object(bili,'send_danmaku',side_effect=requests.Timeout('raw transport details')):
            self.widget.send_text();settle(lambda:not self.widget.busy)
        message,context=self.notices.calls[-1]
        self.assertIn('发送请求超时',str(message));self.assertIn('结果可能未确认',str(message))
        self.assertNotIn('raw transport',str(message));self.assertFalse(context['login_required'])

    def test_gift_read_timeout_notice_does_not_claim_an_uncertain_send(self):
        import requests
        from ddm_plugin_bili_live import gift_api as gifts
        with patch.object(gifts,'get_catalog',side_effect=requests.Timeout('raw read details')), \
             patch.object(gifts,'get_wallet',return_value=99999), \
             patch.object(gifts,'get_bag',return_value=gifts.GiftTab('bag','背包',())):
            self.widget.open_gifts();settle(lambda:not self.widget.gift_controller.state.loading)
        message,context=self.notices.calls[-1]
        self.assertIn('请求超时',str(message));self.assertNotIn('发送',str(message))
        self.assertNotIn('未确认',str(message));self.assertFalse(context['login_required'])

    def test_shutdown_closes_reporter(self):
        self.widget.shutdown();self.assertTrue(self.notices.closed)

if __name__=='__main__':unittest.main()
