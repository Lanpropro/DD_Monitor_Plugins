"""Isolated real-host input/paint benchmark; synthetic media is not live acceptance."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def summary(values):
    ordered = sorted(values)
    return {"count": len(values), **{key: round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)
            if ordered else None for key, q in (("p50", .5), ("p95", .95), ("max", 1))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('--mpv-dir', type=Path)
    parser.add_argument('--fixtures', type=Path)
    parser.add_argument('--size', default='1280x720')
    parser.add_argument('--soak', action='store_true', help='Run only automatic matching with comparison enabled')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--warmup', type=float, default=65)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    host = args.host.resolve()
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    sys.path.insert(0, str(host))
    dll_directory = None
    if args.mpv_dir is not None:
        os.environ['PATH'] = str(args.mpv_dir.resolve()) + os.pathsep + os.environ.get('PATH', '')
        if sys.platform == 'win32':
            dll_directory = os.add_dll_directory(str(args.mpv_dir.resolve()))
    import plugins_user
    package = types.ModuleType('plugins_user._match_sync')
    package.__path__ = [str(ROOT / 'plugins/match_sync')]
    sys.modules[package.__name__] = package
    plugins_user._match_sync = package
    from PySide6 import QtCore
    from PySide6.QtCore import QEvent, QObject, QPointF, QTimer, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from ddm import app as host_app, config, plugins, recording
    from ddm.dialogs import SettingsDialog
    from plugins_user._match_sync import media, viewer as module

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    metrics = {}
    phase = ['warmup']
    pending = []
    stop = threading.Event()
    report = {'qt': QtCore.qVersion(), 'python': sys.executable,
              'plugin': str(module.__file__), 'host': str(host), 'phases': {},
              'source_sha256': hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
              'warmup_seconds': args.warmup, 'phase_seconds': args.seconds, 'size': args.size}
    phases = ((True, True),) if args.soak else ((True, False), (False, False), (True, True), (False, True))
    denied_network = []

    def record(name, value):
        metrics.setdefault(phase[0], {}).setdefault(name, []).append(value)

    class TimedLock:
        def __init__(self, lock, label):
            self.lock, self.label = lock, label

        def __enter__(self):
            self.acquire()
            return self

        def acquire(self, *a, **kw):
            started = time.perf_counter()
            acquired = self.lock.acquire(*a, **kw)
            record(self.label, (time.perf_counter() - started) * 1000)
            return acquired

        def release(self):
            self.lock.release()

        def __exit__(self, *_):
            self.release()

    class Dialog(SettingsDialog):
        def __init__(self, *a, **kw):
            self.posted = pending[0] if pending else None
            self.painted = False
            super().__init__(*a, **kw)

        def paintEvent(self, event):
            super().paintEvent(event)
            if not self.painted:
                self.painted = True
                if self.posted is not None:
                    record('settings_first_paint_ms', (time.perf_counter() - self.posted[0]) * 1000)
                    record('scheduled_click_first_paint_ms', (time.perf_counter() - self.posted[1]) * 1000)
                QTimer.singleShot(40, self.reject)

    class InputProbe(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.MouseButtonRelease and pending:
                record('input_queue_ms', (time.perf_counter() - pending[0][0]) * 1000)
            return False

    def measured(function, label):
        def call(*a, **kw):
            started = time.perf_counter()
            result = function(*a, **kw)
            record(label + '_ms', (time.perf_counter() - started) * 1000)
            if label == 'match':
                record('match_samples', len(a[0]))
                record('match_candidates', len(result.candidates))
                record('match_accepted', int(result.lag is not None))
            elif label == 'refine':
                record('refine_accepted', int(result.lag is not None))
            return result
        return call

    def no_network(*_a, **_kw):
        denied_network.append(True)
        raise AssertionError('Network access is forbidden in this benchmark')

    with tempfile.TemporaryDirectory(prefix='ddm_input_', ignore_cleanup_errors=True) as root, ExitStack() as stack:
        root = Path(root)
        duration = int(args.warmup + len(phases) * args.seconds + 30)
        fixtures = args.fixtures or root
        fixtures.mkdir(parents=True, exist_ok=True)
        sources = {}
        for index, effect in enumerate(('null', 'hflip', 'vflip'), 1):
            source = fixtures / f'{args.size}-{duration}-{index}.mkv'
            if not source.exists():
                print(f'Generating {source.name}', flush=True)
                subprocess.run([str(args.ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin',
                            '-f', 'lavfi', '-i', f'testsrc2=size={args.size}:rate=60,{effect}',
                            '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                            '-t', str(duration), '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast',
                            '-g', '60', '-c:a', 'pcm_s16le', str(source)],
                           check=True, capture_output=True, timeout=180,
                           creationflags=recording._FFMPEG_FLAGS)
            sources[str(index)] = {'url': str(source)}
        stack.enter_context(patch.object(socket.socket, 'connect', no_network))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', no_network))
        stack.enter_context(patch.object(socket, 'getaddrinfo', no_network))
        stack.enter_context(patch.object(plugins, 'DEFAULT_PLUGINS_DIR', str(root / 'plugins')))
        stack.enter_context(patch.object(config, 'REPO', str(root)))
        stack.enter_context(patch.object(recording, 'ffmpeg_path', return_value=str(args.ffmpeg)))
        # Keep the actual host, widgets, settings slot, matching, decoders, audio and Qt timers.
        # Only unrelated startup work and personal resources are disabled.
        for method in ('start_all', 'refresh_account', 'refresh_status', 'refresh_stats',
                       'load_cached_covers', 'load_cached_avatars', 'load_room_avatars'):
            stack.enter_context(patch.object(host_app.MainWindow, method))
        with patch.object(host_app.QTimer, 'singleShot'):
            window = host_app.MainWindow([], [], layout_id='1x1', state={'plugins_enabled': []})
        stack.enter_context(patch.object(host_app, 'SettingsDialog', Dialog))
        stack.enter_context(patch.object(module, 'match_scenes', measured(module.match_scenes, 'match')))
        stack.enter_context(patch.object(module, 'refine_match', measured(module.refine_match, 'refine')))
        stack.enter_context(patch.object(module.Viewer, 'render', measured(module.Viewer.render, 'render')))
        stack.enter_context(patch.object(module.RoomRow, 'refresh_status', measured(module.RoomRow.refresh_status, 'row_status')))
        context = plugins.PluginContext(window.plugins, 'responsiveness')
        viewer = module.Viewer(context, {})
        viewer.sources.update(sources)
        button = window.sidebar.settings_button
        probe = InputProbe(button)
        button.installEventFilter(probe)
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        previous = [time.perf_counter()]

        def tick():
            now = time.perf_counter()
            record('event_loop_lateness_ms', max(0, (now - previous[0]) * 1000 - 10))
            previous[0] = now
        heartbeat.timeout.connect(tick)
        progress = QTimer()
        progress.setInterval(60_000)
        progress.timeout.connect(lambda: print(json.dumps({'progress': phase[0], **{
            name: summary(metrics.get(phase[0], {}).get(name, []))
            for name in ('settings_first_paint_ms', 'event_loop_lateness_ms')}}), flush=True))

        def clicks():
            sequence = 0
            while True:
                interval = (1.7, 2.1, 2.4, 1.9, 2.2)[sequence % 5]
                sequence += 1
                scheduled = time.perf_counter() + interval
                if stop.wait(interval):
                    break
                if pending:
                    continue
                point = QPointF(button.rect().center())
                posted = time.perf_counter()
                # Include sender GIL/scheduler delays separately: a Python
                # sender cannot post input while another thread owns the GIL.
                record('input_sender_delay_ms', (posted - scheduled) * 1000)
                pending.append((posted, scheduled))
                for kind, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                      (QEvent.MouseButtonRelease, Qt.NoButton)):
                    app.postEvent(button, QMouseEvent(kind, point, point, Qt.LeftButton, buttons, Qt.NoModifier))

        def closed(*_):
            pending.clear()
        # The real clicked signal still invokes MainWindow.open_settings synchronously.
        button.clicked.connect(closed)
        click_thread = threading.Thread(target=clicks, daemon=True)
        try:
            window.resize(1400, 950)
            window.show()
            viewer.embed(window.wall.parentWidget())
            viewer.show()
            for key in sources:
                viewer.add_room(key, {'alias': key})
                viewer.rows[key].show_chat.setChecked(False)
            # Reference selection normally queries the live platform for its highest tier.
            # Local fixtures must stay local; capture/decoding remains unmodified.
            viewer.prefer_highest = False
            viewer.toggle_running()
            viewer.audio.lock = TimedLock(viewer.audio.lock, 'clock_lock_wait_ms')
            for row in viewer.rows.values():
                row.decoder.history.lock = TimedLock(row.decoder.history.lock, 'history_lock_wait_ms')
            click_thread.start()
            heartbeat.start()
            progress.start()
            loop = QtCore.QEventLoop()
            QTimer.singleShot(int(args.warmup * 1000), loop.quit)
            loop.exec()
            assert viewer.render_timer.isActive() and viewer.match_timer.isActive()
            assert viewer.audio.thread is not None, 'A real audio output is required'
            assert all(row.decoder.history.received_frames > 100 for row in viewer.rows.values()), {
                key: (row.decoder.history.received_frames, row.video_text) for key, row in viewer.rows.items()}
            report['detail_counts'] = {key: len(row.decoder.history.details) for key, row in viewer.rows.items()}
            report['audio_device_active'] = viewer.audio.device is not None
            for automatic, comparison in phases:
                viewer.alignment.automatic = automatic
                viewer.compare.setChecked(comparison)
                if comparison:
                    viewer.show_comparison('2')
                phase[0] = f'match_{automatic}_compare_{comparison}'
                cpu, wall = time.process_time(), time.perf_counter()
                presented = viewer.canvas.presented
                QTimer.singleShot(int(args.seconds * 1000), loop.quit)
                loop.exec()
                elapsed = time.perf_counter() - wall
                report['phases'][phase[0]] = {name: summary(values) for name, values in metrics.get(phase[0], {}).items()}
                report['phases'][phase[0]]['cpu_cores'] = round((time.process_time() - cpu) / elapsed, 3)
                report['phases'][phase[0]]['main_fps'] = round((viewer.canvas.presented - presented) / elapsed, 2)
                assert not any(row.match_confirmed for key, row in viewer.rows.items()
                               if key != viewer.alignment.reference), 'Unrelated fixtures must not align'
                print(json.dumps({phase[0]: report['phases'][phase[0]]}), flush=True)
                if args.soak:
                    settings = report['phases'][phase[0]]['settings_first_paint_ms']
                    assert settings['count'] >= args.seconds / 3, 'Too few actual settings clicks'
                    assert settings['p95'] < 200 and settings['max'] < 500, settings
                    scheduled = report['phases'][phase[0]]['scheduled_click_first_paint_ms']
                    assert scheduled['p95'] < 200 and scheduled['max'] < 500, scheduled
            assert not denied_network, 'An isolated path attempted network access'
        finally:
            stop.set()
            if click_thread.is_alive():
                click_thread.join(3)
            heartbeat.stop()
            progress.stop()
            decoders = [row.decoder for row in viewer.rows.values() if row.decoder is not None]
            viewer.close()
            for decoder in decoders:
                decoder.stop()
                decoder.thread.join(4)
            window.close()
            app.processEvents()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('PASS: isolated real-host synthetic benchmark completed; live acceptance remains pending')
    app.shutdown()
    if dll_directory is not None:
        dll_directory.close()


if __name__ == '__main__':
    main()
