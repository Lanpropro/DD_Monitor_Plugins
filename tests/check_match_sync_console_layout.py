"""Console reorder/height and retained audio controls; no media or real messages."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
    from PySide6.QtGui import QDragMoveEvent
    from PySide6.QtTest import QTest
    from ddm import plugins
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(module.Decoder, 'start', side_effect=AssertionError('No media')), \
            patch.object(module.Chat, 'start', side_effect=AssertionError('No network')):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'console_layout'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            viewer.resize(1600, 950)
            viewer.show()
            QTest.qWait(40)
            viewer.align_room_controls()
            app.processEvents()
            empty_height = viewer.console_content_height
            viewer.add_room('101', {'alias': 'Machine', 'audio_channel': 3})
            viewer.add_room('202', {'alias': 'Second'})
            QTest.qWait(40)
            viewer.align_room_controls()
            app.processEvents()
            first, second = viewer.rows.values()
            assert first.channel.currentData() == 3, 'Keep existing manual preferences'
            assert second.channel.currentData() == 0, 'New sources use original audio'
            first.audible.setChecked(True)
            # WA_DontShowOnScreen has no stable native global coordinates on Windows.
            # Use one virtual pointer inside the picture for direct shortcut dispatch.
            with patch.object(module.QCursor, 'pos', return_value=QPoint(10, 10)), \
                    patch.object(viewer.picture, 'mapFromGlobal', return_value=viewer.picture.rect().center()):
                viewer._handle_shortcut('audio_cycle')
                assert first.channel.currentData() == 4
                viewer._handle_shortcut('audio_cycle')
                assert not first.audible.isChecked()
                viewer._handle_shortcut('audio_cycle')
                assert first.audible.isChecked() and first.channel.currentData() == 0
            first.channel.setCurrentIndex(first.channel.findData(3))
            before = [row.channel.currentData() for row in viewer.rows.values()]
            viewer.reorder_room('202', '101')
            viewer.main.setCurrentIndex(viewer.main.findData('202'))
            viewer.sync_picture(refresh_metadata=False)
            assert [first.channel.currentData(), second.channel.currentData()] == before
            viewer.reorder_room('101', '202')
            viewer.main.setCurrentIndex(viewer.main.findData('101'))
            viewer.align_room_controls()
            QTest.qWait(30)
            height, sizes = viewer.settings_panel.height(), viewer.body_split.sizes()
            mime = QMimeData()
            mime.setData(module.ROW_MIME, b'202')
            with patch.object(module.motion, 'enabled', return_value=True):
                viewer.begin_row_drag('202')
                point = QPoint(10, 0)
                # Native OLE events may not expose source(), and may hit child controls.
                for target in (first.floating_button, first.channel, first.delay.lineEdit()):
                    event = QDragMoveEvent(target.mapFrom(viewer.controls.widget(), point),
                                           Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
                    assert event.source() is None
                    assert viewer.eventFilter(target, event) and event.isAccepted()
                assert viewer.row_preview == ['202', '101']
                animation = viewer.row_animations['101']
                assert animation.duration() == 150
                animation.setCurrentTime(60)
                halfway = first.pos()
                assert animation.startValue().y() < halfway.y() < animation.endValue().y()
                viewer.preview_row_drag(QPoint(10, viewer.row_slots['101'].bottom() + 1))
                reverse = viewer.row_animations['101']
                assert reverse.startValue() == halfway, 'Retarget from the visible position'
                viewer.align_room_controls()
                viewer.fit_console_height()
                assert viewer.settings_panel.height() == height and viewer.body_split.sizes() == sizes
                viewer.end_row_drag()
                assert not viewer.row_animations and viewer.rows_layout.isEnabled()
            with patch.object(module.motion, 'enabled', return_value=False):
                viewer.begin_row_drag('202')
                viewer.preview_row_drag(point)
                assert first.y() == viewer.row_slots['202'].height() + viewer.rows_layout.spacing()
                assert not viewer.row_animations
                viewer.finish_row_drag(point)
                assert list(viewer.rows) == ['202', '101']
            viewer.align_room_controls()
            QTest.qWait(30)
            two_height = viewer.console_content_height
            assert viewer.settings_panel.height() == height
            console = viewer.send_console
            assert console.toolbar.height() == viewer.console_toolbar.height()
            assert console.layout().contentsMargins().left() == 1
            assert console.body_layout.contentsMargins().left() in (12, 16)
            assert 'SendToolbar' in console.styleSheet()
            assert console.minimize.size() == viewer.minimize_settings.size()
            console.sync_height()
            assert console.height() == viewer.settings_panel.height()
            if output:
                output = Path(output)
                output.parent.mkdir(parents=True, exist_ok=True)
                viewer.grab().save(str(output))
            viewer.remove_room('202')
            viewer.align_room_controls()
            QTest.qWait(20)
            assert viewer.console_content_height == two_height, (viewer.console_content_height, two_height)
            viewer.remove_room('101')
            viewer.align_room_controls()
            QTest.qWait(20)
            assert viewer.console_content_height == two_height
            assert abs(empty_height - two_height) <= 4, (empty_height, two_height)
            return dict(passed=True, source_audio_preserved=True, audio_shortcut_preserved=True,
                        drag_child_events=True, interrupted_animation=True, reduced_motion=True,
                        stable_two_row_height=True, shared_send_toolbar=True, media_playback=False)
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
