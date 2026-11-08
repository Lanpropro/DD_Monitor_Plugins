"""Reversible main-video/overlay conversion, preserving source-time anchors."""
from copy import deepcopy
from .project import add_layer, anchor_at, clip_length, identity, placed_clips, placed_layers, remove_item
from .subtitles import anchor_cues, placed_cues


def insertion_index(project, stamp):
    return next((index for index, (_, start, end) in enumerate(placed_clips(project))
                 if stamp < (start+end)/2), len(project['clips']))


def clip_to_overlay(project, clip_id, position, track=None):
    moved, begin, _ = next(row for row in placed_clips(project) if row[0]['id'] == clip_id)
    total = sum(clip_length(clip) for clip in project['clips'] if clip['id'] != clip_id)
    if len(project['clips']) < 2 or total < .04:
        raise ValueError('主轨必须保留至少一个可播放片段')
    position = max(0, min(position, total-.04))
    shift = position-begin
    layers = [(deepcopy(item), start+shift) for item, start, _ in placed_layers(project) if item['anchor'] == clip_id]
    captions = [(deepcopy(cue), start+shift, end+shift) for cue, start, end in placed_cues(project) if cue['anchor'] == clip_id]
    remove_item(project, clip_id)
    video_id = add_layer(project, 'video', position, moved['source'])
    video = project['layers'][-1]
    info, render = project['media'][moved['source']], project['render']
    ratio = (info.get('video_width') or render['width']) / (info.get('video_height') or render['height'])
    height = min(.8, .3*render['width']/render['height']/ratio)
    width = height*ratio*render['height']/render['width']
    video.update(duration=clip_length(moved), speed=moved['speed'], **{'in': moved['start']},
                 volume=moved['volume'], x=1-width-.05, y=1-height-.05, w=width, h=height)
    if track is not None:
        video['track'] = track
    for item, start in layers:
        if start < total:
            item['anchor'], item['source_at'] = anchor_at(project, start)
            project['layers'].append(item)
    for cue, start, end in captions:
        if start < total:
            rows = anchor_cues(project, [dict(start=start, end=end, text=cue['text'])])
            if rows:
                rows[0]['id'] = cue['id']
                project.setdefault('subtitles', []).extend(rows)
    return video_id


def overlay_to_clip(project, item_id, index):
    item, _, _ = next(row for row in placed_layers(project) if row[0]['id'] == item_id)
    if item['kind'] != 'video':
        raise ValueError('只有画中画视频可以转为主轨片段')
    fps = project['render']['fps']
    length = min(max(1, round(item['duration']*fps))/fps,
                 (project['media'][item['source']]['duration']-item['in'])/item['speed'])
    clip = dict(id=identity(), source=item['source'], start=item['in'],
                end=item['in']+length*item['speed'], speed=item['speed'], volume=item['volume'])
    project['layers'] = [row for row in project['layers'] if row['id'] != item_id]
    project['clips'].insert(max(0, min(index, len(project['clips']))), clip)
    return clip['id']
