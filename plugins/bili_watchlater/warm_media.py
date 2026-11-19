"""Bounded media-prefix cache, served through the host's existing range proxy."""
from dataclasses import dataclass, replace
import re
import time
import requests

VIDEO_BYTES = 8 * 1024 * 1024
AUDIO_BYTES = 2 * 1024 * 1024
MAX_AGE = 120
_CONTENT_RANGE = re.compile(r'bytes (\d+)-(\d+)/(\d+)')
_RANGE = re.compile(r'bytes=(\d+)-(\d*)')


@dataclass(frozen=True)
class Prefix:
    url: str
    data: bytes
    total: int
    content_type: str


def fetch_prefix(session, url, headers, limit, cancelled):
    request_headers = dict(headers, Range=f'bytes=0-{limit - 1}', **{'Accept-Encoding': 'identity'})
    with session.get(url, headers=request_headers, stream=True, timeout=(4, 8)) as response:
        response.raise_for_status()
        match = _CONTENT_RANGE.fullmatch(response.headers.get('Content-Range', ''))
        if (response.status_code != 206 or match is None or int(match[1]) != 0
                or response.headers.get('Content-Encoding', 'identity') != 'identity'):
            return None  # Unsupported CDN ranges: use normal playback instead.
        chunks, received = [], 0
        for chunk in response.iter_content(65536):
            if cancelled():
                return None
            chunk = chunk[:limit - received]
            chunks.append(chunk)
            received += len(chunk)
            if received >= limit:
                break
        data = b''.join(chunks)
        if not data:
            return None
        return Prefix(url, data, int(match[3]), response.headers.get('Content-Type', 'application/octet-stream'))


class PrefixResponse(requests.Response):
    def __init__(self, prefix, start, end, ranged, session, kwargs):
        super().__init__()
        self.url = prefix.url
        self.status_code = 206 if ranged else 200
        self.headers.update({'Content-Type': prefix.content_type, 'Content-Length': str(end - start + 1),
                             'Accept-Ranges': 'bytes'})
        if ranged:
            self.headers['Content-Range'] = f'bytes {start}-{end}/{prefix.total}'
        self.prefix, self.start, self.end = prefix, start, end
        self.session, self.kwargs = session, kwargs
        self.tail = None

    def iter_content(self, chunk_size=65536, decode_unicode=False):
        stop = min(len(self.prefix.data), self.end + 1)
        for offset in range(self.start, stop, chunk_size):
            yield self.prefix.data[offset:min(stop, offset + chunk_size)]
        if stop <= self.end:
            kwargs = dict(self.kwargs)
            kwargs['headers'] = dict(kwargs.get('headers') or {}, Range=f'bytes={stop}-{self.end}')
            self.tail = self.session.get(self.url, **kwargs)
            self.tail.raise_for_status()
            match = _CONTENT_RANGE.fullmatch(self.tail.headers.get('Content-Range', ''))
            if self.tail.status_code != 206 or match is None or int(match[1]) != stop or int(match[3]) != self.prefix.total:
                raise requests.RequestException('Media range changed')
            yield from self.tail.iter_content(chunk_size)

    def close(self):
        if self.tail is not None:
            self.tail.close()
        self.prefix = None


class PrefixSession:
    def __init__(self, prefix, session_factory=requests.Session):
        self.prefix = prefix
        self.session = session_factory()

    def get(self, url, **kwargs):
        raw = (kwargs.get('headers') or {}).get('Range', '')
        match = _RANGE.fullmatch(raw) if raw else None
        start = int(match[1]) if match else 0
        end = min(int(match[2]), self.prefix.total - 1) if match and match[2] else self.prefix.total - 1
        if (url != self.prefix.url or raw and match is None or start >= len(self.prefix.data)
                or end < start):
            return self.session.get(url, **kwargs)
        return PrefixResponse(self.prefix, start, end, bool(raw), self.session, kwargs)

    def close(self):
        self.session.close()


class PreparedMedia:
    def __init__(self, stream, video, audio=None):
        self.stream, self.video, self.audio = stream, video, audio
        self.created = time.monotonic()

    def activate(self, proxy_factory=None):
        if time.monotonic() - self.created > MAX_AGE:
            raise ValueError('Prefetch expired')
        if proxy_factory is None:
            from ddm.hls_proxy import HlsProxy
            proxy_factory = HlsProxy
        proxies = []
        try:
            for prefix in (self.video, self.audio):
                if prefix is not None:
                    proxies.append(proxy_factory(prefix.url, self.stream.headers,
                        session_factory=lambda p=prefix: PrefixSession(p)))
            stream = replace(self.stream, url=proxies[0].url,
                audio_url=proxies[1].url if self.audio is not None else self.stream.audio_url)
            return stream, WarmLease(proxies)
        except Exception:
            for proxy in proxies:
                proxy.stop()
            raise


class WarmLease:
    def __init__(self, proxies):
        self.proxies = proxies

    def close(self):
        for proxy in self.proxies:
            proxy.stop()
        self.proxies.clear()
