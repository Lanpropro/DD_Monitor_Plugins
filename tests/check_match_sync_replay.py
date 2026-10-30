"""Offline ten-minute group replay with real H.264 packets, PCM and chat."""
import argparse
from collections import deque
from importlib import import_module
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget
    from ddm import plugins, recording
    video = import_module(module.__package__ + '.video')
    cache = import_module(module.__package__ + '.replay_cache')
    with tempfile.TemporaryDirectory(prefix='ddm-replay-test-') as directory:
        path = Path(directory) / '650-seconds.mkv'
        subprocess.run([recording.ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=5', '-t', '650',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '10', '-bf', '2', '-an', str(path)],
            check=True, capture_output=True, timeout=60, creationflags=recording._FFMPEG_FLAGS)
        host.settings, host.rooms = {}, []
        manager = plugins.PluginManager(window=host, plugins_dir=Path(directory) / 'plugins')
        viewer = module.Viewer(plugins.PluginContext(manager, 'replay_check'), {})
        assert viewer.replay_bar.isHidden()
        viewer.setAttribute(Qt.WA_DontShowOnScreen, host.testAttribute(Qt.WA_DontShowOnScreen))
        decoders = []
        base = time.monotonic() - 625
        try:
            for key, offset in (('1', 0), ('2', 4)):
                viewer.add_room(key)
                decoder = module.Decoder(key)
                decoder.history.set_origin(base + offset)
                with video.av.open(str(path)) as container:
                    stream = container.streams.video[0]
                    decoder.history.codec = (stream.codec_context.name, stream.codec_context.extradata)
                    decoder.history.video_size = (160, 90)
                    for packet in container.demux(stream):
                        if packet.size:
                            decoder.history.append_packet(packet)
                        for frame in packet.decode():
                            pts = float(frame.pts * frame.time_base)
                            decoder.history.append(base + offset + pts, pts)
                for second in range(650):
                    decoder.history.audio.append(struct.pack('<hh', int(key) * 1000 + second,
                        int(key) * 1000 + second) * 48000)
                assert isinstance(decoder.history.audio, cache.DiskAudioRing)
                assert isinstance(decoder.history.packets[0].data, cache.PacketSlice)
                decoder.fps = 5
                row = viewer.rows[key]
                row.decoder = decoder
                row.sync_state = '已同步'
                row.match_confirmed = True
                row.mix_gain = 1
                row.audible.setChecked(True)
                row.volume.setValue(100)
                decoders.append(decoder)
            viewer.running = True
            viewer.alignment.reference = '1'
            for second in range(626):
                stamp = base + second
                shifts = {'1': 0, '2': 4} if second < 400 else {'1': .5, '2': 4.5}
                viewer.replay.observe(stamp, stamp, shifts)
            histories = viewer.replay_histories()
            now = base + 625
            available = viewer.replay.available(now, histories)
            assert available is not None and abs(available[0] - (now - 600)) < 1.01
            assert viewer.replay.seek(now - 900, now, histories)
            assert viewer.replay.delay <= 600.01, 'Clamp to the actual ten-minute window'
            shown, shifts = viewer.replay.presentation(now, {'1': .5, '2': 4.5}, now)
            assert shifts == {'1': 0, '2': 4}, 'Use historical alignment rather than current live offsets'
            assert abs(shown - (base + 25)) < 1.01
            images = []
            for key, decoder in zip(('1', '2'), decoders):
                target = shown + shifts[key]
                deadline = time.monotonic() + 5
                result = None
                while result is None and time.monotonic() < deadline:
                    result = decoder.picture_at(target, exact=True)
                    QTest.qWait(10)
                assert result is not None and abs(result[0] - target) < .21
                assert result[1].size().width() == 160
                images.append(result[1])
                pcm = decoder.history.pcm_at(target, 16)
                expected = int(key) * 1000 + round(shown - base)
                assert struct.unpack('<h', pcm[:2])[0] == expected, 'PCM must match the historical picture time'
            assert images[0] == images[1], 'Both source timelines must resolve to the same match frame'
            class Device:
                data = b''
                def write(self, data):
                    self.data += data
                    return len(data)
            device = Device()
            class Sink:
                resets = 0
                def processedUSecs(self):
                    return 0
                def bytesFree(self):
                    return 2048 * 4
                def reset(self):
                    self.resets += 1
                def start(self):
                    return device
            pump = viewer.audio
            pump.sink, pump.device = Sink(), device
            pump.anchor = time.monotonic()
            pump.snapshot = (1.0, tuple((decoder.history,
                base + 25.2 + offset - pump.anchor, 1.0, 0, 100)
                for decoder, offset in zip(decoders, (0, 4))))
            pump._fill()
            assert device.data and struct.unpack('<h', device.data[:2])[0] == 1525
            before_flush = pump.clock()
            module.AudioFeed(pump, None, None).flush()
            assert pump.sink.resets == 1 and abs(pump.clock() - before_flush) < .001
            pump.flush_clock = before_flush + 4
            module.AudioFeed(pump, None, None).flush()
            assert abs(pump.clock() - before_flush - 4) < .001, 'Return-live flush must accept the background clock'
            pump.flush_clock = None
            pump.sink = pump.device = None
            pump.clock_segments.clear()
            pump.clock_snapshot = None
            viewer.replay.live()
            viewer.panel.add_message('previous user', 'earlier live section')
            viewer.panel._follow_tail = False
            replay_now = time.monotonic()
            target = replay_now - 100
            for stamp, text in ((target - 1, 'before'), (target + .05, 'during'), (replay_now, 'live only')):
                viewer.replay.record_message(stamp, {'kind': 'danmaku', 'text': text, 'uname': 'fixture',
                    'source_room': '1', 'source_label': 'room1'})
            correction = viewer.audio.correction
            with patch.object(viewer, 'shifts', return_value={'1': .5, '2': 4.5}):
                viewer.seek_replay(target)
                assert viewer.replay.active and viewer.audio.correction == correction
                anchor, updated = viewer._replay_live_anchor, viewer._replay_live_updated
                old_rate = viewer.playback_rate
                viewer.playback_rate = .98
                with patch.object(module.time, 'monotonic', return_value=updated + 1):
                    assert abs(viewer.live_clock() - (anchor + .98 + correction)) < .001
                viewer._replay_live_anchor, viewer._replay_live_updated = anchor, updated
                viewer.playback_rate = old_rate
                assert 'before' in viewer.panel.body.toPlainText()
                assert 'earlier live section' in viewer.panel.body.toPlainText()
                assert any(entry.get('_replay_divider') for entry in viewer.panel._blocks)
                assert 'replay-separator' in viewer.panel.body.toHtml()
                assert viewer.panel._follow_tail, 'Jumping must reveal the newly separated chat section'
                assert 'live only' not in viewer.panel.body.toPlainText()
                viewer.audio.update_snapshot()
                rate, sources = viewer.audio.snapshot
                assert rate == 1 and len(sources) == 2
                clock = viewer.audio.clock()
                shown, old_shifts = viewer.replay.presentation(clock, viewer.shifts())
                assert abs(clock + sources[0][1] - (shown + old_shifts['1'])) < .03
                before_packets = decoders[0].history.sequence
                QTest.qWait(80)
                viewer.rows['1'].pending.append((time.monotonic(), {'kind': 'danmaku', 'uname': 'live user', 'text': 'new incoming live'}))
                with patch.object(viewer, '_recover_clock', side_effect=lambda clock, _shifts: clock) as recover, \
                     patch.object(viewer, '_smooth_shifts', return_value={'1': .5, '2': 4.5}), \
                     patch.object(viewer, '_update_sync_health') as health:
                    viewer.render()
                    recover.assert_called_once()
                    health.assert_called_once()
                    assert decoders[0].history.sequence == before_packets
                    deadline = time.monotonic() + 3
                    while viewer.canvas.image.isNull() and time.monotonic() < deadline:
                        QTest.qWait(10)
                        viewer.render()
                    assert not viewer.canvas.image.isNull(), 'The main canvas must display a decoded replay frame'
                    shown, shown_shifts = viewer.replay.presentation(viewer.audio.clock(), viewer.shifts())
                    assert abs(viewer.canvas.frame_key - shown - shown_shifts['1']) < .3
                assert 'during' in viewer.panel.body.toPlainText()
                assert 'live only' not in viewer.panel.body.toPlainText()
                assert 'new incoming live' not in viewer.panel.body.toPlainText()
                viewer.resize(1800, 900)
                viewer.show()
                viewer.replay_bar.refresh(force=True)
                QTest.qWait(40)
                bar = viewer.replay_bar
                slot = viewer.picture.status_label
                assert bar.parentWidget() is slot
                assert abs(bar.width() - round(slot.width() * 2 / 3)) <= 1
                assert abs(bar.geometry().center().x() - slot.rect().center().x()) <= 1
                assert bar.mapTo(viewer.picture.bottom, QPoint()).x() > viewer.picture.volume_label.geometry().right()
                assert bar.width() <= slot.width() - 30, 'Keep visible space around the centered replay controls'
                assert not bar.back.text() and bar.back.kind == 'live'
                assert 'background: transparent' in bar.back.styleSheet()
                assert 'background: transparent' in bar.time_edit.styleSheet()
                assert not bar.isHidden() and not bar.slider.isHidden(), 'Show the timeline without a toggle'
                assert bar.start_tick.mapTo(bar, QPoint()).y() > bar.slider.mapTo(bar, QPoint()).y()
                assert bar.start_tick.mapTo(slot, QPoint(0, bar.start_tick.height())).y() <= slot.height(), 'Do not clip the timestamps'
                if output:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    viewer.grab().save(str(output))
                QTest.mousePress(bar.slider, Qt.LeftButton, pos=QPoint(bar.slider.width() // 2, bar.slider.height() // 2))
                assert not bar.details.isHidden() and '.' in bar.detail_position.text()
                assert 'background: transparent' in bar.details.styleSheet()
                assert bar.ruler.duration > 0 and bar.ruler.height() >= 32
                if output:
                    viewer.grab().save(str(output.with_name(output.stem + '-detail.png')))
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QTextDocument
                for mode in ('light', 'dark'):
                    module.theme.apply(mode)
                    QTest.qWait(20)
                    line = viewer.panel.body.document().resource(QTextDocument.ImageResource, QUrl('replay-separator'))
                    assert line.pixelColor(0, 0) == module.theme.qcolor(module.theme.TEXT1), mode
                    point = bar.ruler.mapTo(viewer.picture, QPoint(0, 0))
                    shown = viewer.picture.grab().toImage()
                    bar.details.hide()
                    beneath = viewer.picture.grab().toImage()
                    bar.details.show()
                    ratio = viewer.picture.devicePixelRatioF()
                    point = QPoint(round(point.x() * ratio), round(point.y() * ratio))
                    assert shown.pixelColor(point) == beneath.pixelColor(point), 'Ruler background must reveal the picture'
                    if output:
                        viewer.grab().save(str(output.with_name(output.stem + '-detail-' + mode + '.png')))
                QTest.mouseRelease(bar.slider, Qt.LeftButton, pos=QPoint(bar.slider.width() // 2, bar.slider.height() // 2))
                assert bar.details.isHidden()
                QTest.mouseClick(bar.slider, Qt.LeftButton, pos=QPoint(bar.slider.width() // 2, bar.slider.height() // 2))
                assert abs(viewer.replay.delay - bar.slider.maximum() / 2000) < 3
                origin = bar.window_start
                bar.time_edit.setFocus()
                QTest.qWait(10)
                bar.time_edit.setText('02:30')
                QTest.keyClick(bar.time_edit, Qt.Key_Return)
                assert abs(time.monotonic() - viewer.replay.delay - (origin + 150)) < 1
                assert bar.time_edit.scrub_gain(10, .01) > bar.time_edit.scrub_gain(10, 1) * 5
                before_drag = viewer.replay.delay
                center = bar.time_edit.rect().center()
                QTest.mousePress(bar.time_edit, Qt.LeftButton, pos=center)
                QTest.qWait(50)
                QTest.mouseMove(bar.time_edit, center + QPoint(12, 0))
                assert bar.time_edit.scrubbing and not bar.details.isHidden()
                QTest.mouseRelease(bar.time_edit, Qt.LeftButton, pos=center + QPoint(12, 0))
                assert not bar.time_edit.scrubbing and bar.details.isHidden()
                assert viewer.replay.delay < before_drag
                expected_delay = viewer.replay.delay
                with patch.object(import_module(module.__package__ + '.replay').QToolTip, 'showText') as tooltip:
                    bar.time_edit.setText('99:99')
                    bar.enter_time()
                    tooltip.assert_called_once()
                assert viewer.replay.delay == expected_delay
                viewer.return_live()
                assert not viewer.replay.active and viewer.audio.correction == correction
                assert 'live only' in viewer.panel.body.toPlainText()
                assert 'new incoming live' in viewer.panel.body.toPlainText()
                assert any(entry.get('_replay_divider') and '返回直播' in entry['text']
                           for entry in viewer.panel._blocks)
                assert viewer.audio.snapshot[0] == viewer.playback_rate
                assert not bar.slider.isHidden(), 'Keep the timeline visible when returning live'
                viewer.rows['2'].match_confirmed = False
                viewer.rows['2'].sync_state = '等待对齐'
                with patch.object(viewer, '_recover_clock', side_effect=lambda clock, _shifts: clock), \
                     patch.object(viewer, '_smooth_shifts', return_value={'1': .5, '2': 4.5}), \
                     patch.object(viewer, '_update_sync_health'):
                    viewer.render()
                bar.refresh(force=True)
                assert bar.slider.isEnabled(), 'An unconfirmed secondary must not disable normal main replay'
                saved_timeline = viewer.replay
                viewer.replay = module.ReplayTimeline()
                viewer.rows['1'].sync_state = '准备缓存'
                clock_now = time.monotonic()
                with patch.object(viewer, '_recover_clock', side_effect=lambda clock, _shifts: clock), \
                     patch.object(viewer, '_smooth_shifts', return_value={'1': .5, '2': 4.5}), \
                     patch.object(viewer, '_update_sync_health'):
                    with patch.object(module.time, 'monotonic', return_value=clock_now):
                        viewer.render()
                    with patch.object(module.time, 'monotonic', return_value=clock_now + .8):
                        viewer.render()
                        bar.refresh(force=True)
                        assert bar.slider.isEnabled(), 'Initial main playback must work before manual confirmation'
                viewer.replay = saved_timeline
            decoders[0].history.trim_bytes(0)
            assert viewer.replay.available(time.monotonic(), histories) is None, 'Do not offer evicted media'
            viewer.stop()
            for key in tuple(viewer.rows):
                viewer.remove_room(key)
            assert viewer.replay_bar.isHidden() and viewer.send_console.work.isHidden()
            return {'passed': True, 'replay_seconds': 600, 'real_h264_seek': True,
                    'pcm_picture_alignment': True, 'background_live_health': True,
                    'historical_chat': True, 'return_live': True, 'real_network_requests': 0}
        finally:
            viewer.close()
            for decoder in decoders:
                decoder.stop()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(args.host), str(root)]
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication.instance() or QApplication([])
    module.theme.apply('dark')
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        print(check(app, QWidget(), module, args.output))


if __name__ == '__main__':
    main()
