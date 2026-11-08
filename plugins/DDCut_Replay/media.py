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
import tempfile

from .hls import input_options, write_playlist
from .chat import load_events, trim_events, write_json

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
        if self.kind in {'export', 'library_delete'} and self.result:
            data['result'] = self.result
        return data


def check_cancel(job):
    if job.cancel.is_set():
        raise Cancelled()


def run_process(job, command, folder, *, duration=0, capture=False, frames=None):
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
            process = subprocess.Popen(args, stdin=subprocess.PIPE if frames is not None else subprocess.DEVNULL,
                                       stdout=handle, stderr=handle, creationflags=flags)
            writer, errors = None, []
            if frames is not None:
                def feed():
                    try:
                        for frame in frames:
                            check_cancel(job)
                            process.stdin.write(frame)
                    except Exception as error:
                        errors.append(error)
                    finally:
                        try:
                            process.stdin.close()
                        except OSError:
                            pass
                writer = threading.Thread(target=feed, name='ddcut-chat-frames', daemon=True)
                writer.start()
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
                if writer is not None:
                    writer.join()
            if errors:
                raise errors[0]
        return log.read_text(encoding='utf-8', errors='replace') if capture else ''
    finally:
        log.unlink(missing_ok=True)
        progress_file.unlink(missing_ok=True)


def probe(job, ffmpeg, path, work, *, input_args=None):
    sibling = Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    executable = str(sibling) if sibling.is_file() else shutil.which('ffprobe')
    if not executable:
        raise RuntimeError('未找到 ffprobe，请将它放在 FFmpeg 同目录')
    data = json.loads(run_process(job, [executable, '-v', 'error', '-show_format',
        '-show_streams', '-of', 'json', *(input_args if input_args is not None else [str(path)])], work, capture=True))
    streams = data.get('streams', [])
    if not any(stream.get('codec_type') == 'video' for stream in streams):
        raise RuntimeError('文件中没有可回看的视频轨道')
    duration = float(data.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError('无法确定媒体时长，请先确认文件已写入完成')
    video = next(stream for stream in streams if stream.get('codec_type') == 'video')
    rotation = next((side['rotation'] for side in video.get('side_data_list', []) if 'rotation' in side),
                    video.get('tags', {}).get('rotate', 0))
    return {'duration': duration,
            'video_width': video.get('width'), 'video_height': video.get('height'),
            'frame_rate': video.get('avg_frame_rate') if video.get('avg_frame_rate') not in (None, '0/0', '0/1') else video.get('r_frame_rate'),
            'sample_aspect_ratio': video.get('sample_aspect_ratio'), 'rotation': rotation,
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
                entry = {'path': str(path.resolve()), 'title': path.stem, 'created_at': info.st_mtime, 'size': info.st_size,
                         'kind': 'replay' if folder == root / 'replays' else 'recording'}
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


def thumbnail(job, ffmpeg, path, directory, *, input_args=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (job.id + '.png')
    inputs = input_args if input_args is not None else ['-i', str(path)]
    run_process(job, [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', *inputs,
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


def export_clip(job, ffmpeg, source, start, end, mode, target, work, burn=False, encoding=None):
    from .encoding import normalize, video_args
    encoding = normalize(encoding)
    duration = float(source['duration'])
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + .001):
        raise ValueError('选区必须在可读范围内，且出点晚于入点')
    if mode not in {'copy', 'precise'}:
        raise ValueError('Unknown export mode')
    if burn and (mode != 'precise' or not source.get('danmaku_path')):
        raise ValueError('弹幕压制需要弹幕记录并使用精确切片模式')
    target = Path(target)
    chat_target = target.with_suffix('.danmaku.json')
    if target.exists() or chat_target.exists():
        raise FileExistsError('目标已存在，请使用新文件名')
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == 'copy' and target.suffix.lower() != '.mkv':
        raise ValueError('快速复制使用 MKV 容器，以保留原始编码')
    if mode == 'precise' and target.suffix.lower() != '.mp4':
        raise ValueError('精确剪辑使用 MP4 容器')
    partial = target.with_name(f'.{target.stem}.{job.id}.partial{target.suffix}')
    playlist = Path(work) / f'{job.id}.ffconcat'
    chat_written = published = False
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
                    [*video_args(encoding), '-c:a', 'aac', '-movflags', '+faststart'])
        command += [str(partial)]
        if burn:
            from .burn import encode_chat
            encode_chat(job, ffmpeg, source, start, end, local_start, input_args, partial, work, encoding)
        else:
            run_process(job, command, work, duration=end-start)
        actual = probe(job, ffmpeg, partial, work)
        check_cancel(job)
        chat = {}
        if source.get('danmaku_path'):
            write_json(chat_target, trim_events(load_events(source), start, end, shift=source.get('danmaku_offset', 0)),
                       video_burned=burn or source.get('chat_burned', False))
            chat_written = True
            chat = {'danmaku_path': str(chat_target)}
        if target.exists():
            raise FileExistsError('目标已存在，请使用新文件名')
        # Windows rename is atomic and refuses existing targets, including on exFAT.
        if os.name == 'nt':
            partial.rename(target)
        else:
            os.link(partial, target)
        published = True
        job.output = str(target)
        return {'requested_start': start, 'requested_end': end, **chat, 'chat_burned': burn or source.get('chat_burned', False),
                'actual_duration': actual['duration'], 'mode': mode,
                'actual_start': start if mode == 'precise' else None,
                **{key: source[key] for key in ('cover_path', 'avatar_path') if source.get(key)},
                'room': source.get('room'), 'source_created_at': source.get('source_created_at'),
                'snapshot_at': source.get('snapshot_at'),
                'source': source['path'], 'output': str(target)}
    finally:
        if chat_written and not published:
            chat_target.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        playlist.unlink(missing_ok=True)


def export_ranges(job, ffmpeg, source, ranges, mode, targets, work, merge=False, burn=False, encoding=None):
    """Keep completed separate outputs on cancel; commit a merge only when complete."""
    targets = list(map(Path, targets))
    if not ranges or len(targets) != (1 if merge else len(ranges)):
        raise ValueError('选区与目标文件数量不匹配')
    if merge and (mode != 'precise' or targets[0].suffix.lower() != '.mp4'):
        raise ValueError('合并选区使用精确切片模式')
    for item in ranges:
        if not 0 <= item['start'] < item['end'] <= source['duration'] + .001:
            raise ValueError('所有启用选区都必须在可读范围内，且出点晚于入点')
    if len(set(targets)) != len(targets) or any(path.exists() or path.with_suffix('.danmaku.json').exists() for path in targets):
        raise FileExistsError('目标已存在或文件名重复，请使用新文件名')
    Path(work).mkdir(parents=True, exist_ok=True)
    job.result = {'outputs': []}
    with tempfile.TemporaryDirectory(prefix='ranges-', dir=work) as temporary:
        folder = Path(temporary)
        completed = []
        for index, item in enumerate(ranges):
            check_cancel(job)
            job.message = f'正在导出 {index + 1}/{len(ranges)}'
            child = Job(job.title, 'export', cancel=job.cancel)
            destination = folder / f'{index}.mp4' if merge else targets[index]
            result = export_clip(child, ffmpeg, source, item['start'], item['end'], mode, destination, folder, burn, encoding)
            completed.append(result)
            if not merge:
                job.result['outputs'].append(result)
                job.output = result['output']
            job.progress = (index + 1) / (len(ranges) + (1 if merge else 0))
        if merge:
            target = targets[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(f'.{target.stem}.{job.id}.partial.mp4')
            chat_target = target.with_suffix('.danmaku.json')
            chat_written = published = False
            listing = folder / 'merge.ffconcat'
            listing.write_text('ffconcat version 1.0\n' + ''.join(f"file '{i}.mp4'\n" for i in range(len(completed))), encoding='utf-8')
            try:
                duration = sum(result['actual_duration'] for result in completed)
                run_process(job, [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat',
                    '-safe', '0', '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(partial)], folder, duration=duration)
                actual = probe(job, ffmpeg, partial, folder)
                check_cancel(job)
                result = {**completed[0], 'output': str(target), 'actual_duration': actual['duration'],
                          'ranges': ranges, 'mode': 'precise-merge'}
                result.pop('danmaku_path', None)
                if source.get('danmaku_path'):
                    events, offset = [], 0
                    for part in completed:
                        events.extend(dict(row, time=row['time'] + offset) for row in load_events(part))
                        offset += part['actual_duration']
                    write_json(chat_target, events, video_burned=burn or source.get('chat_burned', False))
                    chat_written = True
                    result['danmaku_path'] = str(chat_target)
                check_cancel(job)
                if target.exists():
                    raise FileExistsError('目标已存在，请使用新文件名')
                if os.name == 'nt':
                    partial.rename(target)
                else:
                    os.link(partial, target)
                published = True
                job.result['outputs'].append(result)
                job.output = str(target)
            finally:
                if chat_written and not published:
                    chat_target.unlink(missing_ok=True)
                partial.unlink(missing_ok=True)
    return job.result


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
            if job.kind == 'library_delete' and job.result:
                self.apply_library_delete(job)
            if job.kind == 'export' and job.result:
                self.add_export(job)

    def save(self):
        data = {'preferences': self.preferences, 'entries': self.entries, 'jobs': [job.record() for job in self.jobs[-100:]]}
        temporary = self.state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.state_file)

    def add_entry(self, entry):
        from .library_data import identity
        forgotten = self.preferences.get('library_hidden', [])
        self.preferences['library_hidden'] = [path for path in forgotten if path != identity(entry['path'])]
        existing = next((item for item in self.entries if item['path'] == entry['path']), None)
        if existing is None:
            self.entries.insert(0, entry)
        else:
            existing.update(entry)
        self.save()

    def is_forgotten(self, path):
        from .library_data import identity
        return identity(path) in self.preferences.get('library_hidden', [])

    def forget_entries(self, paths):
        from .library_data import identity
        keys = {identity(path) for path in paths}
        hidden = set(self.preferences.get('library_hidden', [])) | keys
        self.preferences['library_hidden'] = sorted(hidden)
        self.entries[:] = [entry for entry in self.entries if identity(entry['path']) not in keys]
        self.save()

    def apply_library_delete(self, job):
        if job.result and not job.result.get('indexed'):
            self.forget_entries(job.result.get('removed', []))
            job.result['indexed'] = True
            self.save()

    def add_export(self, job, *, restore=False):
        if not job.result:
            return
        for result in job.result.get('outputs', [job.result]):
            path = Path(result.get('output') or job.output)
            if path.is_file() and (restore or not self.is_forgotten(path)):
                self.add_entry({'path': str(path), 'title': path.stem, 'kind': 'clip',
                    'duration': result['actual_duration'], 'created_at': path.stat().st_mtime,
                    'chat_burned': result.get('chat_burned', False),
                    'source': result['source'], 'room': result.get('room') or {},
                    **{key: result[key] for key in ('cover_path', 'avatar_path', 'danmaku_path') if result.get(key)}})

    def submit(self, title, kind, function, *args, lease='', **kwargs):
        if self.closed:
            raise RuntimeError('任务服务已停止')
        job = Job(title=title, kind=kind, lease=lease)
        self.jobs.append(job)
        self.save()

        def execute():
            try:
                check_cancel(job)
                job.state = 'running'
                job.result = function(job, *args, **kwargs)
                job.progress, job.state = 1, 'done'
            except Cancelled:
                job.state = 'cancelled'
                job.message = '已取消；尚未处理的录像保留' if kind == 'library_delete' else '已取消；已有成品保留'
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
            if job.kind == 'library_delete' and job.result:
                self.apply_library_delete(job)
            if job.state == 'done' and not job.delivered and job.kind in {'snapshot', 'import'}:
                self.add_entry(job.result)
            elif job.result and not job.delivered and job.kind == 'export':
                self.add_export(job, restore=True)
        self.save()
