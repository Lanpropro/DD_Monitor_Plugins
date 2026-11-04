"""Check the first sidebar paint with the plugin entry; no playback or accounts."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QEvent, QObject, QRect, Qt
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
    from ddm import theme
    from ddm.widgets import Sidebar
    from plugins.match_sync.plugin import EntryButton

    app = QApplication([])
    host = QWidget()
    host.setAttribute(Qt.WA_DontShowOnScreen)
    host.setStyleSheet(theme.qss())
    sidebar = Sidebar([], auto_compact=False)
    QHBoxLayout(host).addWidget(sidebar)
    host.resize(900, 700)
    action = QAction('比赛二路', host)
    entry = EntryButton(sidebar, action)
    workspace = getattr(sidebar, 'workspace_controls', None)
    if workspace is not None:
        workspace.add('sidebar-test', 'entry', entry, 20)
    frames = []

    class PaintRecorder(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Paint and not sidebar.collapsed:
                frames.append((entry.isVisible(), sidebar.layout_button.isVisible(),
                               entry.geometry().getRect(), sidebar.layout_button.geometry().getRect()))
            return False

    recorder = PaintRecorder(sidebar)
    sidebar.installEventFilter(recorder)
    def settle():
        for _ in range(8):
            app.processEvents()

    host.show()
    settle()
    try:
        # While other tool buttons are temporarily hidden by a layout change,
        # an inactive entry must retain its normal width instead of filling the row.
        normal_width = entry.sizeHint().width()
        sidebar.layout_button.hide()
        sidebar.settings_button.hide()
        sidebar.tool_row.layout().activate()
        if workspace is None:
            assert entry.width() <= normal_width, ('Inactive entry stretched', entry.width(), normal_width)
        sidebar.layout_button.show()
        sidebar.settings_button.show()
        settle()
        for active in (False, True, False):
            entry.set_mode(active)
            entry._stretch_finished()
            settle()
            for _ in range(3):
                sidebar.set_collapsed(True)
                settle()
                assert entry.isVisible() == (workspace is not None)
                frames.clear()
                sidebar.set_collapsed(False)
                assert frames, 'The host must still paint the sidebar synchronously'
                first = frames[0]
                assert first[:2] == (True, workspace is not None or not active), ('Incomplete first frame', active, first)
                if not active and workspace is None:
                    assert not QRect(*first[2]).intersects(QRect(*first[3])), ('Overlapping first frame', first)
                settle()
                assert entry.geometry().getRect() == first[2], ('Entry geometry changed after first paint', active, first[2], entry.geometry().getRect())
        sidebar.set_side('top')
        settle()
        for collapsed in (True, False):
            sidebar.set_collapsed(collapsed)
            settle()
            expected_parent = workspace.row if workspace is not None else sidebar._bar_row
            assert entry.isVisible() and entry.parentWidget() is expected_parent
        sidebar.set_side('left')
        settle()
        if workspace is not None:
            workspace.remove('sidebar-test', 'entry')
        entry.detach()
        settle()
        assert sidebar.layout_button.isVisible()
        print('PASS: complete first paint, inactive/active mode, repeated toggles, portrait and unload')
    finally:
        host.close()
        app.shutdown()


if __name__ == '__main__':
    main()
