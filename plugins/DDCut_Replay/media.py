"""Local-only media jobs. Workers never touch Qt objects or host sessions."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from .hls import input_options, write_playlist

MEDIA_SUFFIXES = {'.mp4', '.mkv', '.mov', '.ts', '.flv', '.webm'}
ACTIVE = {'queued', 'running', 'cancelling'}


class Cancelled(Exception):
    pass


@dataclass
class Job:
    title: str
    kind: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = 'queued'
    progress: float = 0
    message: str = ''
    output: str = ''
    result: object = None
    cancel: threading.Event = field(default_factory=threading.Event)
    future: object = None
    lease: str = ''
    delivered: bool = False

    def record(self):
        data = {key: getattr(self, key) for key in
                ('id', 'title', 'kind', 'state', 'progress', 'message', 'output')}
        if self.kind == 'export' and self.state == 'done':
            data['result'] = self.result
        return data


def check_cancel(job):
    if job.cancel.is_set():
        raise Cancelled()


def run_process(job, command, folder, *, duration=0, capture=False):
    """Drain into files, support cancellation, and always reap the child."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / f'{uuid.uuid4().hex}.log'
    progress_file = log.with_suffix('.progress')
    args = list(command)
    if duration:
        args = args[:-1] + ['-progress', str(progress_file), '-nostats', args[-1]]
    flags = (subprocess.CREATE_NO_WINDOW | 0x00004000) if os.name == 'nt' else 0
    try:
        check_cancel(job)
        with log.open('wb') as handle:
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                       stdout=handle, stderr=handle, creationflags=flags)
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if job.cancel.wait(.1):
                        raise Cancelled()
                    if capture and time.monotonic() - started > 30:
                        raise RuntimeError('媒体探测超时')
                    if duration and progress_file.exists():
                        lines = progress_file.read_text(encoding='utf-8', errors='replace').splitlines()
                        values = [line.split('=', 1)[1] for line in lines if line.startswith('out_time_us=')]
                        if values and values[-1].lstrip('-').isdigit():
                            job.progress = min(.98, max(0, int(values[-1]) / 1_000_000 / duration))
                check_cancel(job)
                if process.returncode:
                    detail = log.read_text(encoding='utf-8', errors='replace')[-1800:]
                    raise RuntimeError(f'媒体处理失败 ({process.returncode})：{detail}')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        return log.read_text(encoding='utf-8', errors='replace') if capture else ''
    finally:
        log.unlink(missing_ok=True)
        progress_file.unlink(missing_ok=True)


def probe(job, ffmpeg, path, work):
    sibling = Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    executable = str(sibling) if sibling.is_file() else shutil.which('ffprobe')
    if not executable:
        raise RuntimeError('未找到 ffprobe，请将它放在 FFmpeg 同目录')
    data = json.loads(run_process(job, [executable, '-v', 'error', '-show_format',
        '-show_streams', '-of', 'json', str(path)], work, capture=True))
    streams = data.get('streams', [])
    if not any(stream.get('codec_type') == 'video' for stream in streams):
        raise RuntimeError('文件中没有可回看的视频轨道')
    duration = float(data.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError('无法确定媒体时长，请先确认文件已写入完成')
    return {'duration': duration,
            'video_codec': next(stream.get('codec_name', '') for stream in streams
                                if stream.get('codec_type') == 'video'),
            'has_audio': any(stream.get('codec_type') == 'audio' for stream in streams),
            'stream_signature': [[stream.get(key) for key in
                                  ('codec_type', 'codec_name', 'width', 'height', 'sample_rate', 'channels')]
                                 for stream in streams if stream.get('codec_type') in {'video', 'audio'}]}


def scan_directory(job, directory):
    root = Path(directory)
    if not directory or not root.is_dir():
        return []
    result = []
    # Only the selected recording directory and its replays; no account/profile scan.
    for folder in (root, root / 'replays'):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            check_cancel(job)
            if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES and '.partial.' not in path.name:
                info = path.stat()
                entry = {'path': str(path.resolve()), 'title': path.stem, 'created_at': info.st_mtime, 'size': info.st_size}
                metadata = path.with_suffix(path.suffix + '.recording.json')
                if metadata.is_file():
                    try:
                        entry.update(json.loads(metadata.read_text(encoding='utf-8')))
                    except (OSError, ValueError):
                        pass
                result.append(entry)
    archives = root / 'DDCut_Replay'
    if archives.is_dir():
        for metadata in archives.glob('*/recording.json'):
            check_cancel(job)
            try:
                entry = json.loads(metadata.read_text(encoding='utf-8'))
                if entry.get('recording') and Path(entry['path']).is_file():
                    result.append(entry)
            except (OSError, ValueError, KeyError):
                continue
    return sorted(result, key=lambda entry: entry['created_at'], reverse=True)


def thumbnail(job, ffmpeg, path, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (job.id + '.png')
    run_process(job, [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(path),
        '-frames:v', '1', '-vf', 'scale=320:-2', str(target)], directory)
    return str(target)


def prepare_snapshot(job, ffmpeg, snapshot, destination, work):
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    if snapshot.get('index_format') == 'hls':
        segments = snapshot['segments']
        source = {**snapshot, 'path': str(Path(segments[0]['path']).with_name('playlist.m3u8')),
                  'duration': sum(segment['duration'] for segment in segments)}
        result = folder / f'review-{job.id}.mkv'
        export_clip(job, ffmpeg, source, 0, source['duration'], 'copy', result, work)
        return {'path': str(result), 'title': snapshot['room'].get('uname') or '录制快照',
                'room': snapshot['room'], 'session_id': snapshot['session_id'],
                'created_at': time.time(), 'source_created_at': snapshot['created_at'],
                'snapshot_at': snapshot['snapshot_at'], 'kind': 'snapshot',
                **probe(job, ffmpeg, result, work)}
    parts = [Path(part) for part in snapshot['parts']]
    needed = sum(path.stat().st_size for path in parts)
    if shutil.disk_usage(folder).free < needed + 256 * 1024 * 1024:
        raise RuntimeError('回看副本空间不足，请清理录制磁盘后重试')
    signatures = [probe(job, ffmpeg, part, work)['stream_signature'] for part in parts]
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise RuntimeError('分段的编码、画面大小或声道不同，不能直接拼接。请停止并保存录制后分别导入成品。')
    result = folder / f'review-{job.id}.mkv'
    partial = result.with_name(f'{result.stem}.partial.mkv')
    playlist = folder / f'{job.id}.concat'
    try:
        playlist.write_text(''.join("file '" + str(path.resolve()).replace('\\', '/').replace("'", "'\\''") + "'\n"
                                    for path in parts), encoding='utf-8')
        run_process(job, [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'concat', '-safe', '0', '-i', str(playlist), '-map', '0:v:0',
            '-map', '0:a?', '-c', 'copy', str(partial)], work)
        info = probe(job, ffmpeg, partial, work)
        check_cancel(job)
        # Unique output: Path.rename also refuses an existing target on Windows.
        partial.rename(result)
        return {'path': str(result), 'title': snapshot['room'].get('uname') or '录制快照',
                'room': snapshot['room'], 'session_id': snapshot['session_id'],
                'created_at': time.time(), 'source_created_at': snapshot['created_at'],
                'snapshot_at': snapshot['snapshot_at'], 'kind': 'snapshot', **info}
    finally:
        partial.unlink(missing_ok=True)
        playlist.unlink(missing_ok=True)


def export_clip(job, ffmpeg, source, start, end, mode, target, work):
    duration = float(source['duration'])
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + .001):
        raise ValueError('选区必须在可读范围内，且出点晚于入点')
    if mode not in {'copy', 'precise'}:
        raise ValueError('Unknown export mode')
    target = Path(target)
    if target.exists():
        raise FileExistsError('目标已存在，请使用新文件名')
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == 'copy' and target.suffix.lower() != '.mkv':
        raise ValueError('快速复制使用 MKV 容器，以保留原始编码')
    if mode == 'precise' and target.suffix.lower() != '.mp4':
        raise ValueError('精确剪辑使用 MP4 容器')
    partial = target.with_name(f'.{target.stem}.{job.id}.partial{target.suffix}')
    playlist = Path(work) / f'{job.id}.ffconcat'
    try:
        input_args = ['-i', source['path']]
        local_start = start
        if source.get('segments'):
            # Freeze the readable range at submission; a growing recording must
            # neither extend this export nor pull in its still-open last file.
            playlist.parent.mkdir(parents=True, exist_ok=True)
            lines = ['ffconcat version 1.0']
            offset = 0
            selected = []
            for segment in source['segments']:
                next_offset = offset + segment['duration']
                if next_offset > start and offset < end:
                    if not selected:
                        local_start = start - offset
                    selected.append(segment)
                offset = next_offset
            if source.get('index_format') == 'hls':
                # Keep relative media/init URIs next to their recording. Only
                # this tiny VOD input is temporary; no recording is copied.
                playlist = Path(source['path']).with_name(f'.clip-{job.id}.m3u8')
                write_playlist(playlist, selected, complete=True)
                input_args = input_options(playlist)
            else:
                # Read-only compatibility with the preceding EDL archives.
                for segment in selected:
                    escaped = str(Path(segment['path']).resolve()).replace('\\', '/').replace("'", "'\\''")
                    lines += [f"file '{escaped}'", f"duration {segment['duration']:.6f}"]
                playlist.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                input_args = ['-f', 'concat', '-safe', '0', '-i', str(playlist)]
        # Input seeking with copy is intentionally keyframe-based; precise decodes to the requested point.
        command = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error']
        if source.get('segments') and mode == 'precise':
            # Decode only the intersecting segments, then trim. Input seeking
            # through concat can retain a TS timestamp offset on video-only
            # material and silently shorten the requested range.
            command += [*input_args, '-ss', f'{local_start:.6f}']
        else:
            command += ['-ss', f'{local_start:.6f}', *input_args]
        command += ['-t', f'{end-start:.6f}', '-map', '0:v:0', '-map', '0:a?']
        command += (['-c', 'copy', '-avoid_negative_ts', 'make_zero'] if mode == 'copy' else
                    ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-c:a', 'aac', '-movflags', '+faststart'])
        command += [str(partial)]
        run_process(job, command, work, duration=end-start)
        actual = probe(job, ffmpeg, partial, work)
        check_cancel(job)
        if target.exists():
            raise FileExistsError('目标已存在，请使用新文件名')
        # Windows rename is atomic and refuses existing targets, including on exFAT.
        if os.name == 'nt':
            partial.rename(target)
        else:
            os.link(partial, target)
        job.output = str(target)
        return {'requested_start': start, 'requested_end': end,
                'actual_duration': actual['duration'], 'mode': mode,
                'actual_start': start if mode == 'precise' else None,
                **{key: source[key] for key in ('cover_path', 'avatar_path') if source.get(key)},
                'room': source.get('room'), 'source_created_at': source.get('source_created_at'),
                'snapshot_at': source.get('snapshot_at'),
                'source': source['path'], 'output': str(target)}
    finally:
        partial.unlink(missing_ok=True)
        playlist.unlink(missing_ok=True)


class TaskHub:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / 'state.json'
        self.entries = []
        self.jobs = []
        self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ddcut')
        try:
            saved = json.loads(self.state_file.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            saved = {}
        self.preferences = saved.get('preferences', {})
        self.entries = saved.get('entries', [])
        for data in saved.get('jobs', [])[-100:]:
            job = Job(title=data['title'], kind=data['kind'])
            for key in ('id', 'state', 'progress', 'message', 'output', 'result'):
                if key in data:
                    setattr(job, key, data[key])
            if job.state in ACTIVE:
                job.state, job.message = 'interrupted', '上次运行已中断，请重新提交'
            job.delivered = True
            self.jobs.append(job)
        for job in self.jobs:
            if job.kind == 'export' and job.state == 'done':
                self.add_export(job)

    def save(self):
        data = {'preferences': self.preferences, 'entries': self.entries, 'jobs': [job.record() for job in self.jobs[-100:]]}
        temporary = self.state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.state_file)

    def add_entry(self, entry):
        existing = next((item for item in self.entries if item['path'] == entry['path']), None)
        if existing is None:
            self.entries.insert(0, entry)
        else:
            existing.update(entry)
        self.save()

    def add_export(self, job):
        path = Path(job.output)
        if job.output and job.result and path.is_file():
            self.add_entry({'path': str(path), 'title': path.stem, 'kind': 'clip',
                'duration': job.result['actual_duration'], 'created_at': path.stat().st_mtime,
                'source': job.result['source'], 'room': job.result.get('room') or {},
                **{key: job.result[key] for key in ('cover_path', 'avatar_path') if job.result.get(key)}})

    def submit(self, title, kind, function, *args, lease=''):
        if self.closed:
            raise RuntimeError('任务服务已停止')
        job = Job(title=title, kind=kind, lease=lease)
        self.jobs.append(job)
        self.save()

        def execute():
            try:
                check_cancel(job)
                job.state = 'running'
                job.result = function(job, *args)
                job.progress, job.state = 1, 'done'
            except Cancelled:
                job.state, job.message = 'cancelled', '已取消；已有成品保留'
            except Exception as error:
                job.state, job.message = 'failed', str(error)

        job.future = self.pool.submit(execute)
        return job

    def cancel(self, job):
        if job.state in ACTIVE:
            job.state = 'cancelling'
            job.cancel.set()

    def close(self):
        if self.closed:
            return
        self.closed = True
        for job in self.jobs:
            self.cancel(job)
        self.pool.shutdown(wait=True)
        for job in self.jobs:
            if job.state == 'done' and not job.delivered and job.kind in {'snapshot', 'import'}:
                self.add_entry(job.result)
            elif job.state == 'done' and not job.delivered and job.kind == 'export':
                self.add_export(job)
        self.save()
