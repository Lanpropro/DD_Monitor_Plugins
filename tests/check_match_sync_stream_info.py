"""Offline right-click routing to the match decoder, not the suspended host player."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    from ddm import plugins
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(module.Decoder, 'start', side_effect=AssertionError('Offline check started decoding')), \
            patch.object(module.Chat, 'start', side_effect=AssertionError('Offline check connected chat')):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'stream_info'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        decoders = []
        try:
            for room_id, width, codec in (('101', 1920, 'h264'), ('202', 1280, 'hevc')):
                viewer.add_room(room_id)
                row = viewer.rows[room_id]
                row.alias.setText('Fixture ' + room_id)
                row.quality = row.actual_quality = 10000
                decoder = row.decoder = module.Decoder(room_id, {'quality': 10000})
                decoders.append(decoder)
                decoder.source_url = f'https://user:password@cdn{room_id}.test/live?token=secret'
                decoder.video_size = (width, 720)
                decoder.fps = 59.94
                decoder.history.codec = (codec, b'')
                decoder.history.frames.extend([(100., 0.), (106., 6.)])
            viewer.running = True
            viewer.select_main('101')
            viewer.resize(1100, 700)
            viewer.show()
            QTest.qWait(20)
            viewer.render_timer.stop()
            viewer.match_timer.stop()

            def choose_info(menu, *_args):
                next(action for action in menu.actions() if action.text() == '直播流信息…').trigger()

            def right_click():
                menu = viewer.picture.build_menu()
                point = QPoint(50, 50)
                event = QContextMenuEvent(QContextMenuEvent.Mouse, point,
                                          viewer.canvas.mapToGlobal(point))
                with patch.object(viewer.picture, 'build_menu', return_value=SimpleNamespace(
                        exec=lambda *_args: choose_info(menu), deleteLater=menu.deleteLater)):
                    app.sendEvent(viewer.canvas, event)
                app.processEvents()

            right_click()
            dialog = viewer.stream_info_dialog
            assert dialog is not None and dialog.isVisible(), 'Canvas menu did not open stream info'
            assert dialog.values['host'].text() == 'cdn101.test'
            assert '1920×720' in dialog.values['video'].text()
            assert 'h264' in dialog.values['video'].text()
            assert 'PyAV' in dialog.values['core'].text()
            assert '6 秒' in dialog.values['buffer'].text()
            assert dialog.values['format'].text() == '未知', 'Do not label source as NUT remux output'
            assert dialog.values['audio'].text() == '未知', 'Do not label source audio as converted PCM'
            assert dialog.values['download'].text() == '未知'
            snapshot = repr(viewer._stream_info_snapshot())
            assert all(secret not in snapshot for secret in ('password', 'token', 'secret', 'https://'))
            right_click()
            assert viewer.stream_info_dialog is dialog, 'Repeated opens must reuse the dialog'

            viewer.select_main('202')
            QTest.qWait(1100)
            assert dialog.values['host'].text() == 'cdn202.test'
            assert 'hevc' in dialog.values['video'].text()
            assert 'Fixture 202' in dialog.room.text()
            viewer.rows['202'].paused = True
            dialog.refresh()
            assert dialog.values['state'].text() == '已暂停'
            viewer.rows['202'].paused = False

            # A real worker owns the lock, so an RLock cannot mask a GUI wait.
            held, release = threading.Event(), threading.Event()
            def hold():
                with viewer.rows['202'].decoder.history.lock:
                    held.set()
                    release.wait(2)
            worker = threading.Thread(target=hold)
            worker.start()
            try:
                assert held.wait(1)
                started = time.perf_counter()
                dialog.refresh()
                assert time.perf_counter() - started < .1, 'Info refresh waited for decoding'
                assert dialog.values['buffer'].text() == '未知'
            finally:
                release.set()
                worker.join(2)

            show_fullscreen = module.QDialog.showFullScreen
            def hidden_fullscreen(window):
                window.setAttribute(Qt.WA_DontShowOnScreen)
                show_fullscreen(window)
            with patch.object(module.QDialog, 'showFullScreen', hidden_fullscreen):
                viewer._fullscreen_picture()
            fullscreen = viewer.fullscreen_dialog
            dialog.close()
            right_click()
            assert viewer.stream_info_dialog is not None
            fullscreen.close()
            app.sendPostedEvents(None, 52)
            viewer.stop()
            assert viewer.stream_info_dialog is None
            right_click()
            assert viewer.stream_info_dialog.values['state'].text() == '未播放'
            viewer.remove_room('101')
            viewer.remove_room('202')
            assert viewer.stream_info_dialog is None
            return {'passed': True, 'canvas_context_menu': True, 'singleton': True,
                    'main_switch_refresh': True, 'fullscreen': True, 'nonblocking': True,
                    'credentials_redacted': True, 'stop_cleanup': True, 'media_playback': False}
        finally:
            viewer.close()
            viewer.deleteLater()
            for decoder in decoders:
                decoder.stop()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    host = QWidget()
    try:
        print(check(app, host, module))
    finally:
        host.close()
        QApplication.sendPostedEvents(None, 52)
        app.shutdown()


if __name__ == '__main__':
    main()
