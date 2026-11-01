"""Bound list work during floating-crop gestures without dropping queued chat."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QImage
    from ddm import plugins
    events = import_module(module.__package__ + '.media').Events
    class Chat:
        def __init__(self, *_args):
            self.events = events()
        def start(self):
            pass
        def stop(self):
            pass
    with tempfile.TemporaryDirectory() as directory, patch.object(module.Decoder, 'start', lambda _self: None), patch.object(module, 'Chat', Chat), patch.object(module.AudioPump, 'start', lambda _self: None):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(plugins.PluginManager(window=host, plugins_dir=directory), 'crop_drag'), {})
        delivered = []
        try:
            for key in ('1', '2'):
                viewer.add_room(key)
            viewer.toggle_running()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            viewer.audio.timer.stop()
            image = QImage(640, 360, QImage.Format_RGB32)
            image.fill(Qt.red)
            crop = viewer.show_floating_crop(viewer.rows['2'], (0, 0, 1, 1), (1, image))
            def add(event):
                delivered.append((event['source_room'], event['text']))
                time.sleep(.0005)  # Model a busy rich-text panel, not a performance promise.
            def enqueue():
                for row in viewer.rows.values():
                    for index in range(100):
                        row.pending.append((time.monotonic() - 40, {'kind': 'danmaku', 'text': str(index), 'uname': 'fixture'}))
            enqueue()
            with patch.object(viewer.panel, 'add_event', side_effect=add), patch.object(viewer, '_sync_sidebar_marks') as marks:
                crop.drag = (QPoint(), crop.geometry(), False)
                started = time.perf_counter()
                viewer.render()
                dragging_ms = (time.perf_counter() - started) * 1000
                assert len(delivered) == 4, 'Bound list work to two messages per source while dragging'
                assert sum(len(row.pending) for row in viewer.rows.values()) == 196
                marks.assert_not_called()
                crop.drag = None
                started = time.perf_counter()
                viewer.render()
                released_ms = (time.perf_counter() - started) * 1000
                assert marks.call_count == 1
                assert len(delivered) == 164, 'Resume normal list throughput on release'
                viewer.render()
                assert len(delivered) == 200
                for key in ('1', '2'):
                    assert [text for room, text in delivered if room == key] == list(map(str, range(100))), 'Deliver backlog in order without loss'
            return {'passed': True, 'queued_messages': 200, 'drag_list_updates': 4, 'no_chat_loss': True,
                    'synthetic_drag_render_ms': round(dragging_ms, 2), 'synthetic_release_render_ms': round(released_ms, 2)}
        finally:
            viewer.close()
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
