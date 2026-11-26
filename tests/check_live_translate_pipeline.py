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

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--python', required=True)
parser.add_argument('--recognition-model', required=True)
parser.add_argument('--music-model', default='', help='Optional prepared audio-tagging directory')
parser.add_argument('--engine', choices=('qwen3_asr',), default='qwen3_asr')
parser.add_argument('--translation-model', required=True)
parser.add_argument('--translation-server', required=True)
parser.add_argument('--sample', type=Path, required=True)
parser.add_argument('--ffmpeg', required=True)
parser.add_argument('--rooms', type=int, choices=(1, 2), default=1)
args = parser.parse_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['DDM_NO_SAVE'] = '1'
from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtTest import QTest
from ddm.plugins import StreamSource
import psutil

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pipeline_translate_plugin', ROOT / 'plugins/live_translate/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Options = sys.modules[spec.name + '.options'].Options
Session = sys.modules[spec.name + '.session'].Session
SessionGroup = sys.modules[spec.name + '.session_group'].SessionGroup


def main():
    app = QCoreApplication.instance() or QCoreApplication([])
    stopped = threading.Event()
    with wave.open(str(args.sample), 'rb') as source, io.BytesIO() as output:
        chunk_bytes = source.getframerate() * source.getsampwidth() * source.getnchannels() // 5
        with wave.open(output, 'wb') as destination:
            destination.setparams(source.getparams())
            frames = source.readframes(source.getnframes())
            # Keep the fixture transport alive through the longer utterance
            # boundary plus bounded translation, rather than cancelling at EOF.
            destination.writeframes(frames + b'\0\0' * source.getframerate() * 6)
        sample_body = output.getvalue()

    class Audio(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(sample_body)))
            self.end_headers()
            try:
                for offset in range(0, len(sample_body), chunk_bytes):
                    self.wfile.write(sample_body[offset:offset + chunk_bytes])
                    self.wfile.flush()
                    if stopped.wait(0.2):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *unused):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Audio)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session, tile = Session(), QObject()
    captions, statuses, translated_rooms = [], [], set()
    coordinator = SessionGroup(session) if args.rooms > 1 else None
    tiles = [tile, *[QObject() for _ in range(args.rooms - 1)]]
    if coordinator is None:
        session.caption.connect(lambda original, translated: captions.append((original, translated)))
        session.status.connect(statuses.append)
    else:
        def caption(target, original, translated):
            captions.append((original, translated))
            if translated:
                translated_rooms.add(target)
        coordinator.caption.connect(caption)
        coordinator.status.connect(statuses.append)
    options = Options(engine=args.engine, python=args.python, model_dir=args.recognition_model,
                      local_model=args.translation_model, local_server=args.translation_server,
                      translation_mode='local', timeout=3,
                      music_detection=bool(args.music_model), music_model_dir=args.music_model)
    owned = []
    owned_directories = []
    recognition_workers = []
    try:
        source = StreamSource('fixture', f'http://127.0.0.1:{server.server_port}/fixture.wav', platform='bilibili')
        if coordinator is None:
            session.start(options, 'must-not-be-sent-to-local-model', source, tile, args.ffmpeg)
        else:
            coordinator.start(options, 'must-not-be-sent-to-local-model', [(target, source) for target in tiles], args.ffmpeg)
        deadline = time.monotonic() + 180
        def complete():
            return (len(translated_rooms) == args.rooms if coordinator is not None else
                    any(translated for _original, translated in captions))
        while not complete() and time.monotonic() < deadline:
            QTest.qWait(25)
            if (coordinator is None and session.tile is None or
                    coordinator is not None and not coordinator.is_running):
                break
        if not complete():
            print(json.dumps({'fixture_events': list((coordinator or session).samples)}, ensure_ascii=True), flush=True)
        assert complete(), f'Not every room produced a translated caption: {statuses[-3:]}'
        sessions = list(coordinator.sessions.values()) if coordinator else [session]
        recognition_workers = [item.worker for item in sessions]
        assert all(worker and worker.is_alive for worker in recognition_workers)
        local = coordinator.local if coordinator else session.local
        if coordinator:
            from pipeline_translate_plugin.recognizer_pool import _models
            assert len(_models) == 1 and next(iter(_models.values()))[2] == args.rooms
        processes = [item.audio for item in sessions] + [local.process]
        owned = [int(process.processId()) for process in processes]
        owned_directories = [group.directory for group in [*[item._ffmpeg_group for item in sessions], local.group]
                             if group is not None and group.directory is not None]
        assert len(set(owned)) == args.rooms + 1 and all(owned), 'Expected one FFmpeg per room and one local translator'
        expected_name = Path(sys.executable).name
        helpers = [psutil.Process(pid) for pid in owned]
        assert all(process.name() == expected_name for process in helpers), \
            'Helper processes were not launched under the host image name'
        assert all(process.ppid() == os.getpid() for process in helpers), \
            'Helper processes were not owned by the host process'
        for item in sessions:
            item._memory()
        expected_rss = sum(psutil.Process(pid).memory_info().rss for pid in owned)
        measured = (coordinator.samples if coordinator else session.samples)[-1]['child_rss_bytes']
        assert measured and abs(measured - expected_rss) < 32 * 1024**2, 'Resource total missed an owned process'
        print(json.dumps({'translated_captions': sum(bool(text) for _original, text in captions),
                          'embedded_recognition': True, 'rooms': args.rooms, 'shared_asr_models': 1,
                          'owned_processes': len(owned),
                          'fixture_child_rss_bytes': measured}, ensure_ascii=True))
    finally:
        stopped.set()
        (coordinator or session).stop()
        deadline = time.monotonic() + 5
        while any(worker.is_alive for worker in recognition_workers) and time.monotonic() < deadline:
            QTest.qWait(20)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        app.processEvents()
    assert recognition_workers and all(not worker.is_alive for worker in recognition_workers)
    if coordinator:
        assert not _models, 'Shared recognition weights were retained after the last room stopped'
    assert all(not psutil.pid_exists(pid) for pid in owned), 'An owned process remained after stop'
    assert all(not directory.exists() for directory in owned_directories), \
        'An owned launch directory remained after stop'
    print('PASS: actual local pipeline produced translated captions, used an embedded recognition thread, '
          'and released FFmpeg/local translation processes; '
          'live accuracy and latency were not measured')


if __name__ == '__main__':
    main()
