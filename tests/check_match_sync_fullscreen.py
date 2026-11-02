"""Fullscreen shell marking and all exit paths; no accounts or live media."""
import argparse
import ctypes
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import Mock, patch


def check_com(fullscreen):
    if sys.platform != 'win32':
        return
    for apartment, create, initialize, mark in (
            (0, 0, 0, 0), (1, 0, 0, 0), (-2147417850, 0, 0, 0),
            (-1, 0, 0, 0), (0, -1, 0, 0), (0, 0, -1, 0), (0, 0, 0, -1)):
        calls = []
        callbacks = [
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(lambda _p: calls.append('release') or 0),
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(lambda _p: initialize),
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)(
                lambda _p, hwnd, enabled: calls.append((hwnd, enabled)) or mark)]
        table = (ctypes.c_void_p * 9)()
        for index, callback in zip((2, 3, 8), callbacks):
            table[index] = ctypes.cast(callback, ctypes.c_void_p).value
        instance = (ctypes.c_void_p * 1)(ctypes.addressof(table))

        def create_instance(_clsid, _outer, _context, _iid, output):
            if create >= 0:
                ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(instance)
            return create

        ole = types.SimpleNamespace(CoInitializeEx=Mock(return_value=apartment),
            CoCreateInstance=Mock(side_effect=create_instance), CoUninitialize=Mock())
        with patch.object(ctypes.windll, 'ole32', ole):
            result = fullscreen.mark_fullscreen(0x123456789, True)
        usable = apartment >= 0 or apartment == -2147417850
        assert result == (usable and create >= 0 and initialize >= 0 and mark >= 0)
        assert calls.count('release') == int(usable and create >= 0)
        assert ((0x123456789, 1) in calls) == (usable and create >= 0 and initialize >= 0)
        assert ole.CoUninitialize.call_count == int(apartment >= 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--native-api', action='store_true', help='Windows shell API with a hidden window only')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(args.host.resolve()), str(root)]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'windows' if args.native_api else 'offscreen'
    dll = os.add_dll_directory(str(args.host.resolve())) if sys.platform == 'win32' else None
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    import plugins_user
    package = types.ModuleType('plugins_user._match_sync')
    package.__path__ = [str(root / 'plugins/match_sync')]
    sys.modules[package.__name__] = package
    plugins_user._match_sync = package
    from plugins_user._match_sync import fullscreen, viewer as module
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    check_com(fullscreen)
    if args.native_api:
        widget = QWidget()
        widget.setAttribute(Qt.WA_DontShowOnScreen)
        hwnd = int(widget.winId())
        assert not widget.isVisible()
        assert fullscreen.mark_fullscreen(hwnd, True), 'Native fullscreen mark failed'
        assert fullscreen.mark_fullscreen(hwnd, False), 'Native fullscreen unmark failed'
        assert not widget.isVisible()
        widget.close()
        print('PASS: real ITaskbarList2 mark/unmark on a hidden HWND; no visible desktop changes')
    else:
        from dev.selfcheck_match_sync import FakeChat, FakeDecoder
        from ddm import plugins
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(module, 'Decoder', FakeDecoder), patch.object(module, 'Chat', FakeChat), \
                patch.object(module.AudioPump, 'start', lambda _self: None):
            host = QWidget()
            host.settings = {}
            viewer = module.Viewer(plugins.PluginContext(
                plugins.PluginManager(window=host, plugins_dir=temporary), 'fullscreen_check'), {})
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            try:
                viewer.add_room('1')
                for mode in ('toggle', 'escape', 'stop', 'close'):
                    image = module.QImage(640, 360, module.QImage.Format_RGB32)
                    image.fill(Qt.red)
                    floating = viewer.show_floating_crop(viewer.rows['1'], (0, 0, .5, .5), (1, image))
                    sizes = viewer.body_split.sizes()
                    with patch.object(module.QApplication, 'platformName', return_value='windows'), \
                            patch.object(module, 'mark_fullscreen', return_value=True, create=True) as mark:
                        viewer._fullscreen_picture()
                        dialog = viewer.fullscreen_dialog
                        assert floating.parentWidget() is dialog
                        hwnd = int(dialog.winId())
                        assert dialog.windowType() == Qt.Window
                        assert dialog.settings is host.settings
                        assert dialog.windowFlags() & Qt.FramelessWindowHint
                        assert not dialog.windowFlags() & Qt.WindowStaysOnTopHint
                        assert dialog.isFullScreen() and dialog.screen() is host.screen()
                        assert dialog.layout().contentsMargins().isNull()
                        assert dialog._fullscreen_cursor.timer.isActive()
                        mark.assert_called_once_with(hwnd, True)
                        if mode == 'toggle':
                            viewer._fullscreen_picture()
                        elif mode == 'escape':
                            QTest.keyClick(dialog, Qt.Key_Escape)
                        elif mode == 'stop':
                            viewer.stop()
                        else:
                            viewer.close()
                        assert mark.call_args_list[-1].args == (hwnd, False), (mode, hwnd, mark.call_args_list)
                        assert mark.call_count == 2
                        assert viewer.fullscreen_dialog is None
                        if mode in ('stop', 'close'):
                            assert not viewer.floating_crops
                        else:
                            assert floating.parentWidget() is viewer
                        assert viewer.body_split.widget(0) is viewer.picture
                        assert viewer.body_split.sizes() == sizes
                        assert not dialog._fullscreen_cursor.timer.isActive()
            finally:
                viewer.close()
                host.close()
                app.processEvents()
        print('PASS: fullscreen window type, screen, shell mark lifecycle and toggle/Esc/stop/close restoration')
    app.shutdown()
    if dll is not None:
        dll.close()


if __name__ == '__main__':
    main()
