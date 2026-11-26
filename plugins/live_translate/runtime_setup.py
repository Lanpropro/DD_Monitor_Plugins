"""Pinned Windows runtime wheels and existing verified model preparation."""
import hashlib
from pathlib import Path
import struct
import sys
import zipfile
from . import prepare_runtime as prepare

# Official PyPI files, matching the already tested local runtime; no pip or
# separate Python installation is required by the packaged host.
WHEELS = (
    ('numpy-2.5.4-cp312-cp312-win_amd64.whl',
     '12/33/fefe62073dc8acfd0f2b9ed7c003af2f50aa61555e113e6db02b8f79f145',
     'a7b1b6353e36a7e50de2973a38d705c88ee93adcf120673cee7f45a4a3fa223a'),
    ('sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl',
     '94/38/64356ad97f68fffcf01fe7545407ad18a2ea86c1ffae5b0950f7fac73638',
     '5579e80196d516e6dae23c8f629292ce3142ab8869925d32b94612fd86f93733'),
    ('sherpa_onnx-1.13.8-cp312-cp312-win_amd64.whl',
     '24/af/33e9dcaa527bbc0c2526959713626607a46da2d7b19871b7773d8607d356',
     '566e49ad3fb2aa8ceb723a52f9809aa6c2ee0a5eb3b0946c3fd2374af807a5c6'),
)


def check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError('Model setup cancelled')


def extract_wheel(archive, destination, cancel):
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for name in package.namelist():
            check_cancel(cancel)
            if not (destination / name).resolve().is_relative_to(destination):
                raise ValueError('Invalid wheel archive path')
        for name in package.namelist():
            check_cancel(cancel)
            package.extract(name, destination)


def prepare_libraries(root, fetch, cancel):
    runtime = root / 'runtime'
    libraries = runtime / 'Lib/site-packages'
    for filename, location, digest in WHEELS:
        check_cancel(cancel)
        marker = libraries / f'.ddm-{digest}.ready'
        if marker.is_file():
            continue
        archive = root / 'downloads' / filename
        if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            fetch(f'https://files.pythonhosted.org/packages/{location}/{filename}', archive, digest)
        extract_wheel(archive, libraries, cancel)
        marker.write_text(filename, encoding='utf-8')
    return runtime


def install_models(directory, progress, cancel):
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
        raise ValueError('Model setup requires the Windows x64 CPython 3.12 host')
    root = Path(directory).resolve()
    stage = ''
    def fetch(url, destination, digest):
        check_cancel(cancel)
        prepare.download(url, destination, digest, cancel=cancel,
                         progress=lambda done, total: progress(stage, done, total))
    stage = '准备识别运行库'
    progress(stage, 0, 0)
    runtime = prepare_libraries(root, fetch, cancel)
    check_cancel(cancel)
    stage = '准备 Qwen3-ASR 识别模型'
    progress(stage, 0, 0)
    recognition = prepare.prepare_recognition(root, fetch=fetch)
    check_cancel(cancel)
    stage = '准备音乐检测模型'
    progress(stage, 0, 0)
    music = prepare.prepare_music(root, fetch=fetch)
    check_cancel(cancel)
    stage = '准备 Qwen3 本地翻译模型'
    progress(stage, 0, 0)
    translation = prepare.prepare_translation(root, fetch=fetch)
    check_cancel(cancel)
    return dict(translation, engine='qwen3_asr', python=str(runtime), model_dir=str(recognition),
                music_detection=True, music_model_dir=str(music))
