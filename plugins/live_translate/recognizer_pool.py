"""Reference-count one ASR model per configuration; serialize native decoding."""
from pathlib import Path
import threading
from . import recognition

_guard = threading.Lock()
_models = {}


def acquire(options, sherpa):
    key = (str(Path(options.model_dir).resolve()), options.threads)
    with _guard:
        if key not in _models:
            _models[key] = [recognition.create_recognizer(options, sherpa), threading.Lock(), 0]
        entry = _models[key]
        entry[2] += 1
        return key, entry[0], entry[1]


def release(key):
    with _guard:
        entry = _models[key]
        entry[2] -= 1
        if not entry[2]:
            del _models[key]
