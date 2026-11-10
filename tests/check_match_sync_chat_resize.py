"""Send console resize preserves chat tail/history without another message."""
import argparse
import os
from pathlib import Path
import sys
import tempfile


def check(app, host, module):
    from ddm import plugins
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'chat_resize'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            viewer.resize(1500, 900)
            viewer.show()
            viewer.add_room('1', {'alias': 'fixture'})
            QTest.qWait(30)
            panel, console = viewer.panel, viewer.send_console
            for index in range(90):
                panel.add_event({'uname': 'fixture', 'text': f'Chat resize message {index} — no live connection'})
            QTest.qWait(30)
            bar = panel.body.verticalScrollBar()
            assert bar.maximum() > 0
            panel.jump_to_bottom()
            QTest.qWait(10)
            text = panel.body.toPlainText()
            expanded = panel.body.viewport().height()
            for _ in range(3):
                console.set_collapsed(True)
                QTest.qWait(20)
                assert panel.body.viewport().height() > expanded
                assert bar.value() == bar.maximum(), 'Collapsing must keep the newest message visible'
                console.set_collapsed(False)
                QTest.qWait(20)
                assert bar.value() == bar.maximum(), 'Expanding must follow tail without waiting for new messages'
                assert panel._follow_tail
            bar.setValue(bar.maximum() // 3)
            panel.body.userScrolled.emit()
            history = bar.value()
            assert not panel._follow_tail
            for collapsed in (True, False):
                console.set_collapsed(collapsed)
                QTest.qWait(20)
                assert bar.value() == history and not panel._follow_tail, 'Preserve intentional history reading'
            # A user scroll after layout activation wins over its deferred restore.
            panel.jump_to_bottom()
            console.set_collapsed(True)
            bar.setValue(bar.maximum() // 2)
            panel.body.userScrolled.emit()
            history = bar.value()
            QTest.qWait(20)
            assert bar.value() == history and not panel._follow_tail
            assert panel.body.toPlainText() == text
            assert len(panel._blocks) == 90
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()
    return dict(passed=True, tail_without_new_message=True, history_preserved=True,
                deferred_user_scroll_wins=True, messages_unchanged=True,
                media_playback=False, real_messages=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    print(check(app, QWidget(), module))
