"""Independent YouTube plugin playback; legacy global_live retains its HLS implementation."""
import re
import threading
import time
import zlib

from ddm.auto_quality import AUTO_QUALITY
from ddm.global_danmaku import request_proxy
from .youtube_platform import YouTubePlatform
from .youtube_adaptive import AdaptiveReader, media_url
from .youtube_runtime import ensure_runtime
from .youtube_tools import extract_live, YouTubeMediaError


def quality_formats(info):
    formats = []
    for item in info.get('formats', []):
        if not isinstance(item, dict):
            continue
        try:
            media_url(item.get('url'))
        except (ValueError, TypeError, YouTubeMediaError):
            continue
        if item.get('protocol') in ('https', 'http_dash_segments', 'http_dash_segments_generator'):
            formats.append(item)
    audio = sorted((f for f in formats if f.get('vcodec') == 'none'
                    and re.match(r'^(mp4a|aac)', str(f.get('acodec')))),
                   key=lambda f: (f.get('format_id') == '140', f.get('tbr') or 0), reverse=True)
    if not audio:
        return []
    options, seen = [], set()
    videos = [f for f in formats if f.get('acodec') == 'none'
              and re.match(r'^(vp0?9|avc1|avc3)(\.|$)', str(f.get('vcodec')))
              and isinstance(f.get('height'), (int, float)) and f['height'] > 0]
    videos.sort(key=lambda f: (f['height'], f.get('fps') or 0,
        str(f.get('vcodec')).startswith('avc' if f['height'] <= 1080 else 'vp'),
        f.get('tbr') or 0), reverse=True)
    for video in videos:
        height, fps = int(video['height']), video.get('fps') or 0
        key = f'{height}p' + (f'{fps:g}' if fps > 30 else '')
        if key in seen:
            continue
        seen.add(key)
        options.append({'qn': 5000000 + (zlib.crc32(key.encode()) & 0xFFFFFF),
            'desc': key + ' · ' + str(video['vcodec']).split('.')[0].upper(), 'label': key,
            'bandwidth': int(((video.get('tbr') or 0) + (audio[0].get('tbr') or 128)) * 1000),
            'video': video['url'], 'audio': audio[0]['url'],
            'duration': max(.5, min(10, video.get('target_duration') or 2))})
    if options:
        options[0]['qn'] = 10000
    return options


class YouTubePlayback(YouTubePlatform):
    def __init__(self, context):
        super().__init__()
        self._context = context
        self._metadata = {}
        self._metadata_lock = threading.Lock()

    def _hls_play_url(self, room_id, quality=10000, *, preview=False):
        canonical = self.normalize(room_id)
        # A preview/fallback probe must not mutate the main adaptive catalogue.
        legacy = YouTubePlatform()
        result = legacy.play_url(canonical, quality, preview=preview)
        options = legacy._qualities.get(canonical, [])
        if preview:
            return result
        known = self._qualities.get(canonical, [])
        if not any('video' in item for item in known):
            self._qualities[canonical] = options
            return result
        selected = next((item for item in options if item['qn'] == result[1]), None)
        if selected is None:
            raise YouTubeMediaError('YouTube 回退未返回实际画质信息')
        label = selected['label']
        actual = next((item['qn'] for item in known if item['label'] == label), None)
        if actual is None:
            actual = 5000000 + (zlib.crc32(label.encode()) & 0xFFFFFF)
            self._qualities[canonical] = [*known, dict(selected, qn=actual)]
        return result[0], actual, result[2], result[3]

    def play_url(self, room_id, quality=10000, *, preview=False, cancelled=lambda: False):
        canonical = self.normalize(room_id)
        advertised = any('video' in item for item in self._qualities.get(canonical, []))
        try:
            return self._adaptive_play_url(canonical, quality, preview=preview, cancelled=cancelled)
        except YouTubeMediaError:
            if cancelled() or (quality != AUTO_QUALITY and (quality != 10000 or advertised)):
                raise
            # First-open/auto remains usable where the original HLS route works.
            # A previously advertised manual high tier must fail explicitly instead.
            return self._hls_play_url(canonical, quality, preview=preview)

    def _adaptive_play_url(self, room_id, quality=10000, *, preview=False, cancelled=lambda: False):
        # Existing hosts remain able to use this candidate's original HLS playback.
        if not callable(getattr(self._context, 'retain_media_source', None)):
            return self._hls_play_url(room_id, quality, preview=preview)
        canonical = self.normalize(room_id)
        proxy = request_proxy(self.room_url(canonical))
        runtime = ensure_runtime(proxy, cancelled)
        with self._metadata_lock:
            cached = self._metadata.get(canonical)
        if cached and time.monotonic() - cached[0] < 60 and cached[1] == proxy:
            options = cached[2]
        else:
            info = extract_live(runtime, self.room_url(canonical), proxy, cancelled)
            options = quality_formats(info)
            if not options:
                raise YouTubeMediaError('YouTube 未返回可用的直播音视频格式')
            with self._metadata_lock:
                self._metadata[canonical] = (time.monotonic(), proxy, options)
        if not preview:
            self._qualities[canonical] = options
        selected = options[-1] if preview or quality == AUTO_QUALITY else next(
            (item for item in options if item['qn'] == quality), None)
        if selected is None:
            raise YouTubeMediaError('YouTube 当前不提供所选画质，请从最新列表选择')
        ffmpeg = self._context.ffmpeg_executable()
        def invalidate():
            with self._metadata_lock:
                current = self._metadata.get(canonical)
                if current is not None and current[2] is options:
                    self._metadata.pop(canonical, None)
        def create(stopped):
            return AdaptiveReader(selected['video'], selected['audio'], selected['duration'],
                                  proxy, ffmpeg, stopped, invalidated=invalidate)
        try:
            reader = create(cancelled)
        except OSError:
            invalidate()
            raise
        if cancelled():
            reader.close()
            raise YouTubeMediaError('YouTube 高画质取流已取消')
        return self._context.retain_media_source(create, reader), selected['qn'], self.kind, {}

    def preview_url(self, room_id, *, cancelled=lambda: False):
        # Preview remains the original low-bitrate HLS route where available.
        try:
            return self._hls_play_url(room_id, preview=True)
        except RuntimeError:
            return self.play_url(room_id, preview=True, cancelled=cancelled)
