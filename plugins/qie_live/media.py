"""Validate one public FLV response and deliver the same connection to the player."""
from io import BytesIO
import re
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

import av
import requests
from urllib3.exceptions import HTTPError as TransportError

from .platform import ORIGIN, USER_AGENT, QieError, cdn_url


PROBE_BYTES = 2 * 1024 * 1024
PROBE_SECONDS = 8


class Probe:
    def __init__(self, raw, cancelled):
        self.raw = raw
        self.cancelled = cancelled
        self.prefix = bytearray()
        self.deadline = monotonic() + PROBE_SECONDS

    def read(self, size):
        if self.cancelled():
            raise QieError('企鹅体育取流已取消')
        available = PROBE_BYTES - len(self.prefix)
        if available <= 0 or monotonic() >= self.deadline:
            raise QieError('未能在探测限额内确认企鹅体育 720p 画质')
        data = self.raw.read(min(size, available))
        self.prefix.extend(data)
        return data


class FlvReader:
    def __init__(self, session, response, prefix):
        self._session = session
        self._response = response
        self._prefix = BytesIO(prefix)
        self._io = Lock()

    def read(self, size):
        with self._io:
            if self._response is None:
                return b''
            buffered = self._prefix.read(size)
            if buffered:
                return buffered
            try:
                return self._response.raw.read(size)
            except TransportError as error:
                raise OSError('企鹅体育直播连接已断开') from error

    def close(self):
        with self._io:
            if self._response is None:
                return
            self._response.close()
            self._response = None
            self._session.close()
            self._prefix.close()


def verify_720p(media):
    if not media.streams.video:
        raise QieError('企鹅体育未返回可确认的 720p 视频')
    frame = next(media.decode(video=0), None)
    if frame is None or (frame.width, frame.height) != (1280, 720):
        raise QieError('企鹅体育当前源未提供 1280×720 视频')


def open_720p(address, number, cancelled):
    verified = cdn_url(address)
    if not verified:
        raise QieError('企鹅体育未返回可识别的公开 FLV 地址')
    parts = urlsplit(verified)
    if not parts.query or not re.fullmatch(rf'/live/{re.escape(number)}(?:[A-Za-z_-][A-Za-z0-9_-]*)?\.flv',
                                          parts.path, re.IGNORECASE):
        raise QieError('企鹅体育返回的媒体地址与当前房间不匹配')
    if cancelled():
        raise QieError('企鹅体育取流已取消')

    session = requests.Session()
    session.trust_env = False
    response = None
    try:
        response = session.get(verified, headers={'Referer': ORIGIN, 'User-Agent': USER_AGENT},
                               stream=True, allow_redirects=False, timeout=(4, 6))
        if response.status_code == 403:
            raise QieError('企鹅体育媒体地址被拒绝（HTTP 403），等待重新取流')
        if response.status_code != 200:
            raise QieError(f'企鹅体育媒体请求失败（HTTP {response.status_code}）')
        probe = Probe(response.raw, cancelled)
        with av.open(probe, format='flv', options={'probesize': str(PROBE_BYTES),
                                                   'analyzeduration': '3500000'}) as media:
            verify_720p(media)
        reader = FlvReader(session, response, probe.prefix)
        response = None
        session = None
        return reader
    except (requests.RequestException, TransportError, av.error.FFmpegError, OSError) as error:
        raise QieError('企鹅体育媒体连接或 720p 视频探测失败') from error
    finally:
        if response is not None:
            response.close()
        if session is not None:
            session.close()
