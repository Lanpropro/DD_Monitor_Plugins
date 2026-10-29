"""Console geometry, room drafts and explicit multi-send; no real accounts."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget
    from ddm import plugins
    from ddm.plugin_controls import TileControl
    BatchSend = import_module(module.SendConsole.__module__).BatchSend
    sent, active = [], []

    class Composer(TileControl):
        def __init__(self, context):
            widget = QWidget(context.parent)
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            self.edit = QLineEdit()
            self.edit.setMaxLength(20)
            button = QPushButton('发送')
            layout.addWidget(self.edit, 1)
            layout.addWidget(button)
            super().__init__(widget, minimum_width=80)
            self.key = context.room['room_id']
            self.timer = QTimer(widget)
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self.finish)
            self.edit.returnPressed.connect(self.send)
            button.clicked.connect(self.send)

        def focus_input(self):
            return True

        def send(self):
            assert not active, 'Multi-send must respect one pending platform request'
            active.append(self.key)
            sent.append((self.key, self.edit.text()))
            self.edit.setReadOnly(True)
            self.timer.start(30)

        def shutdown(self):
            self.timer.stop()
            if self.key in active:
                active.remove(self.key)

        def finish(self):
            active.clear()
            self.edit.clear()
            self.edit.setReadOnly(False)

    with tempfile.TemporaryDirectory() as directory:
        host.settings = {}
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        manager.register_tile_control('fixture', 'send', Composer)
        viewer = module.Viewer(plugins.PluginContext(manager, 'sending_check'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen, host.testAttribute(Qt.WA_DontShowOnScreen))
        try:
            for key in ('1', '2', '3'):
                viewer.add_room(key, {'alias': '主播' + key})
            viewer.resize(1320, 880)
            viewer.show()
            QTest.qWait(80)
            console = viewer.send_console
            first = console.rows['1'].tile._plugin_controls.entries[0].control.edit
            second = console.rows['2'].tile._plugin_controls.entries[0].control.edit
            first.setText('draft 1')
            console.target.setCurrentIndex(console.target.findData('2'))
            second.setText('draft 2')
            assert console.stack.currentWidget() is console.rows['2']
            assert first.text() == 'draft 1' and not sent
            for width, height in ((1320, 880), (820, 700), (640, 1000)):
                viewer.resize(width, height)
                QTest.qWait(40)
                for sizes in ([500, 0, 240], [380, 0, 340]):
                    viewer.body_split.setSizes(sizes)
                    QTest.qWait(40)
                    left = viewer.settings_panel.mapTo(viewer, QPoint()).y()
                    right = console.mapTo(viewer, QPoint()).y()
                    assert abs(left - right) <= 1, (left, right)
            viewer.resize(1320, 880)
            QTest.qWait(40)
            dialog = BatchSend(console)
            for choice, _status in dialog.choices.values():
                choice.setChecked(True)
            dialog.edit.setText('shared')
            dialog.start()
            QTest.qWait(250)
            assert sent == [('1', 'shared'), ('2', 'shared'), ('3', 'shared')]
            assert first.text() == 'draft 1' and second.text() == 'draft 2'
            assert not dialog.timer.isActive()
            dialog.edit.setText('x' * 21)
            dialog.start()
            QTest.qWait(100)
            assert len(sent) == 3, 'Do not truncate and send overlong text'
            dialog.edit.setText('cancel rest')
            dialog.start()
            dialog.reject()
            QTest.qWait(100)
            assert sent[-1] == ('1', 'cancel rest') and len(sent) == 4
            assert not active, 'Closing stops further targets and shuts down the pending composer'
            viewer.rows['2'].alias.setText('changed name')
            assert 'changed name' in console.target.currentText()
            viewer.set_settings_visible(False)
            assert console.isHidden()
            viewer.set_settings_visible(True)
            QTest.qWait(50)
            assert console.isVisible() and second.text() == 'draft 2'
            controls = console.rows['2'].tile._plugin_controls
            viewer.remove_room('2')
            assert controls._disposed and '2' not in console.rows
            assert console.target.currentData() in ('1', '3')
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                viewer.grab().save(str(output))
            manager.unload()
            return {'passed': True, 'synthetic_sends': sent, 'drafts_preserved': True,
                    'aligned_console': True, 'outgoing_real_messages': 0}
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(args.host.resolve()), str(root)]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    dll = os.add_dll_directory(str(args.host.resolve())) if sys.platform == 'win32' else None
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        print(check(app, QWidget(), module, args.output))
    if dll:
        dll.close()


if __name__ == '__main__':
    main()
