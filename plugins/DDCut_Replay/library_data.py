"""Archive dates, physical file accounting and explicit file deletion plans."""
from datetime import datetime
import json
from pathlib import Path
import shutil

from .media import check_cancel


def identity(path):
    return str(Path(path).resolve()).casefold()


def interval(entry):
    start = float(entry.get('created_at') or 0)
    end = float(entry.get('ended_at') or start + float(entry.get('duration') or 0))
    return start, max(start, end)


def on_date(entry, day):
    start, end = interval(entry)
    if not start:
        return False
    begin = datetime.combine(day, datetime.min.time()).timestamp()
    from datetime import timedelta
    finish = datetime.combine(day + timedelta(days=1), datetime.min.time()).timestamp()
    return start < finish and (end > begin or start == begin)


def archive_files(entry):
    """HLS ownership is verified against its recording metadata before any traversal."""
    path = Path(entry['path']).resolve()
    if entry.get('index_format') == 'hls' or path.suffix.lower() == '.m3u8':
        folder = path.parent
        metadata = folder / 'recording.json'
        if folder.parent.name != 'DDCut_Replay' or not metadata.is_file():
            raise ValueError('此HLS不是本插件归档，请在外部文件管理器管理')
        saved = json.loads(metadata.read_text(encoding='utf-8'))
        if identity(saved['path']) != identity(path):
            raise ValueError('归档清单与录像路径不一致')
        files = []
        for item in folder.rglob('*'):
            if item.is_symlink() or not item.resolve().is_relative_to(folder):
                raise ValueError('归档包含外部链接，不能批量删除')
            if item.is_file():
                files.append(item)
        return files
    files = [path]
    for sidecar in (path.with_suffix(path.suffix + '.recording.json'), path.with_suffix('.danmaku.json')):
        if sidecar.is_file():
            files.append(sidecar)
    pictures = path.with_suffix('.danmaku.images')
    if pictures.is_dir():
        for item in pictures.rglob('*'):
            if item.is_symlink() or not item.resolve().is_relative_to(pictures.resolve()):
                raise ValueError('弹幕图片目录包含外部链接')
            if item.is_file():
                files.append(item)
    return files


def collect_stats(job, entries, directory):
    seen, categories, rows, errors = set(), {}, {}, []
    for entry in entries:
        check_cancel(job)
        kind = ('buffer' if entry.get('_buffer') else 'clip' if entry.get('kind') == 'clip'
                else 'cache' if entry.get('index_format') == 'hls' else 'recording')
        group = categories.setdefault(kind, {'bytes': 0, 'duration': 0, 'unknown': 0, 'count': 0})
        group['count'] += 1
        duration = float(entry.get('duration') or 0)
        group['duration'] += duration
        group['unknown'] += not bool(duration)
        size, missing = 0, False
        try:
            files = archive_files(entry)
            for path in files:
                check_cancel(job)
                try:
                    length = path.stat().st_size
                except FileNotFoundError:
                    missing = True
                    continue
                size += length
                key = identity(path)
                if key not in seen:
                    group['bytes'] += length
                    seen.add(key)
        except (OSError, ValueError, KeyError) as error:
            errors.append(f'{entry["path"]}: {error}')
            missing = True
        rows[identity(entry['path'])] = {'bytes': size, 'missing': missing}
    disk = None
    if directory and Path(directory).is_dir():
        usage = shutil.disk_usage(directory)
        disk = {'total': usage.total, 'free': usage.free}
    return {'categories': categories, 'entries': rows, 'disk': disk, 'errors': errors}


def deletion_plan(entries, all_entries, protected=(), references=()):
    protected = {identity(path) for path in protected}
    references = {identity(path) for path in references}
    chosen = {identity(entry['path']) for entry in entries}
    other_files = set()
    for entry in all_entries:
        if identity(entry['path']) not in chosen:
            try:
                other_files.update(identity(path) for path in archive_files(entry))
            except (OSError, ValueError, KeyError):
                other_files.add(identity(entry['path']))
    plans = []
    for entry in entries:
        key = identity(entry['path'])
        files = archive_files(entry)
        keys = {identity(path) for path in files}
        if key in protected or keys & protected:
            raise ValueError('所选录像仍在录制、缓存或回看中，请先停止或返回录像库')
        if keys & references or key in references:
            raise ValueError('所选文件仍被剪辑工程引用，请先在工程中移除引用或另存源文件')
        if keys & other_files:
            raise ValueError('所选录像与其他记录共享文件，不能批量删除')
        plans.append({'path': entry['path'], 'files': [str(path) for path in files]})
    return plans


def delete_files(job, plans):
    result = {'removed': [], 'errors': []}
    job.result = result
    for plan in plans:
        check_cancel(job)
        try:
            for name in plan['files']:
                # Finish an archive once started, so cancelling never forgets a half-deleted entry.
                Path(name).unlink(missing_ok=True)
            result['removed'].append(plan['path'])
        except OSError as error:
            result['errors'].append(f'{plan["path"]}: {error}')
    return result


def human_size(value):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}' if unit != 'B' else f'{value} B'
        value /= 1024
