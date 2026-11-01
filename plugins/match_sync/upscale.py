"""Small CPU crop filter with a single replaceable pending frame."""
import logging
import math
import threading
import time
from collections import deque

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QImage


class LightScaler:
    """Qt smooth resize and limited sharpening; no model or GPU runtime required."""
    def __init__(self):
        import numpy
        self.np = numpy
        self.backend = 'CPU / Qt + NumPy'

    def upscale(self, image, target):
        np = self.np
        rgb = image.convertToFormat(QImage.Format_RGB888)
        scale = min(2., max(target.width() / rgb.width(), target.height() / rgb.height()),
                    math.sqrt(CropEnhancer.MAX_PIXELS / (rgb.width() * rgb.height())))
        size = (max(1, int(rgb.width() * scale)), max(1, int(rgb.height() * scale)))
        rgb = rgb.scaled(*size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        pixels = np.frombuffer(rgb.constBits(), np.uint8).reshape(rgb.height(), rgb.bytesPerLine())
        pixels = pixels[:, :rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3)
        padded = np.pad(pixels.astype(np.int16), ((1, 1), (1, 1), (0, 0)), mode='edge')
        center = padded[1:-1, 1:-1]
        laplacian = 4 * center - padded[:-2, 1:-1] - padded[2:, 1:-1] - padded[1:-1, :-2] - padded[1:-1, 2:]
        detail = np.clip(np.rint(laplacian.astype(np.float32) / 12), -10, 10)
        output = np.clip(center + detail, 0, 255).astype(np.uint8)
        return QImage(output.data, output.shape[1], output.shape[0], output.strides[0], QImage.Format_RGB888).copy()


class CropEnhancer:
    MAX_PIXELS = 640 * 360

    def __init__(self):
        self.condition = threading.Condition()
        self.pending = None
        self.result = None
        self.closed = False
        self.generation = 0
        self.thread = None
        self.stats = dict(ready=False, completed=0, superseded=0, bypassed=0,
                          inference_ms=0., cpu_ms=0., cpu_samples=0, backend='', error='')

    def reset(self):
        with self.condition:
            self.generation += 1
            self.pending = self.result = None

    def submit(self, timestamp, image, crop, target):
        with self.condition:
            if self.closed or self.stats['error']:
                return
            if self.pending is not None:
                self.stats['superseded'] += 1
            self.pending = (self.generation, timestamp, QImage(image), QRectF(crop), QSize(target))
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='match-crop-enhance', daemon=True)
                self.thread.start()
            self.condition.notify()

    def take_result(self):
        with self.condition:
            result, self.result = self.result, None
            return result

    def diagnostics(self):
        with self.condition:
            return dict(self.stats, pending=int(self.pending is not None))

    def stop(self):
        with self.condition:
            self.closed = True
            self.pending = self.result = None
            self.condition.notify()

    def _run(self):
        backend = None
        cpu_window = deque(maxlen=32)
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                generation, timestamp, image, crop, target = self.pending
                self.pending = None
            area = QRectF(crop.x() * image.width(), crop.y() * image.height(),
                          crop.width() * image.width(), crop.height() * image.height()).toAlignedRect().intersected(image.rect())
            if area.isEmpty() or area.width() * area.height() > self.MAX_PIXELS:
                with self.condition:
                    self.stats['bypassed'] += 1
                continue
            try:
                if backend is None:
                    backend = LightScaler()
                    with self.condition:
                        self.stats.update(ready=True, backend=backend.backend)
                started = time.perf_counter()
                cpu_started = time.thread_time()
                output = backend.upscale(image.copy(area), target)
                duration = (time.perf_counter() - started) * 1000
                cpu_window.append(cpu_started)
                # Windows thread CPU counters are coarse for tiny per-frame jobs.
                cpu_ms = (time.thread_time() - cpu_window[0]) * 1000 / len(cpu_window)
                with self.condition:
                    self.stats['inference_ms'] = duration
                    self.stats['cpu_ms'] = cpu_ms
                    self.stats['cpu_samples'] = len(cpu_window)
                    self.stats['completed'] += 1
                    if not self.closed and generation == self.generation:
                        self.result = (timestamp, image, output, area, duration, cpu_ms)
            except Exception as error:
                with self.condition:
                    self.pending = None
                    self.stats['error'] = str(error)
                logging.getLogger(__name__).exception('Crop enhancement unavailable; using source pixels')
                return
