"""Clock assistance functional checks, isolated from real accounts and live media."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget
    from ddm import plugins, recording
    model = import_module(module.__package__ + '.clock_sync')
    capture = import_module(module.__package__ + '.clock_capture')
    ui = import_module(module.__package__ + '.clock_dialog')
    video = import_module(module.__package__ + '.video')
    recovery = import_module(module.__package__ + '.clock_recovery')
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')
    family = QFontDatabase.applicationFontFamilies(font_id)[0] if font_id >= 0 else 'sans-serif'
    assert model.parse_clock('01:29') == 89
    assert model.parse_clock('0129') == 89
    assert model.parse_clock('129') == 89
    assert model.parse_clock('01299') is None
    assert model.parse_clock('01•.29') == 89
    assert model.parse_clock('1:30 2:30') is None
    assert model.parse_clock('01:99') is None
    assert model.parse_clock('2026/11/10 13:40') == 820
    times = tuple(100 + index * .1 for index in range(16))
    readings = [90] * 5 + [89] * 10 + [88]
    assert model.suggest_transition(times, readings, -1) == 5
    assert model.suggest_transition(times, [90] * 16, -1) is None
    assert model.suggest_transition(times, readings[:8] + [95] * 8, -1) is None
    assert model.suggest_transition(times, [None] + readings[1:], -1) is None
    first = model.ClockReading(90, times, 5, 101.5)
    # B's game is 0.3s ahead at the same sampling instant: delay B by 0.3s.
    second = model.ClockReading(90, times, 2, 101.5)
    for direction in (-1, 1):
        desired, errors = model.calculate_offsets({'a': first, 'b': second},
                                                  {'a': -3., 'b': -4.}, 'a', direction)
        assert abs(desired['b'] - (-4.3)) < .0001, desired
        assert abs(desired['a'] + 3) < .0001
        assert errors['b'] < .101
    for invalid in (model.ClockReading(0, times, 5, 101.5),
                    model.ClockReading(90, times, 0, 101.5),
                    model.ClockReading(90, times, 15, 101.5)):
        try:
            invalid.at_anchor(-1)
            raise AssertionError('Invalid transition accepted')
        except ValueError:
            pass

    def timer_image(second):
        image = QImage(640, 360, QImage.Format_RGB32)
        image.fill(Qt.black)
        painter = QPainter(image)
        font = QFont(family, 64)
        font.setPixelSize(85)  # QImage fixture must not change with the host screen DPI.
        painter.setFont(font)
        painter.setPen(Qt.white)
        painter.drawText(image.rect(), Qt.AlignCenter, model.format_clock(second))
        painter.end()
        return image

    with tempfile.TemporaryDirectory(prefix='clock-check-') as directory:
        directory = Path(directory)
        for index in range(80):
            timer_image(90 - index // 20).save(str(directory / f'{index:03d}.png'))
        path = directory / 'timer.mkv'
        subprocess.run([recording.ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-framerate', '20', '-i', str(directory / '%03d.png'), '-c:v', 'libx264',
                        '-preset', 'ultrafast', '-g', '20', '-bf', '2', str(path)],
                       check=True, capture_output=True, timeout=30, creationflags=recording._FFMPEG_FLAGS)
        history = video.VideoHistory(seconds=60)
        history.set_origin(100)
        with video.av.open(str(path)) as container:
            stream = container.streams.video[0]
            history.codec = stream.codec_context.name, stream.codec_context.extradata
            history.video_size = (640, 360)
            for packet in container.demux(stream):
                if packet.size:
                    history.append_packet(packet)
                for frame in packet.decode():
                    pts = float(frame.pts * frame.time_base)
                    history.append(100 + pts, pts)
        cancel = threading.Event()
        frames = capture.capture_band(history, 103, (.2, .25, .6, .5), cancel)
        assert len(frames) >= 12 and frames[-1][0] - frames[0][0] >= 1.1
        assert frames[0][1].width() <= 480 and frames[0][1].height() <= 160
        preview = capture.capture_band(history, 102, (0, 0, 1, 1), cancel, preview=True)
        assert len(preview) == 1 and preview[0][1].width() == 640
        cancel.set()
        assert capture.capture_band(history, 103, (.2, .25, .6, .5), cancel) == []
        cancel.clear()
        earlier = capture.capture_band(history, 101.5, (.2, .25, .6, .5), cancel)
        actual, note = capture.recognize_bands({'a': frames, 'earlier': earlier}, cancel)
        if output:
            frames[0][1].save(str(output.with_name('first-clock.png')))
            earlier[0][1].save(str(output.with_name('earlier-clock.png')))
        if sys.platform == 'win32':
            assert not note and actual['a'][0] == 89, (note, actual)
            suggested = model.suggest_transition([stamp for stamp, _ in frames], actual['a'], -1)
            assert suggested is not None, actual
            assert model.suggest_transition([stamp for stamp, _ in earlier], actual['earlier'], -1) is not None, actual
            first = recovery.read_clocks({'1': earlier, '2': earlier},
                {'1': actual['earlier'], '2': actual['earlier']}, {'1': 101.5, '2': 101.2}, -1)
            second = recovery.read_clocks({'1': frames, '2': frames},
                {'1': actual['a'], '2': actual['a']}, {'1': 103., '2': 102.7}, -1)
            recovery.verify_progress(first, second, -1)
            offsets = recovery.verify_repeat(first, second, -1, {'1': 0., '2': -.3}, '1')
            assert abs(offsets['2']) < .001, offsets
        else:
            assert note
        with patch.object(capture.os, 'name', 'posix'):
            fallback, message = capture.recognize_bands({'a': frames}, cancel)
            assert not fallback and message
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory / 'plugins'), 'clock_check'), {})
        dialog = None
        try:
            viewer.add_room('1')
            viewer.add_room('2')
            viewer.running = True
            for row in viewer.rows.values():
                row.decoder = SimpleNamespace(history=history, pictures=None)
            viewer.playback_rate = .98  # Normal SafetyBuffer headroom building, not user replay.
            with patch.object(viewer, 'shifts', return_value={'1': 0., '2': 0.}), \
                    patch.object(viewer, 'live_clock', return_value=103.):
                async_dialog = ui.ClockDialog(viewer)
                async_dialog.setAttribute(Qt.WA_DontShowOnScreen)
                async_dialog.show()
                assert async_dialog.objectName() == 'MatchSyncClockDialog'
                assert not async_dialog.property('settingsGlassWindow')
                assert not async_dialog.testAttribute(Qt.WA_TranslucentBackground)
                assert async_dialog.findChild(QWidget, 'ClockDialogToolbar') is not None
                assert async_dialog.findChild(QWidget, 'ClockDialogFooter') is not None
                assert async_dialog.findChild(QWidget, 'ClockDialogScroll') is async_dialog.scroll
                assert all(lane.objectName() == 'MatchSyncClockLane'
                           for lane in async_dialog.lanes.values())
                assert async_dialog.calculate.text() == '计算偏移'
                assert all(lane.pick.text() == '框选计时器区域' for lane in async_dialog.lanes.values())
                assert async_dialog.windowFlags() & Qt.FramelessWindowHint
                assert all(lane.avatar.size().width() == 36 and lane.avatar.size().height() == 36
                           for lane in async_dialog.lanes.values())
                assert async_dialog.scroll.verticalScrollBar().maximum() == 0
                QTest.qWait(120)
                assert not async_dialog.busy and all(lane.preview is None for lane in async_dialog.lanes.values()), \
                    'Opening the dialog must not start cache sampling'
                viewer.canvas.set_frame((103., timer_image(90)))
                viewer.comparison_panel.cards = {}
                for key in viewer.rows:
                    canvas = ui.Canvas()
                    canvas.set_frame((103., timer_image(90)))
                    viewer.comparison_panel.cards[key] = (None, None, canvas, None)
                async_dialog.populate_current_previews()
                assert all(lane.preview is not None for lane in async_dialog.lanes.values())
                with patch.object(ui, 'capture_band', side_effect=AssertionError('Current frame should be reused')):
                    async_dialog.start_job(preview=True)
                    deadline = time.monotonic() + 2
                    while async_dialog.busy and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(.01)
                viewer.canvas.set_frame(None)
                viewer.comparison_panel.cards = {}
                async_dialog.start_job(preview=True)
                deadline = time.monotonic() + 10
                while async_dialog.busy and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                assert all(lane.preview is not None for lane in async_dialog.lanes.values()), async_dialog.status.text()
                assert all(lane.strip.count() == 1 and not lane.strip.item(0).icon().isNull()
                           for lane in async_dialog.lanes.values()), 'Getting a picture must display it'
                if output:
                    async_dialog.grab().save(str(output.with_name('clock-preview.png')))
                def accept_region(picker):
                    canvas = picker.findChild(ui.Canvas)
                    assert canvas is not None and not canvas.image.isNull()
                    canvas.crop = QRectF(.2, .25, .6, .5)
                    return ui.QDialog.DialogCode.Accepted
                with patch.object(ui.QDialog, 'exec', accept_region):
                    async_dialog.lanes['1'].choose_region()
                assert async_dialog.lanes['1'].strip.item(0).text() == '计时器选区'
                assert not async_dialog.lanes['1'].frames and async_dialog.sample_token is None
                if output:
                    async_dialog.grab().save(str(output.with_name('clock-region.png')))
                for lane in async_dialog.lanes.values():
                    lane.region = (.2, .25, .6, .5)
                async_dialog.start_job(preview=False)
                deadline = time.monotonic() + 15
                while async_dialog.busy and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                assert not async_dialog.busy and async_dialog.sample_token is not None, async_dialog.status.text()
                viewer.playback_rate = 1.005
                async_dialog.check_session()
                assert async_dialog.sample_token is not None, 'Adaptive live speed must not invalidate cached samples'
                async_dialog.calculate_proposal()
                assert async_dialog.apply.isEnabled(), async_dialog.status.text()
                viewer.replay.delay = 2
                try:
                    ui.session_token(viewer, ('1', '2'))
                except ValueError:
                    pass
                else:
                    raise AssertionError('Replay must remain unavailable for live timer sampling')
                viewer.replay.delay = 0
                async_dialog.reject()
                assert async_dialog.cancelled.is_set()
                async_dialog.deleteLater()
                app.processEvents()
                entered, stopped = threading.Event(), threading.Event()
                def wait_capture(_history, _anchor, _region, cancelled, **_kwargs):
                    entered.set()
                    cancelled.wait(3)
                    stopped.set()
                    return []
                with patch.object(ui, 'capture_band', wait_capture), \
                        patch.object(ui, 'recognize_bands', side_effect=AssertionError('OCR started after cancellation')):
                    closing = ui.ClockDialog(viewer)
                    closing.setAttribute(Qt.WA_DontShowOnScreen)
                    closing.show()
                    closing.start_job(preview=True)
                    deadline = time.monotonic() + 3
                    while not entered.is_set() and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(.01)
                    assert entered.is_set()
                    closing.reject()
                    assert stopped.wait(1), 'Closing must cancel background capture'
                    closing.deleteLater()
                    app.processEvents()
            shifts = {'1': -3., '2': -4.}
            with patch.object(viewer, 'shifts', return_value=shifts), \
                    patch.object(ui.ClockDialog, 'start_job', lambda *_a, **_k: None):
                dialog = ui.ClockDialog(viewer)
                dialog.setAttribute(Qt.WA_DontShowOnScreen)
                dialog.show()
                app.processEvents()
                dialog.sample_keys = ('1', '2')
                dialog.sample_token = ui.session_token(viewer, dialog.sample_keys)
                dialog.sample_shifts = dict(shifts)
                dialog.anchors = {'1': 101.5, '2': 101.5}
                for key, transition in (('1', 5), ('2', 2)):
                    lane = dialog.lanes[key]
                    lane.set_band([(stamp, timer_image(90)) for stamp in times], [90] * len(times), -1)
                    lane.first.setText('01:30')
                    lane.strip.setCurrentRow(transition)
                old_delay = viewer.rows['2'].delay.value()
                dialog.calculate_proposal()
                assert dialog.apply.isEnabled() and abs(dialog.proposal['2'] + 4.3) < .0001
                assert '建议偏移' in dialog.lanes['2'].status.text()
                assert '误差' in dialog.lanes['2'].status.text()
                assert viewer.rows['2'].delay.value() == old_delay, 'Calculating is not applying'
                if output:
                    dialog.grab().save(str(output))
                shifts['2'] = -4.2
                dialog.apply_proposal()
                assert viewer.rows['2'].delay.value() == old_delay, 'Reject changed playback offsets'
                shifts['2'] = -4.
                dialog.lanes['2'].first.setText('01:29')
                assert not dialog.apply.isEnabled(), 'Editing invalidates previous proposal'
                dialog.lanes['2'].first.setText('01:30')
                dialog.calculate_proposal()
                viewer.rows['2'].decoder = SimpleNamespace(history=history)
                dialog.apply_proposal()
                assert viewer.rows['2'].delay.value() == old_delay, 'Reject results from replaced sources'
                dialog.sample_token = ui.session_token(viewer, dialog.sample_keys)
                dialog.calculate_proposal()
                with patch.object(viewer, 'render'), patch.object(viewer, 'changed'):
                    dialog.apply_proposal()
                assert dialog.result() == ui.QDialog.DialogCode.Accepted
                assert abs(viewer.rows['2'].delay.value() - 1.3) < .011
                assert not viewer.automatic.isChecked() and viewer.alignment.manual_locked
                assert viewer.rows['2'].user_confirmed
                assert viewer.compare.isChecked(), 'Applied offsets should return to comparison controls'
                assert dialog.cancelled.is_set()
        finally:
            viewer.running = False
            for row in viewer.rows.values():
                row.decoder = None
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
            viewer.close()
            viewer.deleteLater()
            app.processEvents()
    return {'passed': True, 'clock_directions': True, 'bounded_real_video_capture': True,
            'windows_ocr': not note, 'manual_review': True, 'stale_source_rejected': True,
            'explicit_apply': True, 'async_sample_ocr_ui': True, 'open_is_idle_until_requested': True,
            'real_live_test': False, 'real_messages': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    print(check(app, QWidget(), module, args.output))


if __name__ == '__main__':
    main()
