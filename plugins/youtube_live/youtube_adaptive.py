"""YouTube live sq readers and copy-only VP9/AAC muxing; each playback owns its state."""
from collections import deque
import io
import os
import queue
import subprocess
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from streamlink import Streamlink
from streamlink.stream.ffmpegmux import FFMPEGMuxer

from .youtube_tools import YouTubeMediaError


def media_url(value):
    parts = urlsplit(str(value or ''))
    if (parts.scheme != 'https' or not (parts.hostname or '').endswith('.googlevideo.com') or
            parts.username or parts.password or parts.port not in (None, 443) or
            parts.path != '/videoplayback' or parts.fragment or any(c in value for c in '\r\n\0')):
        raise YouTubeMediaError('YouTube 返回无效的媒体地址')
    return value


def sequence_url(url, number):
    parts = urlsplit(media_url(url))
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'sq']
    return urlunsplit(parts._replace(query=urlencode([*query, ('sq', str(number))])))


class Track(io.RawIOBase):
    def __init__(self, url, number, duration, proxy, stopped, metrics, *, session=None):
        self.url, self.number, self.duration = media_url(url), number, duration
        self._proxy = {'https': proxy, 'http': proxy} if proxy else {}
        self._stop, self._metrics = stopped, metrics
        self._session = session or requests.Session()
        self._session.trust_env = False
        self._buffer = b''
        self._wait_until = 0

    def _segment(self, number, *, probe=False):
        for attempt in range(3):
            if self._stop.is_set():
                raise YouTubeMediaError('YouTube 媒体读取已停止')
            started = time.monotonic()
            try:
                with self._session.get(sequence_url(self.url, number), proxies=self._proxy,
                        timeout=(4, 12), stream=True, allow_redirects=False) as response:
                    if response.status_code in (401, 403, 410):
                        raise YouTubeMediaError('YouTube 高画质地址已失效，请刷新直播')
                    head = response.headers.get('X-Head-Seqnum', '')
                    if not head.isdigit():
                        raise YouTubeMediaError('YouTube 未返回有效的直播分片位置')
                    head = int(head)
                    if probe and response.status_code == 404:
                        return head, b''
                    if response.status_code == 404 and number > head and attempt < 2:
                        if self._stop.wait(self.duration):
                            raise YouTubeMediaError('YouTube 媒体读取已停止')
                        continue
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        if self._stop.is_set():
                            raise YouTubeMediaError('YouTube 媒体读取已停止')
                        size += len(chunk)
                        if size > 32 * 1024 * 1024:
                            raise YouTubeMediaError('YouTube 直播分片超过大小限制')
                        chunks.append(chunk)
                    data = b''.join(chunks)
                    if not data:
                        raise requests.ConnectionError('empty media segment')
                    if not probe:
                        self._metrics.append((time.monotonic(), len(data) * 8 / max(.001, time.monotonic() - started)))
                    return head, data
            except requests.RequestException:
                if attempt == 2 or self._stop.wait(.5 * (attempt + 1)):
                    raise YouTubeMediaError('YouTube 直播分片读取失败，请重试') from None

    def read(self, size=-1):
        if self.closed or self._stop.is_set():
            return b''
        if not self._buffer:
            if self._stop.wait(max(0, self._wait_until - time.monotonic())):
                return b''
            head, self._buffer = self._segment(self.number)
            self.number += 1
            self._wait_until = time.monotonic() + max(0, self.number - max(0, head - 2)) * self.duration
        count = len(self._buffer) if size < 0 else min(size, len(self._buffer))
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def close(self):
        self._stop.set()
        self._session.close()
        super().close()


class Muxer(FFMPEGMuxer):
    def open(self):
        # Bound probing of both inputs; the audio pipe must not wait for megabytes.
        command = []
        for argument in self._cmd:
            if argument == '-i':
                command.extend(['-analyzeduration', '1000000', '-probesize', '1048576'])
            command.append(argument)
        for thread in self.pipe_threads:
            thread.daemon = True
            thread.start()
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        return self

    def close(self):
        if self.closed:
            return
        super().close()
        if self.process is not None:
            self.process.wait(timeout=5)
            self.process.stdin.close()


class AdaptiveReader:
    def __init__(self, video, audio, duration, proxy, ffmpeg, cancelled=lambda: False):
        self._stop = threading.Event()
        self._metrics = deque(maxlen=12)
        self._close_lock = threading.Lock()
        self._closed = False
        self._prefix = b''
        self._session = Streamlink({'ffmpeg-ffmpeg': ffmpeg, 'stream-timeout': 12})
        self._video = Track(video, 0, duration, proxy, self._stop, self._metrics)
        self._audio = Track(audio, 0, duration, proxy, self._stop, self._metrics)
        self._mux = None
        try:
            if not ffmpeg:
                raise YouTubeMediaError('未找到 FFmpeg，请检查程序安装是否完整')
            video_head, _ = self._video._segment(0, probe=True)
            audio_head, _ = self._audio._segment(0, probe=True)
            self._video.number = self._audio.number = max(0, min(video_head, audio_head) - 2)
            self._mux = Muxer(self._session, self._video, self._audio,
                              maps=['0:v:0', '1:a:0'], copyts=True, start_at_zero=True).open()
            ready = queue.Queue(1)
            def first():
                try:
                    ready.put(self._mux.process.stdout.read1(65536))
                except OSError:
                    ready.put(b'')
            threading.Thread(target=first, name='youtube-first-media', daemon=True).start()
            deadline = time.monotonic() + 40
            while True:
                if cancelled() or time.monotonic() > deadline:
                    raise YouTubeMediaError('YouTube 高画质起播超时或已取消')
                try:
                    self._prefix = ready.get(timeout=.1)
                    break
                except queue.Empty:
                    continue
            if not self._prefix:
                raise YouTubeMediaError('YouTube 高画质音视频复用失败')
        except Exception:
            self.close()
            raise

    def read(self, size=65536):
        if self._prefix:
            data, self._prefix = self._prefix[:size], self._prefix[size:]
            return data
        return self._mux.process.stdout.read1(size) if not self._stop.is_set() else b''

    def network_speed(self):
        values = sorted(rate for at, rate in tuple(self._metrics) if time.monotonic() - at < 30)
        return (values[(len(values) - 1) // 4], len(values)) if values else (0, 0)

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._stop.set()
        if self._mux is not None:
            self._mux.close()
        self._video.close()
        self._audio.close()
        self._session.http.close()
