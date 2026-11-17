"""State and ownership contracts across the extracted plugin components."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from check_bili_watchlater import APP,models,plugin_module
import check_bili_watchlater_page_controls as controls
from ddm_plugin_bili_watchlater import list_controller
from PySide6.QtCore import QObject,Signal


class PendingList(QObject):
    completed=Signal(int,int,object)
    failed=Signal(int,int,object)
    def __init__(self,cookies,generation,request_id):
        super().__init__()
        self.cookies=dict(cookies)
        self.generation,self.request_id=generation,request_id
        self.cancelled=False
    def cancel(self): self.cancelled=True


class BoundaryTests(unittest.TestCase):
    setUp=controls.PageControlsTests.setUp
    tearDown=controls.PageControlsTests.tearDown

    def test_legacy_page_state_forwards_to_one_controller_and_qt_owner(self):
        controller=self.page.list_controller
        self.assertIs(controller.parent(),self.page)
        items=[self.item]
        self.page._items=items
        self.assertIs(controller.items,items)
        controller.cookies={'fixture':'value'}
        self.assertIs(self.page._cookies,controller.cookies)
        self.assertNotIn('_items',self.page.__dict__)
        self.assertNotIn('_cookies',self.page.__dict__)
        self.assertIs(self.page.removal_undo.controller,controller)

    def test_account_change_cancels_single_flight_list_and_rejects_late_result(self):
        snapshot=SimpleNamespace(generation=4,values={'SESSDATA':'fixture','bili_jct':'fixture-csrf'})
        self.context.credentials=lambda _:snapshot
        controller=self.page.list_controller
        sent=[]
        with patch.object(list_controller,'ListRequest',PendingList),patch.object(controller,'track',side_effect=sent.append):
            self.page._load(manual=True)
            self.page._load(manual=True)
            self.assertEqual(len(sent),1)
            old=sent[0]
            snapshot.generation=5
            self.page.on_account_changed()
            self.assertTrue(old.cancelled)
            old.completed.emit(old.request_id,old.generation,models.WatchLaterSnapshot(items=[self.item],total=1))
            self.assertEqual(controller.items,[])
            self.assertEqual(controller.generation,5)

    def test_host_adapter_reads_current_context_and_preserves_legacy_delegates(self):
        plugin=plugin_module.BiliWatchLaterPlugin()
        self.assertIsNone(plugin._host_window())
        self.assertFalse(plugin.host.supports_playback())
        tile=object()
        player=object()
        first=SimpleNamespace(players={tile:player},wall=SimpleNamespace(tiles=[tile]),_play_on=Mock())
        plugin.context=SimpleNamespace(window=first)
        self.assertIs(plugin._host_window(),first)
        self.assertIs(plugin._host_player(tile),player)
        self.assertEqual(plugin._host_tiles(),[tile])
        self.assertTrue(plugin.host.supports_playback())
        plugin.context=SimpleNamespace(window=SimpleNamespace())
        self.assertEqual(plugin._host_tiles(),[])
        self.assertIsNone(plugin._host_player(tile))
        self.assertFalse(plugin._play_on_host(tile,'fixture',{}))


if __name__=='__main__': unittest.main()
