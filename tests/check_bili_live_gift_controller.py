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
            self.assertEqual((a.state.wallet,b.state.wallet),(99999,99999))
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
        b.set_active(False)
        with patch.object(api,'send_gift',side_effect=send) as post:
            a.send('main:1',1);settle(entered.is_set);a.shutdown();a.deleteLater()
            APP.sendPostedEvents(None,QEvent.DeferredDelete)
            b.send('main:1',1);self.assertEqual(post.call_count,1);self.assertTrue(self.coordinator.busy)
            release.set();settle(lambda:not self.coordinator.busy)
        self.assertEqual(b.state.wallet,99894)
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
        self.assertEqual(b.state.wallet,99894)
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
        self.assertEqual(a.state.feedback,'');self.assertIn('refresh failed',a.state.wallet_error)
        self.assertEqual(a.state.wallet,99894)

    def test_success_ok_payload_refreshes_wallet_without_feedback(self):
        from unittest.mock import Mock
        a=self.controller();failed=[];sent=[]
        a.failed.connect(lambda *args:failed.append(args));a.sent.connect(sent.append)
        with patch.object(api.requests,'post',return_value=Mock(status_code=200,
                json=lambda:{'code':0,'message':'ok','data':{}})),\
                patch.object(api,'get_wallet',return_value=99894):
            a.send('main:1',1)
            settle(lambda:not self.coordinator.busy and not a.state.loading)
        self.assertEqual(failed,[])
        self.assertEqual(len(sent),1)
        self.assertEqual(a.state.wallet,99894)
        self.assertEqual(a.state.feedback,'')

    def test_manual_refresh_retains_balance_until_authoritative_value_arrives(self):
        a=self.controller();entered,release=threading.Event(),threading.Event();self.releases.append(release)
        def wallet(**kw):entered.set();release.wait(3);return 76543
        with patch.object(api,'get_wallet',side_effect=wallet):
            a.load(force=True);settle(entered.is_set)
            self.assertEqual(a.state.wallet,99999)
            release.set();settle(lambda:not a.state.loading)
        self.assertEqual(a.state.wallet,76543)

    def test_confirmed_quantity_debits_all_tiles_then_reconciles(self):
        a,b=self.controller(),self.controller()
        b.set_context('90002',session.snapshot());b.load();settle(lambda:not b.state.loading)
        entered,release=threading.Event(),threading.Event();self.releases.append(release)
        def wallet(**kw):entered.set();release.wait(3);return 95000
        states=[];a.stateChanged.connect(states.append)
        with patch.object(api,'send_gift',return_value=api.GiftResult('main:1',10,{})),patch.object(api,'get_wallet',side_effect=wallet):
            a.send('main:1',10);settle(lambda:not self.coordinator.busy and entered.is_set())
            self.assertEqual(a.state.wallet,98949)
            self.assertEqual(b.state.wallet,98949)
            self.assertEqual(a.state.feedback,'')
            self.assertTrue(all(s.wallet is not None for s in states))
            release.set();settle(lambda:not _active_requests)
        self.assertEqual((a.state.wallet,b.state.wallet),(95000,95000))

    def test_consecutive_successes_accumulate_and_ignore_older_refresh(self):
        a=self.controller();entered,release=threading.Event(),threading.Event();self.releases.append(release)
        calls=[]
        def wallet(**kw):
            calls.append(True)
            if len(calls)==1:entered.set();release.wait(3);return 99894
            return 99000
        def send(target,item,count,**kw):return api.GiftResult(item.key,count,{})
        with patch.object(api,'send_gift',side_effect=send),patch.object(api,'get_wallet',side_effect=wallet):
            a.send('main:1',1);settle(lambda:not self.coordinator.busy and entered.is_set())
            self.assertEqual(a.state.wallet,99894)
            a.set_active(False)
            a.send('main:1',10);settle(lambda:not self.coordinator.busy)
            self.assertEqual(a.state.wallet,98844)
            release.set();settle(lambda:not _active_requests)
            self.assertEqual(a.state.wallet,98844,'older authoritative response replaced the second debit')
            a.load();settle(lambda:not a.state.loading)
        self.assertEqual(a.state.wallet,99000)

    def test_bag_success_does_not_debit_batteries(self):
        a=self.controller()
        bag=api.normalize_bag(bag_payload(),self.catalog,'90001',now=100)
        # Do not expire this synthetic fixture against the current wall clock.
        item=replace(bag.items[0],expires_at=0)
        a._update(tabs=tuple(t for t in a.state.tabs if t.key!='bag')+(replace(bag,items=(item,)),))
        a.set_active(False)
        with patch.object(api,'send_gift',return_value=api.GiftResult(item.key,2,{})):
            a.send(item.key,2);settle(lambda:not self.coordinator.busy)
        self.assertEqual(a.state.wallet,99999)
        self.assertEqual(next(t for t in a.state.tabs if t.key=='bag').items,())

    def test_uncertain_send_retains_display_but_requires_balance_refresh(self):
        a=self.controller();a.set_active(False)
        with patch.object(api,'send_gift',side_effect=api.GiftSendError('unconfirmed',uncertain=True)) as post:
            a.send('main:1',1);settle(lambda:not self.coordinator.busy)
            self.assertEqual(a.state.wallet,99999)
            self.assertFalse(a.state.wallet_valid)
            a.send('main:1',1);APP.processEvents()
            self.assertEqual(post.call_count,1)
        with patch.object(api,'get_wallet',return_value=98765) as read:
            a.load();settle(lambda:not a.state.loading)
            read.assert_called_once()
        self.assertEqual(a.state.wallet,98765);self.assertTrue(a.state.wallet_valid)

    def test_rejected_send_does_not_debit_balance(self):
        a=self.controller()
        with patch.object(api,'send_gift',side_effect=api.GiftSendError('rejected')):
            a.send('main:1',1);settle(lambda:not self.coordinator.busy)
        self.assertEqual(a.state.wallet,99999)

if __name__=='__main__':unittest.main()
