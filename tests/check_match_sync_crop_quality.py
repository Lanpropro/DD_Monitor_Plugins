"""Light crop filtering, monotonic presentation, stable fallback and source routing."""
import argparse
import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def check_backend(module):
    import numpy as np
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QImage
    upscale = importlib.import_module(module.__package__ + '.upscale')
    # RGB padding, flat colors, deterministic output and a strict sharpening limit.
    source = QImage(101, 60, QImage.Format_RGB888)
    source.fill(0xff507090)
    backend = upscale.LightScaler()
    assert backend.upscale(source, QSize(202, 120)).pixelColor(100, 50).getRgb()[:3] == (80, 112, 144)
    for x in range(101):
        for y in range(60):
            source.setPixel(x, y, 0xfff0f0f0 if (x // 3 + y // 3) % 2 else 0xff303030)
    output = backend.upscale(source, QSize(202, 120))
    repeated = backend.upscale(source, QSize(202, 120))
    assert output == repeated
    smooth = source.scaled(202, 120, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    baseline = np.frombuffer(smooth.constBits(), np.uint8).reshape(120, smooth.bytesPerLine())[:, :606].reshape(120, 202, 3)
    pixels = np.frombuffer(output.constBits(), np.uint8).reshape(120, output.bytesPerLine())[:, :606].reshape(120, 202, 3)
    difference = np.abs(pixels.astype(float) - baseline)
    assert difference.mean() > 1 and 4 < difference.max() <= 10
    bounded = backend.upscale(source, QSize(4000, 3000))
    assert bounded.width() <= 202 and bounded.height() <= 120
    return dict(cpu_only=True, deterministic=True, sharpening_limit=10, padded_rgb=True)


def check_worker(module):
    from PySide6.QtCore import QRectF, QSize
    from PySide6.QtGui import QImage
    upscale = importlib.import_module(module.__package__ + '.upscale')
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Backend:
        backend = 'test'
        def upscale(self, image, target):
            calls.append(image.size())
            entered.set()
            assert release.wait(5)
            return image
    source = QImage(1000, 600, QImage.Format_RGB32)
    source.fill(0xff507090)
    worker = upscale.CropEnhancer()
    try:
        with patch.object(upscale, 'LightScaler', Backend):
            worker.submit(1, source, QRectF(.1, .2, .1, .1), QSize(200, 120))
            assert entered.wait(3)
            for stamp in range(2, 102):
                worker.submit(stamp, source, QRectF(.1, .2, .1, .1), QSize(200, 120))
            assert worker.diagnostics()['pending'] == 1 and worker.diagnostics()['superseded'] == 99
            worker.reset()
            release.set()
            deadline = time.perf_counter() + 3
            while not worker.diagnostics()['completed'] and time.perf_counter() < deadline:
                time.sleep(.005)
            assert worker.take_result() is None
            assert calls == [QSize(100, 60)]
            worker.submit(200, source, QRectF(.1, .2, .1, .1), QSize(200, 120))
            deadline = time.perf_counter() + 3
            result = None
            while result is None and time.perf_counter() < deadline:
                result = worker.take_result()
                time.sleep(.005)
            assert result[0] == 200 and result[1].size() == source.size()
            assert result[2].size() == QSize(100, 60), 'Keep the exact source alongside its filtered result'
            entered.clear()
            release.clear()
            worker.submit(201, source, QRectF(.1, .2, .1, .1), QSize(200, 120))
            assert entered.wait(3)
            worker.stop()
            assert worker.thread.is_alive(), 'Closing must not wait for a busy filter'
    finally:
        release.set()
        worker.stop()
        if worker.thread is not None:
            worker.thread.join(3)
            assert not worker.thread.is_alive()
    return dict(single_pending=True, crop_only=True, old_generation_discarded=True, nonblocking_stop=True)


def check_timeline(app, module):
    from PySide6.QtCore import QRect, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QWidget
    floating_module = importlib.import_module(module.__package__ + '.floating')
    class Worker:
        def __init__(self):
            self.result = None
            self.requests = []
            self.closed = False
        def submit(self, *args):
            self.requests.append(args)
        def reset(self):
            self.result = None
        def stop(self):
            self.closed = True
        def diagnostics(self):
            return dict(ready=True, error='', bypassed=0)
        def take_result(self):
            value, self.result = self.result, None
            return value
    host = QWidget()
    source = QImage(256, 128, QImage.Format_RGB32)
    for x in range(256):
        for y in range(128):
            source.setPixel(x, y, 0xffeeeeee if (x // 4) % 2 else 0xff222222)
    with patch.object(floating_module, 'CropEnhancer', Worker):
        floating = module.FloatingCrop(host, 'fixture', (.12345, .13, .4, .3))
        floating.resize(200, 80)
        floating.set_quality_mode('light')
        try:
            floating.set_frame((100., source))
            worker = floating.enhancer
            floating.set_frame((100.02, source))
            assert floating.frame_key == 100., 'Do not jump to raw input before the corresponding enhanced frame'
            area = QRectF(.12345 * 256, .13 * 128, .4 * 256, .3 * 128).toAlignedRect()
            worker.result = (100.02, source, source.copy(area), area, 1., 1.)
            floating._refresh_enhancement()
            assert floating.frame_key == 100.02 and not floating.enhanced.isNull()
            # The filtered crop and full source must use identical fractional coordinates.
            filtered = QImage(floating.size(), QImage.Format_RGB32)
            painter = QPainter(filtered)
            floating.paint_picture(painter)
            painter.end()
            floating.enhanced = QImage()
            plain = QImage(floating.size(), QImage.Format_RGB32)
            painter = QPainter(plain)
            floating.paint_picture(painter)
            painter.end()
            assert filtered.copy(QRect(15, 15, 170, 50)) == plain.copy(QRect(15, 15, 170, 50)), 'Fractional crops must not shift when changing the filter'
            floating.set_frame((100.04, source))
            worker.result = (100., source, source.copy(area), area, 1., 1.)
            floating._refresh_enhancement()
            assert floating.frame_key == 100.02, 'A late result must never move playback backwards'
            # One GUI pause must not disable a cheap filter, even if its wall time is high.
            resumed = floating.enhance_poll + .150
            worker.result = (100.04, source, source.copy(area), area, 150., 1.)
            with patch.object(floating_module.time, 'perf_counter', return_value=resumed):
                floating._refresh_enhancement()
            assert floating.enhance_state == 'active' and floating.frame_key == 100.04
            floating.set_frame((100.06, source))
            with patch.object(floating_module.time, 'perf_counter') as clock:
                for tick in range(1, 28):
                    clock.return_value = resumed + tick * .020
                    floating._refresh_enhancement()
            assert floating.enhance_state == 'fallback' and floating.frame_key == 100.06 and worker.closed
            assert floating.enhance_reason == 'backend_timeout'
            count = len(worker.requests)
            for index in range(20):
                worker.result = (100.02, source, source.copy(area), area, 1., 1.)
                floating.set_frame((100.06 + index * .02, source))
            assert floating.enhanced.isNull() and len(worker.requests) == count, 'Fallback must remain stable'
            floating.set_quality_mode('source')
            floating.set_quality_mode('light')
            floating.set_frame((10., source))
            assert floating.frame_key == 10. and floating.enhance_state == 'active', 'Explicit seeks start a new timeline'
            floating.set_frame((10.12, source))
            floating.enhancer.result = (10.02, source, source.copy(area), area, 100., 1.)
            floating._refresh_enhancement()
            assert floating.enhance_state == 'active' and floating.frame_key == 10., 'An aged result is skipped without disabling the filter'
            floating.enhancer.result = (10.12, source, source.copy(area), area, 1., 1.)
            floating._refresh_enhancement()
            assert floating.frame_key == 10.12
            # A sustained CPU overrun still falls back; elapsed scheduling time alone does not.
            for tick in range(1, 9):
                stamp = 10.12 + tick * .02
                floating.enhancer.result = (stamp, source, source.copy(area), area, 80., 40.)
                floating.set_frame((stamp, source))
            assert floating.enhance_state == 'fallback' and floating.enhance_reason == 'cpu_budget'
            return dict(monotonic=True, stable_fallback=True, exact_crop_geometry=True, seek_reset=True,
                        gui_pause_not_filter_overload=True, sustained_cpu_overrun=True)
        finally:
            floating.close()
            host.close()
            host.deleteLater()
            app.processEvents()


def check_routing(app, host, module):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from ddm import plugins
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        context = plugins.PluginContext(manager, 'crop_quality_check')
        viewer = module.Viewer(context, {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        source = QImage(100, 60, QImage.Format_RGB32)
        source.fill(0xff507090)
        try:
            with patch.object(module.Decoder, 'start', lambda _self: None), patch.object(module.Chat, 'start', lambda _self: None), patch.object(module.AudioPump, 'start', lambda _self: None):
                viewer.add_room('1')
                viewer.add_room('2')
                viewer.toggle_running()
                viewer.render_timer.stop()
                viewer.match_timer.stop()
                viewer.audio.timer.stop()
                row = viewer.rows['2']
                preference = row.quality
                crop = viewer.show_floating_crop(row, (.1, .2, .2, .3), (1, source))
                assert row.decoder.seed['balanced_quality'] and not viewer.rows['1'].decoder.seed['balanced_quality']
                viewer.open_match_settings()
                assert viewer.crop_quality.count() == 2 and viewer.crop_quality.parentWidget() is viewer.match_settings
                decoder = row.decoder
                with patch.object(viewer, '_start_row', wraps=viewer._start_row) as start:
                    viewer.crop_quality.setCurrentIndex(viewer.crop_quality.findData('light'))
                    viewer.crop_quality.setCurrentIndex(viewer.crop_quality.findData('source'))
                    viewer.crop_quality.setCurrentIndex(viewer.crop_quality.findData('light'))
                    assert start.call_count == 0 and row.decoder is decoder, 'Changing the filter must not reconnect'
                viewer._information(row, decoder, {'requested_quality': 64, 'actual_quality': 64})
                assert row.quality == preference
                crop._fallback_enhancement('cpu_budget')
                viewer.match_settings.refresh_quality_status()
                assert '持续处理较慢' in viewer.match_settings.quality_status.text()
                viewer.save()
                assert context.setting('crop_quality') == 'light'
                crop.close()
                assert row.decoder.seed['crop_quality_policy'] == 'source' and row.quality == preference
                viewer.select_main('2')
                crop = viewer.show_floating_crop(row, (.1, .2, .2, .3), (2, source))
                assert not row.decoder.seed['balanced_quality']
                with patch.object(viewer, '_start_row', wraps=viewer._start_row) as start:
                    viewer.stop()
                    assert start.call_count == 0
            for saved, expected in [('superres', 'light'), ('highest', 'source'), ('unknown', 'source')]:
                context.set_setting('crop_quality', saved)
                restored = module.Viewer(context, {})
                assert restored.crop_quality.currentData() == expected
                restored.close()
                restored.deleteLater()
            return dict(passed=True, two_modes=True, persisted=True, main_protected=True, no_mode_reconnect=True, legacy_migrated=True)
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def check(app, host, module):
    return dict(passed=True, routing=check_routing(app, host, module), backend=check_backend(module),
                worker=check_worker(module), timeline=check_timeline(app, module))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
