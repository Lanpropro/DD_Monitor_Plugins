"""Bounded FFmpeg error classification; never export raw stderr or stream URLs."""
import re


class AudioDiagnostics:
    def __init__(self):
        self._tail = b''
        self.received_bytes = 0

    def feed_error(self, data):
        self._tail = (self._tail + data)[-8192:]

    def reason(self):
        text = self._tail.decode('utf-8', 'replace').lower()
        text = re.sub(r'https?://\S+', '', text)
        match = re.search(r'(?:http error|server returned|http/[\d.]+)\s+([45]\d\d)\b', text)
        if match:
            return 'http_' + match[1]
        for code, patterns in (
            ('timeout', ('timed out', 'timeout')),
            ('dns', ('failed to resolve', 'name or service not known', 'getaddrinfo')),
            ('refused', ('connection refused',)),
            ('reset', ('connection reset', 'connection aborted', 'end of file')),
            ('tls', ('tls', 'ssl')),
            ('no_audio', ('matches no streams', 'does not contain any stream')),
            ('invalid_data', ('invalid data found',)),
            ('unsupported_option', ('unrecognized option', 'option not found')),
            ('decoder', ('unknown decoder', 'decoder not found', 'error while opening decoder')),
        ):
            if any(pattern in text for pattern in patterns):
                return code
        return 'unknown'


def failure_message(reason, exit_code):
    if reason.startswith('http_'):
        return f'音轨请求被直播源拒绝（HTTP {reason[5:]}），请重新播放该直播间后再试'
    messages = {
        'timeout': '读取直播音轨超时；播放器与传译使用独立连接，请重试',
        'dns': '音轨连接无法解析服务器地址，请检查网络',
        'refused': '音轨连接被服务器拒绝，请检查网络或代理',
        'reset': '直播源关闭了音轨连接，请重新播放该直播间后再试',
        'tls': '音轨连接的 TLS 握手失败，请检查网络或代理',
        'no_audio': '直播源中未找到可识别的音轨',
        'invalid_data': 'FFmpeg 无法解析直播音轨数据，请重新播放该直播间后再试',
        'unsupported_option': '当前 FFmpeg 不支持音轨读取参数，请检查 FFmpeg 版本',
        'decoder': '当前 FFmpeg 无法解码这一路音轨',
    }
    return messages.get(reason, f'直播音轨读取已退出（退出码 {exit_code}），请查看日志中的音轨诊断')
