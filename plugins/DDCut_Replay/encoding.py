"""Shared software encoding settings for precise clips and project exports."""
def normalize(value=None):
    result = {'codec': 'h264', 'priority': 'speed', 'bitrate': 0, **(value or {})}
    if result['codec'] not in ('h264', 'h265') or result['priority'] not in ('speed', 'quality'):
        raise ValueError('不支持的编码格式或优先级')
    if not isinstance(result['bitrate'], int) or not 0 <= result['bitrate'] <= 100000:
        raise ValueError('视频码率必须为0（自动）或不超过100000 Kbps的整数')
    return result


def video_args(value=None):
    value = normalize(value)
    args = ['-c:v', 'libx264' if value['codec'] == 'h264' else 'libx265',
            '-preset', 'veryfast' if value['priority'] == 'speed' else 'slow']
    args += ['-b:v', str(value['bitrate'])+'k'] if value['bitrate'] else ['-crf', '20']
    if value['codec'] == 'h265':
        args += ['-tag:v', 'hvc1', '-x265-params', 'log-level=error:pools=2']
    return args
