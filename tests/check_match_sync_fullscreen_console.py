"""Fullscreen reparents the real picture; deferred console work stays safe."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog
    from ddm import plugins
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'fullscreen_console'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
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
                    patch.object(module, 'mark_fullscreen', return_value=True):
                for _ in range(3):
                    viewer.console_size_timer.start(0)
                    viewer._fullscreen_picture()
                    dialog = viewer.fullscreen_dialog
                    assert viewer.body_split.count() == 2 and viewer.picture.parentWidget() is dialog
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
                    assert viewer.body_split.count() == 3 and viewer.body_split.widget(0) is viewer.picture
                    viewer.console_content_height = viewer.settings_height
                    with patch.object(viewer.body_split, 'setSizes', wraps=viewer.body_split.setSizes) as sizes:
                        viewer.fit_console_height()
                        assert sizes.call_count == 1, 'Normal console fitting must resume'
                    app.processEvents()
            return dict(passed=True, fullscreen_cycles=3, two_pane_fit_safe=True,
                        comparison_toggle_safe=True, pending_fit_cancelled=True,
                        queued_fit_safe=True, restored_three_pane_fit=True,
                        media_playback=False)
        finally:
            viewer.console_size_timer.stop()
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
