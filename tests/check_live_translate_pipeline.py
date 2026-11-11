"""Local recording regression through actual FFmpeg, ASR and local translation.

The paced HTTP fixture is functional regression, not live performance evidence.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import wave
from types import SimpleNamespace

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--python', required=True)
parser.add_argument('--recognition-model', required=True)
parser.add_argument('--engine', choices=('reazon', 'qwen3_asr'), default='reazon')
parser.add_argument('--translation-model', required=True)
parser.add_argument('--translation-server', required=True)
parser.add_argument('--sample', type=Path, required=True)
parser.add_argument('--ffmpeg', required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['DDM_NO_SAVE'] = '1'
from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtTest import QTest
import psutil

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pipeline_translate_plugin', ROOT / 'plugins/live_translate/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Options = sys.modules[spec.name + '.options'].Options
Session = sys.modules[spec.name + '.session'].Session


def main():
    app = QCoreApplication.instance() or QCoreApplication([])
    stopped = threading.Event()
    with wave.open(str(args.sample), 'rb') as source, io.BytesIO() as output:
        with wave.open(output, 'wb') as destination:
            destination.setparams(source.getparams())
            frames = source.readframes(source.getnframes())
            destination.writeframes(frames + b'\0\0' * source.getframerate() * 2)
        sample_body = output.getvalue()

    class Audio(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(sample_body)))
            self.end_headers()
            try:
                for offset in range(0, len(sample_body), 19200):
                    self.wfile.write(sample_body[offset:offset + 19200])
                    self.wfile.flush()
                    if stopped.wait(0.1):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *unused):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Audio)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session, tile = Session(), QObject()
    captions, statuses = [], []
    session.caption.connect(lambda original, translated: captions.append((original, translated)))
    session.status.connect(statuses.append)
    options = Options(engine=args.engine, python=args.python, model_dir=args.recognition_model,
                      local_model=args.translation_model, local_server=args.translation_server,
                      translation_mode='local', timeout=3)
    owned = []
    recognition_worker = None
    try:
        source = SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}/fixture.wav', headers={}, platform='bilibili')
        session.start(options, 'must-not-be-sent-to-local-model', source, tile, args.ffmpeg)
        deadline = time.monotonic() + 180
        while not any(translated for _original, translated in captions) and time.monotonic() < deadline:
            QTest.qWait(25)
            if session.tile is None:
                break
        assert any(translated for _original, translated in captions), f'No translated caption: {statuses[-3:]}'
        assert session.worker is not None and session.worker.is_alive, 'Embedded recognition thread is not running'
        recognition_worker = session.worker
        processes = [session.audio, session.local.process]
        owned = [int(process.processId()) for process in processes]
        assert len(set(owned)) == 2 and all(owned), 'Expected FFmpeg and local translation processes only'
        expected_name = Path(sys.executable).name
        helpers = [psutil.Process(pid) for pid in owned]
        assert all(process.name() == expected_name for process in helpers), \
            'Helper processes were not launched under the host image name'
        assert all(process.ppid() == os.getpid() for process in helpers), \
            'Helper processes were not owned by the host process'
        session._memory()
        expected_rss = sum(psutil.Process(pid).memory_info().rss for pid in owned)
        measured = session.samples[-1]['child_rss_bytes']
        assert measured and abs(measured - expected_rss) < 32 * 1024**2, 'Resource total missed an owned process'
        print(json.dumps({'translated_captions': sum(bool(text) for _original, text in captions),
                          'embedded_recognition': True, 'owned_processes': len(owned),
                          'fixture_child_rss_bytes': measured}, ensure_ascii=True))
    finally:
        stopped.set()
        session.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        app.processEvents()
    assert recognition_worker is not None and not recognition_worker.is_alive
    assert all(not psutil.pid_exists(pid) for pid in owned), 'An owned process remained after stop'
    assert not list(Path(args.ffmpeg).resolve().parent.glob('.live-translate-*'))
    assert not list(Path(args.translation_server).resolve().parent.glob('.live-translate-*'))
    print('PASS: actual local pipeline produced translated captions, used an embedded recognition thread, '
          'and released FFmpeg/local translation processes; '
          'live accuracy and latency were not measured')


if __name__ == '__main__':
    main()
