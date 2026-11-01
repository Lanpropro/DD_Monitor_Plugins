"""Bounded asynchronous crop enhancement; source timestamps remain authoritative."""
import logging
from pathlib import Path
import threading
import time

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage


_layer_lock = threading.Lock()
_layer_registered = False


class DepthToSpace:
    """TensorFlow NHWC pixel shuffle, missing from standard OpenCV's TF importer."""
    def __init__(self, params, blobs):
        self.scale = int(params['block_size'])

    def getMemoryShapes(self, inputs):
        n, c, h, w = inputs[0]
        s = self.scale
        return [[n, c // (s * s), h * s, w * s]]

    def forward(self, inputs):
        n, c, h, w = inputs[0].shape
        s = self.scale
        return [inputs[0].reshape(n, s, s, c // (s * s), h, w)
                .transpose(0, 3, 4, 1, 5, 2).reshape(n, c // (s * s), h * s, w * s)]


class Fsrcnn2x:
    """Small bundled neural model; replace this backend to add other SR models."""
    def __init__(self):
        import cv2
        global _layer_registered
        with _layer_lock:
            if not _layer_registered:
                cv2.dnn_registerLayer('DepthToSpace', DepthToSpace)
                _layer_registered = True
        self.net = cv2.dnn.readNetFromTensorflow(str(Path(__file__).with_name('models') / 'FSRCNN_x2.pb'))
        self.backend = 'CPU'
        if cv2.dnn.DNN_TARGET_OPENCL in cv2.dnn.getAvailableTargets(cv2.dnn.DNN_BACKEND_OPENCV):
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_OPENCL)
            self.backend = 'OpenCL'

    def upscale(self, image):
        import cv2
        import numpy as np
        rgb = image.convertToFormat(QImage.Format_RGB888)
        pixels = np.frombuffer(rgb.constBits(), np.uint8).reshape(rgb.height(), rgb.bytesPerLine())
        pixels = pixels[:, :rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3)
        ycrcb = cv2.cvtColor(pixels, cv2.COLOR_RGB2YCrCb).astype(np.float32) / 255
        self.net.setInput(cv2.dnn.blobFromImage(ycrcb[:, :, 0]))
        try:
            y = self.net.forward()[0, 0]
        except cv2.error:
            if self.backend == 'CPU':
                raise
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self.backend = 'CPU'
            y = self.net.forward()[0, 0]
        output = cv2.resize(ycrcb, (rgb.width() * 2, rgb.height() * 2), interpolation=cv2.INTER_CUBIC)
        output[:, :, 0] = y
        output = np.clip(cv2.cvtColor(output, cv2.COLOR_YCrCb2RGB) * 255, 0, 255).astype(np.uint8)
        return QImage(output.data, output.shape[1], output.shape[0], output.strides[0], QImage.Format_RGB888).copy()


class CropEnhancer:
    # One running job and one replaceable pending frame, never a growing FIFO.
    MAX_AGE = .080
    MAX_PIXELS = 640 * 360

    def __init__(self):
        self.condition = threading.Condition()
        self.pending = None
        self.result = None
        self.closed = False
        self.generation = 0
        self.thread = None
        self.stats = dict(completed=0, superseded=0, expired=0, bypassed=0, failures=0,
                          inference_ms=0.0, backend='', error='')

    def reset(self):
        with self.condition:
            self.generation += 1
            self.pending = self.result = None

    def submit(self, timestamp, image, crop):
        with self.condition:
            if self.closed or self.stats['error']:
                return
            if self.pending is not None:
                self.stats['superseded'] += 1
            self.pending = (self.generation, timestamp, time.perf_counter(), QImage(image), QRectF(crop))
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='match-crop-upscale', daemon=True)
                self.thread.start()
            self.condition.notify()

    def image_at(self, timestamp):
        with self.condition:
            result = self.result
        if result is not None and 0 <= timestamp - result[0] <= self.MAX_AGE:
            return result[1]
        return None

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
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                generation, timestamp, submitted, image, crop = self.pending
                self.pending = None
            area = QRectF(crop.x() * image.width(), crop.y() * image.height(),
                          crop.width() * image.width(), crop.height() * image.height()).toAlignedRect().intersected(image.rect())
            if area.isEmpty() or area.width() * area.height() > self.MAX_PIXELS:
                with self.condition:
                    self.stats['bypassed'] += 1
                continue
            try:
                if backend is None:
                    backend = Fsrcnn2x()
                started = time.perf_counter()
                output = backend.upscale(image.copy(area))
                duration = (time.perf_counter() - started) * 1000
                with self.condition:
                    self.stats.update(inference_ms=duration, backend=backend.backend)
                    self.stats['completed'] += 1
                    if not self.closed and generation == self.generation:
                        # A paused frame is still current even after a cold model load.
                        if self.pending is None or time.perf_counter() - submitted <= self.MAX_AGE:
                            self.result = (timestamp, output)
                        else:
                            self.stats['expired'] += 1
            except Exception as error:
                with self.condition:
                    self.stats['failures'] += 1
                    self.stats['error'] = str(error)
                logging.getLogger(__name__).exception('Crop super-resolution unavailable; using source pixels')
                return
