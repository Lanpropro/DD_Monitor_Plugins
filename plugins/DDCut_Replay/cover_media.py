"""Freeze an archive/project position as an owned cover background image."""
from copy import deepcopy
from pathlib import Path
import os
import math
from fractions import Fraction
import tempfile
from .media import Job, check_cancel, export_clip, probe, run_process
from .project_media import render_project
from .project import duration


def capture_frame(job, ffmpeg, source, stamp, target, work):
    target = Path(target)
    if target.exists():
        raise FileExistsError('取帧目标已存在')
    total = duration(source['project']) if 'project' in source else source['duration']
    if total <= 0:
        raise ValueError('没有可读取的视频帧')
    start = max(0, min(stamp, max(0, total-1)))
    span = min(1, total-start)
    Path(work).mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cover-frame-', dir=work) as folder:
        clip = Path(folder)/'frame.mp4'
        child = Job('封面取帧', 'cover_frame', cancel=job.cancel)
        if 'project' in source:
            data = deepcopy(source['project'])
            data['encoding'] = {'codec': 'h264', 'priority': 'speed', 'bitrate': 0}
            render_project(child, ffmpeg, data, clip, folder, begin=start, length=span)
        else:
            export_clip(child, ffmpeg, source, start, start+span, 'precise', clip, folder)
        info = probe(job, ffmpeg, clip, folder)
        rate = float(Fraction(info['frame_rate']))
        local = min(max(0, math.floor((stamp-start)*rate)/rate), max(0, (math.ceil(info['duration']*rate-1e-6)-1)/rate))
        picture = Path(folder)/'frame.png'
        run_process(job, [ffmpeg, '-y', '-v', 'error', '-ss', str(local), '-i', str(clip), '-frames:v', '1', str(picture)], work)
        check_cancel(job)
        if target.exists():
            raise FileExistsError('取帧目标已存在')
        if os.name == 'nt':
            picture.rename(target)
        else:
            os.link(picture, target)
    return {'path': str(target), 'time': start+local}
