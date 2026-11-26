"""In-process recognition thread; no plugin-managed Python subprocess."""
import gc
from dataclasses import replace
import queue
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from .protocol import HEADER, MAX_PENDING
from .translation import TranslationSlot
from .music_detection import MusicDetector, MUSIC_MARKER
from .speech_pipeline import SpeechPipeline


# Keep a retiring QObject alive even after its plugin/session is unloaded.
_recognition_workers = set()


def runtime_paths(options):
    paths = [Path(__file__).resolve().parent / 'vendor/python/Lib/site-packages']
    if options.python:
        executable = Path(options.python)
        if executable.is_dir():
            paths.extend((executable / 'Lib/site-packages', executable / 'site-packages', executable))
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

    def __init__(self, options, api_key='', parent=None, *, group=None):
        super().__init__(parent)
        self.options = options
        self.api_key = api_key
        self.group = group
        # One s16le sample is the smallest valid frame. The byte budget below
        # owns capacity; pipe read granularity must not impose a smaller limit.
        self._queue = queue.Queue(maxsize=MAX_PENDING // 2)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pending = 0
        self._dropped = 0
        self._drop_next = False
        self._source_epoch = 0
        self._thread = threading.Thread(target=self._run, name='live-translate-asr', daemon=True)
        self._retire_timer = QTimer(self)
        self._retire_timer.setInterval(50)
        self._retire_timer.timeout.connect(self._retire)

    @staticmethod
    def available(group=None):
        return not any(worker.is_alive and (group is None or worker.group is not group or worker._stop.is_set())
                       for worker in _recognition_workers)

    @staticmethod
    def ensure_available(group=None):
        if not EmbeddedRecognition.available(group):
            raise RuntimeError('上一轮识别线程仍在运行或退出，请稍后重试')

    def start(self):
        self.ensure_available(self.group)
        _recognition_workers.add(self)
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
        self._clear_frames()
        self.api_key = ''
        if self.is_alive:
            # Native inference cannot be interrupted by joining a Python thread.
            # Detach before Session is deleted; retire only on the GUI thread.
            self.setParent(None)
            self._retire_timer.start()
            return False
        _recognition_workers.discard(self)
        return True

    def _retire(self):
        if not self.is_alive:
            self._retire_timer.stop()
            _recognition_workers.discard(self)
            self.deleteLater()

    def _clear_frames(self, *, reset=False):
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._pending = 0
            self._drop_next = bool(reset)

    def reset_source(self, *, glossary=None):
        with self._lock:
            self._source_epoch += 1
            if glossary is not None:
                self.options = replace(self.options, glossary_language=self.options.target_language, glossary=glossary)
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
        np = sherpa = recognizer = vad = translator = detector = pipeline = None
        model_key = None
        try:
            np, sherpa = _load_runtime(self.options)
            from .recognition import create_vad
            from .recognizer_pool import acquire, release
            model_key, recognizer, decode_lock = acquire(self.options, sherpa)
            vad = create_vad(self.options, sherpa)
            translator = TranslationSlot(self.options, self.api_key)
            pipeline = SpeechPipeline(self.options, np, vad, recognizer, decode_lock, translator)
            if self.options.music_detection:
                detector = MusicDetector(self.options.music_model_dir, sherpa, np)
            if self._stop.is_set():
                return
            self.ready.emit(self)
            active_epoch = self.source_epoch
            while not self._stop.is_set():
                item = self._next_frame()
                if item is None:
                    epoch, jobs = active_epoch, pipeline.poll()
                else:
                    audio, reset, epoch = item
                    self._release_frame(len(audio))
                    if not self._current_source(epoch):
                        continue
                    active_epoch = epoch
                    if self._consume_reset(reset):
                        pipeline.reset(self.options)
                        if detector is not None:
                            detector.reset()
                        self.original.emit(self, epoch, '')
                    waveform = np.frombuffer(audio, dtype='<i2').astype(np.float32) / 32768
                    changed = detector.feed(waveform) if detector is not None else None
                    if not self._current_source(epoch):
                        continue
                    if changed is not None:
                        pipeline.reset()
                        self.original.emit(self, epoch, MUSIC_MARKER if changed else '')
                    if detector is not None and detector.active:
                        continue
                    jobs = pipeline.feed(waveform)
                for job in jobs:
                    result = pipeline.process(job, lambda epoch=epoch: not self._stop.is_set() and self._current_source(epoch),
                                              can_review=lambda: self.pending_bytes == 0)
                    if result is not None:
                        self.caption.emit(self, epoch, result)
        except Exception as error:
            if not self._stop.is_set():
                self.failed.emit(self, _safe_error(error))
        finally:
            if translator is not None:
                translator.close()
            if model_key is not None:
                release(model_key)
            recognizer = vad = translator = detector = pipeline = None
            gc.collect()
