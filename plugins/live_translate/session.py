"""GUI-thread resource ownership, bounded PCM delivery and stale-result rejection."""
from collections import deque
from dataclasses import replace
import time
import weakref

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .options import audio_arguments
from .protocol import AudioGate, MAX_FRAME
from .embedded_recognition import EmbeddedRecognition
from .host_process import HostProcess
from .local_translation import LocalTranslation
from .audio_diagnostics import AudioDiagnostics, failure_message


class Session(QObject):
    caption = Signal(str, str)
    status = Signal(str)
    metrics = Signal(object)
    running = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.audio = None
        self.local = None
        self._relay = None
        self.tile = None
        self._options = None
        self._api_key = ''
        self._source = None
        self._source_key = None
        self._ffmpeg = None
        self._arguments = None
        self._recognition_is_ready = False
        self._gate = AudioGate()
        self._audio_diagnostics = AudioDiagnostics()
        self._audio_started = 0
        self._ffmpeg_group = None
        self.samples = deque(maxlen=300)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._memory)

    def start(self, options, api_key, source, tile, ffmpeg):
        options.validate()
        relay, arguments = self._build_source(source)
        self.stop()
        self._options = options
        self._api_key = api_key if options.translation_mode == 'api' else ''
        self._source = source
        self._source_key = self._source_signature(source)
        self._relay = relay
        self._arguments = arguments
        self._ffmpeg = ffmpeg
        self.tile = weakref.ref(tile)
        self._gate = AudioGate()
        self.samples.clear()
        self._audio_diagnostics = AudioDiagnostics()
        self._record({'event': 'audio_transport', 'relayed': self._relay is not None})
        self.running.emit(True)
        self._timer.start()
        if options.translation_mode == 'local':
            local = self.local = LocalTranslation(self)
            local.ready.connect(lambda endpoint: self._local_ready(local, endpoint))
            local.failed.connect(lambda message: self._failed(local, message))
            self.status.emit('正在加载本地翻译模型…')
            local.start(options)
        else:
            self._start_recognition(options, api_key if options.translation_mode == 'api' else '', ffmpeg, arguments)

    def _build_source(self, source):
        if source is None:
            raise ValueError('直播源不可用，请重新取流')
        arguments = audio_arguments(source.url, dict(source.headers or {}))
        relay = None
        if source.platform in ('youtube', 'twitch'):
            from ddm.global_danmaku import request_proxy
            from ddm.hls_proxy import HlsProxy
            relay = HlsProxy(source.url, dict(source.headers), request_proxy(source.url))
            arguments = audio_arguments(relay.url, {}, relayed=True)
        return relay, arguments

    @staticmethod
    def _source_signature(source):
        headers = tuple(sorted((str(key), str(value))
                               for key, value in dict(source.headers or {}).items()))
        return (str(source.url), str(source.platform), str(source.channel),
                int(source.quality or 0), headers)

    def switch_source(self, source):
        if self._options is None or self.tile is None:
            return False
        signature = self._source_signature(source)
        if signature == self._source_key:
            return False
        relay, arguments = self._build_source(source)
        old_relay, self._relay = self._relay, relay
        self._source = source
        self._source_key = signature
        self._arguments = arguments
        self._gate = AudioGate()
        self._tail = b''
        self._audio_diagnostics = AudioDiagnostics()
        if old_relay is not None:
            old_relay.stop()
        self._record({'event': 'audio_transport', 'relayed': relay is not None,
                      'switched': True})
        if self._recognition_is_ready:
            self._restart_audio(reset_source=True)
        elif self.worker is not None:
            self.worker.reset_source()
            self.status.emit('直播源已切换，等待识别就绪')
        else:
            self.status.emit('直播源已切换，正在加载本地翻译模型')
        return True

    def _local_ready(self, local, endpoint):
        if self.local is local and self._options is not None:
            self._start_recognition(
                replace(self._options, endpoint=endpoint, model='live-translate'),
                local.api_key, self._ffmpeg, self._arguments)

    def _start_recognition(self, options, api_key, ffmpeg, arguments):
        if self.worker is not None:
            return
        self._recognition_is_ready = False
        worker = self.worker = EmbeddedRecognition(options, api_key, self)
        self._ffmpeg = ffmpeg
        self._arguments = arguments
        self._ffmpeg_group = HostProcess(ffmpeg, include_dlls=True)
        worker.ready.connect(self._recognition_ready)
        worker.original.connect(self._recognition_original)
        worker.caption.connect(self._recognition_caption)
        worker.failed.connect(self._recognition_failed)
        self.status.emit('正在加载本地识别模型…')
        worker.start()

    def _recognition_ready(self, worker):
        if self.worker is not worker:
            return
        self._recognition_is_ready = True
        self.status.emit('正在读取所选直播间音轨；本批不延后画面')
        self._start_audio()

    def _start_audio(self):
        if not self._recognition_is_ready or self.worker is None or self.audio is not None:
            return
        self._audio_started = time.monotonic()
        audio = self.audio = QProcess(self)
        audio.readyReadStandardOutput.connect(lambda: self._read_audio(audio, self.worker))
        audio.readyReadStandardError.connect(lambda: self._read_audio_error(audio))
        audio.errorOccurred.connect(
            lambda _error: self._failed(audio, '无法启动音轨读取，请检查 FFmpeg'))
        audio.finished.connect(lambda code, status: self._audio_finished(audio, code, status))
        self._record({'event': 'audio_start'})
        try:
            program = self._ffmpeg_group.prepare()
        except OSError:
            self._failed(self.worker, '无法准备取流进程')
            return
        audio.setProgram(str(program))
        if self._ffmpeg_group.directory is not None:
            audio.setWorkingDirectory(str(self._ffmpeg_group.directory))
        audio.setArguments(self._arguments)
        audio.start()

    def _restart_audio(self, *, reset_source=False):
        audio, self.audio = self.audio, None
        if audio is not None:
            if audio.state() != QProcess.NotRunning:
                audio.kill()
                audio.waitForFinished(1000)
            audio.deleteLater()
        self._tail = b''
        self._gate = AudioGate()
        self._audio_diagnostics = AudioDiagnostics()
        if reset_source and self.worker is not None:
            self.worker.reset_source()
        self._start_audio()

    def _recognition_original(self, worker, epoch, text):
        if self.worker is worker and worker.source_epoch == epoch:
            self.caption.emit(text, '')

    def _recognition_caption(self, worker, epoch, result):
        if self.worker is not worker or worker.source_epoch != epoch:
            return
        self.caption.emit(result['text'], result.get('translated', ''))
        self._record({key: value for key, value in result.items()
                      if key not in ('text', 'translated', 'warning')})
        self.status.emit(result.get('warning') or '字幕更新中；本批不延后画面')

    def _recognition_failed(self, worker, message):
        if self.worker is worker:
            self._failed(worker, message)

    def _read_audio(self, audio, worker):
        data = bytes(audio.readAllStandardOutput())
        if self.audio is not audio or self.worker is not worker:
            return
        if data and not self._audio_diagnostics.received_bytes:
            self._record({'event': 'audio_first_pcm',
                          'elapsed_ms': round((time.monotonic() - self._audio_started) * 1000)})
        self._audio_diagnostics.received_bytes += len(data)
        # s16le may arrive split between samples. Keep at most one trailing byte.
        data = getattr(self, '_tail', b'') + data
        self._tail = data[-1:] if len(data) % 2 else b''
        data = data[:len(data) - len(self._tail)]
        for offset in range(0, len(data), MAX_FRAME):
            frame = self._gate.frame(data[offset:offset + MAX_FRAME], worker.pending_bytes)
            if frame is not None:
                worker.feed(frame)

    def _read_audio_error(self, audio):
        data = bytes(audio.readAllStandardError())
        if self.audio is audio:
            self._audio_diagnostics.feed_error(data)

    def _audio_finished(self, audio, code, status):
        if self.audio is not audio:
            return
        self._read_audio_error(audio)
        reason = self._audio_diagnostics.reason()
        self._record({'event': 'audio_exit', 'exit_code': code,
                      'crashed': status == QProcess.CrashExit, 'reason': reason,
                      'received_pcm_bytes': self._audio_diagnostics.received_bytes,
                      'elapsed_ms': round((time.monotonic() - self._audio_started) * 1000)})
        self._failed(audio, failure_message(reason, code))

    def _memory(self):
        rss = 0
        try:
            import psutil
            for process in (self.audio, self.local.process if self.local else None):
                if process is not None and process.processId():
                    rss += psutil.Process(process.processId()).memory_info().rss
        except (ImportError, OSError):
            rss = None
        except psutil.Error:
            rss = None
        dropped = self._gate.dropped_bytes
        if self.worker is not None:
            dropped += self.worker.dropped_bytes
        self._record({'event': 'resources', 'child_rss_bytes': rss,
                      'recognition_embedded': self.worker is not None,
                      'recognition_thread_alive': bool(self.worker and self.worker.is_alive),
                      'pending_pcm_bytes': self.worker.pending_bytes if self.worker else 0,
                      'dropped_audio_seconds': round(dropped / 32000, 2)})

    def _record(self, value):
        value = dict(value, monotonic_s=round(time.monotonic(), 3))
        self.samples.append(value)
        self.metrics.emit(value)

    def _failed(self, process, message):
        if process is self.worker or process is self.audio or process is self.local:
            self.stop()
            self.status.emit(message)

    def stop(self):
        self._timer.stop()
        # Invalidate first: finished/error signals from the old processes are stale.
        processes = (self.audio,)
        worker, self.worker = self.worker, None
        local, self.local = self.local, None
        relay, self._relay = self._relay, None
        ffmpeg_group, self._ffmpeg_group = self._ffmpeg_group, None
        self.audio = self.tile = None
        self._ffmpeg = self._arguments = None
        self._options = self._source = self._source_key = None
        self._api_key = ''
        self._recognition_is_ready = False
        self._tail = b''
        if relay is not None:
            relay.stop()
        for process in processes:
            if process is not None:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(1000)
                process.deleteLater()
        if worker is not None:
            if not worker.stop():
                self.status.emit('识别线程仍在退出，稍后释放本地模型')
            else:
                worker.deleteLater()
        if local is not None:
            local.stop()
            local.deleteLater()
        if ffmpeg_group is not None:
            ffmpeg_group.cleanup()
        self._audio_diagnostics = AudioDiagnostics()
        self.running.emit(False)
