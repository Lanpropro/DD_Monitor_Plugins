"""Native grouped console controls; no playback, network or real configuration."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from ddm import plugins
    panels = import_module(module.__package__ + '.panels')
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(module.Decoder, 'start', side_effect=AssertionError('No media in UI check')), \
            patch.object(module.Chat, 'start', side_effect=AssertionError('No network in UI check')):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'icon_toolbar'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            viewer.add_room('101', {'alias': 'Machine'})
            viewer.add_room('202', {'alias': '大东彦', 'audible': False})
            viewer.resize(1600, 900)
            viewer.show()
            QTest.qWait(50)
            viewer.align_room_controls()
            first, second = viewer.rows.values()
            assert not first._compact_controls
            assert len(first.control_widgets) == 6
            assert isinstance(viewer.automatic, panels.ConsoleModeToggle)
            assert isinstance(viewer.compare, panels.ConsoleModeToggle)
            assert len(first.group_separators) == 3
            assert first.decrease.parentWidget() is first.offset_box
            assert first.delay.parentWidget() is first.offset_box
            assert first.increase.parentWidget() is first.offset_box
            assert not first.main_button.icon().isNull()
            assert not first.independent.isHidden() and not first.independent.isEnabled()
            assert not first.realign.isHidden() and not first.realign.isEnabled()
            with patch.object(module.QInputDialog, 'getText', return_value=('303', True)):
                viewer.add_room_button.click()
            assert '303' in viewer.rows
            viewer.remove_room('303')
            assert all(not label.isHidden() for label in first.group_labels)
            assert all(label.isHidden() for label in second.group_labels)
            for name in ('audible', 'channel', 'show_chat', 'color_choice', 'delay', 'more'):
                a, b = getattr(first, name), getattr(second, name)
                assert a.mapTo(first, QPoint()).x() == b.mapTo(second, QPoint()).x(), name
            for button in (first.floating_button, first.audible, first.show_chat, first.more, first.realign):
                assert isinstance(button, panels.ConsoleIconButton)
                assert not button.text() and button.toolTip() and button.accessibleName()
                assert button.focusPolicy() == Qt.StrongFocus
            before = first.audible.isChecked()
            QTest.mouseClick(first.audible, Qt.LeftButton)
            assert first.audible.isChecked() != before
            assert first.audible.toolTip() == '开启声音'
            assert viewer.picture.muted
            first.audible.click()
            assert not viewer.picture.muted
            first.show_chat.click()
            assert not first.preferences()['show_chat']
            assert first.show_chat.accessibleName() == '开启弹幕'
            first.show_chat.click()
            first.channel.setCurrentIndex(first.channel.findData(3))
            assert first.preferences()['audio_channel'] == 3
            first.color_choice.setCurrentIndex(2)
            assert first.preferences()['color'] == '#a78bfa'
            first.delay.setValue(.25)
            first.increase.click()
            assert first.delay.value() == .35
            with patch.object(viewer, 'choose_crop') as choose:
                first.floating_button.click()
                choose.assert_called_once_with(first, floating=True)
            first.alias.setText('Updated streamer with a long name')
            assert first.name_label.toolTip() == first.label()
            assert first.name_label.text() != first.label()
            first.alias.setText('Machine')
            # The same native combo keeps its full labels and keyboard behavior.
            assert first.channel.itemText(1) == '仅左输出'
            first.channel.view().window().setAttribute(Qt.WA_DontShowOnScreen)
            first.channel.showPopup()
            assert first.channel.view().isVisible()
            first.channel.hidePopup()
            QTest.keyClick(first.channel, Qt.Key_Down)
            assert first.channel.currentData() == 4
            for width in (1100, 900, 1600):
                viewer.resize(width, 900)
                QTest.qWait(35)
                viewer.align_room_controls()
                app.processEvents()
                expected = width < 1200
                assert first._compact_controls == expected, (width, first.width(), viewer.controls.viewport().width())
                assert viewer.controls.horizontalScrollBar().maximum() == 0, (width, first.minimumSizeHint())
            with patch.object(viewer, 'align_room_controls', wraps=viewer.align_room_controls) as align:
                for _ in range(30):
                    viewer.sync_picture(refresh_metadata=False)
                assert align.call_count == 0, 'Playback updates must not relayout controls'
            with patch.object(viewer.body_split, 'setSizes', wraps=viewer.body_split.setSizes) as sizes:
                viewer.console_content_height = -1
                viewer.align_room_controls()
                assert sizes.call_count == 0, 'Do not resize the splitter inside a layout callback'
                app.processEvents()
                assert sizes.call_count >= 1, 'Apply the requested content height on the next event turn'
            if output:
                output = Path(output)
                output.parent.mkdir(parents=True, exist_ok=True)
                host.setAttribute(Qt.WA_DontShowOnScreen)
                host.resize(1600, 1000)
                viewer.embed(host)
                host.show()
                viewer.show()
                viewer.automatic.setChecked(True)
                viewer.compare.setChecked(False)
                viewer.console_content_height = None
                viewer.align_room_controls()
                viewer.notice.setText('主画面：Machine · 自动对齐已开启')
                viewer.audio_status.setText('声音已接管')
                QTest.qWait(30)
                viewer.settings_panel.grab().save(str(output))
            menu = second.more_menu()
            assert [action.text() for action in menu.actions() if not action.isSeparator()] == ['修改主播名称…', '移除此路']
            next(action for action in menu.actions() if action.text() == '移除此路').trigger()
            assert '202' in viewer.rows, 'Removal must wait until the menu callback returns'
            app.processEvents()
            assert list(viewer.rows) == ['101']
            assert not viewer.removal_notice.isHidden()
            viewer.undo_remove_room()
            assert list(viewer.rows) == ['101', '202']
            assert viewer.rows['202'].label() == '大东彦'
            assert not viewer.rows['202'].audible.isChecked()
            assert viewer.removal_notice.isHidden()
            viewer.remove_room_with_undo('202')
            viewer.removal_timer.timeout.emit()
            assert viewer.removed_room is None and viewer.removal_notice.isHidden()
            # Match the reported case: one source in a very wide embedded console.
            if output:
                host.resize(2500, 1000)
            else:
                viewer.resize(2500, 1000)
            QTest.qWait(30)
            viewer.align_room_controls()
            assert first.avatar.mapTo(first.control_widgets[0], QPoint()).x() <= 2
            name_x = first.name_label.mapTo(first, QPoint()).x()
            avatar_right = first.avatar.mapTo(first, QPoint()).x() + first.avatar.width()
            role_x = first.main_button.mapTo(first, QPoint()).x()
            assert 0 <= name_x - avatar_right <= 12
            assert 0 <= role_x - name_x - first.name_label.width() <= 12
            assert abs(viewer.settings_panel.height() - viewer.console_content_height) < 24
            if output:
                viewer.settings_panel.grab().save(str(output.with_name('single-wide.png')))
            return dict(passed=True, native_controls=True, column_alignment=True,
                        responsive=True, toggles=True, persisted_values=True,
                        crop_action=True, deferred_remove=True, undo_remove=True,
                        compact_identity=True, shared_offset=True, prototype_modes=True,
                        no_playback_relayout=True,
                        media_playback=False)
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    host = QWidget()
    try:
        print(check(app, host, module, args.output))
    finally:
        host.close()
        app.sendPostedEvents(None, 52)
        app.shutdown()


if __name__ == '__main__':
    main()
