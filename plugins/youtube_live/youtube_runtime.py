"""Private pinned tools; automatic preparation never changes the host Python environment."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import zipfile

import requests
from .youtube_tools import YouTubeMediaError


PROVIDER_SHA256 = 'cbbd34d9a3b17e96af4204d153361da38402332fbfa4def9d4517c8a823ed233'
TOOLS = {
    'yt-dlp.exe': ('https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp.exe',
                   'f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d'),
    'node.exe': ('https://nodejs.org/dist/v24.11.0/win-x64/node.exe',
                 'b7d912484d42e7a0d0cb5b26a86410ec973a79ece7d61ad535e2d1a97a9026e1'),
}
_lock = threading.Lock()


def unpack_provider(archive, destination):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != PROVIDER_SHA256:
        raise YouTubeMediaError('YouTube 来源校验组件损坏，请重新安装插件')
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path = Path(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename:
                raise YouTubeMediaError('YouTube 来源校验组件路径无效')
        bundle.extractall(destination)


def ensure_runtime(proxy, cancelled, *, cache=None):
    if os.name != 'nt':
        raise YouTubeMediaError('YouTube 高画质组件目前支持 Windows x64')
    base = Path(cache) if cache else Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'DDMonitorCE/YouTubeMedia'
    root = base / ('2026.10.26-node24.11.0-' + PROVIDER_SHA256[:12])
    while not _lock.acquire(timeout=.1):
        if cancelled():
            raise YouTubeMediaError('YouTube 高画质准备已取消')
    try:
        if (root / 'ready').is_file() and all((root / name).is_file() for name in TOOLS):
            return root
        root.mkdir(parents=True, exist_ok=True)
        for name, (url, checksum) in TOOLS.items():
            target = root / name
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == checksum:
                continue
            if cancelled():
                raise YouTubeMediaError('YouTube 高画质准备已取消')
            temporary = target.with_suffix('.part')
            try:
                with requests.Session() as session:
                    session.trust_env = False
                    with session.get(url, proxies={'https': proxy} if proxy else {},
                                     stream=True, timeout=(5, 20)) as response:
                        response.raise_for_status()
                        digest, size = hashlib.sha256(), 0
                        with temporary.open('wb') as output:
                            for chunk in response.iter_content(262144):
                                if cancelled():
                                    raise YouTubeMediaError('YouTube 高画质准备已取消')
                                size += len(chunk)
                                if size > 128 * 1024 * 1024:
                                    raise YouTubeMediaError('YouTube 组件下载大小异常')
                                digest.update(chunk)
                                output.write(chunk)
                if digest.hexdigest() != checksum:
                    raise YouTubeMediaError('YouTube 组件校验失败，请重新尝试')
                temporary.replace(target)
            except requests.RequestException:
                raise YouTubeMediaError('YouTube 高画质组件下载失败，请检查代理后重试') from None
            finally:
                temporary.unlink(missing_ok=True)
        unpack_provider(Path(__file__).with_name('youtube-provider.zip'), root)
        (root / 'ready').write_text(PROVIDER_SHA256, encoding='ascii')
        return root
    finally:
        _lock.release()
