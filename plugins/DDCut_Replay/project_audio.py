"""Stream the project's mixed PCM to libmpv; its audio clock drives preview."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from .hls import input_options
from .project import duration, placed_clips, placed_layers
from .project_media import tempo


def audio_signature(project):
    voices = []
    for item, start, end in placed_clips(project):
        voices.append((item['source'], start, end, item['start'], item['speed'], item['volume'], 0, 0))
    for item, start, end in placed_layers(project):
        if item['kind'] in ('audio', 'video'):
            voices.append((item['source'], start, end, item['in'], item['speed'], item['volume'], item['fade_in'], item['fade_out']))
    return json.dumps([duration(project), voices, {key: info.get('has_audio') for key, info in project['media'].items()}], sort_keys=True)


def audio_command(ffmpeg, project, begin):
    span = max(0, duration(project)-begin)
    filters = [f'anullsrc=r=48000:cl=stereo,atrim=duration={span:.9f}[silence]']
    inputs, sounds = [], ['[silence]']
    voices = [(item, start, end, item['start']) for item, start, end in placed_clips(project)]
    voices += [(item, start, end, item['in']) for item, start, end in placed_layers(project) if item['kind'] in ('audio', 'video')]
    for item, start, end, source_in in voices:
        info = project['media'][item['source']]
        if end <= begin or not info.get('has_audio') or not item['volume']:
            continue
        elapsed, length = max(start, begin)-start, end-max(start, begin)
        source_in += elapsed*item['speed']
        stream = len(sounds)-1
        inputs += ['-ss', f'{source_in:.9f}', '-t', f'{length*item["speed"]:.9f}',
                   *(input_options(info['path']) if info.get('index_format') == 'hls' else ['-i', info['path']])]
        fades = f',asetpts=PTS-STARTPTS+{elapsed:.9f}/TB'
        if item.get('fade_in', 0):
            fades += f',afade=t=in:d={item["fade_in"]:.9f}'
        if item.get('fade_out', 0):
            fades += f',afade=t=out:st={max(0,item["duration"]-item["fade_out"]):.9f}:d={item["fade_out"]:.9f}'
        filters.append(f'[{stream}:a]asetpts=PTS-STARTPTS,{tempo(item["speed"])},volume={item["volume"]},'
                       f'aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={length:.9f}'
                       f'{fades},asetpts=PTS-STARTPTS,adelay={round((max(start,begin)-begin)*48000)}S:all=1[a{stream}]')
        sounds.append(f'[a{stream}]')
    filters.append(''.join(sounds)+f'amix=inputs={len(sounds)}:normalize=0:duration=first,alimiter=limit=0.95:latency=1[mixed]')
    return [ffmpeg, '-hide_banner', '-v', 'error', *inputs, '-filter_complex_threads', '1',
            '-filter_complex', ';'.join(filters), '-map', '[mixed]', '-t', f'{span:.9f}',
            '-ar', '48000', '-ac', '2', '-f', 's16le', 'pipe:1']


class AudioPipe:
    def __init__(self, command, logfile, size):
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        self.log = tempfile.TemporaryFile(dir=Path(logfile).parent)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self.log,
                bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except Exception:
            self.log.close()
            raise
        self.size = size

    def read(self, size):
        return self.process.stdout.read(size)

    def cancel(self):
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass

    def close(self):
        self.cancel()

    def dispose(self):
        self.cancel()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
        self.log.close()


class AudioClock:
    # Tests select the null audio device; production follows the host's normal audio output.
    output_options = {}

    def __init__(self, ffmpeg, project, begin, work, muted=True):
        from ddm.player import EMBEDDED_OPTIONS
        import mpv
        self.begin, self.end = begin, duration(project)
        self.started = time.monotonic()
        self.pipe = AudioPipe(audio_command(ffmpeg, project, begin), Path(work)/(uuid.uuid4().hex+'.pcm.log'),
                              round((self.end-begin)*48000)*4)
        self.core = None
        try:
            self.core = mpv.MPV(**EMBEDDED_OPTIONS, vo='null', video=False, pause=True, mute=muted, volume=50,
                keep_open=True, demuxer='rawaudio', demuxer_rawaudio_format='s16le',
                demuxer_rawaudio_rate=48000, demuxer_rawaudio_channels='stereo', **self.output_options)
            self.core.register_stream_protocol('ddcutpcm', lambda uri: self.pipe)
            self.core.play('ddcutpcm://preview')
        except Exception:
            self.close()
            raise

    @property
    def ready(self):
        return self.core is not None and self.core.time_pos is not None

    @property
    def position(self):
        return min(self.end, self.begin+float(self.core.time_pos or 0)) if self.core else self.begin

    def error(self):
        if self.pipe.process.poll() not in (None, 0):
            self.pipe.log.seek(0)
            return self.pipe.log.read()[-1200:].decode('utf-8', errors='replace')
        if not self.ready and time.monotonic()-self.started > 15:
            return '无法启动音频预览，请检查音频输出设备或来源文件'
        return ''

    def close(self):
        self.pipe.cancel()
        if self.core:
            self.core.terminate()
            self.core = None
        self.pipe.dispose()
