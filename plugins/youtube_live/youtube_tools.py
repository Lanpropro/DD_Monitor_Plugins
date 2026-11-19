"""Isolated high-resolution metadata extraction; no browser profile or account reads."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


class YouTubeMediaError(OSError):
    pass


def stop_process(process):
    if process.poll() is None:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def failure_message(stderr):
    text = stderr.lower()
    if 'not a bot' in text or 'sign in' in text:
        return 'YouTube 要求验证当前访问；高画质匿名取流暂不可用，请稍后重试'
    if 'timed out' in text or 'timeout' in text or 'ssl' in text or 'operation too slow' in text:
        return 'YouTube 高画质连接失败，请检查当前代理后重试'
    if 'po token' in text or 'potoken' in text or 'getpot' in text:
        return 'YouTube 高画质来源校验失败，请稍后重试'
    return 'YouTube 高画质解析失败，请刷新直播后重试'


def extract_live(runtime, url, proxy, cancelled):
    root = Path(runtime)
    executable = root / 'yt-dlp.exe'
    node = root / 'node.exe'
    provider = root / 'provider'
    if not all(path.is_file() for path in (executable, node, provider / 'server/build/generate_once.js')):
        raise YouTubeMediaError('YouTube 高画质解析组件不完整，请更新 YouTube 插件')
    template = '{"id":%(id)j,"is_live":%(is_live)j,"live_status":%(live_status)j,' \
               '"formats":%(formats.:.{format_id,url,protocol,width,height,fps,tbr,vcodec,acodec,ext,target_duration})j}'
    command = [str(executable), '--ignore-config', '--no-cache-dir', '--no-playlist', '--no-progress',
        '--socket-timeout', '12', '--retries', '1', '--extractor-retries', '1', '--impersonate', 'chrome',
        '--no-js-runtimes', '--js-runtimes', 'node:' + str(node), '--no-remote-components',
        '--no-plugin-dirs', '--plugin-dirs', str(provider / 'plugins'),
        '--extractor-args', 'youtubepot-bgutilscript:server_home=' + str(provider / 'server'),
        '--extractor-args', 'youtube:player_client=web;formats=incomplete',
        '--live-from-start', '--skip-download', '--proxy', proxy or '', '--print', template, '--', url]
    environment = {key: value for key, value in os.environ.items() if key.lower() not in
                   ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'node_options')}
    if proxy:
        environment.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy)
    environment.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    with tempfile.TemporaryDirectory(prefix='ddm-youtube-') as cache:
        environment['XDG_CACHE_HOME'] = cache
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + 120
        try:
            while True:
                if cancelled():
                    raise YouTubeMediaError('YouTube 高画质解析已取消')
                if time.monotonic() >= deadline:
                    raise YouTubeMediaError('YouTube 高画质解析超时，请检查当前代理后重试')
                try:
                    output, errors = process.communicate(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise YouTubeMediaError(failure_message(errors.decode('utf-8', errors='replace')))
            if len(output) > 16 * 1024 * 1024:
                raise YouTubeMediaError('YouTube 高画质响应过大')
            try:
                info = json.loads(output)
            except (ValueError, UnicodeError):
                raise YouTubeMediaError('YouTube 高画质响应无效') from None
            if not isinstance(info, dict) or info.get('is_live') is not True or info.get('live_status') != 'is_live':
                raise YouTubeMediaError('YouTube 此视频当前不是直播')
            return info
        finally:
            stop_process(process)
