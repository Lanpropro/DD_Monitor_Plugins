"""Regressions for GUI clock contention and bounded failed-search scheduling."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
from unittest.mock import patch


def clock_check(module, parent):
    pump = module.AudioPump(parent)
    # Retain the existing media-rate segment calculation and live correction.
    pump.anchor = 100
    pump.played = 480
    pump.thread = object()
    pump.sink = object()
    pump.clock_segments.append((0, 960, 3, .98))
    assert abs(pump.clock() - 103.0098) < 1e-8
    entered, release = threading.Event(), threading.Event()

    def busy_output():
        with pump.lock:
            entered.set()
            release.wait(.25)

    worker = threading.Thread(target=busy_output)
    worker.start()
    try:
        assert entered.wait(1)
        pump.correction = -2
        started = time.perf_counter()
        observed = pump.clock()
        elapsed = time.perf_counter() - started
        assert elapsed < .05, ('GUI waited behind audio supply', elapsed)
        assert abs(observed - 101.0098) < 1e-8
    finally:
        release.set()
        worker.join(1)
        pump.thread = pump.sink = None
        pump.stop()
    assert pump.clock_snapshot is None
    assert abs(pump.clock() - (time.monotonic() - 2)) < .05


def scheduling_check(module, plugins, host):
    from dev.selfcheck_match_sync import FakeDecoder, FakeChat
    from plugins_user._match_sync.engine import Match, Sample
    now = [100.0]
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(module, 'Decoder', FakeDecoder), patch.object(module, 'Chat', FakeChat), \
            patch.object(module.AudioPump, 'start', lambda _: None), \
            patch.object(module.time, 'monotonic', side_effect=lambda: now[0]):
        host.settings = {}
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'contention'), {})
        try:
            for key in ('1', '2', '3'):
                viewer.add_room(key, {'alias': key})
            viewer.toggle_running()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            row = viewer.rows['2']
            row.decoder.history.details = [Sample(90 + i * .05, i, 20) for i in range(120)]
            failed = Match(None, 0, '未找到共同的动态比赛画面')
            for delay in (2, 4, 8, 8):
                viewer._matched(viewer.generation, {'2': failed})
                assert row.next_analysis == now[0] + delay, (delay, row.next_analysis)
                assert viewer.rows['3'].next_analysis == 0, 'One failed feed delayed another feed'
                now[0] += delay
            assert row.search_failures == 3
            # The real manual retry path must immediately clear the backoff.
            viewer.retry_alignment('2')
            assert row.search_failures == 0 and row.next_analysis == 0
            viewer._matched(viewer.generation, {'2': failed})
            assert row.next_analysis == now[0] + 2
            viewer._reset(row, row.decoder)
            assert row.search_failures == 0 and row.next_analysis == 0
            # Do not slow initial collection before detailed matching is possible.
            row.decoder.history.details = []
            row.alignment_started = now[0]
            viewer._matched(viewer.generation, {'2': Match(None, 0, '正在收集比赛画面')})
            assert row.search_failures == 0 and row.next_analysis == 0
            row.match_confirmed = True
            viewer._matched(viewer.generation, {'2': Match(0, .9, '已精校正', refined=True)})
            assert row.search_failures == 0
        finally:
            viewer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(args.host.resolve()))
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    import plugins_user
    package = types.ModuleType('plugins_user._match_sync')
    package.__path__ = [str(root / 'plugins/match_sync')]
    sys.modules[package.__name__] = package
    plugins_user._match_sync = package
    from PySide6.QtWidgets import QApplication, QWidget
    from ddm import plugins
    from plugins_user._match_sync import viewer as module
    app = QApplication([])
    host = QWidget()
    try:
        clock_check(module, host)
        scheduling_check(module, plugins, host)
    finally:
        host.close()
        app.processEvents()
        app.shutdown()
    print('PASS: busy audio cannot queue the GUI clock; failed search backs off per feed and resets on retry/reconnect')


if __name__ == '__main__':
    main()
