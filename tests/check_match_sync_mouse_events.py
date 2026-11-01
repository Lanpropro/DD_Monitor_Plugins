"""Video pointer motion must not fan out into the surrounding layout's hover handlers."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time


def check(app, host, module):
    from PySide6.QtCore import QEvent, QObject, QPointF, Qt
    from PySide6.QtGui import QEnterEvent, QImage, QMouseEvent
    from ddm import plugins
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'mouse_events'), {})
        viewer.embed(getattr(host, '_content', host))
        viewer.show()
        image = QImage(640, 360, QImage.Format_RGB32)
        image.fill(Qt.red)
        viewer.canvas.set_frame((100., image))
        app.processEvents()
        ancestors = []
        parent = viewer.picture.parentWidget()
        while parent is not None:
            ancestors.append((parent, parent.testAttribute(Qt.WA_Hover)))
            parent.setAttribute(Qt.WA_Hover, True)
            parent = parent.parentWidget()
        counts = {'outside_hover': 0, 'tile_hover': 0, 'canvas_moves': 0}
        class Watch(QObject):
            def eventFilter(self, watched, event):
                if event.type() == QEvent.HoverMove:
                    if any(watched is item for item, _hover in ancestors):
                        counts['outside_hover'] += 1
                    if watched is viewer.picture:
                        counts['tile_hover'] += 1
                elif watched is viewer.canvas and event.type() == QEvent.MouseMove:
                    counts['canvas_moves'] += 1
                return False
        watch = Watch(app)
        app.installEventFilter(watch)
        selector = module.Canvas(selecting=True)
        def mouse(widget, kind, point, buttons=Qt.NoButton):
            button = Qt.LeftButton if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) else Qt.NoButton
            event = QMouseEvent(kind, point, QPointF(widget.mapToGlobal(point.toPoint())),
                                button, buttons, Qt.NoModifier)
            app.sendEvent(widget, event)
        try:
            started = time.perf_counter()
            for index in range(1000):
                mouse(viewer.canvas, QEvent.MouseMove, QPointF(20 + index % 200, 20 + index % 100))
            elapsed = (time.perf_counter() - started) * 1000
            assert counts['canvas_moves'] == 1000, counts
            assert counts['outside_hover'] == 0, ('Pointer motion reached layout ancestors', counts)
            # Enter and tile clicks still reach their existing handlers.
            viewer.picture.set_controls_visible(False)
            point = QPointF(40, 40)
            app.sendEvent(viewer.picture, QEnterEvent(point, point, point))
            assert viewer.picture._overlay_visible
            clicks = []
            viewer.picture.clicked.connect(clicks.append)
            mouse(viewer.picture, QEvent.MouseButtonPress, point, Qt.LeftButton)
            mouse(viewer.picture, QEvent.MouseButtonRelease, point)
            assert len(clicks) == 1
            # Crop drags use the original Canvas interaction unchanged.
            selector.set_frame((100., image))
            selector.resize(640, 360)
            selector.grab()
            mouse(selector, QEvent.MouseButtonPress, QPointF(100, 80), Qt.LeftButton)
            mouse(selector, QEvent.MouseMove, QPointF(300, 200), Qt.LeftButton)
            mouse(selector, QEvent.MouseButtonRelease, QPointF(300, 200))
            assert selector.selection_ready and selector.crop.width() > .2 and selector.crop.height() > .2
            return dict(passed=True, input_moves=1000, outside_hover_events=0,
                        synthetic_move_ms=round(elapsed, 3), tile_enter_and_click=True, crop_drag=True)
        finally:
            app.removeEventFilter(watch)
            for parent, hovered in ancestors:
                parent.setAttribute(Qt.WA_Hover, hovered)
            selector.close()
            viewer.close()
            selector.deleteLater()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    host = QWidget()
    host.setAttribute(Qt.WA_DontShowOnScreen)
    host.resize(1600, 950)
    host.show()
    try:
        print(check(app, host, module))
    finally:
        host.close()
        app.processEvents()
        app.shutdown()


if __name__ == '__main__':
    main()
