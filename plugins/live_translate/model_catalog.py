"""Find compatible local candidates near the selected model and bundled models."""
from pathlib import Path
from .options import DEFAULT_ASR_DIR, DEFAULT_TRANSLATION_FILE, Options


def model_name(path):
    name = Path(path).name
    return {DEFAULT_TRANSLATION_FILE: 'Qwen3 1.7B · Q8',
            DEFAULT_ASR_DIR: 'Qwen3-ASR 1.7B · INT8',
            'Qwen3-0.6B-Q8_0.gguf': 'Qwen3 0.6B · Q8',
            'qwen3-asr-0.6b-int8': 'Qwen3-ASR 0.6B · INT8'}.get(name, Path(path).stem if name.lower().endswith('.gguf') else name)


def model_ready(path, *, directory=False):
    try:
        if directory:
            Options(model_dir=str(path)).files()
            return True
        with Path(path).open('rb') as source:
            return source.read(4) == b'GGUF'
    except (OSError, ValueError):
        return False


def discover_models(current, *, directory=False, bundled=None):
    roots = [Path(bundled) if bundled is not None else Path(__file__).parent / 'vendor/models']
    if current:
        roots.insert(0, Path(current).parent)
    found = {}
    for root in roots:
        try:
            candidates = root.iterdir() if directory else root.glob('*.gguf')
            for path in candidates:
                if model_ready(path, directory=directory):
                    found[str(path.resolve()).casefold()] = str(path)
        except OSError:
            continue
    return sorted(found.values(), key=lambda path: (Path(path).name.casefold(), path.casefold()))
