"""Offline quality routing, real neural inference, and bounded asynchronous work."""
import argparse
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def check_backend(module):
    import cv2
    import numpy as np
    from PySide6.QtGui import QImage
    upscale = importlib.import_module(module.__package__ + '.upscale')
    model = Path(upscale.__file__).with_name('models') / 'FSRCNN_x2.pb'
    assert hashlib.sha256(model.read_bytes()).hexdigest() == '366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c'
    # Odd width exercises RGB row padding; fine edges distinguish inference from resize.
    image = QImage(101, 60, QImage.Format_RGB888)
    image.fill(0xff305080)
    for x in range(0, 101, 3):
        for y in range(0, 60, 3):
            image.setPixel(x, y, 0xfff0e0d0)
    backend = upscale.Fsrcnn2x()
    started = time.monotonic()
    output = backend.upscale(image)
    assert output.width() == 202 and output.height() == 120
    actual = np.frombuffer(output.constBits(), np.uint8).reshape(120, output.bytesPerLine())[:, :606].reshape(120, 202, 3)
    original = np.frombuffer(image.constBits(), np.uint8).reshape(60, image.bytesPerLine())[:, :303].reshape(60, 101, 3)
    baseline = cv2.resize(original, (202, 120), interpolation=cv2.INTER_CUBIC)
    assert np.mean(np.abs(actual.astype(float) - baseline)) > .1, 'SR must actually run the neural model'
    return dict(backend=backend.backend, cold_ms=round((time.monotonic() - started) * 1000, 2), neural_output=True)


def check_worker(module):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage
    upscale = importlib.import_module(module.__package__ + '.upscale')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    class Backend:
        backend = 'test'
        def upscale(self, image):
            calls.append((image.width(), image.height()))
            entered.set()
            assert release.wait(5)
            finished.set()
            return image.scaled(image.width() * 2, image.height() * 2)
    image = QImage(1000, 600, QImage.Format_RGB32)
    image.fill(0xff00ff00)
    worker = upscale.CropEnhancer()
    try:
        with patch.object(upscale, 'Fsrcnn2x', Backend):
            worker.submit(1, image, QRectF(.1, .2, .1, .1))
            assert entered.wait(3)
            for stamp in range(2, 102):
                worker.submit(stamp, image, QRectF(.1, .2, .1, .1))
            assert worker.diagnostics()['pending'] == 1
            assert worker.diagnostics()['superseded'] == 99
            worker.reset()
            release.set()
            assert finished.wait(3)
            deadline = time.monotonic() + 3
            while not worker.diagnostics()['completed'] and time.monotonic() < deadline:
                time.sleep(.005)
            assert worker.image_at(1) is None, 'Recrop/mode changes must discard in-flight results'
            assert calls == [(100, 60)], 'Only the selected source region is inferred'
            worker.submit(200, image, QRectF(.1, .2, .1, .1))
            deadline = time.monotonic() + 3
            while worker.image_at(200) is None and time.monotonic() < deadline:
                time.sleep(.005)
            assert worker.image_at(200) is not None
            assert worker.image_at(199) is None and worker.image_at(200.2) is None
            worker.stop()
            worker.thread.join(3)
            assert not worker.thread.is_alive()
            entered.clear()
            release.clear()
            stopped = upscale.CropEnhancer()
            stopped.submit(300, image, QRectF(.1, .2, .1, .1))
            assert entered.wait(3)
            stopped.stop()
            assert stopped.thread.is_alive(), 'Stop must return without waiting for in-flight inference'
            release.set()
            stopped.thread.join(3)
            assert not stopped.thread.is_alive()
    finally:
        release.set()
        worker.stop()
    return dict(single_pending=True, crop_only=True, stale_generation_rejected=True, timestamps_checked=True)


def check_enhanced_picture(app, module):
    from PySide6.QtCore import QEventLoop, QRectF, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    host.resize(640, 360)
    floating = module.FloatingCrop(host, 'fixture', (.25, .25, .5, .5))
    source = QImage(192, 128, QImage.Format_RGB32)
    source.fill(0xff507090)
    floating.set_quality_mode('superres')
    loop, feed = QEventLoop(), QTimer()
    count, enhanced, sizes = [0], [0], set()
    started = time.monotonic()
    def frame():
        count[0] += 1
        floating.set_frame((time.monotonic() - started, source))
        if not floating.enhanced.isNull():
            enhanced[0] += 1
            sizes.add((floating.enhanced.width(), floating.enhanced.height()))
    feed.timeout.connect(frame)
    feed.start(16)
    QTimer.singleShot(2000, loop.quit)
    try:
        loop.exec()
        diagnostics = floating.diagnostics()
        assert count[0] > 5 and enhanced[0] > 0, diagnostics
        assert sizes == {(192, 128)}, sizes
        floating.set_quality_mode('source')
        assert floating.enhanced.isNull() and floating.image.size() == source.size()
        return dict(passed=True, input_frames=count[0], enhanced_frames=enhanced[0], diagnostics=diagnostics)
    finally:
        feed.stop()
        floating.close()
        host.close()
        host.deleteLater()
        app.processEvents()


def check_routing(app, host, module):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage
    from ddm import plugins
    media = importlib.import_module(module.__package__ + '.media')
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        context = plugins.PluginContext(manager, 'crop_quality_check')
        viewer = module.Viewer(context, {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        image = QImage(100, 60, QImage.Format_RGB32)
        image.fill(0xff336699)
        try:
            with patch.object(module.Decoder, 'start', lambda _self: None), \
                    patch.object(module.Chat, 'start', lambda _self: None), \
                    patch.object(module.AudioPump, 'start', lambda _self: None):
                viewer.add_room('1')
                viewer.add_room('2')
                viewer.toggle_running()
                viewer.render_timer.stop()
                viewer.match_timer.stop()
                viewer.audio.timer.stop()
                row = viewer.rows['2']
                original_quality = row.quality
                crop = viewer.show_floating_crop(row, (.1, .2, .2, .3), (1, image))
                viewer.open_match_settings()
                assert viewer.crop_quality.parentWidget() is viewer.match_settings
                assert viewer.crop_quality.count() == 3
                viewer.crop_quality.setCurrentIndex(viewer.crop_quality.findData('highest'))
                assert row.decoder.seed['highest_quality'] and 'url' not in row.decoder.seed
                viewer._information(row, row.decoder, {'requested_quality': 10000, 'actual_quality': 10000})
                assert row.quality == original_quality, 'A crop override must preserve the source preference'
                viewer.crop_quality.setCurrentIndex(viewer.crop_quality.findData('superres'))
                assert row.decoder.seed['balanced_quality'] and not row.decoder.seed['highest_quality']
                assert not viewer.rows['1'].decoder.seed['balanced_quality'], 'Main picture must not be downgraded'
                viewer.select_main('2')
                assert not row.decoder.seed['balanced_quality'], 'Promoting a cropped source must restore main-picture quality'
                viewer.select_main('1')
                assert row.decoder.seed['balanced_quality']
                original_quality = row.quality  # Selecting it as main explicitly promotes its source quality.
                viewer.save()
                assert context.setting('crop_quality') == 'superres'
                viewer.crop_quality.setCurrentIndex(viewer.crop_quality.findData('source'))
                assert row.decoder.seed['quality'] == original_quality and not row.decoder.seed['balanced_quality']
                viewer.crop_quality.setCurrentIndex(viewer.crop_quality.findData('highest'))
                crop.close()
                assert row.decoder.seed['crop_quality_policy'] == 'source'
                assert row.quality == original_quality
                # Stopping must not restart a decoder while closing its crop.
                viewer.show_floating_crop(row, (.1, .2, .2, .3), (2, image))
                with patch.object(viewer, '_start_row', wraps=viewer._start_row) as start:
                    viewer.stop()
                    assert start.call_count == 0
                viewer.save()
            restored = module.Viewer(context, {})
            assert restored.crop_quality.currentData() == 'highest'
            restored.close()
            restored.deleteLater()
            # Resolve tiers in the worker, including URL invalidation and no-720 fallback.
            options = [{'qn': 10000, 'desc': '原画'}, {'qn': 80, 'desc': '1080P'}, {'qn': 64, 'desc': '720P'}, {'qn': 32, 'desc': '流畅'}]
            from types import SimpleNamespace
            requests = []
            platform = SimpleNamespace(kind='test', room_quality_options=lambda _: options,
                room_info=lambda _: None, play_url=lambda _, q: requests.append(q) or ('fixture', q, 0))
            decoder = media.Decoder('2', {'quality': 80, 'balanced_quality': True, 'url': 'stale'}, platform)
            assert decoder._resolve(0)[0] == 'fixture' and requests[-1] == 64
            options[:] = [{'qn': 900, 'desc': '原画'}, {'qn': 7, 'desc': '高清'}, {'qn': 999, 'desc': '流畅'}]
            decoder._resolve(0)
            assert requests[-1] == 7, 'Use platform order, not numeric quality IDs'
            decoder.seed['balanced_quality'] = False
            decoder.seed['highest_quality'] = True
            decoder._resolve(0)
            assert requests[-1] == 900
            decoder.stop()
            return dict(passed=True, persisted=True, main_protected=True, source_restored=True, worker_tiers=True)
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def check(app, host, module):
    return dict(passed=True, routing=check_routing(app, host, module),
                worker=check_worker(module), model=check_backend(module), picture=check_enhanced_picture(app, module))


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
