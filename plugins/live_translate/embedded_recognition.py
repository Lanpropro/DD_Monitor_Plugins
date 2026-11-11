"""In-process recognition thread; no plugin-managed Python subprocess."""
import gc
import queue
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QObject, Signal

from .protocol import HEADER, MAX_PENDING
from .translation import TranslationSlot


def runtime_paths(options):
    paths = [Path(__file__).resolve().parent / 'vendor/python/Lib/site-packages']
    if options.python:
        executable = Path(options.python)
        for root in (executable.parent, executable.parent.parent):
            paths.extend((root / 'Lib/site-packages', root / 'site-packages'))
    result = []
    for path in paths:
        if path.is_dir() and path not in result:
            result.append(path)
    return result


def _load_runtime(options):
    try:
        import numpy
        import sherpa_onnx
        return numpy, sherpa_onnx
    except ImportError:
        for path in runtime_paths(options):
            value = str(path)
            if value not in sys.path:
                sys.path.insert(0, value)
        import numpy
        import sherpa_onnx
        return numpy, sherpa_onnx


def _safe_error(error):
    if isinstance(error, ImportError):
        return '无法在宿主进程中加载识别运行库（需要 CPython 3.12）'
    if isinstance(error, ValueError):
        return '本地识别模型不可用，请检查模型目录'
    return f'本地识别线程失败（{type(error).__name__}），请检查模型与运行库'


class EmbeddedRecognition(QObject):
    """Own one model and one worker thread for the active session."""

    ready = Signal(object)
    original = Signal(object, int, str)
    caption = Signal(object, int, object)
    failed = Signal(object, str)

    def __init__(self, options, api_key='', parent=None):
        super().__init__(parent)
        self.options = options
        self.api_key = api_key
        self._queue = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pending = 0
        self._dropped = 0
        self._drop_next = False
        self._source_epoch = 0
        self._thread = threading.Thread(target=self._run, name='live-translate-asr', daemon=True)

    def start(self):
        self._thread.start()

    def feed(self, frame):
        if self._stop.is_set():
            return False
        if len(frame) < HEADER.size:
            return False
        size, reset = HEADER.unpack(frame[:HEADER.size])
        audio = frame[HEADER.size:]
        if size != len(audio) or not size:
            return False
        with self._lock:
            epoch = self._source_epoch
            if self._pending + size + HEADER.size > MAX_PENDING:
                self._dropped += size
                self._drop_next = True
                return False
            self._pending += size
        try:
            self._queue.put_nowait((audio, reset, epoch))
        except queue.Full:
            with self._lock:
                self._pending -= size
                self._dropped += size
                self._drop_next = True
            return False
        return True

    @property
    def pending_bytes(self):
        with self._lock:
            return self._pending

    @property
    def dropped_bytes(self):
        with self._lock:
            return self._dropped

    @property
    def is_alive(self):
        return self._thread.is_alive()

    @property
    def source_epoch(self):
        with self._lock:
            return self._source_epoch

    def stop(self):
        self._stop.set()
        with self._lock:
            self._source_epoch += 1
        if self._thread.is_alive():
            self._thread.join(5000)
        self._clear_frames()
        self.api_key = ''
        return not self._thread.is_alive()

    def _clear_frames(self, *, reset=False):
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._pending = 0
            self._drop_next = bool(reset)

    def reset_source(self):
        with self._lock:
            self._source_epoch += 1
        self._clear_frames(reset=True)

    def _current_source(self, epoch):
        with self._lock:
            return epoch == self._source_epoch

    def _next_frame(self):
        try:
            return self._queue.get(timeout=0.1)
        except queue.Empty:
            return None

    def _release_frame(self, size):
        with self._lock:
            self._pending = max(0, self._pending - size)

    def _consume_reset(self, reset):
        with self._lock:
            if self._drop_next:
                self._drop_next = False
                return True
            return reset

    def _run(self):
        np = sherpa = recognizer = vad = translator = None
        try:
            np, sherpa = _load_runtime(self.options)
            from .recognition import create_recognizer, create_vad, recognize
            recognizer = create_recognizer(self.options, sherpa)
            vad = create_vad(self.options, sherpa)
            translator = TranslationSlot(self.options, self.api_key)
            if self._stop.is_set():
                return
            self.ready.emit(self)
            while not self._stop.is_set():
                item = self._next_frame()
                if item is None:
                    continue
                audio, reset, epoch = item
                self._release_frame(len(audio))
                if not self._current_source(epoch):
                    continue
                if self._consume_reset(reset):
                    vad.reset()
                vad.accept_waveform(np.frombuffer(audio, dtype='<i2').astype(np.float32) / 32768)
                while not vad.empty() and not self._stop.is_set():
                    samples = np.array(vad.front.samples, dtype=np.float32)
                    start = vad.front.start / 16000
                    vad.pop()
                    began = time.monotonic()
                    text = recognize(recognizer, samples, self.options.engine)
                    asr_ms = (time.monotonic() - began) * 1000
                    if not text or not self._current_source(epoch):
                        continue
                    self.original.emit(self, epoch, text)
                    began = time.monotonic()
                    translated, warning = translator.run(text)
                    if not self._current_source(epoch):
                        continue
                    self.caption.emit(self, epoch, {
                        'event': 'caption',
                        'text': text,
                        'translated': translated,
                        'warning': warning,
                        'audio_start': start,
                        'audio_seconds': len(samples) / 16000,
                        'asr_ms': round(asr_ms, 1),
                        'translation_ms': round((time.monotonic() - began) * 1000, 1),
                    })
        except Exception as error:
            if not self._stop.is_set():
                self.failed.emit(self, _safe_error(error))
        finally:
            if translator is not None:
                translator.close()
            recognizer = vad = translator = None
            gc.collect()
