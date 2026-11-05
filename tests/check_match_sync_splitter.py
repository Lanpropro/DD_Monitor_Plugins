"""Rubber-band dragging defers media and chat layout until release."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QImage, QMouseEvent
    from PySide6.QtTest import QTest
    from ddm import plugins
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
        viewer.setFixedSize(viewer.size())
        QTest.qWait(80)
        text = viewer.panel.body.toPlainText()
        split = viewer.picture_split
        low = split.closestLegalPosition(0, 1)
        high = split.closestLegalPosition(split.width(), 1)
        assert high - low > 200, (low, high)
        positions_for = lambda fractions: tuple(round(low + (high - low) * fraction) for fraction in fractions)
        moves = []
        split.splitterMoved.connect(lambda position, index: moves.append((position, index)))
        def mouse(kind, point, button, buttons):
            app.sendEvent(handle, QMouseEvent(kind, QPointF(point), QPointF(handle.mapToGlobal(point)),
                                             button, buttons, Qt.NoModifier))
        try:
            assert not split.opaqueResize() and not viewer.body_split.opaqueResize()
            with patch.object(viewer.panel, '_render_all', side_effect=AssertionError('Full chat rebuild')):
                for fractions in ((.2, .8, .3, .6), (.7, .1, .6, .3)):
                    handle = split.handle(1)
                    start = handle.rect().center()
                    original = split.sizes()
                    before = len(moves)
                    mouse(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
                    for target in positions_for(fractions):
                        point = start + QPoint(target - handle.x(), 0)
                        mouse(QEvent.MouseMove, point, Qt.NoButton, Qt.LeftButton)
                        QTest.qWait(10)
                        assert split.sizes() == original, (original, split.sizes(), moves, split.opaqueResize())
                    mouse(QEvent.MouseButtonRelease, point, Qt.LeftButton, Qt.NoButton)
                    QTest.qWait(80)
                    assert split.sizes() != original
                    assert len(moves) == before + 1, 'Commit one resize per gesture'
                    assert viewer.canvas.frame_key == 1
                    assert viewer.panel.body.toPlainText() == text
            return {'passed': True, 'rapid_moves': 8, 'committed_resizes': 2,
                    'resize_during_drag': 0, 'static_frame_preserved': True,
                    'messages_preserved': 200, 'forced_grabs': 0}
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
