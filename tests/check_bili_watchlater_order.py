"""Local queue, dual-purpose drag payload and unobstructed preview feedback."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from check_bili_watchlater import APP, models, plugin_module
from check_bili_watchlater_interactions import pages, TileFixture
from ddm_plugin_bili_watchlater.watch_order import WatchOrder
from ddm_plugin_bili_watchlater.order_sidebar import OrderSidebar, ORDER_MIME
from ddm_plugin_bili_watchlater import card_preview, widgets
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QDrag, QDropEvent, QIcon, QMouseEvent
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

    def test_wheel_distance_matches_host_follow_scroll_after_layout_and_resize(self):
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QWidget
        from ddm.sidebar_list import CarouselScroll
        page = self.page()
        panel = OrderSidebar(page,self.order,Mock())
        reference = CarouselScroll()
        content = QWidget()
        content.setFixedSize(250,8000)
        reference.setWidget(content)
        self.addCleanup(lambda:(panel.close(),panel.deleteLater(),reference.close(),reference.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            self.order.update([models.WatchLaterItem(aid=i+1,bvid=f'wheel{i}',title=str(i)) for i in range(60)],'account-A')
            panel.show()
            reference.show()
            for width in (280,360):
                panel.resize(width,650)
                reference.resize(width,650)
                APP.processEvents()
                view = panel.list
                self.assertEqual(view.verticalScrollBar().singleStep(), reference.verticalScrollBar().singleStep())
                for pixels,angle in ((QPoint(),QPoint(0,-120)),(QPoint(0,-17),QPoint())):
                    distances=[]
                    for scroll in (reference,view):
                        scroll.verticalScrollBar().setValue(1000)
                        event=QWheelEvent(QPointF(80,80),QPointF(scroll.mapToGlobal(QPoint(80,80))),
                            pixels,angle,Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
                        APP.sendEvent(scroll.viewport(),event)
                        distances.append(scroll.verticalScrollBar().value()-1000)
                    self.assertEqual(distances[0],distances[1])
                    if not angle.isNull():
                        self.assertGreater(distances[1],0)
                        self.assertLess(distances[1],view.visualRect(view.model().index(0,0)).height())

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
            # Release captured Qt icons/widgets while QApplication is still alive.
            plugin.context.register_sidebar_page.reset_mock()
            container.factory = None
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
        self.assertTrue(panel.list.isRowHidden(0))
        self.assertFalse(panel.list.isRowHidden(2))
        self.assertEqual(self.keys(),['video0','video1','video2','video3'])

    def test_next_video_and_position_use_local_order_not_gallery_filter(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        plugin.page = SimpleNamespace(items_snapshot=lambda: self.items)
        plugin.watch_order = self.order
        self.order.move('video3','video0')
        self.assertEqual(plugin._next_item_for('video3').bvid,'video0')
        self.assertIsNone(plugin._next_item_for('video2'))
        self.assertEqual(plugin._list_position_text('video0'),'2/4')

    def test_excluding_finished_preserves_hidden_order_and_next_anchor(self):
        from ddm_plugin_bili_watchlater.preferences import read_preferences
        self.assertTrue(read_preferences(self.context)['order_include_finished'])
        self.items[0].progress=self.items[1].progress=60
        self.order.include_finished=False
        self.assertEqual(self.keys(),['video2','video3'])
        self.assertIs(self.order.next_after('video0'),self.items[2])
        self.order.move('video3','video2')
        self.assertEqual(self.keys(),['video3','video2'])
        self.assertEqual(self.saved['watch_order']['keys'],['video0','video1','video3','video2'])
        self.order.include_finished=True
        self.assertEqual(self.keys(),['video0','video1','video3','video2'])

    def test_finished_setting_autosaves_refreshes_queue_and_clears_prefetch(self):
        from ddm_plugin_bili_watchlater.settings_dialog import WatchLaterSettings
        from ddm_plugin_bili_watchlater.preferences import read_preferences,save_preferences
        settings=WatchLaterSettings(read_preferences(self.context))
        self.addCleanup(settings.deleteLater)
        settings.changed.connect(lambda values:save_preferences(self.context,values))
        settings.order_include_finished.setChecked(False)
        self.assertIs(self.saved['order_include_finished'],False)
        plugin=plugin_module.BiliWatchLaterPlugin()
        plugin.context=self.context
        plugin.page=SimpleNamespace(items_snapshot=lambda:self.items)
        plugin.watch_order=self.order
        plugin.order_sidebar=SimpleNamespace(refresh=Mock())
        plugin._next_prefetch=Mock()
        plugin._sync_order_sidebar=Mock()
        self.items[1].progress=60
        values=read_preferences(self.context)
        plugin._preferences_changed(values)
        plugin.order_sidebar.refresh.assert_called_once()
        plugin._next_prefetch.clear.assert_called()
        self.assertFalse(self.order.include_finished)
        self.assertIs(plugin.watch_order.next_after('video0'),self.items[2])
        self.assertIs(plugin._next_item_for('video0'),self.items[2])

    def test_avoidance_moves_painted_cards_without_model_reset_and_can_reverse(self):
        from ddm_plugin_bili_watchlater import order_avoidance,order_sidebar
        from PySide6.QtCore import QAbstractAnimation
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda:(panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            view.setCurrentIndex(view.model().index(0,0))
            callbacks=[]
            page.context.start_sidebar_drag=lambda *args,**kwargs:callbacks.append(kwargs['on_finished']) or True
            reset=Mock()
            view.model().modelReset.connect(reset)
            with patch.object(order_sidebar.QDrag,'exec',side_effect=AssertionError('No OLE')):
                view.startDrag(Qt.CopyAction)
            state=view.avoidance
            self.assertTrue(state.active)
            self.assertEqual(state.preview,[None,1,2,3])
            original=view.visualRect(view.model().index(1,0))
            with patch.object(order_avoidance.motion,'enabled',return_value=True):
                point=view.visualRect(view.model().index(2,0)).bottomLeft()-QPoint(0,2)
                before,_y=view.insertion_at(point)
                self.assertEqual(before,3)
                self.assertEqual(state.preview,[1,2,None,3])
                state.animation.setCurrentTime(60)
                self.assertLess(state.rect_for_row(1).top(),original.top())
                current=dict(state.positions)
                state.move_gap(1)
                self.assertEqual(state.origins,current)
                state.animation.setCurrentTime(state.animation.duration())
                self.assertFalse(view.grab().isNull())
            reset.assert_not_called()
            self.assertEqual([item.bvid for item in view.model().items],self.keys())
            with patch.object(order_avoidance.motion,'enabled',return_value=False):
                state.reset_target()
                self.assertEqual(state.animation.state(),QAbstractAnimation.Stopped)
                self.assertEqual(state.before_row(),0)
            callbacks[0]()
            self.assertFalse(state.active)
            self.assertEqual(state.animation.state(),QAbstractAnimation.Stopped)
            self.assertEqual(view.visualRect(view.model().index(1,0)),original)
            view.setRowHidden(1,True)
            state.begin(2)
            state.move_gap(0)
            state.reset_target()
            self.assertEqual(state.before_row(),2)
            self.assertFalse(self.order.move('video2','video2'))
            state.clear()

    def test_finished_icon_appears_without_text_and_filtered_empty_is_explained(self):
        from PySide6.QtGui import QPainter,QPixmap
        from PySide6.QtWidgets import QStyleOptionViewItem
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda:(panel.close(),panel.deleteLater()))
        panel.refresh()
        view=panel.list
        option=QStyleOptionViewItem()
        from PySide6.QtCore import QRect
        option.rect=QRect(0,0,300,170)
        def render():
            image=QPixmap(300,170)
            image.fill(Qt.transparent)
            painter=QPainter(image)
            view.itemDelegate().paint(painter,option,view.model().index(0,0))
            painter.end()
            return image.toImage()
        before=render()
        self.items[0].progress=60
        after=render()
        self.assertNotEqual(before.copy(260,8,30,26),after.copy(260,8,30,26))
        self.assertIn('已看完',view.model().index(0,0).data(Qt.AccessibleTextRole))
        for item in self.items:item.progress=60
        self.order.include_finished=False
        panel.refresh()
        self.assertEqual(view.model().rowCount(),0)
        self.assertIn('没有未看完',panel.status.text())

    def test_quick_click_uses_single_vod_or_empty_but_ambiguous_opens_picker(self):
        plugin = plugin_module.BiliWatchLaterPlugin()
        occupied, empty = TileFixture({'room_id':'live'}), TileFixture({})
        plugin.host.playback_tiles = lambda: [occupied,empty]
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
            self.assertEqual(panel.list.model().rowCount(),4)
            self.assertFalse(panel.grab().isNull())
            panel.list.clicked.emit(panel.list.model().index(2,0))
            play.assert_not_called()
            panel.list.doubleClicked.emit(panel.list.model().index(2,0))
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

    def test_model_keeps_identical_rows_and_resets_only_changed_projection(self):
        from ddm_plugin_bili_watchlater.order_model import OrderModel
        from PySide6.QtTest import QAbstractItemModelTester
        model=OrderModel(APP)
        tester=QAbstractItemModelTester(model,QAbstractItemModelTester.FailureReportingMode.Warning)
        reset=Mock()
        model.modelReset.connect(reset)
        model.replace(self.items)
        model.replace(list(self.items))
        reset.assert_called_once()
        self.assertIs(model.index(0,0).data(Qt.UserRole),self.items[0])
        for _ in range(10):
            model.replace(list(reversed(self.items)))
            model.replace(self.items)
        self.assertEqual(model.rowCount(model.index(0,0)),0)
        tester.deleteLater()
        model.deleteLater()

    def test_drag_hotspot_uses_press_position_and_drag_object_is_retired(self):
        from ddm_plugin_bili_watchlater import order_sidebar
        from shiboken6 import isValid
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            index=view.model().index(1,0)
            grip=QPoint(19,32)
            QTest.mousePress(view.viewport(),Qt.LeftButton,pos=view.visualRect(index).topLeft()+grip)
            with patch.object(order_sidebar.motion,'enabled',return_value=False):
                drag=view.prepare_drag(index)
                self.assertEqual(drag.hotSpot(),grip)
                self.assertEqual(drag.pixmap().deviceIndependentSize().toSize(),view.visualRect(index).size())
            drag.deleteLater()
            with patch.object(order_sidebar.QDrag,'exec',return_value=Qt.IgnoreAction):
                view.startDrag(Qt.CopyAction)
            APP.sendPostedEvents(None,QEvent.DeferredDelete)
            self.assertFalse(isValid(drag))
            self.assertFalse(view.findChildren(QDrag))

    def test_drop_refresh_is_deferred_until_native_drag_exec_returns(self):
        from ddm_plugin_bili_watchlater import order_sidebar
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            view.setCurrentIndex(view.model().index(0,0))
            changed=list(reversed(self.items))
            def during_exec(*_):
                self.assertTrue(view.feedback.dragging)
                view.replace(changed)
                self.assertIs(view.model().index(0,0).data(Qt.UserRole),self.items[0])
                panel.hide()
                panel.show()
                return Qt.MoveAction
            with patch.object(order_sidebar.QDrag,'exec',side_effect=during_exec):
                view.startDrag(Qt.CopyAction)
            # showEvent's canonical snapshot wins over an earlier pending refresh.
            self.assertIsNone(view._pending_items)
            self.assertFalse(view.feedback.dragging)
            self.assertIs(view.model().index(0,0).data(Qt.UserRole),self.items[0])
            self.assertFalse(view.drag_scroll.isActive())

    def test_drag_lifts_original_into_gap_and_restores_without_resetting_model(self):
        from ddm_plugin_bili_watchlater import order_sidebar
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            index=view.model().index(0,0)
            view.setCurrentIndex(index)
            view.feedback.clear()
            rect=view.visualRect(index)
            before=view.viewport().grab(rect).toImage()
            reset=Mock()
            view.model().modelReset.connect(reset)
            prepared=[]
            original_prepare=view.prepare_drag
            def prepare(row):
                drag=original_prepare(row)
                prepared.append(drag.pixmap().toImage())
                return drag
            def during_exec(*_):
                self.assertEqual(view.dragged_bvid,self.items[0].bvid)
                self.assertEqual(view.visualRect(index),rect)
                self.assertEqual(view.model().rowCount(),len(self.items))
                self.assertNotEqual(view.viewport().grab(rect).toImage(),before)
                self.assertEqual(prepared[0],before)
                reset.assert_not_called()
                return Qt.IgnoreAction
            with patch.object(order_sidebar.motion,'enabled',return_value=False), \
                    patch.object(view,'prepare_drag',side_effect=prepare), \
                    patch.object(order_sidebar.QDrag,'exec',side_effect=during_exec):
                view.startDrag(Qt.CopyAction)
            self.assertIsNone(view.dragged_bvid)
            view.feedback.clear()
            self.assertEqual(view.viewport().grab(rect).toImage(),before)
            reset.assert_not_called()

    def test_drag_failure_clears_gap_before_rethrowing(self):
        from ddm_plugin_bili_watchlater import order_sidebar
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            view.setCurrentIndex(view.model().index(0,0))
            with patch.object(order_sidebar.QDrag,'exec',side_effect=RuntimeError('fixture')):
                with self.assertRaisesRegex(RuntimeError,'fixture'):
                    view.startDrag(Qt.CopyAction)
            self.assertIsNone(view.dragged_bvid)
            self.assertFalse(view.feedback.dragging)
            self.assertFalse(view.drag_scroll.isActive())

    def test_shared_drag_displays_visual_before_gap_and_restores_after_callback(self):
        from ddm_plugin_bili_watchlater import order_sidebar
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            index=view.model().index(0,0)
            view.setCurrentIndex(index)
            callbacks=[]
            def start(source,mime,pixmap,hotspot,global_pos,*,on_finished):
                self.assertIs(source,view)
                self.assertIsNone(view.dragged_bvid)
                self.assertFalse(pixmap.isNull())
                self.assertIs(mime,view._active_mime)
                callbacks.append(on_finished)
                return True
            page.context.start_sidebar_drag=start
            with patch.object(order_sidebar.QDrag,'exec',side_effect=AssertionError('Native OLE must not run')):
                view.startDrag(Qt.CopyAction)
            self.assertEqual(view.dragged_bvid,self.items[0].bvid)
            self.assertTrue(view.feedback.dragging)
            drop=QDropEvent(QPointF(4,4),Qt.MoveAction,view._active_mime,Qt.LeftButton,Qt.NoModifier)
            self.assertIsNone(drop.source())
            self.assertTrue(view.owns_drag(drop))
            foreign_mime=view.drag_mime(self.items[0])
            foreign=QDropEvent(QPointF(4,4),Qt.MoveAction,foreign_mime,Qt.LeftButton,Qt.NoModifier)
            self.assertFalse(view.owns_drag(foreign))
            callbacks[0]()
            self.assertIsNone(view.dragged_bvid)
            self.assertIsNone(view._active_drag)
            self.assertIsNone(view._active_mime)
            self.assertFalse(view.feedback.dragging)

    def test_busy_shared_drag_does_not_fall_back_to_native(self):
        from ddm_plugin_bili_watchlater import order_sidebar
        page=self.page()
        page.context.start_sidebar_drag=Mock(return_value=False)
        panel=OrderSidebar(page,self.order,Mock())
        self.addCleanup(lambda: (panel.close(),panel.deleteLater()))
        with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'):
            panel.resize(300,650)
            panel.show()
            APP.processEvents()
            view=panel.list
            view.setCurrentIndex(view.model().index(0,0))
            with patch.object(order_sidebar.QDrag,'exec',side_effect=AssertionError('No concurrent native drag')):
                view.startDrag(Qt.CopyAction)
            self.assertFalse(view.feedback.dragging)
            self.assertIsNone(view._active_drag)
            self.assertIsNone(view.dragged_bvid)

    def test_real_host_shared_session_reorders_and_restores_without_ole(self):
        from ddm.widgets import Sidebar
        from ddm.plugins import PluginContext, PluginManager
        from PySide6.QtWidgets import QHBoxLayout, QWidget
        host=QWidget()
        host.setGeometry(-10000,-10000,700,760)
        host.sidebar=Sidebar([],auto_compact=False)
        QHBoxLayout(host).addWidget(host.sidebar)
        manager=PluginManager(window=host,plugins_dir='unused',enabled=[])
        context=PluginContext(manager,'fixture')
        page=self.page()
        page.context.start_sidebar_drag=context.start_sidebar_drag
        panel=OrderSidebar(page,self.order,Mock())
        context.register_sidebar_page('order',panel,title='观看顺序',icon=QIcon())
        try:
            with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'), \
                    patch.object(QDrag,'exec',side_effect=AssertionError('No OLE')):
                host.show()
                host.sidebar.plugin_pages.select(('fixture','order'))
                APP.processEvents()
                view=panel.list
                view.setCurrentIndex(view.model().index(0,0))
                view.startDrag(Qt.CopyAction)
                session=host.sidebar.drag_session
                self.assertTrue(session.active)
                self.assertTrue(session.ghost.isVisible())
                self.assertEqual(view.dragged_bvid,self.items[0].bvid)
                point=view.visualRect(view.model().index(1,0)).bottomLeft()-QPoint(0,2)
                session.position=view.viewport().mapToGlobal(point)
                with patch('ddm.sidebar_drag.widget_at',return_value=view.viewport()):
                    self.assertTrue(session.finish())
                self.assertEqual(self.keys()[:2],['video1','video0'])
                self.assertIsNone(view.dragged_bvid)
                self.assertFalse(view.feedback.dragging)
                self.assertIsNone(view._active_mime)
        finally:
            context.cancel_sidebar_drag()
            context.unregister_sidebar_page('order')
            panel.deleteLater()
            host.close()
            host.deleteLater()
            APP.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_two_video_drags_then_live_drag_and_switch_back(self):
        from ddm.widgets import Sidebar
        from ddm.sidebar_drag import ROOM_MIME, NAV_MIME
        from PySide6.QtCore import QMimeData
        from PySide6.QtWidgets import QHBoxLayout, QWidget
        page=self.page()
        panel=OrderSidebar(page,self.order,Mock())
        host=QWidget()
        host.setGeometry(-10000,-10000,700,760)
        sidebar=Sidebar([dict(room_id='fixture',uname='Fixture',live=False)],auto_compact=False)
        QHBoxLayout(host).addWidget(sidebar)
        sidebar.plugin_pages.add('fixture','order',panel,'观看顺序',QIcon())
        try:
            with patch.object(page,'refresh_from_wall'),patch.object(page.covers,'_pump'), \
                    patch.object(QDrag,'exec',return_value=Qt.IgnoreAction):
                host.show()
                APP.processEvents()
                for _ in range(8):
                    sidebar.plugin_pages.select(('fixture','order'))
                    APP.processEvents()
                    view=panel.list
                    for row in (0,1):
                        view.setCurrentIndex(view.model().index(row,0))
                        view.startDrag(Qt.CopyAction)
                    sidebar.plugin_pages.select()
                    live=sidebar.items()[0]
                    mime=QMimeData()
                    mime.setData(ROOM_MIME,b'fixture')
                    mime.setData(NAV_MIME,b'fixture')
                    sidebar.drag_session.start(live,mime,live.grab(),QPoint(15,20),live.mapToGlobal(QPoint(15,20)))
                    sidebar.drag_session.cancel()
                    sidebar.plugin_pages.select(('fixture','order'))
                    APP.processEvents()
                    self.assertEqual(view.model().rowCount(),len(self.items))
                    self.assertFalse(view.feedback.dragging)
                    APP.sendPostedEvents(None,QEvent.DeferredDelete)
                    self.assertFalse(view.findChildren(QDrag))
        finally:
            sidebar.drag_session.cancel()
            sidebar.plugin_pages.remove_owner('fixture')
            panel.close()
            panel.deleteLater()
            host.close()
            host.deleteLater()
            APP.sendPostedEvents(None,QEvent.DeferredDelete)

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
            point=view.visualRect(view.model().index(1,0)).center()
            self.assertTrue(view.viewport().hasMouseTracking())
            APP.sendEvent(view.viewport(),QMouseEvent(QEvent.MouseMove,QPointF(point),
                QPointF(view.viewport().mapToGlobal(point)),Qt.NoButton,Qt.NoButton,Qt.NoModifier))
            self.assertEqual(view.viewport().cursor().shape(),Qt.PointingHandCursor)
            QTest.mouseClick(view.viewport(),Qt.LeftButton,pos=point)
            play.assert_not_called()
            self.assertEqual(view.currentIndex().row(),1)
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
            rect=view.visualRect(view.model().index(0,0))
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
            self.assertEqual(view.visualRect(view.model().index(0,0)),rect)
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
                update.assert_called_once_with(view.visualRect(view.model().index(0,0)))
                update.reset_mock()
                view.cover_ready(self.items[-1].cover_url)
                update.assert_not_called()
            y=view.visualRect(view.model().index(0,0)).bottom()-2
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
                def position(self): return QPointF(view.visualRect(view.model().index(0,0)).topLeft())
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
