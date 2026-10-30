"""Console geometry, room drafts and explicit multi-send; no real accounts."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
from unittest.mock import patch


def check_platform_controls(app, host, module, source):
    """Use the maintained domestic composers, with no credentials or requests."""
    from PySide6.QtWidgets import QPushButton
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
                assert send.width() == 60, kind
                gift = controls.applicable()[0].control.widget.gifts
                assert 'QPushButton#TileCtrl:hover' in gift.styleSheet(), kind
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
            self.edit.clear()
            self.edit.setReadOnly(False)

    with tempfile.TemporaryDirectory() as directory:
        host.settings = {}
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        manager.register_tile_control('fixture', 'send', Composer)
        viewer = module.Viewer(plugins.PluginContext(manager, 'sending_check'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen, host.testAttribute(Qt.WA_DontShowOnScreen))
        try:
            for key in ('1', '2', '3'):
                viewer.add_room(key, {'alias': '主播' + key})
            viewer.resize(1320, 880)
            viewer.show()
            QTest.qWait(80)
            console = viewer.send_console
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
            assert isinstance(console, module.SettingsPanel), 'Keep the existing console size and visibility contract'
            panel_height = viewer.panel.height()
            console_height = console.height()
            console.minimize.click()
            QTest.qWait(40)
            assert console.isHidden() and console.restore.isVisible()
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
            assert console.context.text().startswith('发送给')
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
                with patch.object(module.theme, 'is_light', return_value=True):
                    console.refresh_design()
                    QTest.qWait(30)
                    console.grab().save(str(output.with_name(output.stem + '-light.png')))
                console.refresh_design()
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
            QTest.qWait(40)
            assert (dialog.edit.mapTo(console, QPoint()).x(), dialog.edit.mapTo(console, QPoint()).y(),
                    dialog.edit.width(), dialog.edit.height()) == single_edit_geometry
            assert (dialog.send.mapTo(console, QPoint()).x(), dialog.send.mapTo(console, QPoint()).y(),
                    dialog.send.width(), dialog.send.height()) == single_button_geometry
            dialog.edit.setText('shared')
            dialog.start()
            QTest.qWait(250)
            assert sent == [('1', 'shared'), ('2', 'shared'), ('3', 'shared')]
            assert first.text() == 'draft 1' and second.text() == 'draft 2'
            assert not dialog.timer.isActive()
            assert all(console.statuses[key] == '已处理' for key in ('1', '2', '3'))
            assert all(console.rooms.item(index).data(Qt.CheckStateRole) == Qt.Checked.value
                       for index in range(console.rooms.count())), 'Visual checks must match the actual send targets'
            assert '已处理' in console.feedback.text()
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
            assert console.context.text() == '同文发送'
            select({'2'})
            second.setText('draft 2')
            console.saved_input.setText('saved message')
            console.save_text.click()
            assert not console.saved_input.text()
            console.saved_input.setText('saved message')
            console.save_text.click()
            assert console.saved_texts.count() == 1, 'Do not duplicate saved texts'
            assert viewer.context.setting('saved_send_texts') == ['saved message']
            if output:
                console.grab().save(str(output.with_name(output.stem + '-stored-text.png')))
            before_saved_send = len(sent)
            QTest.mouseClick(console.saved_texts.viewport(), Qt.LeftButton,
                             pos=console.saved_texts.visualItemRect(console.saved_texts.item(0)).center())
            QTest.qWait(130)
            assert sent[before_saved_send:] == [('2', 'saved message')]
            assert second.text() == 'draft 2', 'Quick send must preserve the independent draft'
            select({'1', '2'})
            console.saved_texts.itemClicked.emit(console.saved_texts.item(0))
            QTest.qWait(200)
            assert sent[-2:] == [('1', 'saved message'), ('2', 'saved message')]
            assert first.text() == 'draft 1' and second.text() == 'draft 2'
            second.setText('draft 2')
            select(set())
            assert console.pages.isHidden() and console.selected_room is None
            assert console.saved_texts.isEnabled(), 'Saved texts can still be managed without recipients'
            count = len(sent)
            console.saved_texts.itemClicked.emit(console.saved_texts.item(0))
            assert len(sent) == count
            position = console.saved_texts.visualItemRect(console.saved_texts.item(0)).center()
            with patch.object(import_module(module.SendConsole.__module__), 'QMenu') as menu_type:
                menu = menu_type.return_value
                menu.exec.return_value = menu.addAction.return_value
                console.saved_menu(position)
            assert console.saved_texts.count() == 0 and viewer.context.setting('saved_send_texts') == []
            select({'2'})
            assert console.pages.currentWidget() is console.stack
            assert second.text() == 'draft 2'
            console.saved_input.setText('draft 2')
            QTest.keyClick(console.saved_input, Qt.Key_Return)
            assert console.saved_texts.count() == 1
            viewer.rows['2'].alias.setText('changed name')
            assert 'changed name' in console.rooms.currentItem().text()
            viewer.set_settings_visible(False)
            assert console.isHidden()
            viewer.set_settings_visible(True)
            QTest.qWait(50)
            assert console.isVisible() and second.text() == 'draft 2'
            controls = console.rows['2'].tile._plugin_controls
            viewer.remove_room('2')
            assert controls._disposed and '2' not in console.rows
            assert not console.checked_rooms() and console.pages.isHidden()
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
