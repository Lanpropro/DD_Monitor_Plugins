"""Resolve the 720p HLS source published in the official public mobile room."""
import json
import re
from itertools import chain
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

import av
import requests

from .media import PROBE_BYTES, PROBE_SECONDS
from .platform import QieError, USER_AGENT, cdn_url


MOBILE_ORIGIN = 'https://m.qie.tv/'
MOBILE_ROOM = re.compile(r'<script\b[^>]*>\s*window\.\$ROOM_INFO\s*=\s*', re.IGNORECASE)


def mobile_room_payload(page, number):
    assignment = MOBILE_ROOM.search(page)
    if assignment is None:
        raise QieError('企鹅体育官网未返回手机房间播放资料')
    try:
        room, _ = json.JSONDecoder().raw_decode(page, assignment.end())
    except ValueError as error:
        raise QieError('企鹅体育手机房间播放资料格式无效') from error
    if not isinstance(room, dict) or str(room.get('room_id')) != number:
        raise QieError('企鹅体育手机房间资料与当前房间不匹配')
    return room


def fetch_mobile_room(number):
    try:
        with requests.get(MOBILE_ORIGIN + number,
                          headers={'User-Agent': USER_AGENT, 'Cache-Control': 'no-cache'},
                          allow_redirects=False, timeout=(4, 6)) as response:
            if response.status_code != 200:
                raise QieError(f'企鹅体育手机房间请求失败（HTTP {response.status_code}）')
            return mobile_room_payload(response.text, number)
    except requests.RequestException as error:
        raise QieError('企鹅体育手机房间资料请求失败') from error


def mobile_hls_url(room, number):
    if str(room.get('show_status')) != '1':
        raise QieError('企鹅体育手机房间尚未提供直播')

    source = cdn_url(room.get('hls_url'))
    parts = urlsplit(source)
    if (parts.scheme != 'https' or not parts.query
            or not re.fullmatch(rf'/live/{re.escape(number)}(?:[A-Za-z_-][A-Za-z0-9_-]*)?\.m3u8',
                                parts.path, re.IGNORECASE)):
        raise QieError('企鹅体育官网未返回当前房间的 HLS 地址')
    return source


class HlsReader:
    """Deliver continuous MPEG-TS, preserving the HLS video and AAC packets."""

    def __init__(self, media, cancelled):
        self._media = media
        self._lock = Lock()
        self._buffer = bytearray()
        if not media.streams.video:
            raise QieError('企鹅体育未返回可确认的 720p 视频')
        video = media.streams.video[0]
        streams = [video, *media.streams.audio[:1]]
        packets = media.demux(streams)
        prefix = []
        deadline = monotonic() + PROBE_SECONDS
        remaining = PROBE_BYTES
        for packet in packets:
            if cancelled():
                raise QieError('企鹅体育取流已取消')
            remaining -= packet.size
            if remaining < 0 or monotonic() >= deadline:
                raise QieError('未能在探测限额内确认企鹅体育 720p 画质')
            prefix.append(packet)
            if packet.stream == video and (frames := packet.decode()):
                if (frames[0].width, frames[0].height) != (1280, 720):
                    raise QieError('企鹅体育当前源未提供 1280×720 视频')
                break
        else:
            raise QieError('企鹅体育未返回可确认的 720p 视频')
        self._packets = chain(prefix, packets)
        self._output = av.open(self, 'w', format='mpegts', buffer_size=4096,
                               options={'flush_packets': '1'})
        try:
            self._streams = {stream.index: self._output.add_stream_from_template(stream) for stream in streams}
        except Exception:
            self._output.close()
            raise

    def write(self, data):
        self._buffer.extend(data)
        return len(data)

    def read(self, size):
        with self._lock:
            if self._media is None:
                return b''
            try:
                while not self._buffer:
                    packet = next(self._packets, None)
                    if packet is None:
                        return b''
                    if packet.dts is not None:
                        packet.stream = self._streams[packet.stream.index]
                        self._output.mux(packet)
            except av.error.FFmpegError as error:
                raise OSError('企鹅体育 HLS 直播连接已断开') from error
            data = bytes(self._buffer[:size])
            del self._buffer[:size]
            return data

    def close(self):
        with self._lock:
            if self._media is None:
                return
            try:
                self._output.close()
            except av.error.FFmpegError:
                pass  # The player has already stopped reading this stream.
            finally:
                self._media.close()
                self._media = None


def open_mobile_720p(number, cancelled):
    room_url = MOBILE_ORIGIN + number
    headers = {'Referer': room_url, 'User-Agent': USER_AGENT}
    if cancelled():
        raise QieError('企鹅体育取流已取消')
    source = mobile_hls_url(fetch_mobile_room(number), number)
    if cancelled():
        raise QieError('企鹅体育取流已取消')
    media = None
    try:
        with requests.get(source, headers=headers, allow_redirects=False, timeout=(4, 6)) as response:
            if response.status_code != 200:
                raise QieError(f'企鹅体育 HLS 清单请求失败（HTTP {response.status_code}）')
            manifest = response.text.lstrip('\ufeff').splitlines()
            if (not manifest or manifest[0] != '#EXTM3U' or '#EXT-X-ENDLIST' in manifest
                    or any(line.startswith('#EXT-X-STREAM-INF:') for line in manifest)
                    or not any(line.startswith('#EXTINF:') for line in manifest)):
                raise QieError('企鹅体育官网未提供可验证的单档直播清单')
        if cancelled():
            raise QieError('企鹅体育取流已取消')
        options = {'headers': ''.join(f'{key}: {value}\r\n' for key, value in headers.items()),
                   'protocol_whitelist': 'https,tls,tcp,crypto', 'live_start_index': '-2'}
        media = av.open(source, format='hls', timeout=(4, 6), options=options)
        reader = HlsReader(media, cancelled)
        media = None
        return reader
    except (requests.RequestException, av.error.FFmpegError, OSError) as error:
        raise QieError('企鹅体育 HLS 连接或 720p 视频探测失败') from error
    finally:
        if media is not None:
            media.close()
