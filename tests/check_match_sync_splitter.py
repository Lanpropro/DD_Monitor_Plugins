"""Rapid splitter reversals restore exposed surfaces without a new media frame."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from ddm import plugins
    class Paints(QObject):
        def __init__(self):
            super().__init__()
            self.regions = []
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Paint:
                self.regions.append(event.region())
            return False
    with tempfile.TemporaryDirectory() as directory:
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'splitter'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        viewer.resize(2800, 950)
        viewer.show()
        for index in range(200):
            viewer.panel.add_event({'text': 'fixture message ' * 8 + str(index), 'uname': 'fixture'})
        image = QImage(640, 360, QImage.Format_RGB32)
        image.fill(Qt.red)
        viewer.canvas.set_frame((1, image))
        QTest.qWait(80)
        paints = Paints()
        viewer.panel.body.viewport().installEventFilter(paints)
        text = viewer.panel.body.toPlainText()
        split = viewer.picture_split
        low = split.closestLegalPosition(0, 1)
        high = split.closestLegalPosition(split.width(), 1)
        assert high - low > 200, (low, high)
        positions_for = lambda fractions: tuple(round(low + (high - low) * fraction) for fraction in fractions)
        try:
            with patch.object(viewer.panel, '_render_all', side_effect=AssertionError('Full chat rebuild')), \
                    patch.object(viewer.panel.body.viewport(), 'repaint', wraps=viewer.panel.body.viewport().repaint) as restore:
                for positions in (positions_for((.2, .8, .3, .6)), positions_for((.7, .1, .6, .3))):
                    paints.regions.clear()
                    before = restore.call_count
                    for position in positions:
                        viewer.picture_split.moveSplitter(position, 1)
                    assert viewer.splitter_repaint.isActive()
                    QTest.qWait(80)
                    assert restore.call_count == before + 1, (before, restore.call_count, viewer.picture_split.sizes(), viewer.panel.body.viewport().isVisible(), viewer.panel.body.viewport().size(), viewer.splitter_damage.boundingRect())
                    assert paints.regions, 'The exposed chat surface must actually paint'
                    assert viewer.canvas.frame_key == 1, 'Do not rely on a new source frame'
                    assert viewer.panel.body.toPlainText() == text
                    assert viewer.splitter_damage.isEmpty()
            viewer.hide()
            for position in (500, 700):
                viewer.picture_split.moveSplitter(position, 1)
            QTest.qWait(10)
            return {'passed': True, 'rapid_moves': 8, 'coalesced_repairs': 2,
                    'static_frame_preserved': True, 'messages_preserved': 200, 'forced_grabs': 0}
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
