"""Explicit in-process recognition smoke test; not a live-stream benchmark."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--plugin', type=Path)
parser.add_argument('--model-dir', type=Path, required=True)
parser.add_argument('--python', type=Path)
parser.add_argument('--sample', type=Path, required=True)
parser.add_argument('--ffmpeg', type=Path, required=True)
parser.add_argument('--engine', choices=('reazon', 'qwen3_asr'), default='qwen3_asr')
args = parser.parse_args()

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = (args.plugin or ROOT / 'plugins/live_translate').resolve()
sys.path.insert(0, str(args.host.resolve()))
sys.path.insert(0, str(PLUGIN))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QCoreApplication

spec = importlib.util.spec_from_file_location('embedded_translate_plugin', PLUGIN / 'plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from embedded_translate_plugin.options import Options
from embedded_translate_plugin.embedded_recognition import EmbeddedRecognition
from embedded_translate_plugin.protocol import HEADER


def main():
    app = QCoreApplication([])
    options = Options(engine=args.engine, model_dir=str(args.model_dir.resolve()),
                      python=str(args.python.resolve()) if args.python else '',
                      translation_mode='original')
    options.validate()
    audio = subprocess.check_output([
        str(args.ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-i', str(args.sample), '-t', '20', '-ac', '1', '-ar', '16000',
        '-f', 's16le', 'pipe:1',
    ], timeout=30)
    worker = EmbeddedRecognition(options)
    ready, captions, errors = [], [], []
    worker.ready.connect(lambda value: ready.append(value))
    worker.caption.connect(lambda _worker, _epoch, value: captions.append(value))
    worker.failed.connect(lambda _worker, message: errors.append(message))
    try:
        worker.start()
        deadline = time.monotonic() + 60
        while not ready and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.05)
        assert ready and not errors, errors
        for offset in range(0, len(audio), 6400):
            chunk = audio[offset:offset + 6400]
            worker.feed(HEADER.pack(len(chunk), False) + chunk)
            deadline = time.monotonic() + 5
            while worker.pending_bytes > 32000 and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.02)
            app.processEvents()
            time.sleep(0.2)
        deadline = time.monotonic() + 30
        while not captions and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.05)
        assert captions and not errors, errors or 'No embedded captions'
        assert all(value['text'].strip() for value in captions)
        print(f'PASS: embedded {args.engine} produced {len(captions)} captions from the selected sample; '
              'accuracy and live latency were not measured')
    finally:
        assert worker.stop(), 'Embedded recognition thread did not stop'
        app.processEvents()


if __name__ == '__main__':
    main()
