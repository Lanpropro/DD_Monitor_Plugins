"""Local queue, dual-purpose drag payload and unobstructed preview feedback."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from check_bili_watchlater import APP, models, plugin_module
from check_bili_watchlater_interactions import pages, TileFixture
from ddm_plugin_bili_watchlater.watch_order import WatchOrder
from ddm_plugin_bili_watchlater.order_sidebar import OrderSidebar, ORDER_MIME
from ddm_plugin_bili_watchlater import card_preview, widgets
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QDropEvent, QMouseEvent
from PySide6.QtTest import QTest


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.context = SimpleNamespace(credentials=lambda _: None,
            setting=lambda key, default: self.saved.get(key, default),
            set_setting=lambda key, value: self.saved.__setitem__(key,value))
        self.items = [models.WatchLaterItem(aid=i+1,bvid=f'video{i}',title=f'视频{i}',owner_name='UP',duration=60,cid=i+1) for i in range(4)]
        self.order = WatchOrder(self.context)
        self.order.update(self.items,'account-A')

    def keys(self):
        return [item.bvid for item in self.order.snapshot()]

    def test_drag_order_persists_locally_and_restores_after_refresh(self):
        self.assertTrue(self.order.move('video3','video0'))
        self.order.update(self.items,'account-A')
        self.assertEqual(self.keys(),['video3','video0','video1','video2'])
        other = WatchOrder(self.context)
        other.update(self.items,'account-A')
        self.assertEqual([item.bvid for item in other.snapshot()],self.keys())
        self.assertEqual(set(self.saved),{'watch_order'})

    def test_deletion_and_new_items_reconcile_without_bilibili_write(self):
        self.order.move('video3','video0')
        self.order.update(self.items[:2],'account-A')
        self.order.update(self.items,'account-A')
        self.assertEqual(self.keys(),['video0','video1','video2','video3'])

    def test_order_is_account_isolated_and_transient_empty_keeps_saved_keys(self):
        self.order.move('video3','video0')
        self.order.update([],'account-A')
        self.order.update(self.items,'account-A')
        self.assertEqual(self.keys()[0],'video3')
        self.order.update(self.items,'account-B')
        self.assertEqual(self.keys()[0],'video0')

    def test_self_drop_and_unknown_video_do_not_save(self):
        self.assertFalse(self.order.move('video0','video0'))
        self.assertFalse(self.order.move('unknown','video0'))
        self.assertFalse(self.saved)

    def test_sidebar_visibility_setting_registers_and_removes_without_loading_gallery(self):
        plugin=plugin_module.BiliWatchLaterPlugin()
        plugin.context=SimpleNamespace(register_sidebar_page=Mock(return_value=True),unregister_sidebar_page=Mock())
        plugin._order_container=None
        plugin.order_sidebar=None
        with patch.object(plugin,'_create_order_sidebar') as create:
            plugin._sync_order_sidebar(True)
            plugin.context.register_sidebar_page.assert_called_once()
            create.assert_not_called()
            container=plugin._order_container
            plugin._sync_order_sidebar(True)
            self.assertIs(plugin._order_container,container)
            plugin._sync_order_sidebar(False)
            plugin.context.unregister_sidebar_page.assert_called_once_with('watch_order')
            self.assertIsNone(plugin._order_container)
            APP.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_sidebar_visibility_switch_uses_existing_autosave_and_filter_keeps_order(self):
        from ddm_plugin_bili_watchlater.settings_dialog import WatchLaterSettings
        from ddm_plugin_bili_watchlater.preferences import read_preferences
        settings=WatchLaterSettings(read_preferences(self.context))
        self.addCleanup(settings.deleteLater)
        changed=Mock()
        settings.changed.connect(changed)
        settings.show_watch_order.setChecked(False)
        self.assertFalse(changed.call_args.args[0]['show_watch_order'])
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        panel.refresh()
        panel.set_filter('video2')
        self.assertTrue(panel.list.item(0).isHidden())
        self.assertFalse(panel.list.item(2).isHidden())
        self.assertEqual(self.keys(),['video0','video1','video2','video3'])

    def test_next_video_and_position_use_local_order_not_gallery_filter(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        plugin.page = SimpleNamespace(items_snapshot=lambda: self.items)
        plugin.watch_order = self.order
        self.order.move('video3','video0')
        self.assertEqual(plugin._next_item_for('video3').bvid,'video0')
        self.assertIsNone(plugin._next_item_for('video2'))
        self.assertEqual(plugin._list_position_text('video0'),'2/4')

    def test_quick_click_uses_single_vod_or_empty_but_ambiguous_opens_picker(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        occupied, empty = TileFixture({'room_id':'live'}), TileFixture({})
        plugin._playback_tiles = lambda: [occupied,empty]
        plugin._start_vod_for_tile = Mock()
        plugin._play_from_order(self.items[0])
        plugin._start_vod_for_tile.assert_called_with(empty,self.items[0])
        plugin._bindings={'a':SimpleNamespace(tile=occupied)}
        plugin._play_from_order(self.items[1])
        plugin._start_vod_for_tile.assert_called_with(occupied,self.items[1])
        plugin._bindings['b']=SimpleNamespace(tile=empty)
        plugin._show_page=Mock(return_value=True)
        plugin.page=SimpleNamespace(_open_placement_picker=Mock())
        plugin._play_from_order(self.items[2])
        plugin.page._open_placement_picker.assert_called_once_with(self.items[2])

    def page(self):
        page = pages.WatchLaterPage(self.context)
        page._geometry_settings=Mock()
        self.addCleanup(lambda: (page.shutdown(),page.deleteLater(),APP.sendPostedEvents(None,QEvent.DeferredDelete)))
        return page

    def test_queue_cards_render_click_and_produce_payload_accepted_by_real_tile_filter(self):
        page=self.page()
        play=Mock()
        panel=OrderSidebar(page,self.order,play)
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            self.assertEqual(panel.list.count(),4)
            self.assertFalse(panel.grab().isNull())
            panel.list.itemClicked.emit(panel.list.item(2))
            play.assert_not_called()
            panel.list.itemDoubleClicked.emit(panel.list.item(2))
            play.assert_called_once_with(self.items[2])
            mime=panel.list.drag_mime(self.items[2])
            self.assertTrue(mime.hasFormat(models.VOD_MIME))
            self.assertTrue(mime.hasFormat(ORDER_MIME))
            plugin=plugin_module.BiliWatchLaterPlugin()
            plugin._on_video_dropped=Mock()
            tile=TileFixture({})
            event=QDropEvent(QPointF(4,4),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
            self.assertTrue(plugin_module._TileEventFilter(plugin).eventFilter(tile,event))
            self.assertTrue(event.isAccepted())
            plugin._on_video_dropped.assert_called_once_with(tile,'video2')

    def test_native_and_custom_detail_tooltips_are_removed(self):
        page=self.page()
        page._rebuild_cards(self.items[:1])
        for card in (page._cards[0],page._make_row(self.items[0])):
            self.assertFalse(hasattr(card,'_title_tip'))
            self.assertEqual(card.toolTip(),'')
            self.assertEqual(card.cover.toolTip(),'')
            self.assertEqual(card.title_label.toolTip(),'')
            self.assertTrue(card.remove_button.toolTip())

    def test_real_mouse_single_selects_and_double_click_plays_once(self):
        page=self.page()
        play=Mock()
        panel=OrderSidebar(page,self.order,play)
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            point=view.visualItemRect(view.item(1)).center()
            self.assertTrue(view.viewport().hasMouseTracking())
            APP.sendEvent(view.viewport(),QMouseEvent(QEvent.MouseMove,QPointF(point),
                QPointF(view.viewport().mapToGlobal(point)),Qt.NoButton,Qt.NoButton,Qt.NoModifier))
            self.assertEqual(view.viewport().cursor().shape(),Qt.PointingHandCursor)
            QTest.mouseClick(view.viewport(),Qt.LeftButton,pos=point)
            play.assert_not_called()
            self.assertEqual(view.currentRow(),1)
            QTest.mouseDClick(view.viewport(),Qt.LeftButton,pos=point)
            QTest.mouseRelease(view.viewport(),Qt.LeftButton,pos=point)
            play.assert_called_once_with(self.items[1])

    def test_hover_press_interruption_reduced_motion_and_hide_keep_geometry(self):
        from ddm_plugin_bili_watchlater import order_feedback
        from PySide6.QtCore import QAbstractAnimation
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            state=view.feedback
            rect=view.visualItemRect(view.item(0))
            with patch.object(order_feedback.motion,'enabled',return_value=True):
                state.hover(0)
                state.animation.setCurrentTime(60)
                before=state.weights[0]
                state.hover(1)
                self.assertAlmostEqual(state.starts[0],before)
                state.animation.setCurrentTime(state.animation.duration())
                self.assertEqual(state.weights,{1:1.})
            self.assertEqual(view.viewport().cursor().shape(),Qt.PointingHandCursor)
            state.set_pressed(1)
            self.assertEqual(state.pressed,1)
            self.assertFalse(view.grab().isNull())
            self.assertEqual(view.visualItemRect(view.item(0)),rect)
            with patch.object(order_feedback.motion,'enabled',return_value=False):
                state.hover(0)
                self.assertEqual(state.weights,{0:1.})
                self.assertEqual(state.animation.state(),QAbstractAnimation.Stopped)
            panel.hide()
            state.sync_pointer()
            self.assertFalse(state.weights)
            self.assertEqual(state.pressed,-1)
            self.assertEqual(view.viewport().cursor().shape(),Qt.ArrowCursor)

    def test_image_delivery_updates_only_matching_visible_card_and_drag_marker_clears(self):
        page=self.page()
        for index,item in enumerate(self.items):
            item.cover_url=f'https://fixture.invalid/{index}.jpg'
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,300)
            panel.show()
            APP.processEvents()
            view=panel.list
            with patch.object(view.viewport(),'update') as update:
                view.cover_ready('https://fixture.invalid/not-ours.jpg')
                update.assert_not_called()
                view.cover_ready(self.items[0].cover_url)
                update.assert_called_once_with(view.visualItemRect(view.item(0)))
                update.reset_mock()
                view.cover_ready(self.items[-1].cover_url)
                update.assert_not_called()
            y=view.visualItemRect(view.item(0)).bottom()-2
            view.set_drop_y(y)
            self.assertFalse(view.grab().isNull())
            panel.hide()
            self.assertIsNone(view.drop_y)

    def test_drop_reorders_by_insertion_edge_and_rejects_external_source(self):
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            class Drop:
                def source(self): return view
                def mimeData(self): return view.drag_mime(self_item)
                def position(self): return QPointF(view.visualItemRect(view.item(0)).topLeft())
                def setDropAction(self,_): pass
                accept=Mock()
                ignore=Mock()
            self_item=self.items[3]
            drop=Drop()
            view.dropEvent(drop)
            self.assertEqual(self.keys()[0],'video3')
            drop.accept.assert_called_once()
            drop.source=lambda: None
            view.dropEvent(drop)
            drop.ignore.assert_called_once()
            self.assertFalse(view.drag_scroll.isActive())

    def test_visible_sidebar_keeps_cover_requests_alive_when_gallery_hides(self):
        page=self.page()
        self.items[0].cover_url='https://fixture.invalid/cover.jpg'
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            panel.list.load_covers()
            self.assertFalse(page.covers._paused)
            page._pause_covers()
            self.assertFalse(page.covers._paused)
            panel.hide()
            self.assertTrue(page.covers._paused)

    def test_missing_preview_metadata_gives_feedback_and_exit_clears_it(self):
        page=self.page()
        page._visible=True
        cover=widgets.CoverWidget(page)
        cover.set_item(models.WatchLaterItem(aid=1,bvid='missing'),None)
        page.card_preview.watch(cover)
        APP.sendEvent(cover,QEvent(QEvent.Enter))
        self.assertIn('暂无预览',cover._preview_status)
        self.assertFalse(page.card_preview.delay.isActive())
        APP.sendEvent(cover,QEvent(QEvent.Leave))
        self.assertEqual(cover._preview_status,'')

    def test_explicit_refresh_allows_retry_after_unavailable_preview(self):
        page=self.page()
        page.card_preview.unavailable.add(('fixture',1))
        page._load(manual=True)
        self.assertFalse(page.card_preview.unavailable)

    def test_failed_preview_is_reported_and_not_requested_again_on_hover(self):
        class Provider(QObject):
            preview_ready=Signal()
            def __init__(self,*args): super().__init__(args[-1])
            def frame_at(self,_): return None,0
            def state(self): return 'disabled'
        page=self.page()
        page._visible=True
        cover=widgets.CoverWidget(page)
        cover.set_item(self.items[0],None)
        cover.setParent(None)
        self.addCleanup(lambda: (cover.close(),cover.deleteLater()))
        cover.show()
        page.card_preview.watch(cover)
        with patch.object(card_preview,'VodPreviewProvider',Provider),patch.object(card_preview,'retire_preview'):
            APP.sendEvent(cover,QEvent(QEvent.Enter))
            page.card_preview.delay.stop()
            page.card_preview.begin()
            self.assertEqual(cover._preview_status,'暂无可用预览')
            self.assertFalse(page.card_preview.frames.isActive())
            APP.sendEvent(cover,QEvent(QEvent.Leave))
            APP.sendEvent(cover,QEvent(QEvent.Enter))
            self.assertFalse(page.card_preview.delay.isActive())
            page.card_preview.stop()


if __name__ == '__main__':
    unittest.main()
