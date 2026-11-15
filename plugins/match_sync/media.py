"""FFmpeg frame/PCM capture and source acquisition."""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from ddm import bili, recording
from ddm.auto_quality import AUTO_QUALITY
from .engine import FPS, Sample, SIGNATURE_BITS, np
from .video import av, VideoHistory, PictureReader, StreamInput, reformat_frame, recording_template
from .replay_cache import CACHE_BYTES, CACHE_SECONDS
from .diagnostics import FrameCadence, StageTimings, StreamAcquisition

# Compatibility exports; internal users import the owning module.
from .media_signals import Events
from .chat_clients import PlatformChat, Chat

FRAME_TIMEOUT = 8.0


def fingerprint(jpeg: bytes, crop=(0.0, 0.0, 1.0, 1.0)) -> tuple[int, float]:
    image = jpeg if isinstance(jpeg, QImage) else QImage.fromData(jpeg)
    if image.isNull():
        return 0, 0
    x, y, width, height = crop
    image = image.copy(round(x * image.width()), round(y * image.height()),
                       max(1, round(width * image.width())),
                       max(1, round(height * image.height())))
    image = image.scaled(17, 17, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_Grayscale8)
    pixels = bytes(image.constBits())
    stride = image.bytesPerLine()
    signature = 0
    texture = 0
    for row in range(16):
        for column in range(16):
            pixel = pixels[row * stride + column]
            right = pixels[row * stride + column + 1]
            below = pixels[(row + 1) * stride + column]
            signature = (signature << 1) | (pixel > right)
            signature = (signature << 1) | (pixel > below)
            texture += abs(pixel - right) + abs(pixel - below)
    return signature, texture / SIGNATURE_BITS


def scene_feature(image, crop, size=(16, 16)):
    x, y, width, height = crop
    image = image.copy(round(x * image.width()), round(y * image.height()),
                       max(1, round(width * image.width())), max(1, round(height * image.height())))
    width, height = size
    image = image.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_Grayscale8)
    pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(height, image.bytesPerLine())[:, :width]
    centre, scale = pixels.mean(), max(8, pixels.std())
    return np.clip(np.rint(128 + (pixels.astype(np.float32) - centre) * 48 / scale), 0, 255).astype(np.uint8).tobytes()


def analysis_gray(frame, timings, conversions, **options):
    key = tuple(sorted(options.items()))
    repeated = conversions is not None and key in conversions
    started = time.perf_counter()
    gray = reformat_frame(frame, **options)
    if timings is not None:
        timings.record('analysis_gray_convert', time.perf_counter() - started)
        timings.record('analysis_gray_repeat' if repeated else 'analysis_gray_unique', 0.)
    if conversions is not None:
        conversions[key] = gray
    return gray


def frame_sample(frame, timestamp, crop, size, timings=None, conversions=None):
    width, height = size
    selected = crop != (0, 0, 1, 1)
    gray = (analysis_gray(frame, timings, conversions, format="gray") if selected else
            analysis_gray(frame, timings, conversions, width=min(width, frame.width),
                          height=min(height, frame.height), format="gray"))
    started = time.perf_counter()
    plane = gray.planes[0]
    image = QImage(memoryview(plane), gray.width, gray.height, plane.line_size, QImage.Format_Grayscale8)
    if selected:
        x, y, w, h = crop
        image = image.copy(round(x * gray.width), round(y * gray.height),
                           max(1, round(w * gray.width)), max(1, round(h * gray.height)))
        if image.width() > width or image.height() > height:
            image = image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        crop = (0, 0, 1, 1)
    signature, texture = fingerprint(image, crop)
    sample = Sample(timestamp, signature, texture, scene_feature(image, crop, (128, 48) if selected else (16, 16)))
    if timings is not None:
        timings.record('analysis_features', time.perf_counter() - started)
    return sample


def decode_command(executable: str, url: str, headers: dict, port: int, quality: int = 250,
                   *, hls_retry=False, platform_kind="") -> list[str]:
    inputs = recording.input_args(url, headers)
    if url.lower().startswith(("http://", "https://")):
        # Let the outer retry resolve a fresh address instead of looping on a broken CDN.
        inputs = inputs[:-2] + ["-reconnect", "0", "-rw_timeout",
                               "30000000" if hls_retry else "6000000"] + inputs[-2:]
        if hls_retry:
            inputs = inputs[:-2] + ["-http_persistent", "0", "-http_multiple", "1",
                                   "-seg_max_retry", "3"] + inputs[-2:]
    # Only local fixtures/files need pacing. Throttling real live inputs loses
    # packets when the server delivers a burst (especially Douyu's FLV).
    pacing = [] if url.lower().startswith(("http://", "https://", "rtmp://", "rtmps://")) else ["-readrate", "1"]
    return ([executable, "-nostdin"] + pacing + ["-copyts", "-start_at_zero", "-threads", "2"]
            + inputs
            + ["-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af",
               "aresample=async=1",
               "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
               "-avoid_negative_ts", "make_zero", "-f", "nut", "-flush_packets", "1", "pipe:1"])


class Decoder:
    def __init__(self, room_id: str, seed: dict | None = None, platform=None):
        self.room_id = room_id
        self.seed = seed or {}
        self.platform = platform
        self.fps = 60 if platform is not None and platform.kind == "douyu" else FPS
        self.source_url = ""
        self.source_headers = {}
        self.events = Events()
        self.history = self._history()
        self.playback_timestamp = None
        self.pictures = PictureReader()
        self.preview_frames = None
        self.video_size = (0, 0)
        self.last_frame_received = 0.0
        self.received_cadence = FrameCadence()
        self.timings = StageTimings()
        self.acquisition = StreamAcquisition()
        self.crop = (0.0, 0.0, 1.0, 1.0)
        self.crop_generation = 0
        self.analysis_size = (640, 360)
        self.region_frames = deque(maxlen=600)
        self.cancelled = threading.Event()
        self.process = None
        self.thread = None
        self.lock = threading.RLock()

    @staticmethod
    def _history():
        return VideoHistory(seconds=CACHE_SECONDS, byte_limit=CACHE_BYTES, disk_cache=True)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run,
                                       name=f"match-sync-video-{self.room_id}", daemon=True)
        self.thread.start()

    def set_crop(self, crop) -> None:
        with self.lock:
            self.crop = tuple(crop)
            self.crop_generation += 1
            with self.history.lock:
                self.history.samples.clear()
                self.history.details.clear()

    def stop(self) -> None:
        self.cancelled.set()
        self.pictures.stop()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def picture_at(self, timestamp, exact=False):
        if not exact:
            self.playback_timestamp = timestamp
        history = self.history.segment_at(timestamp) or self.history
        frame = history.frame_at(timestamp)
        return self.pictures.get(history, frame, exact=exact) if frame is not None and not self.cancelled.is_set() else None

    def diagnostics(self):
        with self.history.lock:
            bounds = self.history.bounds()
            stamps = [item[0] for item in self.history.frames]
            gaps = sorted((b - a for a, b in zip(stamps, stamps[1:])), reverse=True)
            recent = [stamp for stamp in stamps if stamps and stamp >= stamps[-1] - 3]
            return {"size": self.video_size, "fps": self.fps,
                    "crop_quality_policy": self.seed.get('crop_quality_policy', 'source'),
                    "requested_quality": self.seed.get('quality'),
                    "codec": self.history.codec[0] if self.history.codec else "",
                    "received_packets": self.history.sequence,
                    "received_frames": self.history.received_frames,
                    "largest_frame_gaps": gaps[:3],
                    "recent_received_fps": ((len(recent) - 1) / (recent[-1] - recent[0])) if len(recent) > 1 else 0,
                    "packet_bytes": self.history.byte_size,
                    "buffer_seconds": bounds[1] - bounds[0] if bounds else 0,
                    "picture_decode_ms": self.pictures.decode_ms,
                    "rgb_frames": self.pictures.decoded}

    def _resolve(self, attempt: int) -> tuple[str, dict]:
        if self.cancelled.is_set():
            raise bili.Cancelled()
        self.acquisition.quality(requested=self.seed.get('quality', 10000 if self.platform is not None else 250))
        options = None
        if self.seed.get('highest_quality') and not self.seed.get('balanced_quality'):
            self.acquisition.phase('quality_options')
            try:
                options = (self.platform.room_quality_options(self.room_id) if self.platform is not None
                           else bili.room_quality_options(self.room_id))
            except bili.Cancelled:
                raise
            except Exception:
                options = []
            self.acquisition.mark('quality_options_ready')
            self.seed.pop('url', None)
        if self.seed.get("balanced_quality"):
            self.acquisition.phase('quality_options')
            # The host lists tiers from highest to lowest; IDs are platform-specific.
            options = (self.platform.room_quality_options(self.room_id) if self.platform is not None
                       else bili.room_quality_options(self.room_id))
            self.acquisition.mark('quality_options_ready')
            if self.cancelled.is_set():
                raise bili.Cancelled()
            tiers = [item for item in options if int(item['qn']) != AUTO_QUALITY]
            # Prefer 720p for secondary floating crops; keep source quality otherwise.
            selected = (next((item for item in tiers if '720' in str(item.get('desc', ''))), tiers[len(tiers) // 2])
                        if tiers else {'qn': self.seed.get('quality', 10000)})
            self.seed["quality"] = int(selected['qn'])
            self.acquisition.quality(requested=self.seed['quality'])
            self.seed.pop("url", None)
            self.events.information.emit({"requested_quality": self.seed["quality"], "quality_options": options})
        # Douyu signed addresses may allow only one consumer; do not reuse the wall's URL.
        reuse_seed = self.platform is None or self.platform.kind != "douyu"
        if attempt == 0 and self.seed.get("url") and reuse_seed:
            self.acquisition.mark('seed_address')
            self.source_url = self.seed["url"]
            self.source_headers = dict(self.seed.get("headers") or {})
            self.events.information.emit({"uname": self.seed.get("uname") or "未命名主播",
                                          "title": self.seed.get("title") or ""})
            return self.seed["url"], dict(self.seed.get("headers") or {})
        if self.platform is not None:
            self.acquisition.phase('room_info')
            info = self.platform.room_info(self.room_id)
            self.acquisition.mark('room_info_ready')
            if self.cancelled.is_set():
                raise bili.Cancelled()
            if info:
                self.events.information.emit(info.as_dict())
            self.acquisition.phase('play_url')
            result = self._available_url(lambda quality: self.platform.play_url(self.room_id, quality), options)
            self.acquisition.mark('play_url_ready')
            if self.cancelled.is_set():
                raise bili.Cancelled()
            url, quality, _channel = result[:3]
            self.acquisition.quality(actual=quality)
            headers = dict(result[3]) if len(result) > 3 else {}
            self.acquisition.phase('quality_metadata')
            self.events.information.emit({"actual_quality": quality,
                "quality_options": options if options is not None else self.platform.room_quality_options(self.room_id)})
            self.acquisition.mark('quality_metadata_ready')
            self.source_url, self.source_headers = url, headers
            return url, headers
        self.acquisition.phase('room_info')
        info = bili.room_info(self.room_id)
        self.acquisition.mark('room_info_ready')
        if self.cancelled.is_set():
            raise bili.Cancelled()
        if info:
            self.events.information.emit(info)
        self.acquisition.phase('play_url')
        url, quality, _profile, headers = self._available_url(lambda quality: bili.play_url(
            self.room_id, quality, cancelled=self.cancelled.is_set, source_offset=attempt), options)
        self.acquisition.mark('play_url_ready')
        self.acquisition.quality(actual=quality)
        self.events.information.emit({"actual_quality": quality})
        self.source_url, self.source_headers = url, headers
        return url, headers

    def _available_url(self, fetch, options):
        tiers = options if self.seed.get('highest_quality') and not self.seed.get('balanced_quality') else []
        qualities = list(dict.fromkeys(int(item['qn']) for item in (tiers or [])
                                      if int(item['qn']) != AUTO_QUALITY))
        if not qualities:
            qualities = [self.seed.get('quality', 10000 if self.platform is not None else 250)]
        for index, quality in enumerate(qualities):
            if self.cancelled.is_set():
                raise bili.Cancelled()
            self.seed['quality'] = quality
            self.acquisition.quality(requested=quality)
            try:
                result = fetch(quality)
            except bili.Cancelled:
                raise
            except Exception:
                if index == len(qualities) - 1:
                    raise
                self.acquisition.mark('quality_fallback')
                self.events.state.emit('当前档位取流失败，正在尝试下一档画质…')
                continue
            self.events.information.emit({'requested_quality': quality})
            return result

    def _run(self) -> None:
        if av is None:
            self.events.state.emit("缺少二路视频解码组件 PyAV；请安装新版完整程序或更新 requirements.txt 中的依赖")
            return
        if np is None:
            self.events.state.emit("缺少二路图像比较组件 NumPy；请更新 requirements.txt 中的依赖")
            return
        executable = recording.ffmpeg_path()
        if not executable:
            self.events.state.emit("找不到 FFmpeg：请使用完整发布包，或将 FFmpeg 放入 PATH")
            return
        attempt = 0
        while not self.cancelled.is_set():
            self.acquisition.begin(attempt)
            outcome = 'cancelled'
            self.events.state.emit("正在取流…" if attempt == 0 else "正在重新连接…")
            try:
                url, headers = self._resolve(attempt)
                self.acquisition.mark('resolved')
                if self.cancelled.is_set():
                    return
                self.acquisition.phase('capture_setup')
                if attempt:
                    previous = (self.history.segment_at(self.playback_timestamp)
                                if self.playback_timestamp is not None else None) or self.history
                    previous.previous = None
                    self.history = self._history()
                    self.history.previous = previous
                    self.events.reset.emit()
                outcome = self._capture(executable, url, headers) or 'eof'
            except bili.Cancelled:
                return
            except Exception as error:
                outcome = 'cancelled' if self.cancelled.is_set() else 'error'
                self.acquisition.error(error)
                if not self.cancelled.is_set():
                    self.events.state.emit("连接或解码失败；稍后重试，可停止后重新开始")
            finally:
                self.acquisition.finish(outcome)
            if self.cancelled.wait(min(5, .5 + attempt * .5)):
                break
            attempt += 1

    def _capture(self, executable: str, url: str, headers: dict) -> str:
        process = None
        error_reader = None
        hls_proxy = None
        finished = threading.Event()
        timed_out = threading.Event()
        last_frame = [time.monotonic()]

        def watch_frames():
            while not finished.wait(.2):
                if self.cancelled.is_set() or process.poll() is not None:
                    return
                refresh = getattr(hls_proxy, "refresh_required", None)
                if (refresh is not None and refresh.is_set() or
                        time.monotonic() - last_frame[0] > FRAME_TIMEOUT):
                    timed_out.set()
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    return

        watchdog = threading.Thread(target=watch_frames,
                                    name=f"match-sync-watchdog-{self.room_id}", daemon=True)
        try:
            if self.platform is not None and self.platform.kind in ("twitch", "youtube"):
                from ddm.global_danmaku import request_proxy
                from ddm.hls_proxy import HlsProxy
                hls_proxy = HlsProxy(url, headers, request_proxy(url))
                url, headers = hls_proxy.url, {}
            self.acquisition.phase('ffmpeg_start')
            process = subprocess.Popen(
                decode_command(executable, url, headers, 0, self.seed.get("quality", 250),
                               hls_retry=hls_proxy is not None,
                               platform_kind=self.platform.kind if self.platform is not None else ""),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if self.acquisition.enabled else subprocess.DEVNULL, bufsize=0,
                creationflags=recording._FFMPEG_FLAGS & ~0x00004000)
            self.acquisition.mark('ffmpeg_started')
            if self.acquisition.enabled:
                error_reader = threading.Thread(target=self.acquisition.read_stderr,
                    args=(process.stderr,), name='match-sync-input-errors', daemon=True)
                error_reader.start()
            recording._adopt_process(process)
            # Playback retains the host's orphan-process job, at normal priority.
            kernel = getattr(recording, "_kernel32", None)
            if kernel is not None:
                handle = kernel.OpenProcess(recording._PROCESS_SET_INFORMATION, False, process.pid)
                if handle:
                    try:
                        kernel.SetPriorityClass(handle, 0x00000020)
                    finally:
                        kernel.CloseHandle(handle)
            with self.lock:
                self.process = process
            if self.cancelled.is_set():
                process.terminate()
                return 'cancelled'
            watchdog.start()
            next_sample = 0.0
            next_detail = 0.0
            next_region = next_region_large = 0.0
            with self.lock:
                self.region_frames.clear()
            self.acquisition.phase('input_open')
            with av.open(StreamInput(process.stdout, lambda: self.acquisition.mark('first_byte')), "r", format="nut",
                         options={"probesize": "65536", "analyzeduration": "100000"}) as container, recording_template(self.history, container):
                self.acquisition.mark('input_opened')
                self.acquisition.phase('receive')
                stream = container.streams.video[0]
                stream.codec_context.thread_count = (4 if stream.width * stream.height >= 3840 * 2160 else 2)
                stream.codec_context.thread_type = "AUTO"
                self.history.codec = (stream.codec_context.name, stream.codec_context.extradata)
                rate = stream.average_rate or stream.base_rate or stream.codec_context.framerate
                if rate:
                    self.fps = float(rate)
                reading = time.perf_counter()
                for packet in container.demux():
                    if packet.size:
                        self.acquisition.mark('first_packet')
                    self.timings.record('input_wait', time.perf_counter() - reading)
                    if self.cancelled.is_set():
                        break
                    if packet.stream.type == "audio":
                        started = time.perf_counter()
                        frames = packet.decode()
                        self.timings.record('capture_audio_decode', time.perf_counter() - started, items=len(frames))
                        for frame in frames:
                            if frame.pts is None:
                                continue
                            pts = float(frame.pts * frame.time_base)
                            self.acquisition.mark('first_audio_frame')
                            if self.history.origin is None:
                                # Ignore an old cached video keyframe when anchoring live time.
                                self.history.set_origin(time.monotonic() - pts)
                                self.acquisition.mark('clock_anchored')
                                self.events.state.emit("取流已连接")
                            started = time.perf_counter()
                            pcm = bytes(frame.planes[0])[:frame.samples * 4]
                            self.history.audio.append_at(round(pts * 48000), pcm)
                            self.timings.record('capture_audio_cache', time.perf_counter() - started, payload_bytes=len(pcm))
                        reading = time.perf_counter()
                        continue
                    if packet.size:
                        started = time.perf_counter()
                        self.history.append_packet(packet)
                        self.timings.record('capture_packet_cache', time.perf_counter() - started, payload_bytes=packet.size)
                    started = time.perf_counter()
                    frames = packet.decode()
                    self.timings.record('capture_video_decode', time.perf_counter() - started, items=len(frames))
                    for frame in frames:
                        if frame.pts is None:
                            continue
                        pts = float(frame.pts * frame.time_base)
                        if abs(pts) > 1e9:
                            continue  # An invalid/missing source timestamp cannot establish time.
                        self.acquisition.mark('first_video_frame')
                        timestamp = (self.history.origin or 0) + pts
                        self.received_cadence.record(time.perf_counter(), timestamp)
                        self.video_size = (frame.width, frame.height)
                        self.history.video_size = self.video_size
                        conversions = {}
                        if pts + .0001 >= next_region:
                            next_region = pts + .1
                            large = pts + .0001 >= next_region_large
                            if large:
                                next_region_large = pts + 1
                            gray = analysis_gray(frame, self.timings, conversions, width=320 if large else 160,
                                                 height=180 if large else 90, format='gray')
                            started = time.perf_counter()
                            image = QImage(memoryview(gray.planes[0]), gray.width, gray.height,
                                           gray.planes[0].line_size, QImage.Format_Grayscale8)
                            full = image.copy() if large else None
                            small = image.scaled(160, 90, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) if large else image.copy()
                            self.timings.record('analysis_region_images', time.perf_counter() - started)
                            with self.lock:
                                self.region_frames.append((timestamp, small, full))
                                while self.region_frames and timestamp - self.region_frames[0][0] > 60:
                                    self.region_frames.popleft()
                        sample = None
                        if pts + .0001 >= next_detail:
                            while next_detail <= pts + .0001:
                                next_detail += .05
                            with self.lock:
                                crop = self.crop
                                crop_generation = self.crop_generation
                                width, height = self.analysis_size
                            if crop != (0, 0, 1, 1):
                                width, height = 640, 360
                            detail = frame_sample(frame, timestamp, crop, (width, height), self.timings, conversions)
                            with self.lock:
                                if crop_generation == self.crop_generation:
                                    with self.history.lock:
                                        self.history.details.append(detail)
                                        while self.history.details and timestamp - self.history.details[0].time > self.history.analysis_seconds:
                                            self.history.details.popleft()
                                    if pts + .0001 >= next_sample:
                                        next_sample = pts + .5
                                        sample = detail
                                self.history.append(timestamp, pts, sample)
                        else:
                            self.history.append(timestamp, pts, sample)
                        last_frame[0] = time.monotonic()
                        previews = self.preview_frames
                        if previews is not None:
                            started = time.perf_counter()
                            previews.submit(self, self.history, frame, pts)
                            self.timings.record('preview_submit', time.perf_counter() - started)
                        self.last_frame_received = last_frame[0]
                        self.acquisition.mark('first_cached_video')
                    reading = time.perf_counter()
            if not self.cancelled.is_set():
                refresh = getattr(hls_proxy, "refresh_required", None)
                self.events.state.emit("播放地址已失效，正在重新取流" if refresh is not None and refresh.is_set()
                                       else "画面接收超时，正在重新取流" if timed_out.is_set()
                                       else "直播流已结束或中断，正在重连")
            refresh = getattr(hls_proxy, "refresh_required", None)
            return ('cancelled' if self.cancelled.is_set() else
                    'url_expired' if refresh is not None and refresh.is_set() else
                    'frame_timeout' if timed_out.is_set() else 'eof')
        except Exception:
            if timed_out.is_set() and not self.cancelled.is_set():
                self.events.state.emit("画面接收超时，正在重新取流")
                return 'frame_timeout'
            elif not self.cancelled.is_set():
                raise
            return 'cancelled'
        finally:
            finished.set()
            if watchdog.is_alive():
                watchdog.join(1)
            with self.lock:
                self.process = None
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()
                if error_reader is not None:
                    error_reader.join(1)
                    process.stderr.close()
                self.acquisition.exited(process.returncode)
            if hls_proxy is not None:
                hls_proxy.stop()
