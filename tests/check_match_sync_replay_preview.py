"""Cached H.264 target previews and native timeline gestures, without network."""
import argparse
from fractions import Fraction
from importlib import import_module
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from unittest.mock import patch


def check(app, host, module, output=None):
    import av
    import numpy as np
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtTest import QTest
    from ddm import plugins
    video = import_module(module.__package__ + '.video')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'source.mkv'
        with av.open(str(path), 'w') as container:
            stream = container.add_stream('libx264', rate=10)
            stream.width, stream.height, stream.pix_fmt = 640, 360, 'yuv420p'
            stream.options = {'preset': 'ultrafast', 'g': '10', 'bf': '2'}
            for index in range(80):
                rgb = np.zeros((360, 640, 3), dtype=np.uint8)
                rgb[:, :, 0 if index < 40 else 1] = 180
                frame = av.VideoFrame.from_ndarray(rgb, format='rgb24')
                frame.pts, frame.time_base = index, Fraction(1, 10)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        host.settings, host.rooms = {}, []
        manager = plugins.PluginManager(window=host, plugins_dir=Path(directory) / 'plugins')
        context = plugins.PluginContext(manager, 'replay_preview_check')
        viewer = module.Viewer(context, {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        decoder = module.Decoder('1')
        try:
            viewer.resize(1400, 900)
            viewer.add_room('1')
            viewer.rows['1'].decoder = decoder
            viewer.running = True
            viewer.alignment.reference = '1'
            base = time.monotonic() - 8
            history = decoder.history
            history.set_origin(base)
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                history.codec = (stream.codec_context.name, stream.codec_context.extradata)
                for packet in container.demux(stream):
                    if packet.size:
                        history.append_packet(packet)
                    for frame in packet.decode():
                        pts = float(frame.pts * frame.time_base)
                        history.append(base + pts, pts)
            history.audio.append(bytes(8 * 48000 * 4))
            for index in range(80):
                stamp = base + index / 10
                # Historical offsets must be applied, rather than the live clock.
                viewer.replay.observe(stamp, stamp + .2, {'1': 0})
            viewer.show()
            app.processEvents()
            bar = viewer.replay_bar
            bar.refresh(force=True)
            bar.window_start = base
            bar.slider.setRange(0, 7800)
            bar.slider.setValue(7000)
            preview = bar.preview
            delay = viewer.replay.delay
            live_target = decoder.pictures.target

            def wait_image(seconds):
                bar.show_preview(seconds)
                deadline = time.monotonic() + 5
                while preview.image.isNull() and time.monotonic() < deadline:
                    app.processEvents()
                    preview.poll()
                    time.sleep(.02)
                assert not preview.image.isNull(), 'Read actual H.264 target from cache'
                assert preview.image.width() == 360, 'Convert only thumbnail RGB pixels'
                return preview.image.pixelColor(100, 60)

            first = wait_image(2)
            assert first.red() > 160 and first.green() < 10
            second = wait_image(5)
            assert second.green() > 160 and second.red() < 10
            assert bar.slider.value() == 7000 and viewer.replay.delay == delay
            assert decoder.pictures.target is live_target, 'Preview must not seek the main reader'
            assert preview.x() < 32 and preview.geometry().bottom() < viewer.picture.video.height()
            if output:
                output = Path(output)
                output.mkdir(parents=True, exist_ok=True)
                viewer.grab().save(str(output / 'preview-medium.png'))
            bar.slider.hovered.emit(2000)
            assert not preview.isHidden()
            bar.slider.departed.emit()
            assert preview.isHidden() and not preview.timer.isActive()
            assert not preview.reader.active
            bar.slider.setSliderDown(True)
            frozen_origin = bar.drag_origin
            bar.window_start += .4
            bar.preview_elapsed(3)
            assert abs(preview.request[2][0] - (frozen_origin + 3.2)) < .11
            with patch.object(viewer, 'seek_replay') as seek:
                bar.slider.setSliderDown(False)
                seek.assert_called_once()
                assert abs(seek.call_args.args[0] - (frozen_origin + 3)) < .001
            assert preview.isHidden()
            bar.window_start = base
            viewer.open_match_settings()
            app.processEvents()
            dialog = viewer.match_settings
            for button in dialog.preview_sizes.group.buttons():
                assert dialog.preview_sizes.rect().contains(button.geometry()), (dialog.preview_sizes.size(), button.geometry(), button.minimumSize(), dialog.preview_sizes.sizeHint())
            widths = []
            for size in (200, 280, 360):
                dialog.preview_sizes.group.button(size).click()
                assert context.setting('replay_preview_width') == size
                wait_image(5)
                widths.append(preview.width())
            assert widths[0] < widths[1] < widths[2]
            if output:
                dialog.grab().save(str(output / 'settings.png'))
            dialog.hide()
            viewer.picture.video.resize(240, 160)
            app.processEvents()
            preview.reposition()
            assert preview.width() < 240 and preview.height() < 160
            reader = preview.reader
            viewer.stop()
            assert preview.isHidden() and preview.reader is None
            assert reader.cancelled.is_set() and not reader.thread.is_alive()
            restored = module.Viewer(context, {})
            assert restored.replay_preview_width == 360
            restored.close()
            restored.deleteLater()
            return dict(passed=True, real_cached_h264=True, thumbnail_width=360,
                        historical_offset=True, hover_without_seek=True, drag_origin_frozen=True,
                        main_reader_untouched=True, sizes_persisted=True, stop_cleanup=True,
                        real_network_requests=0)
        finally:
            viewer.close()
            decoder.stop()
            viewer.deleteLater()
            app.sendPostedEvents(None, 52)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(args.host.resolve())]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        print(json.dumps(check(app, QWidget(), module, args.output)))
    app.shutdown()


if __name__ == '__main__':
    main()
