"""Stream the shared Qt chat scene into FFmpeg; no frame sequence on disk."""
from fractions import Fraction
import math

from .chat import ChatReader, align_events
from .chat_scene import ChatScene
from .media import check_cancel, probe, run_process


def encode_chat(job, ffmpeg, source, start, end, local_start, input_args, target, work, encoding=None):
    from .hardware_encoding import select_video_args
    reader = ChatReader()
    events = align_events(reader.read(source['danmaku_path']), source.get('segments', []))
    if source.get('chat_burned') or reader.video_burned:
        raise ValueError('这份视频画面已含弹幕，请关闭压制以免重复叠加')
    info = probe(job, ffmpeg, source['path'], work, input_args=input_args)
    try:
        rate = Fraction(info['frame_rate'])
    except (ValueError, TypeError, ZeroDivisionError) as error:
        raise ValueError('无法确定录像帧率，不能压制弹幕') from error
    if rate <= 0:
        raise ValueError('无法确定录像帧率，不能压制弹幕')
    width, height = info['video_width'], info['video_height']
    aspect = info.get('sample_aspect_ratio')
    if aspect and aspect not in ('N/A', '0:1'):
        width *= float(Fraction(aspect.replace(':', '/')))
    if round(float(info.get('rotation') or 0)) % 180:
        width, height = height, width
    width, height = max(2, round(width / 2) * 2), max(2, round(height / 2) * 2)
    scene = ChatScene(events, width, height, source.get('chat_size', 36), source.get('chat_opacity', 80))
    shift = source.get('danmaku_offset', 0)
    span = end - start

    def frames():
        for index in range(math.ceil(span * rate)):
            check_cancel(job)
            # Evaluate original media time, retaining comments already in flight at the cut.
            frame = scene.frame_at(start + float(index / rate) - shift)
            yield frame.constBits().tobytes()

    command = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error']
    if not source.get('segments'):
        command += ['-ss', f'{local_start:.6f}']
        local_start = 0
    command += [*input_args, '-f', 'rawvideo', '-pixel_format', 'bgra', '-video_size', f'{width}x{height}',
                '-framerate', str(rate), '-i', 'pipe:0']
    filters = (f'[0:v]setpts=PTS-STARTPTS,trim=start={local_start:.6f}:duration={span:.6f},'
               f'setpts=PTS-STARTPTS,scale={width}:{height},setsar=1,fps={rate}[base];'
               '[base][1:v]overlay=shortest=1:format=rgb:alpha=premultiplied,format=yuv420p[video]')
    command += ['-filter_complex', filters, '-map', '[video]', '-map', '0:a?']
    if info['has_audio']:
        command += ['-af', f'asetpts=PTS-STARTPTS,atrim=start={local_start:.6f}:duration={span:.6f},asetpts=PTS-STARTPTS']
    encoding_args = select_video_args(job, ffmpeg, encoding, work, width, height)
    command += ['-t', f'{span:.6f}', *encoding_args,
                '-c:a', 'aac', '-movflags', '+faststart', str(target)]
    run_process(job, command, work, duration=span, frames=frames())
    return encoding_args[1]
