"""Video must not repaint covered parent skins, but preserve transparent margins."""
import argparse
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT), str(args.host.resolve())]
    os.chdir(args.host.resolve())
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QImage, QPainter, QColor
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
    from plugins.match_sync.viewer import Canvas

    class Skin(QWidget):
        paints = 0
        def paintEvent(self, event):
            self.paints += 1
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('blue'))

    app = QApplication.instance() or QApplication([])
    host = Skin()
    host.resize(960, 540)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    canvas = Canvas()
    canvas.transparent_background = True
    layout.addWidget(canvas)
    host.show()
    app.processEvents()
    try:
        image = QImage(960, 540, QImage.Format_RGB32)
        image.fill(QColor('red'))
        canvas.set_frame((0, image))
        app.processEvents()
        host.paints = 0
        for i in range(1, 61):
            canvas.set_frame((i, image))
            app.processEvents()
        assert host.paints == 0, ('Covered skin repainted with each video frame', host.paints)
        assert canvas.testAttribute(Qt.WA_OpaquePaintEvent)
        assert host.grab().toImage().pixelColor(480, 270) == QColor('red')
        # Changing crop with a paused/shared frame must restore the skin in the margins.
        canvas.crop = QRectF(0, 0, .5, 1)
        canvas.zoom_crop = True
        canvas._sync_opaque_paint()
        canvas.update()
        app.processEvents()
        assert not canvas.testAttribute(Qt.WA_OpaquePaintEvent)
        shot = host.grab().toImage()
        assert shot.pixelColor(10, 270) == QColor('blue')
        assert shot.pixelColor(480, 270) == QColor('red')
        canvas.zoom_crop = False
        canvas._sync_opaque_paint()
        host.resize(960, 700)
        app.processEvents()
        assert not canvas.testAttribute(Qt.WA_OpaquePaintEvent)
        shot = host.grab().toImage()
        assert shot.pixelColor(480, 10) == QColor('blue')
        assert shot.pixelColor(480, 350) == QColor('red')
        canvas.set_frame(None)
        app.processEvents()
        assert not canvas.testAttribute(Qt.WA_OpaquePaintEvent)
        assert host.grab().toImage().pixelColor(10, 10) == QColor('blue')
        empty = host.grab().toImage().convertToFormat(QImage.Format_RGB32)
        expected = QImage(empty.size(), QImage.Format_RGB32)
        expected.fill(QColor('blue'))
        assert empty == expected, 'An empty canvas must not draw placeholder text'
        canvas.transparent_background = False
        canvas._sync_opaque_paint()
        canvas.update()
        app.processEvents()
        assert canvas.testAttribute(Qt.WA_OpaquePaintEvent)
        assert host.grab().toImage().pixelColor(10, 10) != QColor('blue')
        print('PASS: 60 video frames cause zero covered-parent repaints; crop, resize, empty state and theme margins preserved')
    finally:
        host.close()
        app.processEvents()


if __name__ == '__main__':
    main()
