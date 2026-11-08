"""Editable file-based projects. Media paths are references, never owned files."""
from copy import deepcopy
import json
import math
import os
import re
from pathlib import Path
import time
import uuid
from .encoding import normalize as normalize_encoding
from .subtitles import validate_subtitles


def identity():
    return uuid.uuid4().hex


def new_project(title='未命名工程'):
    return {'version': 1, 'id': identity(), 'title': title, 'note': '', 'created_at': time.time(),
            'updated_at': time.time(), 'media': {}, 'clips': [], 'layers': [],
            'render': {'width': 1280, 'height': 720, 'fps': 30}, 'danmaku': False, 'visual_order': 'mixed'}


def clip_length(clip):
    return (clip['end'] - clip['start']) / clip.get('speed', 1)


def placed_clips(project):
    offset = 0
    result = []
    for clip in project['clips']:
        result.append((clip, offset, offset + clip_length(clip)))
        offset += clip_length(clip)
    return result


def placed_layers(project):
    anchors = {clip['id']: (clip, offset) for clip, offset, _ in placed_clips(project)}
    result = []
    for layer in project['layers']:
        if layer['anchor'] not in anchors:
            continue
        clip, offset = anchors[layer['anchor']]
        # Hidden when its original material anchor falls outside the trimmed clip.
        if not clip['start'] <= layer['source_at'] < clip['end']:
            continue
        start = offset + (layer['source_at'] - clip['start']) / clip.get('speed', 1)
        result.append((layer, start, start + layer['duration']))
    return result


def duration(project):
    return max([end for _, _, end in placed_clips(project) + placed_layers(project)] or [0])


def anchor_at(project, position):
    clips = placed_clips(project)
    if not clips:
        raise ValueError('请先添加主视频片段')
    position = max(0, min(position, clips[-1][2] - .001))
    clip, offset, _ = next((row for row in clips if row[1] <= position < row[2]), clips[-1])
    return clip['id'], clip['start'] + (position - offset) * clip.get('speed', 1)


def append_clip(project, path, start=0, end=None):
    info = project['media'][path]
    if info.get('type') != 'video':
        raise ValueError('主轨只能添加视频文件')
    clip = {'id': identity(), 'source': path, 'start': start,
            'end': info['duration'] if end is None else end, 'speed': 1, 'volume': 1}
    project['clips'].append(clip)
    return clip['id']


def add_layer(project, kind, position, path=None, text='文字'):
    anchor, stamp = anchor_at(project, position)
    item = {'id': identity(), 'kind': kind, 'anchor': anchor, 'source_at': stamp,
            'duration': 3, 'track': max([row['track'] for row in project['layers']] or [0]) + 1,
            'x': .1, 'y': .1, 'w': .35, 'h': .35, 'opacity': 1,
            'text': text, 'font_size': 48, 'color': '#ffffff', 'volume': 1 if kind == 'audio' else 0,
            'source': path, 'in': 0, 'speed': 1, 'fade_in': 0, 'fade_out': 0, 'rotation': 0}
    if kind in ('audio', 'video'):
        item['duration'] = min(3, project['media'][path]['duration'])
    if kind == 'text':
        from .graphic_style import TEXT_DEFAULTS
        item['text_style'] = dict(TEXT_DEFAULTS)
    project['layers'].append(item)
    return item['id']


def split_clip(project, clip_id, position):
    clip, begin, end = next(row for row in placed_clips(project) if row[0]['id'] == clip_id)
    if not begin + .04 < position < end - .04:
        raise ValueError('分割点必须位于片段内部')
    source_cut = clip['start'] + (position - begin) * clip.get('speed', 1)
    second = dict(clip, id=identity(), start=source_cut)
    clip['end'] = source_cut
    project['clips'].insert(project['clips'].index(clip) + 1, second)
    for cue in list(project.get('subtitles', [])):
        if cue['anchor'] == clip_id:
            if cue['start'] >= source_cut:
                cue['anchor'] = second['id']
            elif cue['end'] > source_cut:
                project['subtitles'].append(dict(cue, id=identity(), anchor=second['id']))
    for layer in list(project['layers']):
        if layer['anchor'] != clip_id:
            continue
        if layer['source_at'] >= source_cut:
            layer['anchor'] = second['id']
        elif layer['source_at'] >= clip['start']:
            remaining = (source_cut - layer['source_at']) / clip.get('speed', 1)
            if layer['duration'] > remaining:
                copy = dict(deepcopy(layer), id=identity(), anchor=second['id'], source_at=source_cut,
                            duration=layer['duration'] - remaining)
                if layer['kind'] in ('video', 'audio'):
                    copy['in'] += remaining * layer.get('speed', 1)
                layer['duration'] = remaining
                project['layers'].append(copy)
    for layer in project['layers']:
        for key in ('fade_in', 'fade_out'):
            layer[key] = min(layer[key], layer['duration'])
    return second['id']


def remove_item(project, item_id):
    project['clips'] = [row for row in project['clips'] if row['id'] != item_id]
    project['layers'] = [row for row in project['layers'] if row['id'] != item_id and row['anchor'] != item_id]
    project['subtitles'] = [row for row in project.get('subtitles', []) if row['id'] != item_id and row['anchor'] != item_id]


def duplicate_item(project, item_id):
    for index, clip in enumerate(project['clips']):
        if clip['id'] == item_id:
            copy = dict(clip, id=identity())
            project['clips'].insert(index+1, copy)
            project['layers'] += [dict(deepcopy(layer), id=identity(), anchor=copy['id'])
                                  for layer in list(project['layers']) if layer['anchor'] == item_id]
            project.setdefault('subtitles', []).extend([dict(cue, id=identity(), anchor=copy['id'])
                for cue in list(project.get('subtitles', [])) if cue['anchor'] == item_id])
            return copy['id']
    for layer in project['layers']:
        if layer['id'] == item_id:
            copy = dict(deepcopy(layer), id=identity())
            project['layers'].append(copy)
            return copy['id']
    for cue in project.get('subtitles', []):
        if cue['id'] == item_id:
            copy = dict(cue, id=identity())
            project['subtitles'].append(copy)
            return copy['id']


def settle_tracks(project):
    occupied = {}
    for layer, start, end in placed_layers(project):
        key = ('audio' if layer['kind'] == 'audio' else 'visual', layer['track'])
        if any(start < b and end > a for a, b in occupied.get(key, [])):
            layer['track'] = max([row['track'] for row in project['layers']] or [0]) + 1
            key = (key[0], layer['track'])
        occupied.setdefault(key, []).append((start, end))


def validate(project):
    if project.get('version') != 1:
        raise ValueError('不支持的工程版本')
    seen = set()
    for clip in project['clips']:
        if clip['id'] in seen:
            raise ValueError('工程片段ID重复')
        seen.add(clip['id'])
        info = project['media'][clip['source']]
        if not 0 <= clip['start'] < clip['end'] <= info['duration'] + .001 or not .25 <= clip['speed'] <= 4:
            raise ValueError('视频入出点或速度超出范围')
        if not 0 <= clip['volume'] <= 2:
            raise ValueError('音量必须在0%到200%之间')
    anchors = set(seen)
    for layer in project['layers']:
        if layer['id'] in seen or layer['anchor'] not in anchors or layer['kind'] not in ('text', 'image', 'video', 'audio'):
            raise ValueError('工程轨道引用无效')
        if not math.isfinite(layer['duration']) or layer['duration'] < .04:
            raise ValueError('轨道内容时长必须大于0')
        for key, low, high in [('source_at', 0, 864000), ('x', -1, 2), ('y', -1, 2), ('w', .02, 2),
                               ('h', .02, 2), ('opacity', 0, 1), ('volume', 0, 2), ('font_size', 8, 200),
                               ('fade_in', 0, layer['duration']), ('fade_out', 0, layer['duration'])]:
            if not isinstance(layer[key], (int, float)) or not math.isfinite(layer[key]) or not low <= layer[key] <= high:
                raise ValueError('轨道属性超出支持范围：' + key)
        if not isinstance(layer['track'], int) or not 1 <= layer['track'] <= 999:
            raise ValueError('轨道编号必须在1到999之间')
        if not isinstance(layer.get('rotation', 0), (int, float)) or not math.isfinite(layer.get('rotation', 0)) or not -180 <= layer.get('rotation', 0) <= 180:
            raise ValueError('旋转角度必须在-180到180度之间')
        if layer['kind'] in ('video', 'audio', 'image') and layer['source'] not in project['media']:
            raise ValueError('轨道来源文件引用不存在')
        if layer['kind'] == 'text' and not re.fullmatch(r'#[0-9a-fA-F]{6}', layer['color']):
            raise ValueError('文字颜色请使用#RRGGBB格式')
        if layer['kind'] == 'text' and 'text_style' in layer:
            from .graphic_style import validate_style
            validate_style(layer['text_style'])
        if layer['kind'] == 'image' and not isinstance(layer.get('flip', False), bool):
            raise ValueError('贴纸翻转开关无效')
        if layer['kind'] in ('audio', 'video'):
            if not .25 <= layer['speed'] <= 4 or not 0 <= layer['in'] < project['media'][layer['source']]['duration']:
                raise ValueError('轨道来源时间或速度无效')
            if layer['in'] + layer['duration'] * layer['speed'] > project['media'][layer['source']]['duration'] + .001:
                raise ValueError('轨道内容超出来源文件时长')
        seen.add(layer['id'])
    render = project['render']
    validate_subtitles(project, anchors, seen)
    normalize_encoding(project.get('encoding'))
    if not 160 <= render['width'] <= 3840 or not 90 <= render['height'] <= 2160 or not 1 <= render['fps'] <= 60:
        raise ValueError('工程画布或帧率超出支持范围')


def write_project(path, project):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = deepcopy(project)
    references = {}
    for source, info in data['media'].items():
        try:
            relative = os.path.relpath(source, path.parent)
        except ValueError:  # Different Windows drive.
            relative = source
        references[source] = relative.replace('\\', '/')
        info['path'] = references[source]
        # Metadata contains optional archive/sidecar paths; keep these resolvable too.
        for key in ('danmaku_path', 'cover_path'):
            if info.get(key):
                try:
                    info[key] = os.path.relpath(info[key], path.parent).replace('\\', '/')
                except ValueError:
                    pass
        for segment in info.get('segments', []):
            for key in ('path', 'init'):
                if segment.get(key):
                    try:
                        segment[key] = os.path.relpath(segment[key], path.parent).replace('\\', '/')
                    except ValueError:
                        pass
    data['media'] = {references[key]: value for key, value in data['media'].items()}
    for row in data['clips'] + data['layers']:
        if row.get('source'):
            row['source'] = references[row['source']]
    if data.get('cover_draft'):
        try:
            data['cover_draft'] = os.path.relpath(data['cover_draft'], path.parent).replace('\\', '/')
        except ValueError:
            pass
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def read_project(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    sources = {}
    for source, info in data['media'].items():
        resolved = str((path.parent / source).resolve())
        sources[source] = resolved
        info['path'] = resolved
        for key in ('danmaku_path', 'cover_path'):
            if info.get(key):
                info[key] = str((path.parent / info[key]).resolve())
        for segment in info.get('segments', []):
            for key in ('path', 'init'):
                if segment.get(key):
                    segment[key] = str((path.parent / segment[key]).resolve())
    data['media'] = {sources[key]: value for key, value in data['media'].items()}
    for row in data['clips'] + data['layers']:
        if row.get('source'):
            row['source'] = sources[row['source']]
    if data.get('cover_draft'):
        data['cover_draft'] = str((path.parent/data['cover_draft']).resolve())
    # Earlier projects always drew graphics over all videos, regardless of track number.
    if data.get('visual_order') != 'mixed':
        visual = sorted({(row['kind'] != 'video', row['track']) for row in data['layers'] if row['kind'] != 'audio'})
        order = {key: index+1 for index, key in enumerate(visual)}
        for row in data['layers']:
            if row['kind'] != 'audio':
                row['track'] = order[(row['kind'] != 'video', row['track'])]
        data['visual_order'] = 'mixed'
    validate(data)
    return data


class ProjectDocument:
    def __init__(self, path, data=None):
        self.path = Path(path)
        self.data = data if data is not None else read_project(path)
        self.undo_stack, self.redo_stack = [], []
        self.revision = 0

    def commit(self, change):
        before = deepcopy(self.data)
        try:
            result = change(self.data)
            settle_tracks(self.data)
            validate(self.data)
        except Exception:
            self.data = before
            raise
        if before != self.data:
            self.undo_stack.append(before)
            self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()
            self.save()
            self.revision += 1
        return result

    def save(self):
        self.data['updated_at'] = time.time()
        write_project(self.path, self.data)

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(deepcopy(self.data))
            self.data = self.undo_stack.pop()
            self.save()
            self.revision += 1

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(deepcopy(self.data))
            self.data = self.redo_stack.pop()
            self.save()
            self.revision += 1
