"""Keep resize and status work out of continuous playback updates."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
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
            return {'passed': True, 'resize_full_rebuilds': 0, 'messages_preserved': 100}
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
