"""Prepare an opt-in root launcher for match-viewer Qt dispatch investigation."""
import argparse
import functools
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time


class UiTrace:
    def __init__(self, path):
        self.path = Path(path)
        self.main_thread = threading.get_ident()
        self.enabled = False
        self.closed = False
        self.deadline = 0.
        self.sequence = 0
        self.stack = []
        self.stats = {}
        self.dropped = 0
        self.records = queue.Queue(maxsize=2048)
        self.stopped = threading.Event()
        self.worker = threading.Thread(target=self._write, name='match-ui-trace-writer', daemon=True)
        self.worker.start()
        self.emit('installed', automatic_capture=False, threshold_ms=8, limit_seconds=90)

    def emit(self, phase, **fields):
        row = dict(time=time.time(), pid=os.getpid(), phase=phase, **fields)
        try:
            self.records.put_nowait(row)
        except queue.Full:
            self.dropped += 1

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8', buffering=1) as output:
            while not self.stopped.is_set() or not self.records.empty():
                try:
                    row = self.records.get(timeout=.1)
                except queue.Empty:
                    continue
                output.write(json.dumps(row, ensure_ascii=False) + '\n')

    def arm(self):
        if self.closed:
            return
        self.stats = {}
        self.deadline = time.perf_counter() + 90
        self.enabled = True
        self.emit('capture-start', limit_seconds=90)

    def finish(self):
        if not self.enabled:
            return
        self.enabled = False
        self.emit('capture-stop', dropped=self.dropped, functions=sorted(
            self.stats.values(), key=lambda item: item['total_ms'], reverse=True)[:40])

    def close(self):
        self.finish()
        self.closed = True
        self.stopped.set()
        self.worker.join(timeout=2)

    def call(self, label, method, *args, category='call', **kwargs):
        if not self.enabled or threading.get_ident() != self.main_thread:
            return method(*args, **kwargs)
        if time.perf_counter() >= self.deadline:
            self.finish()
            return method(*args, **kwargs)
        self.sequence += 1
        frame = dict(span=self.sequence, parent=self.stack[-1]['span'] if self.stack else None,
                     label=label, category=category, children=0, child_ms=0.)
        self.stack.append(frame)
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            return method(*args, **kwargs)
        finally:
            duration = (time.perf_counter() - wall) * 1000
            cpu_ms = (time.thread_time() - cpu) * 1000
            self.stack.pop()
            if self.stack:
                self.stack[-1]['children'] += 1
                self.stack[-1]['child_ms'] += duration
            if label in self.stats or len(self.stats) < 256:
                row = self.stats.setdefault(label, dict(label=label, calls=0, total_ms=0., max_ms=0.))
                row['calls'] += 1
                row['total_ms'] += duration
                row['max_ms'] = max(row['max_ms'], duration)
            if duration >= 8:
                self.emit('slow-span', **frame, duration_ms=round(duration, 3),
                          thread_cpu_ms=round(cpu_ms, 3),
                          exclusive_wall_ms=round(max(0., duration-frame['child_ms']), 3),
                          modal_wait_possible=label.endswith('.exec') or frame['children'] > 8)

    def dispatch(self, notify, receiver, event):
        if not self.enabled or threading.get_ident() != self.main_thread:
            return notify(receiver, event)
        # Save only primitive metadata before delivery; either object can be deleted.
        label = type(receiver).__name__ + ':' + event.type().name
        return self.call(label, notify, receiver, event, category='qt-event')

    def wrap(self, owner, name, label, *, no_args=False):
        original = getattr(owner, name)
        if no_args:
            @functools.wraps(original)
            def wrapped(instance):
                return self.call(label, original, instance)
        else:
            @functools.wraps(original)
            def wrapped(*args, **kwargs):
                return self.call(label, original, *args, **kwargs)
        setattr(owner, name, wrapped)

    def wrap_caption(self, owner):
        self.wrap(owner, 'mouseMoveEvent', 'CropDialog.mouseMoveEvent')
        original = owner.nativeEvent
        trace = self
        messages = {0x00a1: 'caption-press', 0x0231: 'enter-size-move',
                    0x0232: 'exit-size-move', 0x0215: 'capture-changed'}

        @functools.wraps(original)
        def native(instance, event_type, message):
            if not trace.enabled or threading.get_ident() != trace.main_thread or sys.platform != 'win32':
                return original(instance, event_type, message)
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            name = messages.get(msg.message)
            if name is None or msg.message == 0x00a1 and msg.wParam != 2:
                return original(instance, event_type, message)
            # Save primitives before dispatch; it may destroy the receiver.
            dragging = instance._caption_drag is not None
            trace.emit('caption-native', point='enter', message=name, dragging=dragging)
            result = trace.call('CropDialog.native.'+name, original, instance, event_type, message)
            if trace.enabled:
                trace.emit('caption-native', point='return', message=name, handled=bool(result[0]))
            return result

        owner.nativeEvent = native


def install(host, directory):
    sys.path.insert(0, str(Path(host).resolve()))
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QKeySequence, QShortcut, QWindow
    from PySide6.QtWidgets import QApplication, QWidget, QDialog
    from ddm import app as host_app, continuous_corners, theme, settings_general, settings_host
    from ddm.dialogs import SettingsDialog
    if QApplication.instance() is not None:
        raise RuntimeError('Install dispatch capture before QApplication construction')
    trace = UiTrace(Path(directory) / f'qt-ui-dispatch-{os.getpid()}.jsonl')
    application_type = host_app.QApplication
    class Application(application_type):
        def notify(self, receiver, event):
            return trace.dispatch(super().notify, receiver, event)
    host_app.QApplication = Application
    for name in ('show', 'hide', 'close'):
        trace.wrap(QWidget, name, 'QWidget.'+name, no_args=True)
    for name in ('setStyleSheet', 'setParent', 'setVisible', 'setWindowFlags', 'move'):
        trace.wrap(QWidget, name, 'QWidget.'+name)
    trace.wrap(QWindow, 'setParent', 'QWindow.setParent')
    trace.wrap(QDialog, 'exec', 'QDialog.exec', no_args=True)
    for owner, names in ((continuous_corners.SurfaceFilter, ('values', 'eventFilter')),
                         (theme.ThemeSignals, ('eventFilter',)),
                         (settings_general.GeneralSettingsPage, ('__init__',)),
                         (SettingsDialog, ('__init__',)),
                         (settings_host.SettingsHost, ('close',))):
        for name in names:
            trace.wrap(owner, name, owner.__name__+'.'+name, no_args=name=='close')
    original = host_app.MainWindow.__init__
    @functools.wraps(original)
    def initialize(window, *args, **kwargs):
        original(window, *args, **kwargs)
        window._match_ui_dispatch_trace = trace
        QApplication.instance()._match_ui_dispatch_trace = trace
        shortcut = QShortcut(QKeySequence('Ctrl+Alt+F9'), window)
        shortcut.setContext(Qt.ApplicationShortcut)
        shortcut.activated.connect(lambda: trace.finish() if trace.enabled else trace.arm())
        window._match_ui_dispatch_shortcut = shortcut
        timer = QTimer(window)
        timer.setInterval(1000)
        timer.timeout.connect(lambda: trace.finish() if trace.enabled and time.perf_counter() >= trace.deadline else None)
        timer.start()
        window._match_ui_dispatch_timer = timer
        for plugin in window.plugins.plugins:
            if plugin.context.name != 'match_sync':
                continue
            package = type(plugin).__module__
            for part, names in (('floating_control', ('choose_crop',)),
                                ('picture_control', ('open_match_settings',)),
                                ('sending', ('show_chat_actions', 'show_interactions'))):
                module = sys.modules.get(package+'.'+part)
                if module is None:
                    continue
                owner = module.SendConsole if part=='sending' else module
                for name in names:
                    trace.wrap(owner, name, 'match_sync.'+name)
            crop = sys.modules.get(package+'.crop_dialog')
            if crop is not None:
                trace.wrap_caption(crop.CropDialog)
        QApplication.instance().aboutToQuit.connect(trace.close)
        trace.emit('window-ready', shortcut='Ctrl+Alt+F9', capture_active=False)
    host_app.MainWindow.__init__ = initialize
    return trace


def prepare(host, directory):
    host, directory = Path(host).resolve(), Path(directory).resolve()
    if not (host/'run.cmd').is_file():
        raise ValueError('Host root run.cmd is required')
    if any(char in str(host)+str(directory) for char in '%\r\n"'):
        raise ValueError('Unsupported launcher path')
    directory.mkdir(parents=True, exist_ok=True)
    tool = Path(__file__).resolve()
    bootstrap = f'''import os,sys
from pathlib import Path
if os.environ.get('DDM_MATCH_UI_DISPATCH_CAPTURE')=='1' and Path(sys.argv[0]).name=='diagnose_drag_windows.py':
    sys.path.insert(0, {str(tool.parent)!r})
    from diagnose_match_ui_dispatch import install
    install({str(host)!r}, {str(directory)!r})
'''
    (directory/'sitecustomize.py').write_text(bootstrap, encoding='utf-8')
    command = ('@echo off\nsetlocal\nset "DDM_MATCH_UI_DISPATCH_CAPTURE=1"\n'
               f'set "PYTHONPATH={directory};%PYTHONPATH%"\ncd /d "{host}"\ncall "{host / "run.cmd"}"\n')
    launcher = directory/'run-ui-dispatch.cmd'
    launcher.write_text(command, encoding='utf-8')
    return launcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.host, args.output))


if __name__ == '__main__':
    main()
