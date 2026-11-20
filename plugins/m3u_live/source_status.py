"""Verify a live HTTP source without opening a player or downloading a whole segment."""
from urllib.parse import urljoin

import requests

from .playlist import http_url


PROBE_TIMEOUT = (3, 4)
MAX_MANIFEST_BYTES = 128 * 1024


def _read_source(url, headers):
    with requests.get(url, headers=headers, stream=True, timeout=PROBE_TIMEOUT) as response:
        response.raise_for_status()
        chunks = response.iter_content(512)
        first = next(chunks, b'')
        if not first.startswith((b'#EXTM3U', b'\xef\xbb\xbf#EXTM3U')):
            transport_stream = first[0:377:188] == b'\x47\x47\x47'
            flv_stream = first.startswith(b'FLV\x01')
            return response.url, [], 'Content-Length' not in response.headers and (transport_stream or flv_stream)
        data = bytearray(first)
        for chunk in chunks:
            data.extend(chunk)
            if len(data) > MAX_MANIFEST_BYTES:
                return response.url, [], False
        lines = [line.strip() for line in data.decode('utf-8-sig').splitlines() if line.strip()]
        return response.url, lines, False


def source_is_live(channel):
    if not http_url(channel.url):
        return False
    try:
        base, lines, direct = _read_source(channel.url, channel.headers)
        if direct:
            return True
        variant = next((uri for tag, uri in zip(lines, lines[1:])
                        if tag.startswith('#EXT-X-STREAM-INF:') and not uri.startswith('#')), '')
        if variant:
            url = urljoin(base, variant)
            if not http_url(url):
                return False
            base, lines, _ = _read_source(url, channel.headers)
        if ('#EXT-X-ENDLIST' in lines or '#EXT-X-PLAYLIST-TYPE:VOD' in lines
                or not any(line.startswith('#EXT-X-TARGETDURATION:') for line in lines)):
            return False
        segment = next((line for line in reversed(lines) if not line.startswith('#')), '')
        if not segment:
            return False
        url = urljoin(base, segment)
        if not http_url(url):
            return False
        with requests.get(url, headers=channel.headers, stream=True, timeout=PROBE_TIMEOUT) as response:
            response.raise_for_status()
            prefix = next(response.iter_content(512), b'')
            return bool(prefix) and not prefix.lstrip().startswith((b'<', b'{'))
    except (requests.RequestException, ValueError):
        return False
