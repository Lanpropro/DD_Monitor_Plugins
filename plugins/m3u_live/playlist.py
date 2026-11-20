"""Read local and HTTP playlists without confusing HLS segments with IPTV channels."""
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

import requests


MAX_BYTES = 8 * 1024 * 1024
ATTRIBUTES = re.compile(r'([\w-]+)\s*=\s*(?:"([^"]*)"|([^\s,]+))')
EXTINF = re.compile(r'((?:[^",]|"[^"]*")*),(.*)')
ALLOWED_HEADERS = {'http-user-agent': 'User-Agent', 'http-referrer': 'Referer',
                   'http-referer': 'Referer'}


class PlaylistError(ValueError):
    @property
    def user_message(self):
        return str(self)


@dataclass(frozen=True)
class Channel:
    url: str
    name: str
    group: str = ''
    tvg_id: str = ''
    tvg_name: str = ''
    logo: str = ''
    headers: dict = field(default_factory=dict)


def http_url(value):
    try:
        parts = urlsplit(value)
        parts.port  # Reject malformed ports before handing the URL to a client.
        return (parts.scheme in ('http', 'https') and bool(parts.hostname)
                and parts.username is None and parts.password is None
                and not any(char.isspace() or ord(char) < 32 for char in value))
    except ValueError:
        return False


def _resolve(base, value):
    local = urlsplit(base).scheme == 'file'
    url = Path(value).as_uri() if local and Path(value).is_absolute() else urljoin(base, value)
    if http_url(url):
        return url
    parts = urlsplit(url)
    if (local and parts.scheme == 'file' and not parts.netloc
            and parts.path and not any(ord(char) < 32 for char in url)):
        return url
    raise ValueError('unsupported channel URL')


def parse_playlist(contents, base):
    try:
        lines = [line.strip() for line in contents.decode('utf-8-sig').splitlines() if line.strip()]
    except UnicodeDecodeError as error:
        raise PlaylistError('列表编码无法读取，请保存为 UTF-8 后重试') from error
    if not lines:
        raise PlaylistError('播放列表为空')
    extended = lines[0].split(maxsplit=1)[0] == '#EXTM3U'
    if lines[0].startswith('<'):
        raise PlaylistError('链接返回的是网页，请输入 M3U 或 HLS 地址')
    if extended and any(line.startswith('#EXT-X-') for line in lines[1:]):
        name = unquote(_url_name(base)) or 'HLS 直播'
        return [Channel(base, name)], True, 0

    channels, invalid = [], 0
    group = ''
    pending = None
    for line in lines:
        if line.startswith('#EXTINF:'):
            invalid += pending is not None
            match = EXTINF.fullmatch(line[len('#EXTINF:'):])
            if match is None:
                invalid += 1
                pending = None
                continue
            attrs = {key.casefold(): quoted or bare for key, quoted, bare in ATTRIBUTES.findall(match[1])}
            pending = Channel('', match[2].strip() or attrs.get('tvg-name', ''),
                              attrs.get('group-title', '') or group, attrs.get('tvg-id', ''),
                              attrs.get('tvg-name', ''), attrs.get('tvg-logo', ''))
        elif line.startswith('#EXTGRP:'):
            name = line[len('#EXTGRP:'):].strip()
            if pending is None:
                group = name
            elif not pending.group:
                pending = replace(pending, group=name)
        elif line.startswith('#EXTVLCOPT:') and pending is not None:
            key, _, value = line[len('#EXTVLCOPT:'):].partition('=')
            header = ALLOWED_HEADERS.get(key.strip().casefold())
            value = value.strip()
            if (header and value and value.isascii() and value.isprintable()
                    and (header != 'Referer' or http_url(value))):
                pending.headers[header] = value
        elif not line.startswith('#'):
            channel, pending = pending, None
            try:
                url = _resolve(base, line)
                logo = urljoin(base, channel.logo) if channel and channel.logo else ''
                if line.startswith('<'):
                    raise ValueError('invalid M3U line')
                channel = channel or Channel('', '', group)
                channels.append(replace(channel, url=url, name=channel.name or unquote(_url_name(url)) or '频道',
                                        logo=logo if http_url(logo) else ''))
            except ValueError:
                invalid += 1
    invalid += pending is not None
    if not channels:
        raise PlaylistError('未找到可导入的 HTTP(S) 或本地频道')
    return channels, False, invalid


def _url_name(url):
    return urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]


def read_playlist(source, cancelled):
    try:
        if http_url(source):
            data = bytearray()
            with requests.get(source, timeout=(4, 5), stream=True) as response:
                response.raise_for_status()
                base = response.url
                for chunk in response.iter_content(64 * 1024):
                    if cancelled():
                        raise PlaylistError('导入已取消')
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise PlaylistError('播放列表超过 8 MB')
        else:
            path = Path(source).expanduser().resolve()
            base = path.as_uri()
            with path.open('rb') as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise PlaylistError('播放列表超过 8 MB')
    except requests.HTTPError as error:
        raise PlaylistError(f'播放列表请求失败（HTTP {error.response.status_code}）') from error
    except requests.RequestException as error:
        raise PlaylistError('播放列表连接失败，请检查网络或链接') from error
    except OSError as error:
        raise PlaylistError('无法读取本地播放列表，请检查文件路径') from error
    if cancelled():
        raise PlaylistError('导入已取消')
    return parse_playlist(data, base)
