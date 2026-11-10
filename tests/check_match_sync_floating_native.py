"""Transient native crop move routing, live image reuse and cleanup; offline."""
import argparse
import ctypes
from ctypes import wintypes
from importlib import import_module
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QWidget
    from shiboken6 import isValid
    native = import_module(module.__package__ + '.floating_move')
    floating = import_module(module.__package__ + '.floating')
    host.setAttribute(Qt.WA_DontShowOnScreen)
    host.resize(1200, 800)
    host.show()
    image = QImage(640, 360, QImage.Format_RGB32)
    image.fill(Qt.red)
    crop = floating.FloatingCrop(host, 'Native crop fixture', (0, 0, 1, 1))
    crop.set_frame((1., image))
    crop.fit_geometry(50, 60, 240)
    crop.show()
    show = native.MoveSurface.show

    def hidden(surface):
        surface.setAttribute(Qt.WA_DontShowOnScreen)
        show(surface)

    def begin():
        crop.drag = (QPoint(100, 100), QRect(crop.geometry()), None)
        accepted = []
        event = SimpleNamespace(spontaneous=lambda: True, position=lambda: QPointF(40, 40),
            globalPosition=lambda: QPointF(120, 120), accept=lambda: accepted.append(True))
        crop.mouseMoveEvent(event)
        assert accepted == [True]
        move = crop.native_move
        assert move.active and crop.isHidden() and move.preview.isVisible()
        assert move.preview.isWindow() and move.preview.parentWidget() is host
        assert move.preview.windowFlags() & Qt.Tool
        assert move.preview.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert not crop.testAttribute(Qt.WA_NativeWindow), 'Do not promote the crop or its ancestors'
        return move

    try:
        traces = []
        with patch.object(native, '_trace', side_effect=traces.append), \
                patch.object(ctypes.windll.user32, 'GetAsyncKeyState', return_value=0), \
                patch.object(native.MoveSurface, 'show', hidden), \
                patch.object(QApplication, 'platformName', return_value='windows'), \
                patch('PySide6.QtGui.QWindow.startSystemMove', return_value=True) as start:
            move = begin()
            start.assert_called_once()
            assert move.preview.size() == crop.size()
            assert crop.input_frame[1] is image
            green = QImage(image.size(), image.format())
            green.fill(Qt.green)
            crop.set_frame((2., green))
            assert move.preview.grab().toImage().pixelColor(crop.width()//2, crop.height()//2) == QColor(Qt.green)
            assert move.preview.grab().toImage().pixelColor(0, 0).alpha() == 0
            with patch.object(move.preview, 'update') as update:
                crop.set_frame((2., green))
                update.assert_not_called()
                crop.set_frame((3., green))
                update.assert_called_once()
            move.preview.move(host.mapToGlobal(QPoint(260, 190)))
            move.finish()
            assert crop.native_move is None and crop.drag is None
            assert crop.pos() == QPoint(260, 190) and crop.isVisible()
            assert crop.input_frame[0] == 3.
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not isValid(move) and not isValid(move.preview)

            # Screen RECTs use physical pixels, including negative desktop origins.
            move = begin()
            def client_origin(hwnd, pointer):
                point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
                point.x -= 1200
                point.y += 100
                return 1
            picture = QWidget(host)
            picture.setGeometry(300, 180, 500, 300)
            crop.picture_widget = picture
            for lock in (0, 1):
                crop.lock_mode = lock
                bounds = crop.movement_bounds()
                scale = 1.5
                width, height = round(crop.width()*scale), round(crop.height()*scale)
                with patch.object(host, 'devicePixelRatioF', return_value=scale), \
                        patch.object(ctypes.windll.user32, 'ClientToScreen', side_effect=client_origin):
                    for x, y in ((-5000, -5000), (5000, 5000)):
                        rect = wintypes.RECT(x, y, x+width, y+height)
                        move.constrain(rect)
                        left, top = -1200+round(bounds.x()*scale), 100+round(bounds.y()*scale)
                        assert left <= rect.left <= left+round(bounds.width()*scale)-width
                        assert top <= rect.top <= top+round(bounds.height()*scale)-height
                        assert rect.right-rect.left == width and rect.bottom-rect.top == height
            crop.lock_mode = 0
            original = QRect(move.original)
            move.preview.move(host.mapToGlobal(QPoint(500, 300)))
            app.sendEvent(host, QEvent(QEvent.Move))
            assert crop.native_move is None and crop.geometry() == original and crop.isVisible()

            move = begin()
            message = wintypes.MSG()
            move.preview.move(host.mapToGlobal(QPoint(210, 140)))
            with patch.object(QWidget, 'nativeEvent', return_value=(False, 0)):
                message.message = 0x0231
                move.preview.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
                assert traces[-1] == 'entered'
                message.message = 0x0232
                move.preview.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
            assert crop.native_move is None and crop.pos() == QPoint(210, 140)

            move = begin()
            original = QRect(move.original)
            move.preview.move(host.mapToGlobal(QPoint(500, 300)))
            with patch.object(ctypes.windll.user32, 'GetAsyncKeyState', return_value=0x8000), \
                    patch.object(QWidget, 'nativeEvent', return_value=(False, 0)):
                move.preview.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
            assert crop.native_move is None and crop.geometry() == original

            move = begin()
            other = QWidget()
            other.setAttribute(Qt.WA_DontShowOnScreen)
            other.resize(host.size())
            other.show()
            session = SimpleNamespace(floating_crops={'fixture': crop})
            module.floating_control.reparent_floating_crops(session, other)
            assert crop.native_move is None and not move.active and crop.parentWidget() is other
            module.floating_control.reparent_floating_crops(session, host)
            other.close()
            other.deleteLater()
            for _ in range(20):
                move = begin()
                move.finish()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                assert not any(isinstance(w, native.MoveSurface) for w in app.topLevelWidgets())

            # Closing a source must not reveal it again or retain the proxy.
            move = begin()
            crop.close()
            assert crop.native_move is None and not move.active and not move.preview.isVisible()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not isValid(crop) and not isValid(move.preview)

            crop = floating.FloatingCrop(host, 'Fallback fixture', (0, 0, 1, 1))
            crop.fit_geometry(30, 30, 240)
            crop.show()
            crop.drag = (QPoint(), QRect(crop.geometry()), None)
            with patch('PySide6.QtGui.QWindow.startSystemMove', return_value=False):
                assert not native.start_native_move(crop, SimpleNamespace(spontaneous=lambda: True,
                    globalPosition=lambda: QPointF()))
                assert crop.native_move is None and crop.isVisible() and crop.drag is not None
            with patch.object(native, 'NativeCropMove', side_effect=AssertionError('Synthetic input must not start a system move')):
                assert not native.start_native_move(crop, SimpleNamespace(spontaneous=lambda: False))
            move = begin()
            crop.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not isValid(crop) and not isValid(move.preview)
        return dict(passed=True, transient_tool=True, source_remains_child=True,
                    shared_live_image=True, native_move_routed=True,
                    physical_bounds_and_negative_desktop=True, cell_lock_bounds=True,
                    native_exit_and_owner_move_cleanup=True, source_close_cleanup=True,
                    escape_restores_press_position=True,
                    fullscreen_reparent_cleanup=True, transient_surfaces_released_20_cycles=True,
                    source_direct_deletion_cleanup=True,
                    unsupported_fallback=True, actual_system_drag=False,
                    real_streams=0, real_messages_sent=0, performance_claim=False)
    finally:
        if isValid(crop):
            crop.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer
    app = QApplication([])
    print(check(app, QWidget(), viewer))


if __name__ == '__main__':
    main()
