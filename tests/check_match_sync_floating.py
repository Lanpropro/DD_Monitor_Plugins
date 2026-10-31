"""Source-resolution crops, app-wide movement, sizing and source lifecycle; offline."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QEnterEvent, QImage, QPainter, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QWidget
    from ddm import plugins
    host.settings, host.rooms = {}, []
    host.resize(1400, 900)
    host.show()
    with tempfile.TemporaryDirectory() as directory:
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        viewer = module.Viewer(plugins.PluginContext(manager, 'floating_check'), {})
        viewer.setWindowFlags(Qt.Widget)
        viewer.setParent(host)
        viewer.setGeometry(250, 0, 950, 700)
        viewer.show()
        viewer.embedded = True
        try:
            viewer.add_room('1', {'alias': 'Main'})
            viewer.add_room('2', {'alias': 'Camera'})
            row = viewer.rows['2']
            source = QImage(1920, 1080, QImage.Format_RGB32)
            source.fill(QColor('red'))
            painter = QPainter(source)
            painter.fillRect(960, 540, 960, 540, QColor('lime'))
            painter.end()
            requests = []
            reader = SimpleNamespace(configure=lambda *args, **kwargs: requests.append(kwargs['active']))
            decoder = SimpleNamespace(picture_at=lambda stamp, exact=False: (stamp, source), pictures=reader,
                                      history=SimpleNamespace(frame_at=lambda stamp: (stamp, source)),
                                      analysis_size=(320, 180), fps=30)
            row.decoder = decoder
            original_crop = row.crop
            def select_region(dialog):
                selecting = dialog.findChild(module.Canvas)
                assert selecting.selecting and '悬浮画面' in dialog.windowTitle()
                selecting.crop = QRectF(.5, .5, .5, .5)
                return QDialog.Accepted
            with patch.object(module.QDialog, 'exec', select_region):
                viewer.choose_crop(row, floating=True)
            assert row.room_id in viewer.floating_crops and row.crop == original_crop
            floating = viewer.show_floating_crop(row, (.5, .5, .5, .5), (1, source))
            QTest.qWait(30)
            assert floating.parentWidget() is host and floating.window() is host
            assert floating.image.size() == source.size(), 'Do not crop an already downscaled tile screenshot'
            assert floating.grab().toImage().pixelColor(floating.width() // 2, floating.height() // 2) == QColor('lime')
            app.sendEvent(floating, QEvent(QEvent.Leave))
            assert not floating.tools_visible
            assert floating.grab().toImage().pixelColor(floating.width() // 2, 2) == QColor('lime'), 'The image must fill the former header space'
            assert floating.mask().isEmpty(), 'Do not use an integer region mask for rounded video'
            rounded = floating.grab().toImage()
            assert rounded.pixelColor(0, 0).alpha() == 0
            assert any(0 < rounded.pixelColor(x, y).alpha() < 255 for x in range(20) for y in range(20)), 'Rounded corners need fractional alpha coverage'
            edge = rounded.width() - 1
            assert rounded.pixelColor(edge, rounded.height() // 2) == QColor('lime'), 'Source pixels must reach the right edge without a black strip'
            app.sendEvent(floating, QEnterEvent(QPointF(20, 20), QPointF(20, 20), QPointF(20, 20)))
            assert floating.tools_visible and floating.close_rect().top() > 0
            ratio = floating.devicePixelRatioF()
            assert floating.grab().toImage().pixelColor(round(20 * ratio), round(20 * ratio)) != QColor('lime'), 'Hover tools overlay the image'
            assert row.crop == original_crop, 'Floating crop must not modify comparison or alignment'
            start = floating.pos()
            QTest.mousePress(floating, Qt.LeftButton, pos=QPoint(40, 40))
            QTest.mouseMove(floating, QPoint(40, 40) + QPoint(600, 300))
            QTest.mouseRelease(floating, Qt.LeftButton, pos=QPoint(40, 40))
            assert floating.x() > start.x() + 400, 'Move beyond the main picture and its grid'
            floating.fit_geometry(5, 5, 320)
            width = floating.width()
            wheel = QWheelEvent(QPointF(80, 80), QPointF(floating.mapToGlobal(QPoint(80, 80))),
                                QPoint(), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            app.sendEvent(floating, wheel)
            assert floating.width() > width
            corner = floating.rect().bottomRight() - QPoint(4, 4)
            QTest.mousePress(floating, Qt.LeftButton, pos=corner)
            QTest.mouseMove(floating, corner + QPoint(80, 40))
            QTest.mouseRelease(floating, Qt.LeftButton, pos=corner)
            assert floating.width() > width * 1.2
            assert abs(floating.width() / floating.height() - 16 / 9) < .02
            viewer.render_floating_crops(100, {'1': 0, '2': -4})
            assert floating.frame_key == 96, 'Use the displayed source timeline, including replay shifts'
            viewer._configure_picture_budget()
            assert requests[-1] is True, 'Hidden secondary source must retain native RGB decoding while floated'
            row.paused = True
            viewer.render_floating_crops(101, {'2': -4})
            assert floating.frame_key == 96
            viewer.render_floating_crops(102, {'1': 0})
            assert floating.image.isNull(), 'Do not show a stale live picture when historical source is unavailable'
            row.paused = False
            viewer.render_floating_crops(100, {'2': -4})
            dialog = QDialog(host)
            dialog.resize(1000, 650)
            dialog.show()
            viewer.reparent_floating_crops(dialog)
            assert floating.parentWidget() is dialog
            viewer.reparent_floating_crops(host)
            dialog.close()
            host.resize(450, 300)
            QTest.qWait(20)
            assert host.rect().contains(floating.geometry()), 'Keep the crop accessible after app resizing'
            host.resize(1400, 900)
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                app.sendEvent(floating, QEnterEvent(QPointF(20, 20), QPointF(20, 20), QPointF(20, 20)))
                host.grab().save(str(output))
                app.sendEvent(floating, QEvent(QEvent.Leave))
                host.grab().save(str(output.with_name(output.stem + '-clean.png')))
            with patch.object(viewer, '_stop_row', side_effect=lambda item: setattr(item, 'decoder', None)):
                viewer.remove_room('2')
            assert not viewer.floating_crops
            other = viewer.show_floating_crop(viewer.rows['1'], (0, 0, .5, .5), (1, source))
            app.sendEvent(other, QEnterEvent(QPointF(20, 20), QPointF(20, 20), QPointF(20, 20)))
            QTest.mouseClick(other, Qt.LeftButton, pos=other.close_rect().center())
            assert not viewer.floating_crops
            viewer.show_floating_crop(viewer.rows['1'], (0, 0, 1, 1), (1, source))
            viewer.stop()
            assert not viewer.floating_crops
            return {'passed': True, 'source_resolution': '1920x1080', 'app_wide_drag': True,
                    'wheel_corner_resize': True, 'replay_source_time': True, 'network_requests': 0}
        finally:
            row.decoder = None
            viewer.embedded = False
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    print(check(app, QWidget(), module, args.output))


if __name__ == '__main__':
    main()
