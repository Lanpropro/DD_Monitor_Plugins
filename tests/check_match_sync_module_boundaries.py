"""Presentation/audio ownership and legacy exports; no network or audio device."""
import argparse
import ast
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QEvent, QMetaObject, QObject, QThread, Qt, Signal
    from shiboken6 import isValid
    from ddm import plugins
    audio = import_module(module.__package__ + '.audio')
    canvas = import_module(module.__package__ + '.canvas')
    crop_dialog = import_module(module.__package__ + '.crop_dialog')
    crop_ui = import_module(module.__package__ + '.crop_ui')
    crop_process = import_module(module.__package__ + '.crop_process')
    crop_protocol = import_module(module.__package__ + '.crop_protocol')
    crop_worker = import_module(module.__package__ + '.crop_worker')
    room_widgets = import_module(module.__package__ + '.room_widgets')
    comparison = import_module(module.__package__ + '.comparison')
    room_input = import_module(module.__package__ + '.room_input')
    timing = import_module(module.__package__ + '.playback_timing')
    sending = import_module(module.__package__ + '.sending')
    chat_panel = import_module(module.__package__ + '.chat_panel')
    composers = import_module(module.__package__ + '.send_composers')
    media = import_module(module.__package__ + '.media')
    clients = import_module(module.__package__ + '.chat_clients')
    signals = import_module(module.__package__ + '.media_signals')
    assert sending.SendingDanmakuPanel is chat_panel.SendingDanmakuPanel
    assert sending.SendRow is composers.SendRow and sending.BatchSend is composers.BatchSend
    assert media.Chat is clients.Chat and media.PlatformChat is clients.PlatformChat
    assert media.Events is clients.Events is signals.Events
    controller_names = ('source_control', 'chat_control', 'alignment_control', 'picture_control',
                        'recording_control', 'replay_control', 'presentation', 'floating_control',
                        'viewer_ui', 'host_integration', 'console_control', 'session_control')
    controllers = [import_module(module.__package__ + '.' + name) for name in controller_names]
    assert module.AudioFeed is audio.AudioFeed and module.AudioPump is audio.AudioPump
    assert module.Canvas is canvas.Canvas
    assert module.RATE == audio.RATE and module.QImage is canvas.QImage
    assert module.RoomRow is room_widgets.RoomRow and module.CompactStatus is room_widgets.CompactStatus
    assert module.ComparisonPanel is comparison.ComparisonPanel
    assert module.parse_room is room_input.parse_room and module.cache_gap is timing.cache_gap
    assert module.picture_poll_interval is timing.picture_poll_interval
    assert room_input.parse_room('https://live.bilibili.com/00123') == '123'
    try:
        room_input.parse_room('https://example.invalid/123')
    except ValueError:
        pass
    else:
        raise AssertionError('Input validation boundary accepted an unrelated host')
    assert module.Results is module.alignment_control.Results
    assert module.floating_control.CropDialog is crop_dialog.CropDialog
    for owned in (audio, canvas, crop_dialog, crop_ui, crop_process, crop_protocol, crop_worker,
                  room_widgets, comparison, room_input, timing,
                  chat_panel, composers, clients, signals, *controllers):
        source = ast.parse(Path(owned.__file__).read_text(encoding='utf-8'))
        assert not any(isinstance(n, ast.ImportFrom) and n.module == 'viewer' for n in ast.walk(source)), \
            'Owning modules must not import their coordinator'
    sinks = []
    class Sink(QObject):
        stateChanged = Signal()
        def __init__(self, output, fmt, parent):
            super().__init__(parent)
            self.owner = QThread.currentThread()
            assert self.owner == parent.thread() != app.thread()
            self.started = time.perf_counter()
            self.written = self.writes = self.resets = 0
            sinks.append(self)
        def check_owner(self):
            assert QThread.currentThread() == self.owner
        def setBufferSize(self, size):
            self.check_owner()
            self.capacity = size // 4
        def start(self):
            self.check_owner()
            return self
        def processedUSecs(self):
            self.check_owner()
            return int((time.perf_counter() - self.started) * 1_000_000)
        def bytesFree(self):
            self.check_owner()
            queued = self.written - int((time.perf_counter() - self.started) * audio.RATE)
            return max(0, min(2048, self.capacity - queued)) * 4
        def write(self, data):
            self.check_owner()
            self.written += len(data) // 4
            self.writes += 1
            return len(data)
        def reset(self):
            self.check_owner()
            self.started = time.perf_counter()
            self.written = 0
            self.resets += 1
        def stop(self):
            self.check_owner()
        def error(self):
            self.check_owner()
            return SimpleNamespace(value=0)

    output = SimpleNamespace(id=lambda: b'fixture', isNull=lambda: False,
                             isFormatSupported=lambda _format: True)
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'module_boundaries'), {})
        try:
            assert type(viewer.canvas) is canvas.Canvas and viewer.canvas.parentWidget() is viewer.picture.video
            assert viewer.canvas.paint_probe is viewer.playback_probe
            assert type(viewer.comparison_panel) is comparison.ComparisonPanel
            assert type(viewer.panel) is chat_panel.SendingDanmakuPanel
            assert viewer.panel.parent() is viewer.chat_pane
            assert viewer.panel._divider_resize.parent() is viewer.panel
            batch = viewer.send_console.batch_panel
            assert type(batch) is composers.BatchSend and batch.console is viewer.send_console
            assert batch.timer.parent() is batch and batch.timer.interval() == 50
            assert not batch.timer.isActive() and not batch.pending and batch.active is None
            for timer, interval in ((viewer.crop_repaint_timer, 16), (viewer.buffer_notice_timer, 15000),
                                    (viewer.removal_timer, 5000), (viewer.render_timer, 33),
                                    (viewer.match_timer, 500), (viewer.save_timer, 350)):
                assert timer.parent() is viewer and timer.thread() == app.thread()
                assert timer.interval() == interval
            viewer.add_room('1', {'alias': 'fixture'})
            row = viewer.rows['1']
            assert type(row) is room_widgets.RoomRow and row.viewer is viewer
            assert type(row.status) is room_widgets.CompactStatus
            assert type(viewer.results) is module.alignment_control.Results
            # Overrides keep the original QObject receiver and bound QDialog fallback.
            from PySide6.QtWidgets import QDialog
            marker_event = QEvent(QEvent.User)
            assert viewer.eventFilter(row, marker_event) == QDialog.eventFilter(viewer, row, marker_event)
            with patch.object(module.host_integration, '_shortcut_action', return_value=None) as handler:
                assert viewer._shortcut_action(row, marker_event) is None
                handler.assert_called_once_with(viewer, row, marker_event)
            with patch.object(module.session_control, 'reject') as handler:
                viewer.reject()
                receiver, base = handler.call_args.args
                assert receiver is viewer and base.__self__ is viewer and base.__name__ == 'reject'
            marker = object()
            with patch.object(module.source_control, '_valid', return_value=marker) as valid:
                assert viewer._valid(row, None) is marker
                valid.assert_called_once_with(viewer, row, None, False)
            pump = viewer.audio
            assert type(pump) is audio.AudioPump and pump.parent() is viewer
            assert QObject.thread(pump) == app.thread() and pump.thread is None
            assert pump.timer.parent() is pump and pump.timer.thread() == app.thread()
            assert pump.timer.interval() == 10 and pump.timer.timerType() == Qt.PreciseTimer
            with patch.object(audio.QMediaDevices, 'defaultAudioOutput', return_value=output), \
                    patch.object(audio, 'QAudioSink', Sink):
                for _ in range(3):
                    pump.start()
                    deadline = time.perf_counter() + 2
                    while not sinks or pump.sink is None or not sinks[-1].writes:
                        assert time.perf_counter() < deadline, 'Audio worker did not start'
                        app.processEvents()
                        time.sleep(.005)
                    feed, worker, sink = pump.feed, pump.thread, sinks[-1]
                    assert feed.thread() == worker and feed.parent() is None
                    assert worker.parent() is pump and worker.isRunning()
                    assert feed.timer.parent() is feed and feed.timer.thread() == worker
                    assert feed.timer.interval() == 10 and feed.timer.timerType() == Qt.PreciseTimer
                    before = sink.writes
                    time.sleep(.04)  # No GUI event processing; not a performance benchmark.
                    assert sink.writes > before, 'Feeding must remain on the audio thread'
                    QMetaObject.invokeMethod(feed, 'flush', Qt.BlockingQueuedConnection)
                    assert sink.resets == 1
                    pump.stop()
                    app.sendPostedEvents(None, QEvent.DeferredDelete)
                    assert pump.feed is pump.thread is pump.sink is pump.device is None
                    assert not pump.timer.isActive()
                    assert not isValid(feed) and not isValid(sink) and not isValid(worker)
            return dict(passed=True, compatibility_identity=True, independent_modules=True,
                        canvas_parent_preserved=True, audio_parent_preserved=True,
                        gui_and_audio_timers_preserved=True, worker_cycles=3,
                        feed_flush_stop_on_worker=True, no_real_audio_device=True)
        finally:
            viewer.close()
            viewer.deleteLater()
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
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
