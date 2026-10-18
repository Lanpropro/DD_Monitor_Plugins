"""Barrier-based shared submission and stale account-data checks."""
from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch
from bili_live_support import session
from bili_live_fixtures import room_payload,bag_payload
from PySide6.QtCore import QCoreApplication, QEvent
from ddm_plugin_bili_live import gift_api as api
from ddm_plugin_bili_live.gift_controller import GiftController, GiftCoordinator
from ddm_plugin_bili_live.workers import _active_requests

APP=QCoreApplication.instance() or QCoreApplication([])
def settle(predicate):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        APP.processEvents()
        if predicate():return
        time.sleep(.005)
    raise AssertionError('barrier not completed')

class ControllerTests(unittest.TestCase):
    def setUp(self):
        session.set_login_cookies('fake','csrf','device')
        self.coordinator=GiftCoordinator()
        self.controllers=[];self.releases=[]
        self.catalog=api.normalize_catalog(room_payload(),'90001')
        self.patches=[patch.object(api,'get_catalog',return_value=self.catalog),
            patch.object(api,'get_wallet',return_value=99999),
            patch.object(api,'get_bag',return_value=api.GiftTab('bag','背包',())),
            patch.object(api,'prepare_target',return_value=api.GiftTarget('90001',123,456))]
        for p in self.patches:p.start()
    def tearDown(self):
        for release in self.releases:release.set()
        for c in self.controllers:
            try:c.shutdown()
            except RuntimeError:pass
        settle(lambda:not _active_requests)
        for p in reversed(self.patches):p.stop()
    def controller(self):
        c=GiftController(session,self.coordinator);self.controllers.append(c)
        c.set_context('90001',session.snapshot());c.load()
        settle(lambda:not c.state.loading)
        return c
    def barrier(self):
        entered,release=threading.Event(),threading.Event();self.releases.append(release)
        def send(*args,**kwargs):
            entered.set();release.wait(3)
            return api.GiftResult(args[1].key,args[2],{})
        return entered,release,send
    def test_one_send_across_two_controllers(self):
        a,b=self.controller(),self.controller();entered,release,send=self.barrier()
        with patch.object(api,'send_gift',side_effect=send) as post:
            a.send('main:1',1);settle(entered.is_set);b.send('main:1',1)
            self.assertTrue(self.coordinator.busy);self.assertEqual(post.call_count,1)
            release.set();settle(lambda:not self.coordinator.busy)
    def test_room_change_during_target_preparation_never_posts(self):
        a=self.controller();entered,release,prepare=self.barrier()
        def target(*args,**kw):entered.set();release.wait(3);return api.GiftTarget('90001',123,456)
        with patch.object(api,'prepare_target',side_effect=target),patch.object(api.requests,'post') as post:
            a.send('main:1',1);settle(entered.is_set);a.set_context('90002',session.snapshot())
            release.set();settle(lambda:not self.coordinator.busy)
        post.assert_not_called()
    def test_destroyed_view_does_not_release_inflight_reservation(self):
        a,b=self.controller(),self.controller();entered,release,send=self.barrier()
        with patch.object(api,'send_gift',side_effect=send) as post:
            a.send('main:1',1);settle(entered.is_set);a.shutdown();a.deleteLater()
            APP.sendPostedEvents(None,QEvent.DeferredDelete)
            b.send('main:1',1);self.assertEqual(post.call_count,1);self.assertTrue(self.coordinator.busy)
            release.set();settle(lambda:not self.coordinator.busy)
    def test_account_change_does_not_apply_old_result(self):
        a=self.controller();entered,release,send=self.barrier();sent=[];a.sent.connect(sent.append)
        with patch.object(api,'send_gift',side_effect=send):
            a.send('main:1',1);settle(entered.is_set)
            session.set_login_cookies('new','csrf-new');a.set_context('90001',session.snapshot())
            release.set();settle(lambda:not self.coordinator.busy)
        self.assertEqual(sent,[]);self.assertIsNone(a.state.wallet)
    def test_old_wallet_and_bag_reads_after_success_are_ignored(self):
        a,b=self.controller(),self.controller();entered,release=threading.Event(),threading.Event();self.releases.append(release)
        def old_wallet(**kw):entered.set();release.wait(3);return 88888
        with patch.object(api,'get_wallet',side_effect=old_wallet):
            b.load(force=True);settle(entered.is_set)
            b.set_active(False)
            with patch.object(api,'send_gift',return_value=api.GiftResult('main:1',1,{})):
                a.send('main:1',1);settle(lambda:not self.coordinator.busy)
            release.set();settle(lambda:not _active_requests)
        self.assertIsNone(b.state.wallet)
        self.assertEqual(next(t for t in b.state.tabs if t.key=='bag').items,())
    def test_hidden_panel_reopens_with_refreshed_bag(self):
        a,b=self.controller(),self.controller()
        b.set_active(False)
        with patch.object(api,'send_gift',return_value=api.GiftResult('main:1',1,{})):
            a.send('main:1',1);settle(lambda:not self.coordinator.busy)
            settle(lambda:not _active_requests)
        bag=api.normalize_bag(bag_payload(),self.catalog,'90001',now=100)
        with patch.object(api,'get_bag',return_value=bag):
            b.load();settle(lambda:not b.state.loading)
        refreshed=next(t for t in b.state.tabs if t.key=='bag')
        self.assertEqual([(i.bag_id,i.stock) for i in refreshed.items],[(701,10),(702,2)])

    def test_old_account_completion_preserves_new_account_reads(self):
        a=self.controller();entered,release,send=self.barrier()
        wallet_entered,bag_entered,read_release=threading.Event(),threading.Event(),threading.Event()
        self.releases.append(read_release)
        bag=api.normalize_bag(bag_payload(),self.catalog,'90001',now=100)
        def wallet(**kw):wallet_entered.set();read_release.wait(3);return 4321
        def inventory(*args,**kw):bag_entered.set();read_release.wait(3);return bag
        with patch.object(api,'send_gift',side_effect=send):
            a.send('main:1',1);settle(entered.is_set)
            session.set_login_cookies('new','csrf-new')
            a.set_context('90001',session.snapshot())
            with patch.object(api,'get_wallet',side_effect=wallet),patch.object(api,'get_bag',side_effect=inventory):
                a.load();settle(lambda:wallet_entered.is_set() and bag_entered.is_set())
                release.set();settle(lambda:not self.coordinator.busy)
                read_release.set();settle(lambda:not _active_requests)
        self.assertEqual(a.state.loading,frozenset())
        self.assertEqual(a.state.wallet,4321)
        self.assertEqual([(i.bag_id,i.stock) for t in a.state.tabs if t.key=='bag' for i in t.items],[(701,10),(702,2)])

    def test_success_then_refresh_failure_stays_success(self):
        a=self.controller();sent=[];failed=[];a.sent.connect(sent.append);a.failed.connect(lambda *x:failed.append(x))
        with patch.object(api,'send_gift',return_value=api.GiftResult('main:1',1,{})),patch.object(api,'get_wallet',side_effect=ValueError('refresh failed')):
            a.send('main:1',1);settle(lambda:not self.coordinator.busy and not a.state.loading)
        self.assertEqual(len(sent),1);self.assertEqual(failed,[])
        self.assertIn('成功',a.state.feedback);self.assertIn('refresh failed',a.state.wallet_error)

if __name__=='__main__':unittest.main()
