"""Source-anchored manual caption data and validation."""
import math
import re


def default_style():
    return dict(font='', size=48, color='#ffffff', outline_color='#000000', outline=2,
                margin=6, position='bottom', bold=False, italic=False, background=False)


def placed_cues(project):
    from .project import placed_clips
    clips = {clip['id']: (clip, offset) for clip, offset, _ in placed_clips(project)}
    result = []
    for cue in project.get('subtitles', []):
        if cue['anchor'] not in clips:
            continue
        clip, offset = clips[cue['anchor']]
        start, end = max(cue['start'], clip['start']), min(cue['end'], clip['end'])
        if end > start:
            speed = clip['speed']
            result.append((cue, offset+(start-clip['start'])/speed, offset+(end-clip['start'])/speed))
    return sorted(result, key=lambda row: (row[1], row[2]))


def anchor_cues(project, cues):
    from .project import identity, placed_clips
    rows = []
    for cue in cues:
        for clip, offset, stop in placed_clips(project):
            start, end = max(cue['start'], offset), min(cue['end'], stop)
            if end > start:
                rows.append(dict(id=identity(), anchor=clip['id'], start=clip['start']+(start-offset)*clip['speed'],
                                 end=clip['start']+(end-offset)*clip['speed'], text=cue['text']))
    return rows


def set_cue(project, identity, start, end, text):
    if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end:
        raise ValueError('字幕出点必须晚于入点')
    rows = anchor_cues(project, [dict(start=start, end=end, text=text)])
    if not rows:
        raise ValueError('字幕必须位于主视频范围内')
    if identity:
        rows[0]['id'] = identity
    project['subtitles'] = [cue for cue in project.get('subtitles', []) if cue['id'] != identity]+rows
    return rows[0]['id']


def validate_subtitles(project, anchors, ids):
    for cue in project.get('subtitles', []):
        if cue['id'] in ids or cue['anchor'] not in anchors or not isinstance(cue['text'], str):
            raise ValueError('字幕引用无效')
        if not all(isinstance(cue[key], (int, float)) and math.isfinite(cue[key]) for key in ('start', 'end')) or not 0 <= cue['start'] < cue['end']:
            raise ValueError('字幕时间无效')
        ids.add(cue['id'])
    style = {**default_style(), **project.get('subtitle_style', {})}
    if style['position'] not in ('top', 'middle', 'bottom') or not isinstance(style['font'], str):
        raise ValueError('字幕样式无效')
    for key, low, high in [('size', 8, 200), ('outline', 0, 12), ('margin', 0, 40)]:
        if not isinstance(style[key], (int, float)) or not math.isfinite(style[key]) or not low <= style[key] <= high:
            raise ValueError('字幕样式超出范围：'+key)
    for key in ('color', 'outline_color'):
        if not isinstance(style[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', style[key]):
            raise ValueError('字幕颜色请使用#RRGGBB格式')
