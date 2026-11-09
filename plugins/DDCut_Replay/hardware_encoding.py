"""Probe actual encoder initialization in a worker before selecting hardware."""
from pathlib import Path
import shutil

from .encoding import normalize, video_args
from .media import check_cancel, run_process


_cache = {}


def select_video_args(job, ffmpeg, value, work, width, height):
    value = normalize(value)
    if value['backend'] == 'software':
        return video_args(value)
    executable = Path(shutil.which(ffmpeg) or ffmpeg).resolve()
    stat = executable.stat()
    key = (str(executable), stat.st_mtime_ns, stat.st_size, tuple(sorted(value.items())), width, height)
    check_cancel(job)
    if key not in _cache:
        job.message = '检测硬件编码器'
        supported = run_process(job, [ffmpeg, '-hide_banner', '-encoders'], work, capture=True)
        names = {line.split()[1] for line in supported.splitlines() if len(line.split()) >= 2}
        prefix = 'h264' if value['codec'] == 'h264' else 'hevc'
        selected = None
        for backend in ('nvenc', 'videotoolbox', 'qsv', 'amf'):
            candidate = prefix+'_'+backend
            if candidate not in names:
                continue
            job.message = '试编码：'+candidate
            try:
                run_process(job, [ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=size={width}x{height}:rate=30', '-frames:v', '2',
                    *video_args(value, candidate), '-pix_fmt', 'yuv420p', '-f', 'null', '-'], work, capture=True)
            except RuntimeError:
                # Listed encoders may lack a compatible device/driver/session.
                # Cancellation has its own exception and must propagate.
                continue
            selected = candidate
            break
        _cache[key] = selected
    encoder = _cache[key]
    job.message = '编码器：'+(encoder or ('libx264' if value['codec'] == 'h264' else 'libx265')+'（硬件不可用，使用软件）')
    return video_args(value, encoder)
