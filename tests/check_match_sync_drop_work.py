"""Room updates retain composers; disk PCM setup never blocks GUI construction."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QPushButton
    from ddm import plugins
    from ddm.plugin_controls import TileControl
    cache = import_module(module.__package__ + '.replay_cache')
    with patch.object(cache.tempfile, 'TemporaryFile', side_effect=AssertionError('No disk setup on GUI construction')):
        ring = cache.DiskAudioRing(.1)
        assert ring.read(0, 8) == bytes(32)
    allocating, release, read_done = threading.Event(), threading.Event(), threading.Event()
    writer_id, errors = [], []
    real_file = cache.tempfile.TemporaryFile
    pcm = bytes((1, 2, 3, 4)) * 12
    def slow_file(*args, **kwargs):
        writer_id.append(threading.get_ident())
        allocating.set()
        assert release.wait(3), 'Test did not release disk allocation'
        return real_file(*args, **kwargs)
    def write():
        try:
            ring.append_at(4, pcm)
        except BaseException as error:
            errors.append(error)
    def read():
        assert ring.read(0, 8) == bytes(32)
        read_done.set()
    with patch.object(cache.tempfile, 'TemporaryFile', slow_file):
        writer = threading.Thread(target=write)
        reader = threading.Thread(target=read)
        writer.start()
        try:
            assert allocating.wait(2)
            reader.start()
            assert read_done.wait(1), 'Reading an unprepared cache must not wait for disk setup'
        finally:
            release.set()
            writer.join(3)
            if reader.ident is not None:
                reader.join(3)
    assert not errors, errors
    assert writer_id == [writer.ident] and writer.ident != threading.get_ident()
    assert ring.read(0, 16) == bytes(16) + pcm
    ring.data.close()
    ring.file.close()

    class Composer(TileControl):
        def __init__(self, context):
            widget = QWidget(context.parent)
            layout = QHBoxLayout(widget)
            self.edit = QLineEdit()
            layout.addWidget(self.edit)
            layout.addWidget(QPushButton('发送'))
            super().__init__(widget, minimum_width=80)

        def focus_input(self):
            return True

    with tempfile.TemporaryDirectory() as directory:
        host.settings, host.rooms = {}, []
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        manager.register_tile_control('fixture', 'send', Composer)
        viewer = module.Viewer(plugins.PluginContext(manager, 'drop_work'), {})
        try:
            viewer.add_room('1')
            console = viewer.send_console
            item = console.rooms.item(0)
            composer = console.rows['1']
            editor = composer.tile._plugin_controls._repeat_editor()
            editor.setText('preserved draft')
            with patch.object(console.rooms, 'clear', side_effect=AssertionError('Do not rebuild recipients')), \
                    patch.object(console, 'setStyleSheet', wraps=console.setStyleSheet) as style:
                for _ in range(10):
                    console.sync_rooms()
                viewer.add_room('2')
                assert console.rooms.item(0) is item and console.rows['1'] is composer
                assert style.call_count == 0, 'Unchanged console style must not repolish its entire subtree'
                second = console.rooms.item(1)
                second.setCheckState(Qt.Checked)
                viewer.rows = {'2': viewer.rows['2'], '1': viewer.rows['1']}
                viewer.update_main_choices()
                assert console.rooms.item(1) is item and console.rooms.item(0) is second
                assert set(console.checked_rooms()) == {'1', '2'}
                assert editor.text() == 'preserved draft'
                viewer.rows['1'].alias.setText('renamed')
                assert console.rows['1'] is composer and editor.text() == 'preserved draft'
                viewer.remove_room('2')
                assert console.rooms.count() == 1 and console.rooms.item(0) is item
            with patch.object(cache.tempfile, 'TemporaryFile', side_effect=AssertionError('GUI drop created disk cache')), \
                    patch.object(module.Decoder, 'start'), patch.object(module.Chat, 'start'), \
                    patch.object(module.AudioPump, 'start'):
                viewer.embedded = True
                try:
                    assert viewer._add_dragged('3') and viewer.running
                    assert all(row.decoder is not None for row in viewer.rows.values())
                finally:
                    viewer.stop()
                    viewer.embedded = False
            return {'passed': True, 'gui_disk_creations': 0, 'cold_read_nonblocking': True,
                    'recipient_rebuilds': 0, 'unchanged_styles_reapplied': 0, 'draft_and_selection_preserved': True}
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
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
