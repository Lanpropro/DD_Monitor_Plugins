"""Merged chat uses each source's existing composer; synthetic input only."""
import argparse
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget
    from ddm import danmaku_actions, plugins
    from ddm.plugin_controls import TileControl

    sent, stopped = [], []

    class Composer(TileControl):
        def __init__(self, context):
            widget = QWidget(context.parent)
            layout = QHBoxLayout(widget)
            self.editor = QLineEdit(widget)
            self.editor.setMaxLength(20)
            layout.addWidget(self.editor)
            super().__init__(widget, minimum_width=80)
            self.key = context.room['room_id']
            self.editor.returnPressed.connect(lambda: sent.append((self.key, self.editor.text())))

        def focus_input(self):
            self.editor.setFocus()
            return True

        def shutdown(self):
            stopped.append(self.key)

    with tempfile.TemporaryDirectory() as directory:
        host.settings = {}
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        manager.register_tile_control('fixture', 'composer', Composer)
        viewer = module.Viewer(plugins.PluginContext(manager, 'interaction_check'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen, host.testAttribute(Qt.WA_DontShowOnScreen))
        try:
            for key in ('1', '2'):
                viewer.add_room(key, {'alias': 'same name'})
            viewer.resize(1320, 880)
            viewer.show()
            app.processEvents()
            panel = viewer.panel
            first = panel.targets['1']._plugin_controls
            second = panel.targets['2']._plugin_controls
            first.entries[0].control.editor.setText('first draft')
            panel.target.setCurrentIndex(panel.target.findData('2'))
            second.entries[0].control.editor.setText('second draft')
            assert panel._tile_controls is second
            assert first.entries[0].control.editor.text() == 'first draft'
            assert not sent
            # Metadata survives the host's actual bounded block trimming.
            panel.set_max_blocks(100)
            for index in range(105):
                panel.add_event({'text': str(index), 'source_room': '1' if index % 2 else '2'})
            assert len(panel._blocks) == 100
            with patch.object(danmaku_actions.CommentToolbar, 'exec', lambda menu, _: menu.repeat_action):
                panel._message_clicked(0, QPoint())
            assert sent == [('1', '5')], 'Use the comment source, not the selected composer or alias'
            assert second.entries[0].control.editor.text() == 'second draft'
            assert not first.repeat_text('x' * 21, '1')
            assert not first.repeat_text('wrong room', '2')
            # The picture overlay keeps raw text separately from its source label.
            overlay = viewer.video_danmaku
            overlay.set_active(True)
            viewer.picture.set_video_active(True)
            viewer.picture.danmaku_button.setChecked(True)
            app.processEvents()
            viewer.overlay_pending['2'] = (100, {'kind': 'danmaku', 'text': '【same name】 raw',
                                                       'source_text': 'raw', 'source_room': '2'})
            viewer._render_overlay(100, {'1': 0, '2': 0})
            assert overlay.comments
            comment = overlay.comments[-1]
            assert comment.source_room == '2' and comment.source_text == 'raw'
            with patch.object(overlay, 'comment_at', return_value=comment), \
                    patch.object(danmaku_actions.CommentToolbar, 'exec', lambda menu, _: menu.repeat_action):
                panel.overlay_actions(comment.message, QPoint())
            assert sent[-1] == ('2', 'raw')
            viewer.chat_visible.setChecked(False)
            assert panel.isHidden() and not overlay.isHidden()
            viewer.chat_visible.setChecked(True)
            app.processEvents()
            assert panel.isVisible() and panel._tile_controls is second
            assert second.entries[0].control.editor.text() == 'raw'
            viewer.remove_room('1')
            assert first._disposed and stopped == ['1']
            assert not first.repeat_text('stale', '1')
            assert '1' not in panel.targets
            manager.unload()
            assert second._disposed and sorted(stopped) == ['1', '2']
            assert len(sent) == 2, 'No implicit sends on switch, remove, close or unload'
            return {'passed': True, 'synthetic_sends': sent, 'personal_config_read': False}
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
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
        print(check(app, QWidget(), module))
    if dll:
        dll.close()


if __name__ == '__main__':
    main()
