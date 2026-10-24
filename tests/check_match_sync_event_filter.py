"""Unrelated settings events must not inspect match controls; retain routed input."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(args.host.resolve())]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from ddm import plugins
    from plugins.match_sync import viewer as module
    app = QApplication([])
    host = QWidget()
    host.settings = {}
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'event_filter'), {})
        try:
            watched = QObject()
            with patch.object(viewer.controls, 'widget', side_effect=AssertionError('Unrelated event inspected controls')):
                for kind in (QEvent.Paint, QEvent.Timer, QEvent.Polish, QEvent.LayoutRequest,
                             QEvent.MetaCall, QEvent.UpdateRequest, QEvent.EnabledChange):
                    assert not viewer.eventFilter(watched, QEvent(kind))
            viewer.add_room('1', {'alias': '1'})
            with patch.object(viewer, 'show_comparison') as compare:
                viewer.eventFilter(viewer.rows['1'].delay, QEvent(QEvent.FocusIn))
                compare.assert_called_once_with('1')
            with patch.object(module.QTimer, 'singleShot') as schedule:
                viewer.eventFilter(viewer.picture.bottom, QEvent(QEvent.Resize))
                schedule.assert_called_once_with(0, viewer._layout_restore_control)
            with patch.object(viewer, '_shortcut_action', return_value='focus'), \
                    patch.object(viewer, '_handle_shortcut') as handle:
                override = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_F, Qt.NoModifier)
                assert viewer.eventFilter(viewer, override) and override.isAccepted()
                handle.assert_not_called()
                key = QKeyEvent(QEvent.KeyPress, Qt.Key_F, Qt.NoModifier)
                assert viewer.eventFilter(viewer, key) and key.isAccepted()
                handle.assert_called_once_with('focus')
            viewer.hidden_host_widgets[host] = True
            with patch.object(host, 'hide') as hide:
                viewer.eventFilter(host, QEvent(QEvent.Show))
                hide.assert_called_once()
        finally:
            viewer.close()
            host.close()
            app.processEvents()
            app.shutdown()
    print('PASS: settings paint/layout events skip match controls; keyboard, focus, resize and hidden-host routing remain intact')


if __name__ == '__main__':
    main()
