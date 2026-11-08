"""Editable cover drafts; file references are external and never deleted here."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import uuid

SIZES = {'wide': (1920, 1080), 'home': (1440, 1080)}


def identity():
    return uuid.uuid4().hex


def new_cover():
    return dict(version=1, background='', sync=True, views={key: dict(frame=dict(x=.5, y=.5, zoom=1), elements=[]) for key in SIZES})


def new_element(kind, path=''):
    return dict(id=identity(), kind=kind, path=path, text='封面文字', x=.18, y=.25, w=.64, h=.3,
                rotation=0, opacity=1, font='', font_size=100, color='#ffffff', stroke='#111111',
                outline=6, bold=True, italic=False, background='', shadow=False, flip=False, align='center')


def change_element(data, ratio, item_id, values=None, *, remove=False):
    for key in (SIZES if data['sync'] else (ratio,)):
        rows = data['views'][key]['elements']
        item = next((row for row in rows if row['id'] == item_id), None)
        if remove:
            data['views'][key]['elements'] = [row for row in rows if row['id'] != item_id]
        elif item:
            item.update(values)
        elif key != ratio:
            original = next((row for row in data['views'][ratio]['elements'] if row['id'] == item_id), None)
            if original:
                rows.append({**deepcopy(original), **values})


def validate(data):
    if data.get('version') != 1 or set(data['views']) != set(SIZES) or not isinstance(data['background'], str):
        raise ValueError('封面草稿格式无效')
    for view in data['views'].values():
        frame = view['frame']
        for key, low, high in [('x', 0, 1), ('y', 0, 1), ('zoom', 1, 4)]:
            if not isinstance(frame[key], (int, float)) or not math.isfinite(frame[key]) or not low <= frame[key] <= high:
                raise ValueError('背景位置或缩放超出范围')
        seen = set()
        for item in view['elements']:
            if item['id'] in seen or item['kind'] not in ('text', 'image'):
                raise ValueError('封面元素无效')
            seen.add(item['id'])
            for key, low, high in [('x', -1, 2), ('y', -1, 2), ('w', .02, 2), ('h', .02, 2),
                                   ('rotation', -180, 180), ('opacity', 0, 1), ('font_size', 8, 240), ('outline', 0, 30)]:
                if not isinstance(item[key], (int, float)) or not math.isfinite(item[key]) or not low <= item[key] <= high:
                    raise ValueError('封面元素属性超出范围：'+key)
            if item['align'] not in ('left', 'center', 'right'):
                raise ValueError('文字对齐无效')
            for key in ('text', 'path', 'font'):
                if not isinstance(item[key], str):
                    raise ValueError('封面文本或文件引用无效')
            for key in ('color', 'stroke', 'background'):
                if item[key] != '' and not re.fullmatch(r'#[0-9a-fA-F]{6}', item[key]):
                    raise ValueError('封面颜色请使用#RRGGBB格式')


def write_cover(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    copy = deepcopy(data)
    def relative(value):
        try:
            return os.path.relpath(value, path.parent).replace('\\', '/') if value else ''
        except ValueError:
            return value
    copy['background'] = relative(copy['background'])
    for view in copy['views'].values():
        for item in view['elements']:
            item['path'] = relative(item['path'])
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(copy, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def read_cover(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    def absolute(value):
        return str((path.parent/value).resolve()) if value else ''
    data['background'] = absolute(data['background'])
    for view in data['views'].values():
        for item in view['elements']:
            item['path'] = absolute(item['path'])
    validate(data)
    return data


class CoverDocument:
    def __init__(self, path, data=None):
        self.path = Path(path)
        self.data = data if data is not None else read_cover(path)
        self.undo_stack, self.redo_stack = [], []

    def commit(self, operation):
        before = deepcopy(self.data)
        try:
            result = operation(self.data)
            validate(self.data)
            if before != self.data:
                write_cover(self.path, self.data)
        except Exception:
            self.data = before
            raise
        if before != self.data:
            self.undo_stack = (self.undo_stack+[before])[-100:]
            self.redo_stack.clear()
        return result

    def history(self, redo=False):
        source, target = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if source:
            restored = deepcopy(source[-1])
            write_cover(self.path, restored)
            target.append(deepcopy(self.data))
            self.data = source.pop()
