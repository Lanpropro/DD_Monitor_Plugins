"""Prepare isolated local recognition and CPU translation, without account settings."""
import argparse
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

from options import REAZON_FILES

LLAMA_TAG = 'b12211'
LLAMA_URL = ('https://github.com/ggml-org/llama.cpp/releases/download/'
             'b12211/llama-b12211-bin-win-cpu-x64.zip')
LLAMA_SHA256 = '23729000408c6968faf5e322afc99319d3b8c576ba872ef7663d9b89810090f3'
TRANSLATION_FILE = 'Qwen3-0.6B-Q8_0.gguf'
TRANSLATION_URL = 'https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/' + TRANSLATION_FILE
TRANSLATION_SHA256 = '9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031'


def download(url, destination, sha256=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    request = urllib.request.Request(url, headers={'User-Agent': 'DDM-LiveTranslate-Setup'})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=30) as response, partial.open('wb') as output:
        while chunk := response.read(262144):
            output.write(chunk)
            digest.update(chunk)
    if sha256 and digest.hexdigest() != sha256:
        raise ValueError('Downloaded file checksum does not match the official artifact')
    partial.replace(destination)


def prepare_translation(root):
    if sys.platform != 'win32':
        raise ValueError('Automatic translation runtime setup currently supports Windows x64')
    directory = root / 'translation' / LLAMA_TAG
    server = directory / 'llama-server.exe'
    if not server.is_file():
        archive = root / 'translation' / (LLAMA_TAG + '.zip')
        print('Downloading llama.cpp CPU runtime', flush=True)
        download(LLAMA_URL, archive, LLAMA_SHA256)
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                target = (directory / item.filename).resolve()
                if not target.is_relative_to(directory.resolve()):
                    raise ValueError('Invalid runtime archive path')
            package.extractall(directory)
        if not server.is_file():
            raise ValueError('Translation runtime archive does not contain llama-server.exe')
    model = root / 'translation' / TRANSLATION_FILE
    if not model.is_file():
        print('Downloading Qwen3 0.6B Q8 translation model (about 640 MB)', flush=True)
        download(TRANSLATION_URL, model, TRANSLATION_SHA256)
    return {'translation_mode': 'local', 'local_model': str(model), 'local_server': str(server)}


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
    model_dir = root / 'reazon-ja-en'
    base = 'https://huggingface.co/csukuangfj/reazonspeech-k2-v2-ja-en/resolve/main/'
    for filename in REAZON_FILES.values():
        path = model_dir / filename
        if not path.is_file():
            print('Downloading', filename, flush=True)
            download(base + filename, path)
    vad = model_dir / 'silero_vad.onnx'
    if not vad.is_file():
        download('https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx', vad)
    settings = {'python': str(python), 'model_dir': str(model_dir)}
    if not args.recognition_only:
        settings.update(prepare_translation(root))
    print(json.dumps(settings, ensure_ascii=True))


if __name__ == '__main__':
    main()
