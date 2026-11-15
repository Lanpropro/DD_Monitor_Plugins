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
    with tempfile.TemporaryDirectory() as directory, patch.object(module.Decoder, 'start', lambda _self: None), patch.object(module.chat_control, 'Chat', Chat), patch.object(module.AudioPump, 'start', lambda _self: None):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(plugins.PluginManager(window=host, plugins_dir=directory), 'crop_drag'), {})
        delivered = []
        try:
            for key in ('1', '2'):
                viewer.add_room(key)
            viewer.toggle_running()
            viewer.playback_probe.stop()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            viewer.audio.timer.stop()
            image = QImage(640, 360, QImage.Format_RGB32)
            image.fill(Qt.red)
            crop = viewer.show_floating_crop(viewer.rows['2'], (0, 0, 1, 1), (1, image))
            work_us = [0]
            insertion_us = [500]
            def add(event):
                delivered.append((event['source_room'], event['text']))
                work_us[0] += insertion_us[0]  # Deterministic work model; not a performance measurement.
            def enqueue():
                for row in viewer.rows.values():
                    for index in range(100):
                        row.pending.append((time.monotonic() - 40, {'kind': 'danmaku', 'text': str(index), 'uname': 'fixture'}))
            enqueue()
            with patch.object(viewer.panel, 'add_event', side_effect=add), \
                    patch.object(module.time, 'perf_counter', side_effect=lambda: work_us[0] / 1_000_000), \
                    patch.object(viewer, '_sync_sidebar_marks') as marks:
                crop.drag = (QPoint(), crop.geometry(), False)
                viewer.render()
                assert len(delivered) == 4, 'Bound list work to two messages per source while dragging'
                assert sum(len(row.pending) for row in viewer.rows.values()) == 196
                marks.assert_not_called()
                crop.drag = None
                viewer.render()
                assert marks.call_count == 1
                assert 4 < len(delivered) <= 14, 'Release must not drain an entire costly backlog in one frame'
                assert sum(len(row.pending) for row in viewer.rows.values()) + len(delivered) == 200
                assert all(event['uname'] == 'fixture' for row in viewer.rows.values() for _, event in row.pending), \
                    'Deferred messages must stay unmodified until delivery'
                batches = 0
                while any(row.pending for row in viewer.rows.values()):
                    assert batches < 200, 'Budget must make progress'
                    before = len(delivered)
                    viewer.render()
                    assert 0 < len(delivered) - before <= 10
                    batches += 1
                assert len(delivered) == 200
                for key in ('1', '2'):
                    assert [text for room, text in delivered if room == key] == list(map(str, range(100))), 'Deliver backlog in order without loss'
                assert len(viewer.replay.messages) == 200, 'Every live message must still enter replay history'
                insertion_us[0] = 5000
                row = viewer.rows['1']
                for text in ('slow-1', 'slow-2'):
                    row.pending.append((time.monotonic() - 40, {'kind': 'danmaku', 'text': text, 'uname': 'fixture'}))
                viewer.render()
                assert len(delivered) == 201 and len(row.pending) == 1, 'One slow insertion must yield after making progress'
                assert row.pending[0][1]['uname'] == 'fixture'
                viewer.render()
                assert len(delivered) == 202 and not row.pending
                assert len(viewer.replay.messages) == 202
            return {'passed': True, 'queued_messages': 200, 'drag_list_updates': 4, 'no_chat_loss': True,
                    'time_budget_model': True, 'remaining_messages_unmodified': True, 'slow_insertion_progress': True}
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
