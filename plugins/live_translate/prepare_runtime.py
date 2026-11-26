"""Prepare isolated local recognition and CPU translation, without account settings."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
from http.client import IncompleteRead
from pathlib import Path
import subprocess
import sys
import tarfile
import shutil
from threading import Lock
import urllib.request
import zipfile
import time

try:
    from .options import DEFAULT_ASR_DIR, DEFAULT_TRANSLATION_FILE
except ImportError:
    from options import DEFAULT_ASR_DIR, DEFAULT_TRANSLATION_FILE

RECOGNITION_URL = ('https://huggingface.co/thieunv-asilla/sherpa-onnx-qwen3-asr-1.7B-int8/'
                   'resolve/69eb686fd94a4a865bb5340a3d6ac0d7f1fec0d5/')
RECOGNITION_FILES = {
    'conv_frontend.onnx': '3cb27a9fe94d95c938e476f2012b21aba2ec0bfceef33b0e58acd208946bafdd',
    'encoder.int8.onnx': 'a5deedae034ece715de8ed204378d8c77f889af3a60c2566581135e84cced7cd',
    'decoder.int8.onnx': 'c43c853fa6e97d08365cb8a5502b360b595cd43c00dc60e4d8ca7cc18cad460b',
    'tokenizer/merges.txt': '8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5',
    'tokenizer/tokenizer_config.json': '4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c',
    'tokenizer/vocab.json': 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910',
}
VAD_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx'
VAD_SHA256 = '9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6'
MUSIC_ARCHIVE = 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15.tar.bz2'
MUSIC_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/audio-tagging-models/' + MUSIC_ARCHIVE
# Pinned from the official HTTPS artifact; this older release has no published digest.
MUSIC_SHA256 = '07e2fafcdcbc461f2816188d9b0bbafced12584030cf67d5652e549ef256a2c6'

LLAMA_TAG = 'b12211'
LLAMA_URL = ('https://github.com/ggml-org/llama.cpp/releases/download/'
             'b12211/llama-b12211-bin-win-cpu-x64.zip')
LLAMA_SHA256 = '23729000408c6968faf5e322afc99319d3b8c576ba872ef7663d9b89810090f3'
TRANSLATION_URL = ('https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/'
                   'resolve/90862c4b9d2787eaed51d12237eafdfe7c5f6077/' + DEFAULT_TRANSLATION_FILE)
TRANSLATION_SHA256 = '061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a'
LARGE_FILES = {
    RECOGNITION_FILES['encoder.int8.onnx']: 314222162,
    RECOGNITION_FILES['decoder.int8.onnx']: 2037458645,
    TRANSLATION_SHA256: 1834426016,
}
RANGE_SIZE = 128 * 1024 * 1024


def _download_ranges(url, partial, total, progress, cancel):
    """Fetch fixed ranges of pinned weights, then let the caller verify the full hash."""
    with partial.open('wb') as output:
        output.truncate(total)
    lock = Lock()
    received = {}
    last_progress = 0.

    def fetch_range(start):
        nonlocal last_progress
        end = min(start + RANGE_SIZE, total) - 1
        offset = start
        for attempt in range(5):
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Model setup cancelled')
            request = urllib.request.Request(
                f'{url}?download=true&ddm_range={offset}&try={attempt}',
                headers={'User-Agent': 'DDM-LiveTranslate-Setup', 'Range': f'bytes={offset}-{end}'})
            deadline = time.monotonic() + 180
            try:
                with urllib.request.urlopen(request, timeout=30) as response, partial.open('r+b') as output:
                    if response.headers.get('Content-Range') != f'bytes {offset}-{end}/{total}':
                        raise ValueError('Unexpected model download range')
                    read_chunk = getattr(response, 'read1', response.read)
                    output.seek(offset)
                    while offset <= end:
                        if cancel is not None and cancel.is_set():
                            raise InterruptedError('Model setup cancelled')
                        if time.monotonic() > deadline:
                            raise TimeoutError('Model download range stalled')
                        chunk = read_chunk(min(65536, end - offset + 1))
                        if not chunk:
                            raise OSError('Incomplete model download range')
                        output.write(chunk)
                        offset += len(chunk)
                        if progress is not None:
                            with lock:
                                received[start] = max(received.get(start, 0), offset - start)
                                now = time.monotonic()
                                done = sum(received.values())
                                if now - last_progress >= .25 or done == total:
                                    last_progress = now
                                    progress(done, total)
                return
            except InterruptedError:
                raise
            except (OSError, IncompleteRead):
                if attempt == 4:
                    raise

    with ThreadPoolExecutor(max_workers=5) as pool:
        for _ in pool.map(fetch_range, range(0, total, RANGE_SIZE)):
            pass


def download(url, destination, sha256=None, *, progress=None, cancel=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    request = urllib.request.Request(url, headers={'User-Agent': 'DDM-LiveTranslate-Setup'})
    digest = hashlib.sha256()
    try:
        if sha256 in LARGE_FILES:
            _download_ranges(url, partial, LARGE_FILES[sha256], progress, cancel)
            with partial.open('rb') as source:
                for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError('Model setup cancelled')
                    digest.update(chunk)
            if digest.hexdigest() != sha256:
                raise ValueError('Downloaded file checksum does not match the pinned artifact')
            partial.replace(destination)
            return
        with urllib.request.urlopen(request, timeout=30) as response, partial.open('wb') as output:
            total, received, last = int(response.headers.get('Content-Length', 0)), 0, 0
            read_chunk = getattr(response, 'read1', response.read)
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('Model setup cancelled')
                chunk = read_chunk(65536)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress is not None and (time.monotonic() - last >= .25 or received == total):
                    progress(received, total)
                    last = time.monotonic()
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Model setup cancelled')
        if sha256 and digest.hexdigest() != sha256:
            raise ValueError('Downloaded file checksum does not match the official artifact')
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def prepare_translation(root, *, fetch=None):
    fetch = fetch or download
    if sys.platform != 'win32':
        raise ValueError('Automatic translation runtime setup currently supports Windows x64')
    directory = root / 'translation' / LLAMA_TAG
    server = directory / 'llama-server.exe'
    if not server.is_file():
        archive = root / 'translation' / (LLAMA_TAG + '.zip')
        print('Downloading llama.cpp CPU runtime', flush=True)
        fetch(LLAMA_URL, archive, LLAMA_SHA256)
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                target = (directory / item.filename).resolve()
                if not target.is_relative_to(directory.resolve()):
                    raise ValueError('Invalid runtime archive path')
            package.extractall(directory)
        if not server.is_file():
            raise ValueError('Translation runtime archive does not contain llama-server.exe')
    model = root / 'translation' / DEFAULT_TRANSLATION_FILE
    if not model.is_file():
        print('Downloading Qwen3 1.7B Q8 translation model (about 1.83 GB)', flush=True)
        fetch(TRANSLATION_URL, model, TRANSLATION_SHA256)
    return {'translation_mode': 'local', 'local_model': str(model), 'local_server': str(server)}


def prepare_recognition(root, *, fetch=None):
    fetch = fetch or download
    model_dir = root / DEFAULT_ASR_DIR
    for name, digest in RECOGNITION_FILES.items():
        target = model_dir / name
        if not target.is_file():
            print(f'Downloading Qwen3-ASR 1.7B INT8: {name}', flush=True)
            fetch(RECOGNITION_URL + name, target, digest)
    vad = model_dir / 'silero_vad.onnx'
    if not vad.is_file():
        fetch(VAD_URL, vad, VAD_SHA256)
    return model_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='Runtime and model storage outside the plugin package')
    parser.add_argument('--recognition-only', action='store_true', help='Skip local translation artifacts')
    args = parser.parse_args()
    root = args.directory.resolve()
    runtime = root / 'runtime'
    if not runtime.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(runtime)], check=True)
    python = runtime / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', '-r',
                    str(Path(__file__).with_name('requirements-worker.txt'))], check=True)
    model_dir = prepare_recognition(root)
    settings = {'engine': 'qwen3_asr', 'python': str(python), 'model_dir': str(model_dir)}
    music_dir = prepare_music(root)
    settings.update(music_detection=True, music_model_dir=str(music_dir))
    if not args.recognition_only:
        settings.update(prepare_translation(root))
    print(json.dumps(settings, ensure_ascii=True))


def prepare_music(root, *, fetch=None):
    fetch = fetch or download
    directory = root / 'audio-tagging'
    names = ('model.int8.onnx', 'class_labels_indices.csv')
    if all((directory / name).is_file() for name in names):
        return directory
    archive = root / MUSIC_ARCHIVE
    if not archive.is_file():
        print('Downloading audio tagging model (about 111 MB)', flush=True)
        fetch(MUSIC_URL, archive, MUSIC_SHA256)
    with archive.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != MUSIC_SHA256:
            raise ValueError('Audio tagging archive checksum mismatch')
    with tarfile.open(archive, 'r:bz2') as package:
        for name in names:
            member = package.getmember(MUSIC_ARCHIVE.removesuffix('.tar.bz2') + '/' + name)
            if not member.isfile():
                raise ValueError('Audio tagging archive contains an invalid model file')
            directory.mkdir(parents=True, exist_ok=True)
            with package.extractfile(member) as source, (directory / name).open('wb') as output:
                shutil.copyfileobj(source, output, 262144)
    return directory


if __name__ == '__main__':
    main()
