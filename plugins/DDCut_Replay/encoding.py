"""Shared encoding settings and encoder-specific FFmpeg arguments."""
def normalize(value=None):
    result = {'codec': 'h264', 'priority': 'speed', 'bitrate': 0, 'backend': 'software', **(value or {})}
    if result['codec'] not in ('h264', 'h265') or result['priority'] not in ('speed', 'quality'):
        raise ValueError('不支持的编码格式或优先级')
    if not isinstance(result['bitrate'], int) or not 0 <= result['bitrate'] <= 100000:
        raise ValueError('视频码率必须为0（自动）或不超过100000 Kbps的整数')
    if result['backend'] not in ('software', 'auto'):
        raise ValueError('不支持的编码方式')
    return result


def video_args(value=None, encoder=None):
    value = normalize(value)
    encoder = encoder or ('libx264' if value['codec'] == 'h264' else 'libx265')
    quality = value['priority'] == 'quality'
    args = ['-c:v', encoder]
    if encoder.endswith('_nvenc'):
        args += ['-preset', 'p7' if quality else 'p1', '-rc', 'vbr']
        if not value['bitrate']:
            args += ['-cq', '20', '-b:v', '0']
    elif encoder.endswith('_qsv'):
        args += ['-preset', 'slow' if quality else 'veryfast']
        if not value['bitrate']:
            args += ['-global_quality', '20']
    elif encoder.endswith('_amf'):
        args += ['-quality', 'quality' if quality else 'speed', '-rc', 'vbr_peak' if value['bitrate'] else 'cqp']
        if not value['bitrate']:
            args += ['-qp_i', '20', '-qp_p', '20']
    elif encoder.endswith('_videotoolbox'):
        args += ['-prio_speed', '0' if quality else '1', '-allow_sw', '0']
        if not value['bitrate']:
            args += ['-b:v', '8000k']
    else:
        args += ['-preset', 'slow' if quality else 'veryfast']
        if not value['bitrate']:
            args += ['-crf', '20']
    if value['bitrate']:
        args += ['-b:v', str(value['bitrate'])+'k']
    if value['codec'] == 'h265':
        args += ['-tag:v', 'hvc1']
        if encoder == 'libx265':
            args += ['-x265-params', 'log-level=error:pools=2']
    return args
