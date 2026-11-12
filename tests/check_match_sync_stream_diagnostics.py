"""Startup receipts and classified input failures; no accounts or live media."""
import argparse
from contextlib import nullcontext
from importlib import import_module
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch


def check(app, host, diagnostic, media, video):
    process_control = import_module(media.recording._ensure_job.__module__)
    secret = 'DO_NOT_LOG_PRIVATE_VALUE'
    address = 'https://private.invalid/live?token=' + secret
    trace = diagnostic.StreamAcquisition()
    with patch.object(diagnostic.time, 'perf_counter', return_value=10.):
        trace.begin(0)
    trace.quality(requested=250, actual=1004000)
    with patch.object(diagnostic.time, 'perf_counter', return_value=10.125):
        trace.mark('resolved')
    trace.mark('resolved')
    assert trace.snapshot()['milestones_ms']['resolved'] == 125.
    trace.phase('input_open')
    trace.error(RuntimeError('HTTP error 403 Forbidden ' + address + ' Cookie: ' + secret))
    trace.exited(1)
    trace.finish('error')
    first = trace.snapshot()
    assert first['requested_quality'] == 250 and first['actual_quality'] == 1004000
    assert first['error_phase'] == 'input_open' and first['ffmpeg_exit_code'] == 1
    assert first['error_codes'] == ['http_403']
    trace.begin(1)
    assert trace.snapshot()['previous_attempt']['error_codes'] == ['http_403']
    assert trace.snapshot()['previous_attempt']['actual_quality'] == 1004000
    assert 'requested_quality' not in trace.snapshot() and 'actual_quality' not in trace.snapshot()
    assert not trace.snapshot()['milestones_ms']
    with trace.lock:
        assert trace.snapshot() == {'busy': True}, 'GUI-independent snapshots must never wait on the writer'
    for status in range(400, 430):
        trace.stderr('HTTP/1.1 ' + str(status) + ' ' + address)
    assert len(trace.snapshot()['error_codes']) == 8
    for message, code in (
            ('Connection timed out', 'timeout'), ('Failed to resolve hostname', 'dns'),
            ('Connection reset by peer', 'connection'), ('TLS certificate verify failed', 'tls'),
            ('Invalid data found when processing input', 'invalid_data'), ('Unknown decoder h264', 'decoder'),
            ("Stream map '0:a:0' matches no streams", 'missing_audio'),
            ('Permission denied', 'permission'), ('Cannot allocate memory', 'memory')):
        assert code in diagnostic.stream_error_codes(message)
    assert diagnostic.stream_error_codes(address + ' Cookie: ' + secret) == []
    assert diagnostic.stream_error_codes('Failed to resolve https://ssl.invalid/private?token=' + secret) == ['dns']
    assert secret not in json.dumps(first) + json.dumps(trace.snapshot())

    # Empty reads do not count as first input; the callback runs exactly once.
    calls = []
    stream = video.StreamInput(io.BytesIO(b'abcd'), lambda: calls.append(True))
    assert stream.read(0) == b'' and not calls
    assert stream.read(2) == b'ab' and stream.read(2) == b'cd'
    assert stream.read(2) == b'' and calls == [True]

    decoder = media.Decoder('fixture', {'balanced_quality': True})
    decoder.events = SimpleNamespace(information=Mock(), state=Mock(), reset=Mock())
    entered, release = threading.Event(), threading.Event()
    def play_url(*_args):
        entered.set()
        assert release.wait(3)
        return address, 20000, 0, {'Cookie': secret}
    platform = SimpleNamespace(kind='douyu', room_info=Mock(return_value=None), play_url=Mock(side_effect=play_url),
        room_quality_options=Mock(return_value=[{'qn': 20000, 'desc': '2K'}]))
    decoder.platform = platform
    decoder.acquisition.begin(0)
    resolved = []
    worker = threading.Thread(target=lambda: resolved.append(decoder._resolve(0)))
    try:
        worker.start()
        assert entered.wait(2)
        receipt = decoder.acquisition.snapshot()
        assert receipt['phase'] == 'play_url' and 'room_info_ready' in receipt['milestones_ms']
        assert receipt['requested_quality'] == 20000 and 'actual_quality' not in receipt
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and resolved == [(address, {'Cookie': secret})]
    assert platform.play_url.call_count == 1 and platform.room_quality_options.call_count == 2
    assert 'play_url_ready' in decoder.acquisition.snapshot()['milestones_ms']
    assert decoder.acquisition.snapshot()['actual_quality'] == 20000
    assert decoder.acquisition.snapshot()['phase'] == 'quality_metadata'
    assert 'private.invalid' not in json.dumps(decoder.acquisition.snapshot())
    # A retry after failure to get an address must preserve the failing phase.
    def resolve_failure(_attempt):
        decoder.acquisition.phase('play_url')
        raise RuntimeError('HTTP Error 403: ' + address)
    cancelled = SimpleNamespace(is_set=lambda: False, wait=lambda _seconds: True)
    with patch.object(decoder, '_resolve', side_effect=resolve_failure), \
            patch.object(decoder, '_capture') as capture, patch.object(decoder, 'cancelled', cancelled), \
            patch.object(media.recording, 'ffmpeg_path', return_value='ffmpeg'):
        decoder._run()
    capture.assert_not_called()
    failed = decoder.acquisition.snapshot()
    assert failed['outcome'] == 'error' and failed['error_phase'] == 'play_url'
    assert failed['error_codes'] == ['http_403'] and 'resolved' not in failed['milestones_ms']

    # Descriptor packets exercise the real capture routing without a media codec.
    decoder.seed = {}
    decoder.platform = None
    decoder.acquisition.begin(0)
    history = SimpleNamespace(lock=threading.RLock(), origin=None, append_packet=Mock(), append=Mock(),
                              audio=SimpleNamespace(append_at=Mock()))
    history.set_origin = lambda value: setattr(history, 'origin', value)
    decoder.history = history
    audio = SimpleNamespace(pts=0, time_base=1., planes=[b'\0' * 4], samples=1)
    frame = SimpleNamespace(pts=-.01, time_base=1., width=2560, height=1440)
    packets = [SimpleNamespace(size=1, stream=SimpleNamespace(type=kind), decode=lambda f=f: [f])
               for kind, f in (('audio', audio), ('video', frame))]
    context = SimpleNamespace(name='h264', extradata=b'')
    container = SimpleNamespace(streams=SimpleNamespace(video=[SimpleNamespace(codec_context=context,
        width=2560, height=1440, average_rate=60)]), demux=lambda: iter(packets))
    process = SimpleNamespace(pid=1, stdout=io.BytesIO(b'header'),
        stderr=io.BytesIO(('HTTP error 403 Forbidden ' + address).encode()),
        poll=lambda: 0, wait=Mock(return_value=0), returncode=0, terminate=Mock())
    def open_input(source, *_args, **_kwargs):
        assert source.read(1) == b'h'
        return nullcontext(container)
    decoder.preview_frames = SimpleNamespace(submit=Mock())
    with patch.object(media.subprocess, 'Popen', return_value=process) as launch, \
            patch.object(media.recording, '_adopt_process'), patch.object(process_control, '_kernel32', None), \
            patch.object(media, 'av', SimpleNamespace(open=open_input)), \
            patch.object(media, 'recording_template', return_value=nullcontext()):
        assert decoder._capture('ffmpeg', address, {'Cookie': secret}) == 'eof'
    decoder.preview_frames.submit.assert_called_once_with(decoder, history, frame, -.01)
    decoder.preview_frames = None
    assert launch.call_args.kwargs['stderr'] == subprocess.PIPE
    milestones = decoder.acquisition.snapshot()['milestones_ms']
    assert {'ffmpeg_started', 'first_byte', 'input_opened', 'first_packet', 'first_audio_frame',
            'clock_anchored', 'first_video_frame', 'first_cached_video', 'ffmpeg_exited'} <= milestones.keys()
    assert process.stdout.closed and process.stderr.closed
    assert decoder.process is None
    assert 'http_403' in decoder.acquisition.snapshot()['error_codes']
    assert secret not in json.dumps(decoder.acquisition.snapshot())
    capture_trace = decoder.acquisition
    decoder.acquisition = diagnostic.StreamAcquisition()
    failed_process = SimpleNamespace(**dict(vars(process), stdout=io.BytesIO(),
        stderr=io.BytesIO(b'Invalid data found when processing input'), returncode=1))
    with patch.object(decoder, '_resolve', return_value=(address, {})), \
            patch.object(decoder, 'cancelled', cancelled), \
            patch.object(media.recording, 'ffmpeg_path', return_value='ffmpeg'), \
            patch.object(media.subprocess, 'Popen', return_value=failed_process), \
            patch.object(media.recording, '_adopt_process'), patch.object(process_control, '_kernel32', None), \
            patch.object(media, 'av', SimpleNamespace(open=Mock(side_effect=RuntimeError('Invalid data found')))):
        decoder._run()
    failed = decoder.acquisition.snapshot()
    assert failed['outcome'] == 'error' and failed['error_phase'] == 'input_open'
    assert failed['ffmpeg_exit_code'] == 1 and 'invalid_data' in failed['error_codes']
    assert failed_process.stdout.closed and failed_process.stderr.closed

    # A real pipe must be drained beyond its capacity; never persist its raw text.
    pipe_trace = diagnostic.StreamAcquisition()
    pipe_trace.begin(0)
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    child = subprocess.Popen([str(python), '-c',
        "import sys; sys.stderr.write('DO_NOT_LOG_PRIVATE_VALUE' * 10000 + '\\nHTTP error 503\\n')"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    reader = threading.Thread(target=pipe_trace.read_stderr, args=(child.stderr,), daemon=True)
    reader.start()
    try:
        assert child.wait(timeout=5) == 0
        reader.join(2)
        assert not reader.is_alive() and child.stderr.closed
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)
    assert pipe_trace.snapshot()['error_codes'] == ['http_503']
    assert secret not in json.dumps(pipe_trace.snapshot())
    with patch.dict(os.environ, DDM_MATCH_DIAGNOSTICS='0'):
        disabled = diagnostic.StreamAcquisition()
        disabled.begin(0)
        disabled.quality(requested=250, actual=1004000)
        disabled.phase('receive')
        disabled.error(RuntimeError(secret))
        disabled.mark('first_byte')
        disabled.exited(1)
        disabled.finish('error')
        assert disabled.snapshot() == {}
        decoder.acquisition = disabled
        quiet_process = SimpleNamespace(**dict(vars(process), stdout=io.BytesIO(b'header'), stderr=None))
        with patch.object(media.subprocess, 'Popen', return_value=quiet_process) as launch, \
                patch.object(media.recording, '_adopt_process'), patch.object(process_control, '_kernel32', None), \
                patch.object(media, 'av', SimpleNamespace(open=open_input)), \
                patch.object(media, 'recording_template', return_value=nullcontext()):
            assert decoder._capture('ffmpeg', address, {}) == 'eof'
        assert launch.call_args.kwargs['stderr'] == subprocess.DEVNULL
        assert quiet_process.stdout.closed and disabled.snapshot() == {}
    decoder.acquisition = capture_trace

    probe = diagnostic.PlaybackProbe(host, Path('unused-stream-diagnostics.jsonl'))
    probe.pipeline_sources = ((0, decoder),)
    try:
        state = probe.snapshot()
        assert state['source_pipelines'][0]['acquisition']['milestones_ms']['first_video_frame'] >= 0
        assert secret not in json.dumps(state) and 'private.invalid' not in json.dumps(state)
    finally:
        probe.pipeline_sources = ()
        probe.deleteLater()
        app.processEvents()
    return dict(passed=True, startup_milestones=True, resolve_phase=True, retry_failure=True,
                bounded_errors=True, no_raw_secrets=True, pipe_drained=True, disabled=True, media_generated=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    host = QWidget()
    try:
        package = 'plugins.match_sync.'
        print(check(app, host, import_module(package + 'diagnostics'),
                    import_module(package + 'media'), import_module(package + 'video')))
    finally:
        host.close()
        app.processEvents()
        app.shutdown()


if __name__ == '__main__':
    main()
