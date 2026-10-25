"""Public hook compatibility, device publication and pre-submit cancellation."""
import threading
import time
import warnings
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from bili_live_support import ROOT, bili, manager, entry, module_name
from PySide6.QtCore import QCoreApplication, Qt
from ddm.plugins import PluginManager, PluginContext
from ddm_plugin_bili_live.session import BiliSession
from ddm_plugin_bili_live.workers import Request, start_worker, _active_requests

APP = QCoreApplication.instance() or QCoreApplication([])

def settle(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('request did not finish')

class LifecycleTests(unittest.TestCase):
    def test_plugin_relative_imports_do_not_emit_package_warnings(self):
        host=PluginManager()
        plugin=host._load_module('package_warning_test',str(ROOT/'plugins/bili_live/plugin.py'))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always',DeprecationWarning)
            plugin.on_load(PluginContext(host,'package_warning_test'))
            plugin.on_unload()
        self.assertFalse([w for w in caught if '__package__' in str(w.message)],caught)

    def test_worker_is_retained_until_native_thread_cleanup_finishes(self):
        release=threading.Event();cleanup_done=threading.Event()
        worker=Request('cleanup',lambda c:release.wait(2),cookies={},account_generation=0)
        # Keep deletion observable while a direct finished handler models Qt's
        # native thread-local teardown after run() has returned.
        worker.deleteLater=lambda:None
        def native_cleanup():
            time.sleep(.1);cleanup_done.set()
        start_worker(worker)
        worker.finished.connect(native_cleanup,Qt.DirectConnection)
        release.set()
        try:
            settle(lambda:worker not in _active_requests)
            self.assertTrue(cleanup_done.is_set(),'finished alone does not join native thread-local cleanup')
        finally:
            worker.wait();QCoreApplication.processEvents()

    def test_older_host_rejected_before_registration(self):
        register = Mock()
        with self.assertRaisesRegex(RuntimeError, '配套构建'):
            entry.on_load(SimpleNamespace(register_tile_control=register))
        register.assert_not_called()

    def test_device_publication_is_gui_only_and_generation_checked(self):
        values = {'SESSDATA':'old', 'bili_jct':'csrf'}
        host = PluginManager()
        threads = []
        host.register_credentials('bilibili', lambda:values,
            update=lambda changes:(threads.append(threading.get_ident()), values.update(changes)),
            identity_keys=('SESSDATA','bili_jct'), writable_fields=('buvid3',))
        session = BiliSession(PluginContext(host,'test'))
        before = session.snapshot()
        got = []
        worker = Request('device', lambda c:got.append(c), cookies=before.values,
                         account_generation=before.generation, require_device=True)
        worker.deviceReady.connect(session.publish_device)
        with patch.object(bili,'ensure_buvid3',return_value='fake-device'):
            start_worker(worker)
            settle(lambda:bool(got) and not _active_requests)
        self.assertEqual(threads,[threading.get_ident()])
        self.assertEqual(values['buvid3'],'fake-device')
        self.assertEqual(session.snapshot().generation,before.generation)
        values.update(SESSDATA='new',buvid3='login-device')
        host.notify_credentials('bilibili')
        session.publish_device(before.generation,'late-device')
        self.assertEqual(values['buvid3'],'login-device')

    def test_room_or_account_cancel_during_resolve_never_posts(self):
        entered, release = threading.Event(), threading.Event()
        def resolve(*a, **kw):
            entered.set()
            release.wait(2)
            return '90001',456
        worker = Request('send',lambda c:bili.send_danmaku('1','test',cookies=c,still_current=worker.still_current),
            cookies={'SESSDATA':'fake','bili_jct':'fake','buvid3':'fake'},account_generation=1)
        with patch.object(bili,'resolve_room',side_effect=resolve),patch.object(bili.requests,'post') as post:
            start_worker(worker)
            settle(entered.is_set)
            worker.cancel()
            release.set()
            settle(lambda:not _active_requests)
        post.assert_not_called()

if __name__ == '__main__':
    unittest.main()
