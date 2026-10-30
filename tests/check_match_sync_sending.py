"""Console geometry, room drafts and explicit multi-send; no real accounts."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import types
from unittest.mock import patch


def check_platform_controls(app, host, module, source):
    """Use the maintained domestic composers, with no credentials or requests."""
    from PySide6.QtWidgets import QPushButton, QWidget
    from ddm import plugins
    package = types.ModuleType('sending_domestic_fixture')
    package.__path__ = [str(source.resolve())]
    sys.modules[package.__name__] = package
    composers = import_module(package.__name__ + '.douyu_composer')

    class Platform(plugins.Platform):
        def __init__(self, kind):
            self.kind = kind

        def matches(self, room_id):
            return str(room_id).startswith(self.kind + ':')

        def normalize(self, room_id):
            return str(room_id)

        def _playback_cookies(self):
            raise AssertionError('Credentials forbidden')

    with tempfile.TemporaryDirectory() as directory:
        host.settings, host.rooms = {}, []
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        context = plugins.PluginContext(manager, 'domestic_fixture')
        for kind, composer in (('douyu', composers.DouyuComposer), ('huya', composers.HuyaComposer),
                               ('douyin', composers.DouyinComposer)):
            platform = Platform(kind)
            context.register_platform(platform)
            composers.DouyuInteraction(context, platform, composer_class=composer, control_id=kind)
        from ddm.widgets import Tile
        legacy = Tile({'room_id': 'douyu:12345', 'uname': 'fixture'}, host)
        manager.attach_tile(legacy)
        assert not legacy._plugin_controls.applicable(), 'Missing platform reproduces the old hidden-control bug'
        manager.detach_tile(legacy)
        legacy.deleteLater()
        viewer = module.Viewer(plugins.PluginContext(manager, 'platform_check'), {})
        try:
            for kind in ('douyu', 'huya', 'douyin'):
                key = kind + ':12345'
                viewer.add_room(key)
                row = viewer.send_console.rows[key]
                controls = row.tile._plugin_controls
                assert row.tile.room['platform'] == kind
                assert len(controls.applicable()) == 1, kind
                assert controls._repeat_editor() is not None, kind
                assert row.unavailable.isHidden(), kind
                editor = controls._repeat_editor()
                send = next(button for button in row.findChildren(QPushButton)
                            if button.property('sendPrimary'))
                assert editor.height() == viewer.send_console.batch_panel.edit.height() == 32, kind
                assert send.size() == viewer.send_console.batch_panel.send.size(), kind
                assert send.width() == 32 and hasattr(send, '_match_sync_face'), kind
                gift = controls.applicable()[0].control.widget.gifts
                assert 'QPushButton#TileCtrl:hover' in gift.styleSheet(), kind
                assert 'border-radius: 12px' in gift.styleSheet(), kind
                assert 'background: transparent' in send.styleSheet(), kind
                shared = controls.applicable()[0].control.widget.findChild(QWidget, 'SendInteractions')
                assert shared is not None and len(shared.findChildren(QPushButton)) == 2
                assert all(not button.isHidden() for button in shared.findChildren(QPushButton))
            console = viewer.send_console
            anchor = console.batch_panel.interactions.findChildren(QPushButton)[1]
            native = console.rows['douyu:12345'].tile._plugin_controls.applicable()[0].control.widget
            seen = []
            with patch.object(native, 'open_gifts', side_effect=lambda: seen.append(native.gifts is anchor)):
                menu = console.interaction_menu('gift', anchor)
                assert [action.text() for action in menu.actions()] == ['斗鱼', '虎牙', '抖音']
                menu.actions()[0].menu().actions()[0].trigger()
                assert seen == [True] and native.gifts is not anchor
                assert not menu.actions()[1].menu().actions()[0].isEnabled()
                menu.deleteLater()
            menu = console.interaction_menu('emote', anchor)
            assert all(not action.menu().actions()[0].isEnabled() for action in menu.actions())
            menu.deleteLater()
            return {'domestic_controls': ['douyu', 'huya', 'douyin'], 'credentials_read': False}
        finally:
            manager.unload()
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def check(app, host, module, output=None):
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QWidget
    from ddm import plugins
    from ddm.plugin_controls import TileControl
    sent, active = [], []

    class Composer(TileControl):
        def __init__(self, context):
            widget = QWidget(context.parent)
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            self.edit = QLineEdit()
            self.edit.setMaxLength(20)
            button = QPushButton('发送')
            layout.addWidget(self.edit, 1)
            layout.addWidget(button)
            super().__init__(widget, minimum_width=80)
            self.key = context.room['room_id']
            self.timer = QTimer(widget)
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self.finish)
            self.edit.returnPressed.connect(self.send)
            button.clicked.connect(self.send)

        def focus_input(self):
            return True

        def send(self):
            assert not active, 'Multi-send must respect one pending platform request'
            active.append(self.key)
            sent.append((self.key, self.edit.text()))
            self.edit.setReadOnly(True)
            self.timer.start(30)

        def shutdown(self):
            self.timer.stop()
            if self.key in active:
                active.remove(self.key)

        def finish(self):
            active.clear()
            if not self.widget.property('fail_send'):
                self.edit.clear()
            self.edit.setReadOnly(False)

    with tempfile.TemporaryDirectory() as directory:
        host.settings = {}
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        manager.register_tile_control('fixture', 'send', Composer)
        viewer = module.Viewer(plugins.PluginContext(manager, 'sending_check'), {})
        assert viewer.send_console.findChild(module.QLabel, 'SendContext') is None
        assert viewer.send_console.work.isHidden()
        assert viewer.send_console.title.text() == '弹幕控制台'
        assert not viewer.notice.text()
        assert not viewer.panel.body.toPlainText().strip(), 'No instructional text before a room is added'
        viewer.setAttribute(Qt.WA_DontShowOnScreen, host.testAttribute(Qt.WA_DontShowOnScreen))
        try:
            viewer.resize(1320, 880)
            viewer.show()
            QTest.qWait(30)
            empty_console = viewer.send_console
            empty_image = empty_console.grab().toImage()
            point = empty_console.rooms.mapTo(empty_console, empty_console.rooms.rect().center())
            assert empty_image.pixelColor(point) == empty_image.pixelColor(empty_image.width() - 30, point.y()), 'Empty recipients must not leave a tinted rectangle'
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                empty_console.grab().save(str(output.with_name(output.stem + '-empty.png')))
            for key in ('1', '2', '3'):
                viewer.add_room(key, {'alias': '主播' + key})
            viewer.resize(1320, 880)
            viewer.show()
            QTest.qWait(80)
            console = viewer.send_console
            for button in (viewer.automatic, viewer.compare, viewer.rows['1'].audible,
                           viewer.match_settings_button, viewer.minimize_settings, console.minimize):
                assert button.cursor().shape() == Qt.PointingHandCursor, button
                assert button.testAttribute(Qt.WA_Hover), button
            panels = import_module(module.__package__ + '.panels')
            from PySide6.QtCore import QEvent
            if sys.platform == 'win32':
                with patch.object(panels.QApplication, 'platformName', return_value='windows'), \
                        patch.object(panels.QApplication, 'widgetAt', return_value=viewer.automatic), \
                        patch.object(panels.ctypes.windll.user32, 'LoadCursorW', return_value=123) as load, \
                        patch.object(panels.ctypes.windll.user32, 'SetCursor') as apply:
                    override = panels.QApplication.overrideCursor()
                    assert not viewer.automatic._match_link_cursor.eventFilter(viewer.automatic, QEvent(QEvent.Enter))
                    load.assert_called_once_with(None, 32649)
                    apply.assert_called_once_with(123)
                    load.reset_mock()
                    viewer.automatic._match_link_cursor.eventFilter(viewer.automatic, QEvent(QEvent.Leave))
                    load.assert_not_called()
                    assert panels.QApplication.overrideCursor() is override, 'Do not leave a global cursor override'
            assert not console.minimize.text() and not viewer.minimize_settings.text()
            assert not console.minimize.autoDefault() and not console.minimize.isDefault()
            viewer.panel.body.setFocus()
            viewer.panel.bottom_button.show()
            QTest.mouseClick(viewer.panel.bottom_button, Qt.LeftButton)
            assert not console.minimize.hasFocus(), 'Returning to chat bottom must not focus the minimize icon'
            assert not hasattr(console, 'settings_button')
            assert console.minimize.size() == viewer.minimize_settings.size(), (console.minimize.size(), viewer.minimize_settings.size())
            assert console.minimize.styleSheet() == viewer.minimize_settings.styleSheet()
            QTest.mouseClick(viewer.match_settings_button, Qt.LeftButton)
            assert viewer.match_settings is not None and viewer.match_settings.isVisible()
            assert viewer.buffer_reserve.parentWidget() is viewer.match_settings
            assert viewer.automatic.parentWidget() is viewer.settings_panel
            assert viewer.compare.parentWidget() is viewer.settings_panel
            old_reserve = viewer.buffer_reserve.currentIndex()
            viewer.buffer_reserve.setCurrentIndex(viewer.buffer_reserve.findData(8))
            assert viewer.reserve_seconds == 8
            viewer.buffer_reserve.setCurrentIndex(old_reserve)
            if output:
                viewer.match_settings.grab().save(str(output.with_name(output.stem + '-settings.png')))
            viewer.match_settings.close()
            def finish_batch():
                deadline = time.monotonic() + 2
                while console.batch_panel.timer.isActive() and time.monotonic() < deadline:
                    QTest.qWait(20)
                assert not console.batch_panel.timer.isActive(), 'The synthetic send queue must finish'
            assert not console.saved_input.isHidden() and not console.work.isHidden()
            assert not console.feedback.isVisible()
            assert not any('左键选择主播；' in label.text() for label in console.findChildren(module.QLabel))
            first = console.rows['1'].tile._plugin_controls.entries[0].control.edit
            second = console.rows['2'].tile._plugin_controls.entries[0].control.edit
            def select(keys):
                for index in range(console.rooms.count()):
                    item = console.rooms.item(index)
                    if (item.data(Qt.UserRole) in keys) != (item.checkState() == Qt.Checked):
                        console.rooms.scrollToItem(item)
                        QTest.mouseClick(console.rooms.viewport(), Qt.LeftButton,
                                         pos=console.rooms.visualItemRect(item).center())
                assert set(console.checked_rooms()) == set(keys)

            first.setText('draft 1')
            select({'2'})
            second.setText('draft 2')
            item = console.rooms.item(1)
            position = console.rooms.visualItemRect(item).center()
            before_clicks = set(console.checked_rooms())
            with patch.object(module.motion, 'enabled', return_value=True):
                for index in range(6):
                    QTest.mouseClick(console.rooms.viewport(), Qt.LeftButton, pos=position)
                    assert ('2' in console.checked_rooms()) == (index % 2 == 1)
                delegate = console.rooms.itemDelegate()
                assert delegate.animations['2'].duration() == 180
                assert delegate.animations['2'].state() == delegate.animations['2'].State.Running
            assert set(console.checked_rooms()) == before_clicks
            QTest.mouseDClick(console.rooms.viewport(), Qt.LeftButton, pos=position)
            QTest.mouseRelease(console.rooms.viewport(), Qt.LeftButton, pos=position)
            assert '2' not in console.checked_rooms(), 'Double-click must count the next press, not swallow it'
            QTest.qWait(200)
            with patch.object(module.motion, 'enabled', return_value=True):
                select({'2'})
                QTest.qWait(20)
                entering = console.rooms.viewport().grab().toImage()
                QTest.qWait(200)
                assert entering != console.rooms.viewport().grab().toImage(), 'Card insertion must produce visible intermediate frames'
            assert console.stack.currentWidget() is console.rows['2']
            assert first.text() == 'draft 1' and not sent
            assert console.rooms.count() == 3
            assert '有草稿' in console.rooms.item(0).text()
            select({'1'})
            assert console.stack.currentWidget() is console.rows['1']
            select({'2'})
            console.rooms.setFocus()
            console.rooms.setCurrentRow(0)
            QTest.keyClick(console.rooms, Qt.Key_Down if console.width() >= 520 else Qt.Key_Right)
            assert console.checked_rooms() == ['2'] and not sent
            avatar = QPixmap(32, 32)
            avatar.fill(QColor('#728B75'))
            viewer.rows['2'].avatar.set_pixmap_image(avatar)
            console.refresh_avatar('2')
            assert isinstance(console.rooms.item(1).data(Qt.DecorationRole), QPixmap)
            QTest.qWait(160)
            picture = console.rooms.viewport().grab()
            rect = console.rooms.visualItemRect(console.rooms.item(1))
            sample = picture.toImage().pixelColor(round((rect.left() + 10) * picture.devicePixelRatio()),
                                                  round(rect.center().y() * picture.devicePixelRatio()))
            assert sample != module.theme.qcolor(module.theme.ACCENT), 'Do not outline selected avatars in blue'
            def avatar_x():
                image = console.rooms.viewport().grab().toImage()
                ratio = console.rooms.devicePixelRatioF()
                row_rect = console.rooms.visualItemRect(console.rooms.item(1))
                pixels = [x for y in range(round(row_rect.top() * ratio), round(row_rect.bottom() * ratio))
                          for x in range(round(row_rect.left() * ratio), round(row_rect.right() * ratio))
                          if image.pixelColor(x, y) == QColor('#728B75')]
                return sum(pixels) / len(pixels) / ratio
            selected_x = avatar_x()
            select(set())
            QTest.qWait(200)
            unselected_x = avatar_x()
            assert 10 <= selected_x - unselected_x <= 14, 'Selected cards insert right; deselected cards retract left'
            select({'2'})
            QTest.qWait(200)
            assert viewer.panel.property('joined') is True
            joined_image = viewer.panel.grab().toImage()
            assert joined_image.pixelColor(0, joined_image.height() - 1) == joined_image.pixelColor(20, joined_image.height() - 1), 'Joined chat must have square lower corners'
            assert isinstance(console, module.SettingsPanel), 'Keep the existing console size and visibility contract'
            panel_height = viewer.panel.height()
            console_height = console.height()
            console.minimize.click()
            QTest.qWait(40)
            assert console.isHidden() and console.restore.isVisible()
            assert viewer.panel.property('joined') is False
            assert viewer.settings_panel.isVisible(), 'Sending minimizes independently'
            assert viewer.panel.height() >= panel_height + console_height - 1
            viewer.resize(900, 720)
            QTest.qWait(40)
            assert console.isHidden(), 'Resize must not reopen a minimized console'
            if output:
                viewer.grab().save(str(output.with_name(output.stem + '-minimized.png')))
            viewer.set_settings_visible(False)
            QTest.qWait(40)
            assert console.restore.isVisible(), 'Restore sending must survive both consoles being minimized'
            if output:
                viewer.grab().save(str(output.with_name(output.stem + '-both-minimized.png')))
            console.restore.click()
            QTest.qWait(40)
            assert console.isVisible() and console.restore.isHidden()
            assert viewer.settings_panel.isHidden(), 'Sending can be restored without opening the left console'
            assert second.text() == 'draft 2' and not sent
            viewer.set_settings_visible(True)
            for width, height in ((1320, 880), (820, 700), (640, 1000)):
                viewer.resize(width, height)
                QTest.qWait(40)
                for sizes in ([500, 0, 240], [380, 0, 340]):
                    viewer.body_split.setSizes(sizes)
                    QTest.qWait(40)
                    left = viewer.settings_panel.mapTo(viewer, QPoint()).y()
                    right = console.mapTo(viewer, QPoint()).y()
                    assert abs(left - right) <= 1, (left, right)
                    regular = console.width() >= 520
                    assert console.body_layout.direction() == (QBoxLayout.LeftToRight if regular
                                                              else QBoxLayout.TopToBottom)
                    assert first.isVisible() == (console.selected_room == '1')
            assert console.findChild(module.QLabel, 'SendContext') is None
            for right_width in (640, 300):
                viewer.resize(1500, 880)
                viewer.picture_split.setSizes([1500 - right_width, right_width])
                QTest.qWait(60)
                assert (console.width() >= 520) == (right_width == 640), console.width()
                if right_width == 300:
                    assert console.body_scroll.horizontalScrollBar().maximum() == 0
                    button = second.parentWidget().findChildren(QPushButton)[0]
                    assert button.mapTo(console, QPoint(button.width(), 0)).x() <= console.width()
                if output:
                    viewer.grab().save(str(output.with_name(output.stem + f'-width-{right_width}.png')))
            viewer.resize(1320, 880)
            viewer.picture_split.setSizes([700, 620])
            QTest.qWait(40)
            feedback_bottom = console.feedback.mapTo(console.body_scroll.viewport(),
                                                      QPoint(0, console.feedback.height())).y()
            assert not console.feedback.isVisible() or feedback_bottom <= console.body_scroll.viewport().height(), (
                feedback_bottom, console.body_scroll.viewport().height(),
                console.body_scroll.widget().minimumSizeHint(), console.work.minimumSizeHint(),
                console.pages.minimumSizeHint(), console.feedback.sizeHint(),
                console.body_scroll.widget().minimumSize(), console.body_scroll.widget().size(),
                console.body_layout.minimumSize(), console.work.geometry(), console.pages.geometry())
            if output:
                console.grab().save(str(output.with_name(output.stem + '-single-detail.png')))
                module.theme.apply('light')
                QTest.qWait(30)
                console.grab().save(str(output.with_name(output.stem + '-light.png')))
                module.theme.apply('dark')
            single_button = next(button for button in second.parentWidget().findChildren(QPushButton)
                                 if button.property('sendPrimary'))
            single_edit_geometry = (second.mapTo(console, QPoint()).x(), second.mapTo(console, QPoint()).y(),
                                    second.width(), second.height())
            single_button_geometry = (single_button.mapTo(console, QPoint()).x(),
                                      single_button.mapTo(console, QPoint()).y(),
                                      single_button.width(), single_button.height())
            select({'1', '2', '3'})
            dialog = console.batch_panel
            assert console.pages.currentWidget() is dialog
            dialog.edit.clear()
            assert not dialog.send.isEnabled(), 'Empty multi-send must be disabled'
            dialog.edit.setText('   ')
            assert not dialog.send.isEnabled(), 'Whitespace is not a message'
            dialog.edit.clear()
            QTest.qWait(40)
            assert (dialog.edit.mapTo(console, QPoint()).x(), dialog.edit.mapTo(console, QPoint()).y(),
                    dialog.edit.width(), dialog.edit.height()) == single_edit_geometry
            assert (dialog.send.mapTo(console, QPoint()).x(), dialog.send.mapTo(console, QPoint()).y(),
                    dialog.send.width(), dialog.send.height()) == single_button_geometry
            dialog.edit.setText('shared')
            dialog.start()
            finish_batch()
            assert sent == [('1', 'shared'), ('2', 'shared'), ('3', 'shared')]
            assert first.text() == 'draft 1' and second.text() == 'draft 2'
            assert not dialog.timer.isActive()
            assert all(console.statuses[key] == '✓' for key in ('1', '2', '3'))
            assert all(console.rooms.item(index).data(Qt.CheckStateRole) == Qt.Checked.value
                       for index in range(console.rooms.count())), 'Visual checks must match the actual send targets'
            assert not console.feedback.isVisible()
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                viewer.grab().save(str(output.with_name(output.stem + '-multi.png')))
                console.grab().save(str(output.with_name(output.stem + '-multi-detail.png')))
            dialog.edit.setText('x' * 21)
            dialog.start()
            QTest.qWait(100)
            assert len(sent) == 3, 'Do not truncate and send overlong text'
            dialog.edit.setText('cancel rest')
            dialog.start()
            assert dialog.cancel.isVisible()
            dialog.cancel.click()
            QTest.qWait(100)
            assert sent[-1] == ('1', 'cancel rest') and len(sent) == 4
            assert not active, 'Closing stops further targets and shuts down the pending composer'
            assert console.statuses['2'] == '已取消，未发送'
            assert dialog.send.text() == '发送'
            assert hasattr(dialog.send, '_match_sync_face')
            select({'2'})
            second.setText('draft 2')
            console.saved_input.setText('saved message')
            console.save_text.click()
            assert not console.saved_input.text()
            console.saved_input.setText('saved message')
            console.save_text.click()
            assert console.saved_texts.count() == 1, 'Do not duplicate saved texts'
            assert viewer.context.setting('saved_send_texts') == ['saved message']
            for index in range(30):
                console.add_saved_text('scroll phrase ' + str(index))
            QTest.qWait(40)
            assert console.saved_texts.verticalScrollBar().maximum() > 0
            assert console.saved_texts.horizontalScrollBar().maximum() == 0
            console.saved_texts.scrollToBottom()
            assert console.saved_texts.visualItemRect(console.saved_texts.item(30)).intersects(console.saved_texts.viewport().rect())
            while console.saved_texts.count() > 1:
                console.saved_texts.takeItem(console.saved_texts.count() - 1)
            console.saved_texts.scrollToTop()
            console.store_phrase('second phrase')
            QTest.qWait(40)
            first_position = console.saved_texts.visualItemRect(console.saved_texts.item(0)).center()
            second_position = console.saved_texts.visualItemRect(console.saved_texts.item(1)).center()
            count_before_drag = len(sent)
            QTest.mousePress(console.saved_texts.viewport(), Qt.LeftButton, pos=first_position)
            QTest.qWait(470)
            QTest.mouseMove(console.saved_texts.viewport(), second_position)
            QTest.mouseRelease(console.saved_texts.viewport(), Qt.LeftButton, pos=second_position)
            assert len(sent) == count_before_drag, 'A long press or drag must not send'
            assert viewer.context.setting('saved_send_texts') == ['second phrase', 'saved message']
            position = console.saved_texts.visualItemRect(console.saved_texts.item(0)).center()
            QTest.mouseClick(console.saved_texts.viewport(), Qt.RightButton, pos=position)
            assert console.saved_texts.count() == 2, 'Right click only reveals the delete badge'
            if output:
                console.grab().save(str(output.with_name(output.stem + '-delete-badge.png')))
            rect = console.saved_texts.visualItemRect(console.saved_texts.item(0))
            QTest.mouseClick(console.saved_texts.viewport(), Qt.LeftButton, pos=rect.topRight() + QPoint(-8, 8))
            assert viewer.context.setting('saved_send_texts') == ['saved message']
            if output:
                console.grab().save(str(output.with_name(output.stem + '-stored-text.png')))
            before_saved_send = len(sent)
            QTest.mouseClick(console.saved_texts.viewport(), Qt.LeftButton,
                             pos=console.saved_texts.visualItemRect(console.saved_texts.item(0)).center())
            finish_batch()
            assert sent[before_saved_send:] == [('2', 'saved message')]
            assert second.text() == 'draft 2', 'Quick send must preserve the independent draft'
            select({'1', '2'})
            console.saved_texts.itemClicked.emit(console.saved_texts.item(0))
            finish_batch()
            assert sent[-2:] == [('1', 'saved message'), ('2', 'saved message')]
            assert first.text() == 'draft 1' and second.text() == 'draft 2'
            second.setText('draft 2')
            QTest.qWait(30)
            saved_input_y = console.saved_input.mapTo(console, QPoint()).y()
            select(set())
            QTest.qWait(30)
            assert console.saved_input.mapTo(console, QPoint()).y() == saved_input_y, 'No selection must retain compose space'
            assert not console.pages.isHidden() and console.selected_room is None
            assert console.pages.currentWidget() is console.batch_panel
            assert not console.batch_panel.send.isEnabled()
            assert console.batch_panel.interactions.isVisible(), 'Emote and gift icons stay visible without selected rooms'
            assert console.rooms.currentItem() is None, 'Unchecked rooms must immediately lose their focus frame'
            assert console.saved_texts.isEnabled(), 'Saved texts can still be managed without recipients'
            count = len(sent)
            console.saved_texts.itemClicked.emit(console.saved_texts.item(0))
            assert len(sent) == count
            position = console.saved_texts.visualItemRect(console.saved_texts.item(0)).center()
            QTest.mouseClick(console.saved_texts.viewport(), Qt.RightButton, pos=position)
            rect = console.saved_texts.visualItemRect(console.saved_texts.item(0))
            QTest.mouseClick(console.saved_texts.viewport(), Qt.LeftButton, pos=rect.topRight() + QPoint(-8, 8))
            assert console.saved_texts.count() == 0 and viewer.context.setting('saved_send_texts') == []
            viewer.panel.add_event({'uname': 'some author', 'text': 'message to save', 'source_room': '2'})
            event = viewer.panel._blocks[-1]
            assert event['source_room'] == '2', 'Keep source room for the merged chat repeat button'
            menu = console.create_chat_toolbar(event)
            assert not menu.repeat_action.isEnabled(), '+1 must be disabled without selected recipients'
            assert viewer.panel.body.contextMenuPolicy() == Qt.DefaultContextMenu
            with patch.object(console, 'create_chat_toolbar', return_value=menu), patch.object(menu, 'exec', return_value=menu.save_action):
                viewer.panel.body.messageClicked.emit(0, QPoint(20, 20))
            assert viewer.context.setting('saved_send_texts') == ['message to save']
            select({'1'})
            menu = console.create_chat_toolbar(event)
            count = len(sent)
            with patch.object(console, 'create_chat_toolbar', return_value=menu), patch.object(menu, 'exec', return_value=menu.repeat_action):
                console.show_chat_actions(event, QPoint(20, 20))
            finish_batch()
            assert sent[count:] == [('1', 'message to save')], '+1 must target selected recipients, not the source'
            assert second.text() == 'draft 2'
            assert first.text() == 'draft 1'
            select({'1', '2'})
            menu = console.create_chat_toolbar(event)
            count = len(sent)
            with patch.object(console, 'create_chat_toolbar', return_value=menu), patch.object(menu, 'exec', return_value=menu.repeat_action):
                console.show_chat_actions(event, QPoint(20, 20))
            finish_batch()
            assert sent[count:] == [('1', 'message to save'), ('2', 'message to save')]
            assert first.text() == 'draft 1' and second.text() == 'draft 2'
            unavailable_menu = console.create_chat_toolbar({**event, 'source_room': 'missing'})
            assert unavailable_menu.repeat_action.isEnabled(), 'The source must not affect selected-target sending'
            unavailable_menu.deleteLater()
            console.saved_texts.clear()
            console.persist_texts()
            select({'2'})
            assert console.pages.currentWidget() is console.stack
            assert second.text() == 'draft 2'
            console.saved_input.setText('draft 2')
            QTest.keyClick(console.saved_input, Qt.Key_Return)
            assert console.saved_texts.count() == 1
            second.setText('direct success')
            single_button.click()
            QTest.qWait(130)
            assert console.statuses['2'] == '✓' and not console.feedback.isVisible()
            QTest.qWait(2550)
            assert '2' not in console.statuses, 'Success check should disappear'
            composer = console.rows['2'].tile._plugin_controls.entries[0].control
            composer.widget.setProperty('fail_send', True)
            second.setText('failed attempt')
            single_button.click()
            QTest.qWait(130)
            assert console.statuses.get('2') != '✓', 'A failed request must not display success'
            composer.widget.setProperty('fail_send', False)
            second.setText('draft 2')
            viewer.rows['2'].alias.setText('changed name')
            assert 'changed name' in console.rooms.currentItem().text()
            viewer.set_settings_visible(False)
            assert console.isHidden()
            viewer.set_settings_visible(True)
            QTest.qWait(50)
            assert console.isVisible() and second.text() == 'draft 2'
            assert viewer.panel.property('joined') is True
            controls = console.rows['2'].tile._plugin_controls
            viewer.remove_room('2')
            assert controls._disposed and '2' not in console.rows
            assert not console.checked_rooms() and not console.pages.isHidden()
            viewer.panel.set_placeholder('')
            for index in range(80):
                viewer.panel.add_message('fixture', f'old section {index}')
            QTest.qWait(20)
            viewer.panel.jump_to_bottom()
            scrollbar = viewer.panel.body.verticalScrollBar()
            old_scroll = scrollbar.value()
            viewer.panel.begin_replay_section()
            viewer.panel.add_event({'text': '回看分段', '_replay_divider': True})
            for index in range(12):
                viewer.panel.add_message('fixture', f'replayed section {index}')
            viewer.panel.end_replay_section()
            if module.motion.enabled():
                assert scrollbar.value() < scrollbar.maximum(), 'Replay should scroll into the new section, not jump'
            QTest.qWait(260)
            assert scrollbar.value() == scrollbar.maximum() and scrollbar.maximum() >= old_scroll
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                viewer.grab().save(str(output))
            viewer.close()
            reopened = module.Viewer(plugins.PluginContext(manager, 'sending_check'), {})
            try:
                assert reopened.send_console.saved_texts.item(0).text() == 'draft 2'
                assert reopened.send_console.saved_texts.isEnabled()
            finally:
                reopened.close()
                reopened.deleteLater()
            return {'passed': True, 'synthetic_sends': sent, 'drafts_preserved': True,
                    'aligned_console': True, 'saved_texts_reopened': True, 'outgoing_real_messages': 0}
        finally:
            manager.unload()
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--platform-source', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(args.host.resolve()), str(root)]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    dll = os.add_dll_directory(str(args.host.resolve())) if sys.platform == 'win32' else None
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        print(check(app, QWidget(), module, args.output))
        if args.platform_source:
            print(check_platform_controls(app, QWidget(), module, args.platform_source))
    if dll:
        dll.close()


if __name__ == '__main__':
    main()
