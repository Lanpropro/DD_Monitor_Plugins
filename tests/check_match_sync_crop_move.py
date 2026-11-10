"""Caption dragging must keep Qt dispatch available; offline functional checks."""
import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QCursor, QImage, QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
    from ddm import plugins

    host.settings, host.rooms = {}, []
    host.setAttribute(Qt.WA_DontShowOnScreen)
    with tempfile.TemporaryDirectory() as directory:
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        viewer = module.Viewer(plugins.PluginContext(manager, 'crop_move_check'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        viewer.add_room('1')
        image = QImage(320, 180, QImage.Format_RGB32)
        image.fill(Qt.red)
        viewer.canvas.set_frame((1., image))
        viewer.choose_crop(viewer.rows['1'])
        dialog = viewer.crop_dialog
        assert dialog._caption_move_timer.parent() is dialog
        assert dialog._caption_move_timer.interval() == 16 and not dialog._caption_move_timer.isActive()
        app.processEvents()
        dialog.move(200, 200)
        initial = dialog.pos()
        message = wintypes.MSG()
        message.message, message.wParam = 0x00a1, 2  # WM_NCLBUTTONDOWN, HTCAPTION

        def send_mouse(kind, position, button, buttons):
            event = QMouseEvent(kind, QPointF(20, -10), QPointF(position), button, buttons, Qt.NoModifier)
            app.sendEvent(dialog, event)
            return event

        try:
            with patch.object(QDialog, 'nativeEvent', return_value=(False, 77)) as base, \
                    patch.object(QApplication, 'platformName', return_value='windows'), \
                    patch.object(QCursor, 'pos', return_value=QPoint(300, 220)), \
                    patch.object(dialog, 'grabMouse') as grab, patch.object(dialog, 'releaseMouse') as release:
                result = dialog.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
                assert result == (True, 0), 'Caption presses must not enter the native moving loop'
                base.assert_not_called()
                grab.assert_called_once()
                with patch.object(dialog, 'move', wraps=dialog.move) as moved:
                    for index in range(200):
                        send_mouse(QEvent.MouseMove, QPoint(300+index, 220), Qt.NoButton, Qt.LeftButton)
                    assert moved.call_count == 0, 'Mouse bursts must wait for one scheduled position update'
                    assert dialog._caption_move_timer.isActive()
                    dialog._caption_move_timer.timeout.emit()
                    assert moved.call_count == 1 and dialog.pos() == initial+QPoint(199, 0)
                    assert not dialog._caption_move_timer.isActive()
                progressed = []
                for point in (QPoint(330, 240), QPoint(280, 210), QPoint(-100, -50)):
                    QTimer.singleShot(0, lambda: progressed.append(True))
                    event = send_mouse(QEvent.MouseMove, point, Qt.NoButton, Qt.LeftButton)
                    dialog._caption_move_timer.timeout.emit()
                    assert event.isAccepted() and dialog.pos() == initial + point-QPoint(300, 220)
                    app.processEvents()
                assert len(progressed) == 3, 'Qt events continue between caption moves'
                send_mouse(QEvent.MouseMove, QPoint(-90, -40), Qt.NoButton, Qt.LeftButton)
                event = send_mouse(QEvent.MouseButtonRelease, QPoint(-70, -30), Qt.LeftButton, Qt.NoButton)
                assert event.isAccepted() and dialog._caption_drag is None
                assert dialog.pos() == initial+QPoint(-370, -250), 'Release commits its own final global position'
                assert not dialog._caption_move_timer.isActive() and dialog._caption_pending is None
                release.assert_called_once()
                landed = dialog.pos()

                dialog.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
                send_mouse(QEvent.MouseMove, QPoint(350, 240), Qt.NoButton, Qt.LeftButton)
                app.sendEvent(dialog, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
                assert dialog.pos() == landed and dialog.isVisible() and dialog._caption_drag is None
                assert viewer.crop_dialog is dialog, 'Escape during a move cancels only the move'

                dialog.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
                send_mouse(QEvent.MouseMove, QPoint(400, 300), Qt.NoButton, Qt.LeftButton)
                app.sendEvent(dialog, QEvent(QEvent.UngrabMouse))
                assert dialog._caption_drag is None, 'Capture loss must clear drag state'
                assert not dialog._caption_move_timer.isActive() and dialog._caption_pending is None
                dialog._caption_move_timer.timeout.emit()
                assert dialog.pos() == landed

                # Resize borders, double clicks and system menu messages retain Qt behavior.
                for code, hit in ((0x00a1, 10), (0x00a3, 2), (0x0112, 0xf100)):
                    base.reset_mock()
                    message.message, message.wParam = code, hit
                    assert dialog.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message)) == (False, 77)
                    base.assert_called_once()
                message.message, message.wParam = 0x00a1, 2
                dialog.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
                send_mouse(QEvent.MouseMove, QPoint(500, 500), Qt.NoButton, Qt.LeftButton)
                dialog.reject()
                assert dialog._caption_drag is None and viewer.crop_dialog is None
                assert not dialog._caption_move_timer.isActive() and dialog._caption_pending is None
                assert dialog.pos() == landed, 'Closing must discard the pending move'
                assert release.call_count == 3  # Release, Escape, then hide; UngrabMouse already lost capture.
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            return dict(passed=True, native_caption_consumed=True, qt_event_progress=True,
                        global_position_and_negative_coordinates=True, escape_restores=True,
                        capture_loss_cleanup=True, close_releases_mouse=True,
                        mouse_burst_200_to_1=True, final_release_position=True,
                        pending_timer_only=True,
                        native_resize_and_system_controls_preserved=True,
                        actual_native_drag_performed=False, performance_claim=False)
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
