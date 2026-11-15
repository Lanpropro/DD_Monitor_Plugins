"""Timer recovery regression: no live connections, credentials or audio output."""
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
    from ddm import plugins
    model = import_module(module.__package__ + '.clock_sync')
    recovery = import_module(module.__package__ + '.clock_recovery')
    ui = import_module(module.__package__ + '.clock_dialog')
    timing = import_module(module.__package__ + '.alignment_control')
    times = tuple(100 + i * .1 for i in range(16))

    def readings(delta=0, elapsed=0, direction=-1):
        return {key: model.ClockReading(90 + direction * elapsed,
                    tuple(t + elapsed + (delta if key == '2' else 0) for t in times),
                    5, 101.5 + elapsed) for key in ('1', '2')}

    for direction in (-1, 1):
        old = readings(direction=direction)
        first = readings(.3, 2, direction)
        second = readings(.3, 4, direction)
        recovery.verify_progress(old, first, direction)
        desired = recovery.verify_repeat(first, second, direction, {'1': 0., '2': 0.}, '1')
        assert abs(desired['2'] - .3) < .001, desired
        for bad in (readings(1.3, 4, direction), readings(.3, 2, direction)):
            try:
                recovery.verify_repeat(first, bad, direction, {'1': 0., '2': 0.}, '1')
            except ValueError:
                pass
            else:
                raise AssertionError('Inconsistent or overlapping measurements accepted')
        stale = {key: model.ClockReading(item.first_second, item.times, item.transition,
                                        item.anchor + 2) for key, item in first.items()}
        try:
            recovery.verify_repeat(first, stale, direction, {'1': 0., '2': 0.}, '1')
        except ValueError:
            pass
        else:
            raise AssertionError('Extrapolating the same cached band is not a second measurement')
        try:
            recovery.verify_progress(old, readings(8, 4, direction), direction)
        except ValueError:
            pass
        else:
            raise AssertionError('A large timer discontinuity cannot be automatically accepted')
    try:
        recovery.read_clocks({'1': [(t, None) for t in times]}, {'1': [90] * 16}, {'1': 101.5}, -1)
    except ValueError:
        pass
    else:
        raise AssertionError('A frozen timer is not evidence')

    class History:
        def bounds(self):
            return 100., 106.
        def segment_at(self, _position):
            return self
        def bounds_at(self, _position):
            return self.bounds()

    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'clock_recovery'), {})
        entered, release = threading.Event(), threading.Event()
        try:
            for key in ('1', '2'):
                viewer.add_room(key, {'alias': 'timer-' + key})
                viewer.rows[key].decoder = SimpleNamespace(history=History(), last_frame_received=0)
            viewer.running = True
            viewer.automatic.setChecked(False)
            viewer.alignment.reference = '1'
            viewer.playback_rate = 1
            viewer.clock_dialog = None
            control = viewer.clock_recovery
            assert control.thread() == app.thread() and control.timer.parent() is control
            regions = {'1': (0., 0., .1, .1), '2': (0., 0., .1, .1)}
            shifts = {'1': 0., '2': 0.}
            with patch.object(viewer, 'shifts', return_value=shifts), patch.object(viewer, 'render'), \
                    patch.object(viewer, 'changed'):
                control.arm(readings(), regions, -1)
                # Cache starvation alone does not invalidate a timer calibration.
                viewer.rows['2'].was_isolated = True
                control.tick()
                assert not control.pending and not viewer.rows['2'].awaiting_confirmation
                viewer.rows['2'].decoder.history = History()
                viewer._reset(viewer.rows['2'], viewer.rows['2'].decoder)
                token = control.cancelled
                control.source_reset('2')
                assert control.cancelled is token, 'Deferred reset of same history must be idempotent'
                assert control.pending == {'2'} and viewer.rows['2'].awaiting_confirmation
                assert '静音' in control.status_text()
                assert any('来源时间轴' in hint for hint in viewer.rows['2'].status.hints)
                viewer.rows['2'].match_confirmed = False
                timing._update_sync_health(viewer, 103., shifts)
                assert viewer.rows['2'].mix_gain == 0 and viewer.rows['2'].sync_state == '等待确认对齐'

                def deliver(measured):
                    control.active_job = object()
                    control.busy = True
                    control.complete((control.active_job, control.cancelled,
                                      control.identities(('1', '2')), measured, ''))

                deadline=control.deadline
                token=control.cancelled
                control.first=readings(.3,2)
                control.active_job=object();control.busy=True
                control.complete((control.active_job,token,control.identities(('1','2')),
                                  None,'计时器未可靠翻秒，请重新确认对齐'))
                assert not control.failed and control.pending=={'2'}, 'Retry unreadable samples within the existing deadline'
                assert control.first is None and not control.busy
                assert control.deadline==deadline and control.cancelled is token
                assert control.next_sample>time.monotonic()
                assert viewer.rows['2'].awaiting_confirmation and viewer.rows['2'].mix_gain==0

                viewer.rows['2'].paused = True
                deliver(readings(.3, 2))
                assert control.first is None and control.pending
                viewer.rows['2'].paused = False
                deliver(readings(.3, 2))
                assert control.first is not None and control.pending
                assert viewer.rows['2'].awaiting_confirmation
                with patch.object(ui, 'apply_offsets', wraps=ui.apply_offsets) as apply:
                    deliver(readings(.3, 4))
                    assert apply.call_count == 1
                assert not control.pending and not viewer.rows['2'].awaiting_confirmation
                assert abs(viewer.rows['2'].delay.value() + .3) < .011

                # Exercise worker -> queued GUI receiver -> second measurement -> apply.
                control.arm(readings(), regions, -1)
                viewer.rows['2'].decoder.history = History()
                control.source_reset('2')
                control.profile = (*control.profile[:5], control.identities(('1', '2')))
                now = [103.5]
                def captured(_history, anchor, _region, _cancel):
                    assert threading.current_thread() is not threading.main_thread()
                    return [(anchor - 1.5 + i * .1, None) for i in range(16)]
                def recognized(bands, _cancel):
                    value = 88 - round(now[0] - 103.5)
                    return {key: [value] * 5 + [value - 1] * 11 for key in bands}, ''
                with patch.object(recovery, 'time', SimpleNamespace(monotonic=lambda: now[0])), \
                        patch.object(History, 'bounds', lambda _self: (100., now[0] + .2)), \
                        patch.object(recovery, 'capture_band', captured), \
                        patch.object(recovery, 'recognize_bands', recognized):
                    viewer.playback_rate = .98
                    for _ in range(2):
                        control.next_sample = 0
                        control.tick()
                        deadline = time.monotonic() + 3
                        while control.busy and time.monotonic() < deadline:
                            app.processEvents()
                            time.sleep(.005)
                        assert not control.busy and not control.failed, viewer.notice.text()
                        now[0] += 2
                    assert not control.pending and control.first is None
                    assert not viewer.rows['2'].awaiting_confirmation
                    viewer.playback_rate = 1

                # A reference reset isolates every calibrated secondary, never the reference.
                control.arm(readings(), regions, -1)
                viewer.rows['1'].decoder.history = History()
                control.source_reset('1')
                assert control.pending == {'2'} and not viewer.rows['1'].awaiting_confirmation
                control.fail('无法识别')
                assert control.failed and viewer.rows['2'].awaiting_confirmation
                assert '原选区' in viewer.notice.text()
                assert '重新确认' in control.status_text()

                # Manual edits override pending recovery, including a queued result.
                control.arm(readings(), regions, -1)
                viewer.rows['2'].decoder.history = History()
                control.source_reset('2')
                shifts['2'] = .01
                deliver(readings(.3, 2))
                assert control.profile is None and not viewer.rows['2'].awaiting_confirmation
                shifts['2'] = 0.

                # A real background worker is cancelled without blocking the GUI.
                control.arm(readings(), regions, -1)
                viewer.rows['2'].decoder.history = History()
                control.source_reset('2')
                control.profile = (*control.profile[:5], control.identities(('1', '2')))
                control.next_sample = 0
                def blocked(_history, _anchor, _region, cancel):
                    entered.set()
                    cancel.wait(3)
                    release.set()
                    return []
                with patch.object(recovery, 'capture_band', blocked), \
                        patch.object(recovery, 'recognize_bands', return_value=({}, '已取消')):
                    control.tick()
                    assert entered.wait(1)
                    start = time.monotonic()
                    control.cancel()
                    assert time.monotonic() - start < .1
                    assert release.wait(1)
                    control.worker.join(1)
                    control.arm(readings(), regions, -1)
                    control.tick()
                    assert not control.busy, 'Cancelled worker cannot permanently occupy the only slot'
                    assert not control.pending
                control.arm(readings(), regions, -1)
                viewer.rows['2'].decoder.history = History()
                control.source_reset('2')
                control.deadline = 0
                control.tick()
                assert control.failed and '超时' in viewer.notice.text()
        finally:
            viewer.clock_recovery.cancel()
            viewer.running = False
            for row in viewer.rows.values():
                row.decoder = None
            viewer.close()
            viewer.deleteLater()
            app.processEvents()
    return dict(passed=True, same_timeline_kept=True, two_bands_required=True,
                reset_isolated=True, stale_results_rejected=True, cancellation_nonblocking=True,
                frozen_or_reset_timer_rejected=True, unreadable_sample_retried=True,
                retry_deadline_preserved=True, real_live_test=False, real_messages=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    print(check(app, QWidget(), module))
