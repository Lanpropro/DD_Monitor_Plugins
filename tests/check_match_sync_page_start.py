"""Verify the workspace source-start lifecycle without opening media."""
import argparse
from contextlib import nullcontext
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QAction
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync.plugin import MatchSyncPlugin

    app = QApplication([])
    host = QWidget()
    host.show()
    page = QWidget(host)
    page.rows = ['fixture']
    page.running = False
    events = []

    def start():
        page.running = True
        events.append('start')

    page.toggle_running = start
    plugin = MatchSyncPlugin()
    plugin.viewer = page
    plugin.button = QAction(host)
    plugin.switch_probe = SimpleNamespace(watch_target=lambda *_args: None,
                                          span=lambda *_args: nullcontext(),
                                          record=lambda *_args, **_kwargs: None)
    plugin.context = SimpleNamespace()
    plugin._source_start_timer = QTimer(host)
    plugin._source_start_timer.setSingleShot(True)
    plugin._source_start_timer.timeout.connect(plugin._start_visible_sources)
    try:
        plugin._change_mode(True)
        assert page.isVisible() and not events
        QTest.qWait(70)
        assert events == ['start']
        page.running = False
        plugin._change_mode(True)
        page.hide()
        plugin._closed()
        QTest.qWait(70)
        assert events == ['start']
        plugin.open_viewer({})
        assert events == ['start', 'start']
        plugin.switch_probe = None
        with patch.object(page, 'close', wraps=page.close) as close:
            plugin.on_unload()
            plugin.on_unload()
            assert plugin.viewer is None and close.call_count == 1
        print('PASS: Deferred workspace start, canceled close, and original direct open.')
    finally:
        host.close()
        app.shutdown()


if __name__ == '__main__':
    main()
