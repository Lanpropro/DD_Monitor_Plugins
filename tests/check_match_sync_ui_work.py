"""Keep resize and status work out of continuous playback updates."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextCursor, QTextDocument
    from PySide6.QtTest import QTest
    from ddm import plugins
    sending = import_module(module.__package__ + '.sending')
    with tempfile.TemporaryDirectory() as directory:
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'ui_work'), {})
        panel = sending.SendingDanmakuPanel(host)
        try:
            panel.resize(600, 400)
            panel.show()
            for index in range(100):
                panel.add_event({'text': str(index), 'uname': 'fixture', 'source_room': '1',
                                 '_replay_divider': index in (20, 80)})
            QTest.qWait(20)
            text = panel.body.toPlainText()
            blocks = panel.body.document().blockCount()
            with patch.object(panel, '_render_all', side_effect=AssertionError('Resize rebuilt chat')):
                for width in (400, 750, 320, 600):
                    panel.resize(width, 400)
                    QTest.qWait(20)
                    assert panel.body.toPlainText() == text
                    assert panel.body.document().blockCount() == blocks
                    for index in (20, 80):
                        cursor = QTextCursor(panel.body.document().findBlockByNumber(index))
                        cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
                        image = cursor.charFormat().toImageFormat()
                        expected = max(1, panel.body.viewport().width() - 24)
                        assert image.isValid() and image.width() == expected, (width, image.width(), expected)
                        resource = panel.body.document().resource(QTextDocument.ImageResource, QUrl(image.name()))
                        assert resource.width() == expected and resource.height() == 80
            assert [entry['source_room'] for entry in panel._blocks] == ['1'] * 100
            with patch.object(module.Decoder, 'start', lambda _self: None), \
                    patch.object(module.Chat, 'start', lambda _self: None), \
                    patch.object(module.AudioPump, 'start', lambda _self: None):
                viewer.add_room('1')
                viewer.toggle_running()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            viewer.audio.timer.stop()
            row = viewer.rows['1']
            row.decoder.fps = 60
            calls = []
            row.platform = SimpleNamespace(kind='bilibili', room_quality_options=lambda _key: calls.append(_key) or [])
            viewer.status_ui_due = 0
            base = time.monotonic()
            with patch.object(viewer, 'render_floating_crops', wraps=viewer.render_floating_crops) as frames:
                for index in range(125):
                    with patch.object(module.time, 'monotonic', return_value=base + index * .008):
                        viewer.render()
                assert frames.call_count == 125, 'Do not throttle the playback path'
            assert len(calls) == 4, len(calls)
            assert viewer.render_timer.interval() == 8
            # Explicit source information and room changes bypass periodic UI throttling.
            row.title = 'new title'
            viewer.sync_picture()
            assert viewer.picture.room['title'] == 'new title'
            assert len(calls) == 5
            row.volume.setValue(37)
            row.audible.setChecked(False)
            before_controls = len(calls)
            viewer.sync_picture(refresh_metadata=False)
            assert viewer.picture.volume == 37 and viewer.picture.muted
            assert len(calls) == before_controls, 'Playback controls must not require a metadata refresh'
            return {'passed': True, 'resize_full_rebuilds': 0, 'messages_preserved': 100,
                    'playback_updates': 125, 'metadata_polls': 4, 'immediate_controls': True}
        finally:
            panel.close()
            viewer.close()
            panel.deleteLater()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
