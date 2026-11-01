"""Source-resolution crops, app-wide movement, sizing and source lifecycle; offline."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check_selection(app, module, source):
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
    from PySide6.QtGui import QMouseEvent, QWheelEvent

    canvas = module.Canvas(selecting=True)
    canvas.setAttribute(Qt.WA_DontShowOnScreen)
    canvas.resize(960, 640)  # Include letterboxing in coordinate checks.
    canvas.set_frame((1, source))
    canvas.show()
    app.processEvents()

    def position(x, y):
        area = canvas.image_rect
        return QPoint(round(area.x() + x * area.width()), round(area.y() + y * area.height()))

    def drag(start, end):
        # Native QTest.mouseMove moves the shared desktop pointer. Deliver local
        # gestures directly so user/parallel-window movement cannot interleave.
        for kind, point, button, buttons in ((QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
                                             (QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
                                             (QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)):
            app.sendEvent(canvas, QMouseEvent(kind, QPointF(point), QPointF(canvas.mapToGlobal(point)),
                                              button, buttons, Qt.NoModifier))

    try:
        drag(position(.2, .2), position(.6, .6))
        original = QRectF(canvas.crop)
        drag(position(.4, .4), position(.45, .45))
        assert abs(canvas.crop.width() - original.width()) < .003, 'Dragging inside must move, not replace, the selection'
        assert abs(canvas.crop.x() - .25) < .003
        for start, end in (((.25, .45), (.2, .45)), ((.65, .45), (.7, .45)),
                           ((.45, .25), (.45, .2)), ((.45, .65), (.45, .7))):
            drag(position(*start), position(*end))
        assert abs(canvas.crop.x() - .2) < .003 and abs(canvas.crop.width() - .5) < .003
        assert abs(canvas.crop.y() - .2) < .003 and abs(canvas.crop.height() - .5) < .003
        for start, end in (((.2, .2), (.15, .15)), ((.7, .15), (.75, .1)),
                           ((.15, .7), (.1, .75)), ((.75, .75), (.8, .8))):
            drag(position(*start), position(*end))
        assert abs(canvas.crop.x() - .1) < .003 and abs(canvas.crop.width() - .7) < .003
        original = QRectF(canvas.crop)
        drag(position(.9, .9), position(.95, .95))
        assert canvas.crop == original, 'Outside drags must not discard the selection'
        drag(position(.4, .4), position(-1, -1))
        assert canvas.crop.topLeft() == QPointF(0, 0)
        assert abs(canvas.crop.width() - original.width()) < .003
        drag(position(0, .35), position(.9, .35))
        assert canvas.crop.width() > 0 and canvas.crop.right() <= 1, 'An edge cannot cross its opposite edge'
        canvas.reset_selection()
        assert canvas.crop == QRectF(0, 0, 1, 1)
        drag(position(.7, .7), position(.3, .3))
        assert abs(canvas.crop.x() - .3) < .003 and abs(canvas.crop.width() - .4) < .003, 'Reset allows an explicit new selection'
        point = position(.4, .4)
        before = canvas._point(QPointF(point))
        selection = QRectF(canvas.crop)
        wheel = QWheelEvent(QPointF(point), QPointF(canvas.mapToGlobal(point)), QPoint(), QPoint(0, 240),
                            Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
        app.sendEvent(canvas, wheel)
        canvas.repaint()
        assert canvas.selection_zoom > 1 and canvas.crop == selection
        after = canvas._point(QPointF(point))
        assert abs(before.x() - after.x()) < .0001 and abs(before.y() - after.y()) < .0001, 'Zoom must keep the source pixel under the cursor'
        right = position(.7, .5)
        drag(right, right + QPoint(12, 0))
        assert abs(canvas.crop.right() - (.7 + 12 / canvas.image_rect.width())) < .003, 'Zoomed edge edits must map back to source coordinates'
        canvas.reset_selection()
        assert canvas.selection_zoom == 1 and canvas.selection_pan == QPointF()
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


def check(app, host, module, output=None):
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
    from PySide6.QtGui import QColor, QEnterEvent, QImage, QPainter, QPixmap, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton, QWidget
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
            avatar = QPixmap(48, 48)
            avatar.fill(QColor('magenta'))
            row.avatar.set_pixmap_image(avatar)
            source = QImage(1920, 1080, QImage.Format_RGB32)
            source.fill(QColor('red'))
            painter = QPainter(source)
            painter.fillRect(960, 540, 960, 540, QColor('lime'))
            painter.end()
            check_selection(app, module, source)
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
                dialog.setAttribute(Qt.WA_DontShowOnScreen)
                dialog.show()
                app.processEvents()
                buttons = dialog.findChild(QDialogButtonBox)
                reset = next(button for button in dialog.findChildren(QPushButton) if button.text() == '使用整个画面')
                confirm = buttons.button(QDialogButtonBox.Ok)
                cancel = buttons.button(QDialogButtonBox.Cancel)
                centers = [button.mapTo(dialog, button.rect().center()).y() for button in (reset, confirm, cancel)]
                assert max(centers) - min(centers) <= 1, 'All crop actions belong on the same row'
                gap = confirm.mapTo(dialog, QPoint()).x() - reset.mapTo(dialog, reset.rect().topRight()).x()
                assert 0 <= gap <= 24, 'Keep full-frame, confirm and cancel adjacent'
                selecting.crop = QRectF(.5, .5, .5, .5)
                selecting.selection_ready = True
                if output:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    dialog.grab().save(str(output.with_name('selection.png')))
                dialog.hide()
                return QDialog.Accepted
            with patch.object(module.QDialog, 'exec', select_region):
                viewer.choose_crop(row, floating=True)
            assert row.room_id in viewer.floating_crops and row.crop == original_crop
            floating = viewer.show_floating_crop(row, (.5, .5, .5, .5), (1, source))
            QTest.qWait(30)
            assert floating.parentWidget() is host and floating.window() is host
            assert not floating.testAttribute(Qt.WA_TranslucentBackground)
            assert not floating.testAttribute(Qt.WA_NoSystemBackground), 'Moving crops require background restoration'
            assert floating.image.size() == source.size(), 'Do not crop an already downscaled tile screenshot'
            assert floating.grab().toImage().pixelColor(floating.width() // 2, floating.height() // 2) == QColor('lime')
            app.sendEvent(floating, QEvent(QEvent.Leave))
            assert not floating.tools_visible
            assert floating.grab().toImage().pixelColor(floating.width() // 2, 2) == QColor('lime'), 'The image must fill the former header space'
            assert floating.mask().isEmpty(), 'Do not use an integer region mask for rounded video'
            ratio = floating.devicePixelRatioF()
            rounded = QImage(round(floating.width() * ratio), round(floating.height() * ratio), QImage.Format_ARGB32_Premultiplied)
            rounded.setDevicePixelRatio(ratio)
            rounded.fill(Qt.transparent)
            painting = QPainter(rounded)
            floating.paint_picture(painting)
            painting.end()
            assert rounded.pixelColor(0, 0).alpha() == 0
            assert any(0 < rounded.pixelColor(x, y).alpha() < 255 for x in range(20) for y in range(20)), 'Rounded corners need fractional alpha coverage'
            edge = rounded.width() - 1
            assert rounded.pixelColor(edge, rounded.height() // 2) == QColor('lime'), 'Source pixels must reach the right edge without a black strip'
            app.sendEvent(floating, QEnterEvent(QPointF(20, 20), QPointF(20, 20), QPointF(20, 20)))
            assert floating.tools_visible and floating.close_rect().top() > 0
            ratio = floating.devicePixelRatioF()
            hover_image = floating.grab().toImage()
            assert hover_image.pixelColor(round(20 * ratio), round(20 * ratio)) == QColor('magenta'), 'Show only the source broadcaster avatar'
            assert hover_image.pixelColor(round(48 * ratio), round(20 * ratio)) == QColor('lime'), 'Do not leave a title label behind the avatar'
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
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                floating.fit_geometry(400, 300, 320)
                original = floating.geometry()
                corner = QPoint(4 if dx < 0 else floating.width() - 4, 4 if dy < 0 else floating.height() - 4)
                point = floating.mapTo(host, corner)
                QTest.mousePress(host.windowHandle(), Qt.LeftButton, pos=point)
                QTest.mouseMove(host.windowHandle(), point + QPoint(dx * 80, dy * 45))
                QTest.mouseRelease(host.windowHandle(), Qt.LeftButton, pos=point + QPoint(dx * 80, dy * 45))
                assert floating.width() > original.width() + 60, f'Corner {dx, dy} must resize'
                anchor = lambda rect: QPoint(rect.x() if dx > 0 else rect.x() + rect.width(),
                                             rect.y() if dy > 0 else rect.y() + rect.height())
                assert (anchor(floating.geometry()) - anchor(original)).manhattanLength() <= 1, 'Keep the opposite corner fixed'
                point = floating.mapTo(host, QPoint(4 if dx < 0 else floating.width() - 4,
                                                    4 if dy < 0 else floating.height() - 4))
                QTest.mousePress(host.windowHandle(), Qt.LeftButton, pos=point)
                QTest.mouseMove(host.windowHandle(), point - QPoint(dx * 60, dy * 34))
                QTest.mouseRelease(host.windowHandle(), Qt.LeftButton, pos=point - QPoint(dx * 60, dy * 34))
                assert floating.width() < original.width() + 40, f'Corner {dx, dy} must shrink'
                assert (anchor(floating.geometry()) - anchor(original)).manhattanLength() <= 1
            adjusted = []
            def adjust_region(dialog):
                selecting = dialog.findChild(module.Canvas)
                assert selecting.crop == floating.crop and selecting.selection_ready, 'Reopen the existing editable crop'
                selecting.crop = QRectF(.4, .4, .5, .5)
                adjusted.append(True)
                return QDialog.Accepted
            with patch.object(module.QDialog, 'exec', adjust_region):
                QTest.mouseClick(floating, Qt.LeftButton, pos=floating.adjust_rect().center())
            assert adjusted and floating.crop == QRectF(.4, .4, .5, .5)
            assert row.crop == original_crop, 'Fine tuning must not change alignment/comparison crops'
            def cancel_adjustment(dialog):
                dialog.findChild(module.Canvas).crop = QRectF(0, 0, 1, 1)
                return QDialog.Rejected
            with patch.object(module.QDialog, 'exec', cancel_adjustment):
                QTest.mouseClick(floating, Qt.LeftButton, pos=floating.adjust_rect().center())
            assert floating.crop == QRectF(.4, .4, .5, .5), 'Cancel must preserve the applied crop'
            floating.fit_geometry(10, 10, 400)
            anchor = floating.mapTo(host, floating.rect().bottomRight() - QPoint(4, 4))
            QTest.mousePress(host.windowHandle(), Qt.LeftButton, pos=anchor)
            QTest.mouseMove(host.windowHandle(), anchor + QPoint(106, -60))
            before_crossing = floating.width()
            QTest.mouseMove(host.windowHandle(), anchor + QPoint(108, -60))
            assert abs(floating.width() - before_crossing) <= 3, 'Crossing axis dominance must not jump from shrinking to growing'
            QTest.mouseRelease(host.windowHandle(), Qt.LeftButton, pos=anchor + QPoint(108, -60))
            QTest.mouseClick(floating, Qt.LeftButton, pos=floating.lock_rect().center())
            assert floating.lock_mode == 1
            assert floating.picture_widget is viewer.picture
            floating.tools_visible = True
            icon = floating.grab().toImage()
            ratio = floating.devicePixelRatioF()
            area = floating.lock_rect()
            points = [(x / ratio, y / ratio) for y in range(round(area.top() * ratio), round((area.bottom() + 1) * ratio))
                      for x in range(round(area.left() * ratio), round((area.right() + 1) * ratio))
                      if min(icon.pixelColor(x, y).getRgb()[:3]) > 140]
            assert points
            assert abs(sum(x for x, _y in points) / len(points) - (area.x() + area.width() / 2)) <= 1
            assert abs(sum(y for _x, y in points) / len(points) - (area.y() + area.height() / 2)) <= 1
            expected_bounds = QRect(viewer.picture.mapTo(host, QPoint()), viewer.picture.size()).intersected(host.rect())
            assert floating.movement_bounds() == expected_bounds, 'Lock to the entire tile, including its controls and letterboxing'
            floating.fit_geometry(-500, -500, 300)
            assert floating.movement_bounds().contains(floating.geometry()), 'Picture-locked crop stays in the main canvas'
            QTest.mouseClick(floating, Qt.LeftButton, pos=floating.lock_rect().center())
            assert floating.lock_mode == 2
            locked = floating.geometry()
            QTest.mousePress(floating, Qt.LeftButton, pos=QPoint(40, 40))
            QTest.mouseMove(floating, QPoint(120, 100))
            QTest.mouseRelease(floating, Qt.LeftButton, pos=QPoint(120, 100))
            wheel = QWheelEvent(QPointF(40, 40), QPointF(floating.mapToGlobal(QPoint(40, 40))),
                                QPoint(), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            app.sendEvent(floating, wheel)
            assert floating.geometry() == locked, 'Position lock prevents dragging and wheel resize'
            for point in (QPoint(4, 4), QPoint(floating.width() - 4, 4),
                          QPoint(4, floating.height() - 4), floating.rect().bottomRight() - QPoint(4, 4)):
                QTest.mousePress(floating, Qt.LeftButton, pos=point)
                QTest.mouseMove(floating, point + QPoint(20, 20))
                QTest.mouseRelease(floating, Qt.LeftButton, pos=point + QPoint(20, 20))
                assert floating.geometry() == locked, 'Position lock must cover every corner'
            QTest.mouseClick(floating, Qt.LeftButton, pos=floating.lock_rect().center())
            assert floating.lock_mode == 0
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
            floating.fit_geometry(800, 500, 280)
            preferred = floating.geometry()
            host.resize(700, 450)
            app.processEvents()
            host.resize(1400, 900)
            app.processEvents()
            assert (floating.pos() - preferred.topLeft()).manhattanLength() <= 2
            assert abs(floating.width() - preferred.width()) <= 1, 'A shrink/grow cycle must restore the placed crop'
            host.resize(450, 300)
            QTest.qWait(20)
            assert host.rect().contains(floating.geometry()), 'Keep the crop accessible after app resizing'
            host.resize(1400, 900)
            for index in range(12):
                floating.fit_geometry(40 + index * 8, 40 + index * 4, 240 + (index % 3) * 75)
                moving = source.copy()
                painting = QPainter(moving)
                painting.fillRect(1000 + index * 30, 600, 80, 120, QColor('blue'))
                painting.end()
                floating.set_frame((1000 + index, moving))
                app.processEvents()
            # A stalled/paused background has no set_frame call to erase old crops.
            # Observe direct repaint requests; grab() would force painting and hide the bug.
            canvas_area = QRect(viewer.canvas.mapTo(host, QPoint()), viewer.canvas.size())
            floating.fit_geometry(canvas_area.x() + 20, canvas_area.y() + 20, 180)
            app.processEvents()
            unchanged = viewer.canvas.frame_key
            with patch.object(viewer.canvas, 'repaint', wraps=viewer.canvas.repaint) as repaint:
                old = floating.geometry()
                floating.fit_geometry(old.x() + 90, old.y() + 30, old.width())
                assert repaint.call_count > 0, 'Repaint the exposed opaque video surface without a new source frame'
                for call in repaint.call_args_list:
                    assert viewer.canvas.rect().contains(call.args[0])
                assert viewer.canvas.frame_key == unchanged
            with patch.object(viewer.canvas, 'repaint', wraps=viewer.canvas.repaint) as repaint:
                floating.drag = (QPoint(), floating.geometry(), None)
                for index in range(100):
                    floating.fit_geometry(canvas_area.x() + 20 + index, canvas_area.y() + 20, 180)
                assert repaint.call_count == 0, 'Mouse bursts must not synchronously repaint the video for each event'
                assert viewer.crop_repaint_timer.isActive()
                floating.drag = None
                floating.gesture_finished.emit()
                assert repaint.call_count == 1, 'Release flushes all exposed areas in one repaint'
                assert not viewer.crop_repaint_regions and not viewer.crop_repaint_timer.isActive()
                floating.drag = (QPoint(), floating.geometry(), None)
                floating.fit_geometry(floating.x() + 10, floating.y(), floating.width())
                QTest.qWait(35)
                assert repaint.call_count == 2, 'A stalled background is restored during dragging, not only on release'
                floating.drag = None
                floating.gesture_finished.emit()
            with patch.object(viewer.canvas, 'repaint', wraps=viewer.canvas.repaint) as repaint:
                floating.hide()
                assert repaint.call_count > 0, 'Hiding/closing must also restore the background'
                floating.show()
            before_move = floating.pos()
            position = floating.mapTo(host, QPoint(40, 40))
            assert host.childAt(position) is floating
            QTest.mousePress(host.windowHandle(), Qt.LeftButton, pos=position)
            QTest.mouseMove(host.windowHandle(), position + QPoint(90, 45))
            QTest.mouseRelease(host.windowHandle(), Qt.LeftButton, pos=position + QPoint(90, 45))
            assert floating.pos() == before_move + QPoint(90, 45), 'Resize/frame updates must preserve hit-tested dragging'
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
            position = other.mapTo(host, other.close_rect().center())
            assert host.childAt(position) is other
            QTest.mouseClick(host.windowHandle(), Qt.LeftButton, pos=position)
            assert not viewer.floating_crops
            viewer.show_floating_crop(viewer.rows['1'], (0, 0, 1, 1), (1, source))
            viewer.stop()
            assert not viewer.floating_crops
            return {'passed': True, 'source_resolution': '1920x1080', 'app_wide_drag': True,
                    'wheel_corner_resize': True, 'four_corner_resize': True, 'editable_selection': True,
                    'inline_actions': True, 'fine_tune_button': True, 'replay_source_time': True,
                    'resize_placement_restored': True, 'cursor_zoom': True, 'burst_repaints_100_to_1': True, 'network_requests': 0}
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
