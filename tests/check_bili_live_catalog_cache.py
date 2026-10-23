"""Catalog persistence is display-only until current account permissions arrive."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from bili_live_support import session,bili
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication,QToolButton
from PySide6.QtTest import QTest
from ddm_plugin_bili_live.composer import DanmakuComposer

APP=QApplication.instance() or QApplication([])
PACKAGES=[{'pkg_type':2,'pkg_id':7,'pkg_name':'主播表情','emoticons':[
    {'emoji':'好','emoticon_unique':'room_101_7','perm':1}]}]

def settle(predicate):
    end=time.monotonic()+3
    while time.monotonic()<end:
        APP.processEvents()
        if predicate():return
        QTest.qWait(5)
    raise AssertionError('catalog did not settle')

class CatalogCacheTests(unittest.TestCase):
    def setUp(self):
        from ddm_plugin_bili_live.catalog_cache import CatalogCache
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cache=CatalogCache(self.root)
        self.cookies={'SESSDATA':'secret-session','bili_jct':'secret-csrf'}
        self.widgets=[];self.releases=[]
    def tearDown(self):
        for event in self.releases:event.set()
        for widget in self.widgets:widget.shutdown();widget.deleteLater()
        from ddm_plugin_bili_live.workers import _active_requests
        settle(lambda:not _active_requests)
        APP.sendPostedEvents(None,QEvent.DeferredDelete);APP.processEvents();self.temp.cleanup()
    def composer(self):
        session.set_login_cookies('secret-session','secret-csrf','device-test')
        widget=DanmakuComposer(session,catalog_cache=self.cache);self.widgets.append(widget)
        widget.set_room('101');widget.show();return widget
    def test_normal_checks_never_read_real_storage_or_persist_unexpected_fields(self):
        from ddm_plugin_bili_live.catalog_cache import CatalogCache
        with patch('ddm_plugin_bili_live.catalog_cache.os.environ',{'DDM_NO_SAVE':'1'}):
            disabled=CatalogCache()
            with patch.object(Path,'stat',side_effect=AssertionError('real cache accessed')):
                self.assertIsNone(disabled.load('101',self.cookies))
                disabled.save('101',self.cookies,PACKAGES)
        leaked=[dict(PACKAGES[0],SESSDATA='secret-session',bili_jct='secret-csrf')]
        self.cache.save('101',self.cookies,leaked)
        self.assertEqual(self.cache.load('101',self.cookies),PACKAGES)
    def test_catalog_cache_is_bounded_and_refresh_keeps_the_picker_visible(self):
        for room in range(40):self.cache.save(str(room),self.cookies,PACKAGES)
        self.assertLessEqual(len(list(self.root.glob('*.json'))),32)
        self.assertEqual(self.cache.load('39',self.cookies),PACKAGES)
        widget=self.composer()
        with patch.object(bili,'get_emoticons',return_value=PACKAGES):
            widget.open_emoticons();settle(lambda:widget._catalog_pending is None)
            self.assertTrue(widget.popup.isVisible())
            widget.popup.retry_button.click();settle(lambda:widget._catalog_pending is None)
            self.assertTrue(widget.popup.isVisible())

    def test_disk_roundtrip_is_account_room_scoped_and_contains_no_credentials(self):
        self.cache.save('101',self.cookies,PACKAGES)
        result=type(self.cache)(self.root).load('101',self.cookies)
        self.assertEqual(result,PACKAGES)
        result[0]['emoticons'][0]['perm']=0
        self.assertEqual(self.cache.load('101',self.cookies),PACKAGES)
        self.assertIsNone(self.cache.load('202',self.cookies))
        self.assertIsNone(self.cache.load('101',dict(self.cookies,SESSDATA='other')))
        self.assertTrue(list(self.root.glob('*.json')))
        for path in self.root.glob('*.json'):
            raw=path.read_text(encoding='utf-8')
            self.assertNotIn('secret-session',raw);self.assertNotIn('secret-csrf',raw)
    def test_corrupt_oversized_expired_or_malformed_snapshots_are_ignored(self):
        self.cache.save('101',self.cookies,PACKAGES)
        path=next(self.root.glob('*.json'));original=path.read_text(encoding='utf-8')
        for data in ('{',json.dumps({'version':99,'packages':PACKAGES}),
                     json.dumps({'version':1,'saved_at':0,'packages':PACKAGES}),
                     json.dumps({'version':1,'saved_at':time.time(),'packages':[{'emoticons':[1]}]}),
                     ' '* (4*1024*1024+1)):
            with self.subTest(size=len(data)):
                path.write_text(data,encoding='utf-8');self.assertIsNone(self.cache.load('101',self.cookies))
        path.write_text(original,encoding='utf-8');self.assertEqual(self.cache.load('101',self.cookies),PACKAGES)
    def test_unwritable_cache_does_not_turn_successful_fetch_into_failure(self):
        with patch('ddm_plugin_bili_live.catalog_cache.os.replace',side_effect=OSError('readonly')):
            self.cache.save('101',self.cookies,PACKAGES)
        self.assertIsNone(self.cache.load('101',self.cookies))
        self.assertEqual(list(self.root.glob('*.tmp')),[])
    def test_restart_shows_disk_catalog_before_network_but_never_sends_stale_permissions(self):
        self.cache.save('101',self.cookies,PACKAGES)
        widget=self.composer();entered,release=threading.Event(),threading.Event();self.releases.append(release)
        def fresh(*args,**kwargs):
            entered.set();release.wait(3)
            return [dict(PACKAGES[0],emoticons=[dict(PACKAGES[0]['emoticons'][0],perm=0)])]
        with patch.object(bili,'get_emoticons',side_effect=fresh),patch.object(bili,'send_danmaku') as post:
            widget.open_emoticons();settle(entered.is_set)
            settle(lambda:bool(widget.popup.scroll.findChildren(QToolButton)))
            button=widget.popup.scroll.findChildren(QToolButton)[0]
            self.assertEqual(button.text(),'好');self.assertFalse(button.isEnabled())
            widget._choose_cached_emoticon(2,PACKAGES[0]['emoticons'][0]);post.assert_not_called()
            release.set();settle(lambda:widget._catalog_pending is None)
            settle(lambda:bool(widget.popup.scroll.findChildren(QToolButton)))
            self.assertFalse(widget.popup.scroll.findChildren(QToolButton)[0].isEnabled())
        self.assertEqual(self.cache.load('101',self.cookies)[0]['emoticons'][0]['perm'],0)
    def test_fresh_catalog_survives_composer_restart_and_failed_refresh(self):
        first=self.composer()
        with patch.object(bili,'get_emoticons',return_value=PACKAGES):
            first.open_emoticons();settle(lambda:first._catalog_pending is None)
        first.shutdown()
        second=self.composer()
        with patch.object(bili,'get_emoticons',side_effect=ValueError('offline')):
            second.open_emoticons();settle(lambda:second._catalog_pending is None)
            settle(lambda:bool(second.popup.scroll.findChildren(QToolButton)))
        self.assertIn('offline',second.popup.status.text())
        self.assertFalse(second.popup.scroll.findChildren(QToolButton)[0].isEnabled())
        self.assertEqual(self.cache.load('101',self.cookies),PACKAGES)
    def test_stale_account_result_cannot_persist_or_show_other_accounts_catalog(self):
        widget=self.composer();entered,release=threading.Event(),threading.Event();self.releases.append(release)
        def fresh(*args,**kwargs):entered.set();release.wait(3);return PACKAGES
        with patch.object(bili,'get_emoticons',side_effect=fresh):
            widget.open_emoticons();settle(entered.is_set)
            session.set_login_cookies('other','other-csrf');widget.sync_credentials()
            release.set()
            from ddm_plugin_bili_live.workers import _active_requests
            settle(lambda:not _active_requests)
        self.assertEqual(list(self.root.glob('*.json')),[])
        self.assertEqual(widget.popup.tabs.count(),0)

if __name__=='__main__':unittest.main()
