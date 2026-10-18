"""Public hook compatibility, device publication and pre-submit cancellation."""
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from bili_live_support import bili, manager, entry, module_name
from PySide6.QtCore import QCoreApplication
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
