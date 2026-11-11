"""Fullscreen reparents the real picture; deferred console work stays safe."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QImage, QPainter, QRegion
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
    from ddm import fullscreen_cursor, plugins
    host.settings, host.rooms = {'fullscreen_auto_hide_controls': True}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'fullscreen_console'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        viewer.picture.bottom.setAttribute(Qt.WA_DontShowOnScreen)
        show_fullscreen = QDialog.showFullScreen
        def hidden_fullscreen(dialog):
            dialog.setAttribute(Qt.WA_DontShowOnScreen)
            show_fullscreen(dialog)
        try:
            viewer.resize(1400, 900)
            viewer.show()
            viewer.add_room('1', {'alias': 'fixture'})
            app.processEvents()
            assert viewer.body_split.count() == 3
            with patch.object(module.QDialog, 'showFullScreen', hidden_fullscreen), \
                    patch.object(module.picture_control, 'mark_fullscreen', return_value=True):
                for _ in range(3):
                    viewer.console_size_timer.start(0)
                    viewer._fullscreen_picture()
                    dialog = viewer.fullscreen_dialog
                    assert viewer.body_split.count() == 2 and viewer.picture.parentWidget() is dialog
                    assert viewer.picture.property('fullscreen'), 'The fullscreen picture needs the host presentation state'
                    assert viewer.picture.bottom.property('fullscreen'), 'Use the transparent fullscreen bar'
                    assert viewer.picture.video.height() == viewer.picture.height(), 'The bar must overlay the full video'
                    cursor = dialog._fullscreen_cursor
                    point = viewer.picture.video.mapToGlobal(viewer.picture.video.rect().center())
                    with patch.object(dialog, 'isActiveWindow', return_value=True), \
                            patch.object(fullscreen_cursor.QCursor, 'pos', return_value=point) as position, \
                            patch.object(fullscreen_cursor.time, 'monotonic', return_value=104.), \
                            patch.object(fullscreen_cursor, 'set_native_cursor'), \
                            patch.object(module.motion, 'enabled', return_value=False), \
                            patch.object(QApplication, 'activePopupWidget', return_value=None), \
                            patch.object(QApplication, 'activeModalWidget', return_value=None), \
                            patch.object(QApplication, 'mouseButtons', return_value=Qt.NoButton):
                        cursor._position, cursor._last_move = point, 100.
                        cursor.update_cursor()
                        assert viewer.picture.controls.isHidden() and viewer.picture.bottom.isHidden()
                        assert viewer.picture._fullscreen_controls_hidden
                        position.return_value = point + QPoint(10, 0)
                        cursor.update_cursor()
                        assert not viewer.picture.controls.isHidden() and not viewer.picture.bottom.isHidden()
                        assert not cursor.hidden and not viewer.picture._fullscreen_controls_hidden
                        host.settings['fullscreen_auto_hide_controls'] = False
                        cursor._last_move = 100.
                        cursor.update_cursor()
                        assert viewer.picture.controls.isHidden() and not viewer.picture.bottom.isHidden()
                        host.settings['fullscreen_auto_hide_controls'] = True
                        position.return_value = point + QPoint(20, 0)
                        cursor.update_cursor()
                    if app.platformName() == 'windows':
                        bar = viewer.picture.bottom
                        assert bar.testAttribute(Qt.WA_TranslucentBackground) and bar.qt_composited
                        image = QImage(bar.size(), QImage.Format_ARGB32_Premultiplied)
                        image.fill(Qt.transparent)
                        painter = QPainter(image)
                        bar.render(painter, QPoint(), QRegion(), QWidget.DrawChildren)
                        painter.end()
                        assert image.pixelColor(5, 0).alpha() < 10
                        assert 100 < image.pixelColor(5, image.height() - 1).alpha() < 200
                        if output:
                            Path(output).parent.mkdir(parents=True, exist_ok=True)
                            image.save(str(output))
                    viewer.fit_console_height()
                    assert not viewer.console_size_timer.isActive(), 'Cancel pending fit before reparenting'
                    with patch.object(viewer.body_split, 'setSizes', side_effect=AssertionError('Used three-pane layout in fullscreen')):
                        viewer.fit_console_height()
                        viewer.set_comparison_visible(True)
                        viewer.set_comparison_visible(False)
                        # New resize callbacks may still arrive after fullscreen began.
                        viewer.console_size_timer.start(0)
                        app.processEvents()
                    dialog.close()
                    assert viewer.fullscreen_dialog is None
                    assert not viewer.picture.property('fullscreen') and not viewer.picture.bottom.property('fullscreen')
                    assert not viewer.picture._fullscreen_controls_hidden
                    assert viewer.body_split.count() == 3 and viewer.body_split.widget(0) is viewer.picture
                    viewer.console_content_height = viewer.settings_height
                    with patch.object(viewer.body_split, 'setSizes', wraps=viewer.body_split.setSizes) as sizes:
                        viewer.fit_console_height()
                        assert sizes.call_count == 1, 'Normal console fitting must resume'
                    app.processEvents()
            return dict(passed=True, fullscreen_cycles=3, two_pane_fit_safe=True,
                        comparison_toggle_safe=True, pending_fit_cancelled=True,
                        queued_fit_safe=True, restored_three_pane_fit=True,
                        idle_bars_hidden=True, pointer_restores_controls=True,
                        fullscreen_presentation_restored=True, native_alpha_checked=app.platformName() == 'windows',
                        auto_hide_setting_preserved=True,
                        media_playback=False)
        finally:
            viewer.console_size_timer.stop()
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module, args.output))


if __name__ == '__main__':
    main()
