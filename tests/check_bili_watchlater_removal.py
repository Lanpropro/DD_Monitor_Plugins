"""Removal feedback and real re-add semantics, using only offline API substitutes."""
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from check_bili_watchlater import APP,models,http_api
import check_bili_watchlater_page_controls as controls
from ddm_plugin_bili_watchlater import page as pages,removal_undo,workers,undo_notice
from ddm_plugin_bili_watchlater.watch_order import WatchOrder
from PySide6.QtCore import QObject,Qt,Signal
from PySide6.QtTest import QTest


class Request(QObject):
    completed=Signal(int,int,object)
    failed=Signal(int,int,object)
    def __init__(self,cookies,generation,request_id,aid):
        super().__init__()
        self.cookies=dict(cookies)
        self.generation,self.request_id,self.aid=generation,request_id,aid
    def cancel(self): pass
    def success(self): self.completed.emit(self.request_id,self.generation,{})
    def failure(self,error): self.failed.emit(self.request_id,self.generation,error)


class RemovalTests(unittest.TestCase):
    def setUp(self):
        controls.PageControlsTests.setUp(self)
        self.items=[models.WatchLaterItem(aid=i+1,bvid=f'fixture{i}',title=f'视频{i}') for i in range(4)]
        self.page._items=list(self.items)
        self.page._has_session=self.page._has_csrf=True
        self.page._cookies={'SESSDATA':'fixture','bili_jct':'fixture-csrf'}
        self.page._generation=3
        self.sent=[]
        self.patches=[patch.object(pages,'RemoveRequest',Request),
            patch.object(removal_undo,'AddRequest',Request),
            patch.object(self.page,'_track',side_effect=self.sent.append),
            patch.object(self.page,'_render_after_remove'),patch.object(self.page,'_render')]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda:controls.PageControlsTests.tearDown(self))

    def remove(self,item):
        self.page._confirm_remove(item)
        worker=self.sent[-1]
        worker.success()
        return worker

    @contextmanager
    def shown(self):
        with patch.object(self.page,'_load'),patch.object(self.page,'_cache_fresh',return_value=True), \
                patch.object(self.page,'_apply_saved_geometry',return_value=True),patch.object(self.page,'_start_entrance'):
            self.page.resize(980,700)
            self.page.show()
            APP.processEvents()
            yield

    def test_no_confirmation_success_shows_one_in_window_undo_action(self):
        with self.shown(),patch.object(undo_notice.motion,'enabled',return_value=False):
            self.page._confirm_remove(self.items[1])
            self.assertIsNone(self.page._inline_panel)
            self.assertTrue(self.page.removal_undo.notice.isHidden())
            self.assertIn(self.items[1],self.page._items)
            self.sent[-1].success()
            notice=self.page.removal_undo.notice
            self.assertTrue(notice.isVisible())
            self.assertFalse(notice.isWindow())
            self.assertTrue(notice.undo.isEnabled())
            self.assertIn('已移除 1',notice.message.text())
            self.assertTrue(self.page.rect().contains(notice.geometry()))
            QTest.mouseClick(notice.undo,Qt.LeftButton)
            self.assertEqual(self.sent[-1].aid,self.items[1].aid)
            self.assertEqual(self.page._busy_operation,'restore')

    def test_undo_waits_for_server_success_then_restores_original_canonical_position(self):
        self.remove(self.items[1])
        undo=self.page.removal_undo
        undo.undo()
        self.assertNotIn(self.items[1],self.page._items)
        self.sent[-1].success()
        self.assertEqual(self.page._items,self.items)
        self.assertFalse(undo.entries)
        self.assertEqual(undo.notice.message.text(),'已撤回移除')
        self.assertFalse(self.page._busy_operation)

    def test_repeated_removals_and_batch_undo_restore_all_once_and_keep_order(self):
        self.remove(self.items[1])
        self.remove(self.items[2])
        undo=self.page.removal_undo
        undo.undo()
        count=len(self.sent)
        undo.undo()
        self.assertEqual(len(self.sent),count)
        self.sent[-1].success()
        self.assertEqual(self.page._busy_operation,'restore')
        APP.processEvents()
        self.assertEqual(self.sent[-1].aid,self.items[2].aid)
        self.sent[-1].success()
        self.assertEqual(self.page._items,self.items)

    def test_partial_multiselect_failure_can_undo_only_successful_deletions(self):
        self.page._multi_remove_queue=[item.key for item in self.items[:2]]
        self.page._pump_multi_remove()
        self.sent[-1].success()
        self.assertFalse(self.page.removal_undo.notice.undo.isEnabled())
        APP.processEvents()
        self.sent[-1].failure(http_api.BiliAPIError('fixture failure'))
        self.assertFalse(self.page._multi_remove_queue)
        self.assertTrue(self.page.removal_undo.notice.undo.isEnabled())
        self.assertEqual(list(self.page.removal_undo.entries),[self.items[0].key])
        self.page.removal_undo.undo()
        self.sent[-1].success()
        self.assertEqual(self.page._items,self.items)

    def test_failed_delete_keeps_item_and_has_no_success_notice(self):
        self.page._confirm_remove(self.items[0])
        self.sent[-1].failure(http_api.BiliAPIError('fixture failure'))
        self.assertEqual(self.page._items,self.items)
        self.assertFalse(self.page.removal_undo.entries)
        self.assertTrue(self.page.removal_undo.notice.isHidden())

    def test_failed_restore_keeps_undo_and_explicit_retry_has_one_new_post(self):
        self.remove(self.items[0])
        undo=self.page.removal_undo
        undo.undo()
        count=len(self.sent)
        self.sent[-1].failure(http_api.BiliAPIError('fixture failure'))
        APP.processEvents()
        self.assertEqual(len(self.sent),count)
        self.assertNotIn(self.items[0],self.page._items)
        self.assertTrue(undo.notice.undo.isEnabled())
        undo.undo()
        self.assertEqual(len(self.sent),count+1)
        self.sent[-1].success()
        self.assertEqual(self.page._items,self.items)

    def test_account_switch_and_shutdown_reject_late_restore(self):
        for shutdown in (False,True):
            self.remove(self.items[0])
            undo=self.page.removal_undo
            undo.undo()
            worker=self.sent[-1]
            if shutdown:
                self.page.shutdown()
            else:
                self.page.on_account_changed()
            worker.success()
            self.assertFalse(undo.entries)
            self.assertNotIn(self.items[0],self.page._items)
            if not shutdown:
                self.page._has_session=self.page._has_csrf=True
                self.page._items=list(self.items)

    def test_timeout_never_sends_network_and_hover_or_keyboard_focus_pauses_expiry(self):
        with self.shown():
            self.remove(self.items[0])
            undo=self.page.removal_undo
            count=len(self.sent)
            with patch.object(undo.notice,'underMouse',return_value=True):
                undo.expire()
                self.assertTrue(undo.entries)
            with patch.object(undo.notice,'underMouse',return_value=False), \
                    patch.object(removal_undo.QApplication,'focusWidget',return_value=undo.notice.undo):
                undo.expire()
                self.assertTrue(undo.entries)
            with patch.object(undo.notice,'underMouse',return_value=False), \
                    patch.object(removal_undo.QApplication,'focusWidget',return_value=None):
                undo.expire()
            self.assertFalse(undo.entries)
            self.assertEqual(len(self.sent),count)

    def test_saved_playback_order_is_restored_after_readd(self):
        order=WatchOrder(self.context)
        order.update(self.items,'fixture')
        order.move(self.items[1].bvid,self.items[0].bvid)
        keys=list(order.keys)
        self.page.items_changed.connect(lambda items:order.update(items,'fixture'))
        self.page.removal_undo.snapshot_order=lambda:list(order.keys)
        self.page.removal_undo.restore_order=order.restore
        self.remove(self.items[1])
        self.assertNotIn(self.items[1].bvid,order.keys)
        self.page.removal_undo.undo()
        self.sent[-1].success()
        self.assertEqual(order.keys,keys)

    def test_notice_identifies_video_and_receives_uploader_avatar_from_shared_cache(self):
        from PySide6.QtGui import QPixmap
        from ddm_plugin_bili_watchlater.card_presentation import avatar_thumbnail
        self.items[0].owner_name='测试UP'
        self.items[0].owner_face='https://i0.hdslb.com/bfs/face/fixture.jpg'
        with self.shown(),patch.object(self.page.covers,'get',return_value=None):
            self.remove(self.items[0])
            notice=self.page.removal_undo.notice
            self.assertEqual(notice.title.full_text(),self.items[0].title)
            self.assertEqual(notice.pile.avatars[0].accessibleName(),'测试UP的头像')
            pixmap=QPixmap(32,32)
            pixmap.fill(Qt.red)
            url=avatar_thumbnail(self.items[0].owner_face)
            self.page.covers._cache[url]=pixmap
            self.page.covers.image_ready.emit(url)
            self.assertIs(notice.pile.avatars[0]._pixmap,pixmap)

    def test_batch_avatars_overlap_and_expansion_grows_upward_using_configured_rows(self):
        many=[models.WatchLaterItem(aid=i+1,bvid=f'batch{i}',title=f'已移除标题{i}',owner_name=f'UP{i}') for i in range(12)]
        with self.shown(),patch.object(undo_notice.motion,'enabled',return_value=False):
            self.page.resize(980,1000)
            notice=self.page.removal_undo.notice
            self.page.preferences['undo_expand_rows']=8
            notice.set_items(many)
            notice.present('已移除 12 个视频',enabled=True)
            APP.processEvents()
            self.assertEqual(notice.undo.text(),'全部撤回')
            self.assertEqual(len(notice.pile.items),3)
            self.assertLess(notice.pile.avatars[1].x(),notice.pile.avatars[0].width())
            bottom=notice.geometry().bottom()
            notice.toggle_details()
            APP.processEvents()
            self.assertEqual(notice.geometry().bottom(),bottom)
            self.assertEqual(notice.details.height(),8*notice.details.row_height())
            self.assertEqual(notice.details.model().rowCount(),12)
            self.assertGreater(notice.details.verticalScrollBar().maximum(),0)
            self.page.resize(980,480)
            APP.processEvents()
            self.assertLess(notice.details.height(),8*notice.details.row_height())
            self.assertGreaterEqual(notice.details.height(),notice.details.row_height())
            self.page.resize(980,1000)
            APP.processEvents()
            self.assertEqual(notice.details.height(),8*notice.details.row_height())
            notice.toggle_details()
            self.assertEqual(notice.details.height(),0)
            self.assertEqual(notice.geometry().bottom(),bottom)
            notice.dismiss(animated=False)
            self.assertFalse(notice.items)
            self.assertEqual(notice.details.model().rowCount(),0)

    def test_expand_row_setting_autosaves_and_invalid_values_use_default(self):
        from ddm_plugin_bili_watchlater.preferences import read_preferences
        self.page._open_settings()
        control=self.page._settings_dialog.undo_expand_rows
        control.setCurrentIndex(control.findData(7))
        self.assertEqual(self.values['undo_expand_rows'],7)
        self.assertEqual(self.page.preferences['undo_expand_rows'],7)
        for invalid in (True,0,11,'4',None):
            self.values['undo_expand_rows']=invalid
            self.assertEqual(read_preferences(self.context)['undo_expand_rows'],4)

    def test_expand_animation_can_reverse_without_moving_bottom_anchor(self):
        with self.shown(),patch.object(undo_notice.motion,'enabled',return_value=True):
            notice=self.page.removal_undo.notice
            notice.set_items(self.items)
            notice.present('已移除 4 个视频',enabled=True)
            notice.animation.setCurrentTime(notice.animation.duration())
            bottom=notice.geometry().bottom()
            notice.toggle_details()
            notice.details_animation.setCurrentTime(60)
            current=notice.detail_height
            self.assertGreater(current,0)
            notice.toggle_details()
            self.assertEqual(notice.details_animation.startValue(),current)
            notice.details_animation.setCurrentTime(notice.details_animation.duration())
            self.assertEqual(notice.detail_height,0)
            self.assertEqual(notice.geometry().bottom(),bottom)

    def test_add_protocol_posts_once_with_csrf_and_does_not_follow_redirects(self):
        session=Mock()
        session.post.return_value=SimpleNamespace(status_code=200,json=lambda:{'code':0,'data':{}})
        api=http_api.BiliWatchLaterAPI(session)
        api.add(self.page._cookies,123)
        session.post.assert_called_once()
        call=session.post.call_args
        self.assertEqual(call.args[0],http_api.ADD_ENDPOINT)
        self.assertEqual(call.kwargs['data'],{'aid':'123','csrf':'fixture-csrf'})
        self.assertFalse(call.kwargs['allow_redirects'])
        session.post.reset_mock()
        session.post.return_value=SimpleNamespace(status_code=412)
        with self.assertRaises(http_api.BiliAPIError): api.add(self.page._cookies,123)
        session.post.assert_called_once()

    def test_add_worker_clears_cookie_snapshot_on_success_failure_and_cancel(self):
        for fail,cancel in ((False,False),(True,False),(False,True)):
            worker=workers.AddRequest(self.page._cookies,3,1,123)
            done,failed=Mock(),Mock()
            worker.completed.connect(done)
            worker.failed.connect(failed)
            if cancel: worker.cancel()
            with patch.object(workers,'BiliWatchLaterAPI') as api:
                if fail: api.return_value.add.side_effect=http_api.BiliAPIError('fixture failure')
                worker.run()
                api.return_value.close.assert_called_once()
                self.assertFalse(worker.cookies)
                self.assertEqual(done.call_count,int(not fail and not cancel))
                self.assertEqual(failed.call_count,int(fail))
            worker.deleteLater()


if __name__=='__main__': unittest.main()
