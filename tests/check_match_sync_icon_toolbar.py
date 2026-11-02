"""Native grouped console controls; no playback, network or real configuration."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QEnterEvent, QMouseEvent
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
            viewer.resize(2000, 900)
            viewer.show()
            QTest.qWait(50)
            viewer.align_room_controls()
            first, second = viewer.rows.values()
            assert not first._compact_controls
            assert len(first.control_widgets) == 6
            assert isinstance(viewer.automatic, panels.ConsoleModeToggle)
            assert isinstance(viewer.compare, panels.ConsoleModeToggle)
            assert len(first.group_separators) == 3
            assert not hasattr(viewer, 'add_room_button')
            assert first.main_button.styleSheet() == second.main_button.styleSheet()
            assert first.main_button.size() == second.main_button.size()
            assert first.main_button.mapTo(first, QPoint()).x() == second.main_button.mapTo(second, QPoint()).x()
            second.join.show()
            app.processEvents()
            assert second.join.parentWidget() is second.main_button.parentWidget()
            assert second.join.y() == second.main_button.y()
            assert 0 <= second.join.x() - second.main_button.geometry().right() <= 12
            with patch.object(viewer, 'confirm_alignment') as confirm:
                second.join.click()
                confirm.assert_called_once_with('202')
            for button in (first.main_button, second.join, first.decrease, first.increase,
                           first.volume, first.channel, first.color_choice):
                assert button.cursor().shape() == Qt.PointingHandCursor
                assert button.testAttribute(Qt.WA_Hover)
            assert first.delay.lineEdit().cursor().shape() == Qt.IBeamCursor
            with patch.object(first.audible, 'underMouse', return_value=False):
                normal = first.audible.grab().toImage()
            with patch.object(first.audible, 'underMouse', return_value=True):
                assert first.audible.grab().toImage() != normal, 'Checked icons must still give hover feedback'
            with patch.object(viewer.automatic, 'underMouse', return_value=False):
                normal = viewer.automatic.grab().toImage()
            with patch.object(viewer.automatic, 'underMouse', return_value=True):
                assert viewer.automatic.grab().toImage() != normal
            first.main_button.setAttribute(Qt.WA_UnderMouse, False)
            normal = first.main_button.grab().toImage()
            first.main_button.setAttribute(Qt.WA_UnderMouse, True)
            app.sendEvent(first.main_button, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
            app.sendEvent(first.main_button, QMouseEvent(QEvent.MouseMove, QPointF(5, 5), QPointF(5, 5),
                                                       Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            QTest.qWait(40)
            assert first.main_button.grab().toImage() != normal
            first.main_button.setAttribute(Qt.WA_UnderMouse, False)
            app.sendEvent(first.main_button, QEvent(QEvent.Leave))
            assert first.decrease.parentWidget() is first.offset_box
            assert first.delay.parentWidget() is first.offset_box
            assert first.increase.parentWidget() is first.offset_box
            assert not first.main_button.icon().isNull()
            assert not first.independent.isHidden() and not first.independent.isEnabled()
            assert not first.realign.isHidden() and not first.realign.isEnabled()
            assert first.independent.cursor().shape() == Qt.ArrowCursor
            surfaces = [panels.SettingsPanel(viewer, name) for name in ('MatchSyncSettings', 'MatchSyncSendConsole')]
            for surface in surfaces:
                surface.resize(120, 140)
            images = [surface.grab().toImage() for surface in surfaces]
            assert images[0].pixelColor(0, 70) == images[1].pixelColor(0, 70), 'Use the adjacent console edge treatment'
            for surface in surfaces:
                surface.close()
                surface.deleteLater()
            assert all(not label.isHidden() for label in first.group_labels)
            assert all(label.isHidden() for label in second.group_labels)
            for name in ('audible', 'channel', 'show_chat', 'color_choice', 'delay', 'remove_button', 'hint_fast', 'hint_slow'):
                a, b = getattr(first, name), getattr(second, name)
                assert a.mapTo(first, QPoint()).x() == b.mapTo(second, QPoint()).x(), name
            assert isinstance(first.audible, module.VolumeButton)
            assert isinstance(first.show_chat, module.DanmakuToggleButton)
            assert isinstance(first.realign, module.RefreshButton)
            for button in (first.floating_button, first.audible, first.show_chat, first.remove_button, first.realign, first.hint_fast, first.hint_slow):
                assert button.toolTip() and button.accessibleName()
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
            for width in (1100, 900, 2000):
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
            app.processEvents()
            height = viewer.settings_panel.height()
            splitter = viewer.body_split.sizes()
            viewer.begin_row_drag('202')
            viewer.align_room_controls()
            viewer.fit_console_height()
            QTest.qWait(30)
            assert viewer.settings_panel.height() == height
            assert viewer.body_split.sizes() == splitter
            assert not viewer.console_size_timer.isActive()
            viewer.end_row_drag()
            QTest.qWait(30)
            assert viewer.settings_panel.height() == height
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
                second.join.show()
                QTest.qWait(30)
                viewer.settings_panel.grab().save(str(output))
            assert not hasattr(second, 'more_menu') and not hasattr(second, 'rename')
            second.remove_button.click()
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
            QTest.qWait(20)  # Content sizing is applied outside the layout callback.
            assert abs(viewer.settings_panel.height() - viewer.console_content_height) < 24, (
                viewer.settings_panel.height(), viewer.console_content_height, viewer.body_split.sizes(),
                viewer.settings_panel.minimumSizeHint().height(), viewer.settings_panel.maximumHeight(),
                viewer.picture.minimumSizeHint().height(), viewer.comparison_panel.minimumSizeHint().height(),
                viewer.console_size_timer.isActive())
            if output:
                viewer.settings_panel.grab().save(str(output.with_name('single-wide.png')))
            return dict(passed=True, native_controls=True, column_alignment=True,
                        responsive=True, toggles=True, persisted_values=True,
                        crop_action=True, direct_remove=True, undo_remove=True,
                        compact_identity=True, shared_offset=True, prototype_modes=True,
                        inline_confirmation=True, role_alignment=True, drag_height_fixed=True,
                        adjacent_console_edge=True, interactive_cursors=True,
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
