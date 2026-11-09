"""Project media inspection, real waveforms and the shared preview/export renderer."""
from array import array
from itertools import groupby
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from ddm import theme
from .chat import load_events, write_json
from .hls import input_options, read_segments
from .media import Job, check_cancel, export_clip, probe, run_process, thumbnail
from .project import duration, placed_clips, placed_layers, validate
from .subtitles import placed_cues
from .subtitle_render import SubtitleGraphics
from .subtitle_srt import srt_text
from .graphic_style import TEXT_DEFAULTS
from .graphic_text import draw_text


def inspect_file(job, ffmpeg, path, work, *, thumbnails=None):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError('来源文件不存在，请重新定位：' + str(path))
    image = QImage(str(path))
    if not image.isNull():
        return {'path': str(path), 'title': path.stem, 'type': 'image', 'duration': 0,
                'video_width': image.width(), 'video_height': image.height(), 'has_audio': False, 'cover_path': str(path)}
    sibling = Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    executable = str(sibling) if sibling.is_file() else shutil.which('ffprobe')
    if not executable:
        raise RuntimeError('未找到ffprobe')
    inputs = input_options(path) if path.suffix.lower() == '.m3u8' else [str(path)]
    data = json.loads(run_process(job, [executable, '-v', 'error', '-show_format', '-show_streams',
                                      '-of', 'json', *inputs], work, capture=True))
    streams = data.get('streams', [])
    video = next((row for row in streams if row.get('codec_type') == 'video'), None)
    audio = any(row.get('codec_type') == 'audio' for row in streams)
    segments = read_segments(path) if path.suffix.lower() == '.m3u8' else None
    length = (sum(row['duration'] for row in segments) if segments is not None
              else float(data.get('format', {}).get('duration') or 0))
    if not (video or audio) or not math.isfinite(length) or length <= 0:
        raise ValueError('无法确定文件的音视频内容或时长')
    result = {'path': str(path), 'title': path.stem, 'type': 'video' if video else 'audio',
              'duration': length, 'has_audio': audio,
              'video_width': video.get('width', 0) if video else 0,
              'video_height': video.get('height', 0) if video else 0}
    if path.suffix.lower() == '.m3u8':
        result.update(index_format='hls', segments=segments)
    sidecar = path.with_suffix('.danmaku.json')
    if sidecar.is_file():
        from .chat import ChatReader
        reader = ChatReader()
        reader.read(sidecar)
        result.update(danmaku_path=str(sidecar), chat_burned=reader.video_burned)
    if video and thumbnails:
        result['cover_path'] = thumbnail(job, ffmpeg, path, thumbnails,
                                        input_args=input_options(path) if result.get('index_format') == 'hls' else None)
    return result


def waveform(job, ffmpeg, info, work):
    if not info.get('has_audio'):
        return []
    Path(work).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wave-', dir=work) as folder:
        target = Path(folder) / 'mono.f32'
        inputs = input_options(info['path']) if info.get('index_format') == 'hls' else ['-i', info['path']]
        run_process(job, [ffmpeg, '-y', '-v', 'error', *inputs, '-vn', '-ac', '1', '-ar', '8000',
                          '-t', str(info['duration']), '-f', 'f32le', str(target)], work, duration=info['duration'])
        samples = target.stat().st_size // 4
        size = max(1, math.ceil(samples / 2048))
        peaks = []
        with target.open('rb') as handle:
            while block := handle.read(size * 4):
                check_cancel(job)
                values = array('f')
                values.frombytes(block)
                peaks.append(max(map(abs, values), default=0))
        maximum = max(peaks, default=1) or 1
        return [round(min(1, value / maximum), 4) for value in peaks]


def tempo(speed):
    factors = []
    while speed < .5:
        factors.append(.5)
        speed /= .5
    while speed > 2:
        factors.append(2)
        speed /= 2
    return ','.join(f'atempo={value:.8f}' for value in [*factors, speed])


class ProjectGraphics:
    def __init__(self, project, width, height, layers=None):
        self.layers = [(row, start, end) for row, start, end in (placed_layers(project) if layers is None else layers) if row['kind'] in ('text', 'image')]
        self.layers.sort(key=lambda row: row[0]['track'])
        self.width, self.height = width, height
        self.images = {}

    def frame_at(self, stamp):
        image = QImage(self.width, self.height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for item, start, end in self.layers:
            if not start <= stamp < end:
                continue
            painter.save()
            painter.setOpacity(item['opacity'])
            rect = QRectF(item['x'] * self.width, item['y'] * self.height,
                          item['w'] * self.width, item['h'] * self.height)
            painter.translate(rect.center())
            painter.rotate(item.get('rotation', 0))
            if item['kind'] == 'text' and 'text_style' in item:
                rect = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())
            else:
                painter.translate(-rect.width()/2, -rect.height()/2)
                rect = QRectF(0, 0, rect.width(), rect.height())
            if item['kind'] == 'image':
                if item['source'] not in self.images:
                    self.images[item['source']] = QImage(item['source'])
                picture = self.images[item['source']]
                if picture.isNull():
                    painter.end()
                    raise FileNotFoundError('贴纸图片无法读取：' + item['source'])
                if item.get('flip'):
                    painter.translate(rect.width(), 0)
                    painter.scale(-1, 1)
                painter.drawImage(rect, picture)
            elif 'text_style' in item:
                draw_text(painter, rect, {**item, **TEXT_DEFAULTS, **item['text_style']}, self.width / 1920)
            else:
                font = QFont(theme.danmaku_font_family())
                font.setPixelSize(max(8, round(item['font_size'] * self.width / 1920)))
                metrics = QFontMetrics(font)
                painter.setClipRect(rect)
                path = QPainterPath()
                for index, line in enumerate(item['text'].splitlines() or ['']):
                    path.addText(rect.left(), rect.top() + metrics.ascent() + index * metrics.lineSpacing(), font, line)
                painter.setPen(QPen(Qt.black, max(1, self.width / 960)))
                painter.setBrush(QColor(item['color']))
                painter.drawPath(path)
            painter.restore()
        painter.end()
        return image


def graphic_segment(job, ffmpeg, graphics, target, work, begin, span, fps):
    def produce():
        for frame in range(math.ceil(span*fps)):
            check_cancel(job)
            image = graphics.frame_at(begin+frame/fps)
            yield image.constBits().tobytes()
    run_process(job, [ffmpeg, '-y', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'bgra',
                      '-video_size', f'{graphics.width}x{graphics.height}', '-framerate', str(fps), '-i', 'pipe:0',
                      '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'bgra', str(target)], work, duration=span, frames=produce())


def render_project(job, ffmpeg, project, target, work, *, begin=0, length=None, preview=False, project_path=''):
    validate(project)
    total = duration(project)
    end = total if length is None else min(total, begin + length)
    span = end - begin
    if not project['clips'] or span <= 0:
        raise ValueError('请先在主轨添加有效的视频片段')
    target = Path(target)
    chat_target = target.with_suffix('.danmaku.json')
    subtitle_target = target.with_suffix('.srt')
    has_subtitles = bool(placed_cues(project))
    if target.exists() or chat_target.exists() or (has_subtitles and subtitle_target.exists()):
        raise FileExistsError('目标文件已存在，请使用新文件名')
    referenced = {row['source'] for row in project['clips'] + project['layers'] if row.get('source')}
    for source in referenced:
        info = project['media'][source]
        if not Path(info['path']).is_file():
            raise FileNotFoundError('来源文件缺失，请在工程文件列表重新定位：' + info['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    Path(work).mkdir(parents=True, exist_ok=True)
    width, height, fps = (project['render'][key] for key in ('width', 'height', 'fps'))
    if preview and width > 640:
        height, width = round(height * 640 / width / 2) * 2, 640
    width, height = width // 2 * 2, height // 2 * 2
    partial = target.with_name(f'.{target.stem}.{job.id}.partial.mp4')
    written = subtitle_written = published = False
    try:
        with tempfile.TemporaryDirectory(prefix='project-', dir=work) as temporary:
            folder = Path(temporary)
            inputs, filters, chain, events = [], [], [], []
            input_count = 0
            cuts = [(clip, max(start, begin), min(stop, end), start) for clip, start, stop in placed_clips(project)
                    if start < end and stop > begin]
            for index, (clip, start, stop, origin) in enumerate(cuts):
                check_cancel(job)
                job.message = f'准备片段 {index + 1}/{len(cuts)}'
                info = project['media'][clip['source']]
                speed = clip['speed']
                source_start = clip['start'] + (start - origin) * speed
                source_end = min(clip['end'], source_start + (stop - start) * speed)
                child = Job(job.title, 'export', cancel=job.cancel)
                burn = project.get('danmaku', False) and info.get('danmaku_path') and not info.get('chat_burned')
                result = export_clip(child, ffmpeg, info, source_start, source_end, 'precise', folder / f'cut-{index}.mp4', folder, bool(burn))
                stream = input_count
                inputs += ['-i', result['output']]
                input_count += 1
                segment_length = stop - start
                filters.append(f'[{stream}:v]setpts=(PTS-STARTPTS)/{speed},scale={width}:{height}:force_original_aspect_ratio=decrease,'
                               f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},trim=duration={segment_length:.6f}[v{index}]')
                if probe(job, ffmpeg, result['output'], folder)['has_audio']:
                    filters.append(f'[{stream}:a]asetpts=PTS-STARTPTS,{tempo(speed)},volume={clip["volume"]},'
                                   f'aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={segment_length:.6f}[a{index}]')
                else:
                    filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={segment_length:.6f}[a{index}]')
                chain += [f'[v{index}]', f'[a{index}]']
                if info.get('danmaku_path') and not info.get('chat_burned'):
                    for event in load_events(result):
                        row = dict(event, time=event['time'] / speed + start - begin)
                        if row.get('kind') == 'super_chat':
                            row['duration'] = row.get('duration', 60) / speed
                        events.append(row)
                job.progress = .35 * (index + 1) / max(1, len(cuts))
            main_end = max([stop for _, _, stop in placed_clips(project)] or [0])
            tail = end - max(begin, main_end)
            if tail > .001:
                index = len(cuts)
                filters += [f'color=c=black:s={width}x{height}:r={fps}:d={tail:.6f}[v{index}]',
                            f'anullsrc=r=48000:cl=stereo,atrim=duration={tail:.6f}[a{index}]']
                chain += [f'[v{index}]', f'[a{index}]']
            filters.append(''.join(chain) + f'concat=n={len(chain)//2}:v=1:a=1[base][mainaudio]')
            video, audio = 'base', ['[mainaudio]']
            layers = sorted(placed_layers(project), key=lambda row: row[0]['track'])
            visuals = [row for row in layers if row[0]['kind'] != 'audio' and row[1] < end and row[2] > begin]
            groups = {}
            for graphics, rows in groupby(visuals, key=lambda row: row[0]['kind'] != 'video'):
                rows = list(rows)
                if graphics:
                    groups[rows[0][0]['id']] = rows
            for item, start, stop in layers:
                if item['id'] in groups:
                    graphics = ProjectGraphics(project, width, height, groups[item['id']])
                    graphic_file = folder / (item['id'] + '-graphics.mkv')
                    job.message = '准备图文轨道'
                    graphic_segment(job, ffmpeg, graphics, graphic_file, work, begin, span, fps)
                    stream = input_count
                    input_count += 1
                    inputs += ['-i', str(graphic_file)]
                    name = f'graphics{stream}'
                    filters.append(f'[{video}][{stream}:v]overlay=eof_action=pass:repeatlast=0:format=rgb:alpha=premultiplied[{name}]')
                    video = name
                if item['kind'] not in ('video', 'audio') or stop <= begin or start >= end:
                    continue
                check_cancel(job)
                offset = max(start, begin) - begin
                segment_length = min(stop, end) - max(start, begin)
                source_in = item['in'] + (max(start, begin) - start) * item['speed']
                info = project['media'][item['source']]
                stream = input_count
                if item['kind'] == 'video':
                    child = Job(job.title, 'export', cancel=job.cancel)
                    result = export_clip(child, ffmpeg, info, source_in, source_in + segment_length * item['speed'],
                                         'precise', folder / (item['id'] + '.mp4'), folder)
                    inputs += ['-i', result['output']]
                else:
                    inputs += ['-ss', f'{source_in:.6f}', '-t', f'{segment_length * item["speed"]:.6f}', '-i', info['path']]
                input_count += 1
                if item['kind'] == 'video':
                    w, h = max(2, round(item['w'] * width / 2) * 2), max(2, round(item['h'] * height / 2) * 2)
                    rotation = math.radians(item.get('rotation', 0))
                    rotated_w = max(2, math.ceil(abs(w * math.cos(rotation)) + abs(h * math.sin(rotation)) - 1e-7))
                    rotated_h = max(2, math.ceil(abs(w * math.sin(rotation)) + abs(h * math.cos(rotation)) - 1e-7))
                    filters.append(f'[{stream}:v]setpts=(PTS-STARTPTS)/{item["speed"]}+{offset:.6f}/TB,scale={w}:{h},'
                                   f'setsar=1,format=rgba,rotate={rotation:.10f}:ow={rotated_w}:oh={rotated_h}:c=none,'
                                   f'colorchannelmixer=aa={item["opacity"]}[pip{stream}]')
                    name = f'picture{stream}'
                    x, y = round(item['x']*width + (w-rotated_w)/2), round(item['y']*height + (h-rotated_h)/2)
                    filters.append(f'[{video}][pip{stream}]overlay=x={x}:y={y}:format=rgb:'
                                   f'eof_action=pass:repeatlast=0:enable=\'gte(t,{offset:.6f})\'[{name}]')
                    video = name
                if info.get('has_audio'):
                    elapsed = max(start, begin)-start
                    fades = f',asetpts=PTS-STARTPTS+{elapsed:.6f}/TB'
                    if item.get('fade_in', 0) > 0:
                        fades += f',afade=t=in:d={item["fade_in"]:.6f}'
                    if item.get('fade_out', 0) > 0:
                        fades += f',afade=t=out:st={max(0, item["duration"]-item["fade_out"]):.6f}:d={item["fade_out"]:.6f}'
                    fades += ',asetpts=PTS-STARTPTS'
                    filters.append(f'[{stream}:a]asetpts=PTS-STARTPTS,{tempo(item["speed"])},volume={item["volume"]},'
                                   f'aresample=48000,aformat=channel_layouts=stereo,atrim=duration={segment_length:.6f}{fades},'
                                   f'adelay={round(offset*48000)}S:all=1[sound{stream}]')
                    audio.append(f'[sound{stream}]')
            filters.append(''.join(audio) + f'amix=inputs={len(audio)}:normalize=0:duration=longest,alimiter=limit=0.95:latency=1[audio]')
            if has_subtitles and project.get('burn_subtitles', True):
                subtitle_file = folder/'subtitles.mkv'
                job.message = '准备字幕轨道'
                graphic_segment(job, ffmpeg, SubtitleGraphics(project, width, height), subtitle_file, work, begin, span, fps)
                inputs += ['-i', str(subtitle_file)]
                filters.append(f'[{video}][{input_count}:v]overlay=eof_action=pass:repeatlast=0:format=rgb:alpha=premultiplied[captions]')
                video = 'captions'
            from .hardware_encoding import select_video_args
            encoding_args = select_video_args(job, ffmpeg, None if preview else project.get('encoding'), work, width, height)
            command = [ffmpeg, '-y', '-v', 'error', *inputs, '-filter_complex', ';'.join(filters),
                       '-map', f'[{video}]', '-map', '[audio]', '-t', f'{span:.6f}', *encoding_args,
                       '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(partial)]
            job.message = ('合成工程预览' if preview else '合成剪辑工程')+'（'+encoding_args[1]+'）'
            run_process(job, command, work, duration=span)
            info = probe(job, ffmpeg, partial, work)
            check_cancel(job)
            write_json(chat_target, sorted(events, key=lambda row: row['time']), video_burned=project.get('danmaku', False))
            written = True
            if has_subtitles and not preview:
                with subtitle_target.open('x', encoding='utf-8-sig') as handle:
                    handle.write(srt_text(project, begin, end))
                subtitle_written = True
            if target.exists():
                raise FileExistsError('目标文件已存在')
            if os.name == 'nt':
                partial.rename(target)
            else:
                os.link(partial, target)
            published = True
            job.output = str(target)
            return {'output': str(target), 'source': project_path, 'actual_duration': info['duration'],
                    'mode': 'project', 'encoder': encoding_args[1], 'danmaku_path': str(chat_target), 'chat_burned': project.get('danmaku', False),
                    'subtitle_path': str(subtitle_target) if subtitle_written else '',
                    'requested_start': begin, 'requested_end': end}
    finally:
        partial.unlink(missing_ok=True)
        if written and not published:
            chat_target.unlink(missing_ok=True)
        if subtitle_written and not published:
            subtitle_target.unlink(missing_ok=True)
