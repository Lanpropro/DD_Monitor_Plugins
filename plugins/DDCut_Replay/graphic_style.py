"""Shared cover/project text style values, independent of Qt and media jobs."""
import math
import re


TEXT_PRESETS = [
    ('白字黑边', ('#ffffff', '#111111', '', False)),
    ('醒目黄', ('#ffe04d', '#111111', '', False)),
    ('红字白边', ('#ff3b30', '#ffffff', '', True)),
    ('蓝字白边', ('#0084ff', '#ffffff', '', True)),
    ('黑底黄字', ('#ffe04d', '', '#111111', False)),
]
TEXT_DEFAULTS = dict(font='', bold=False, italic=False, outline=1, stroke='#000000',
                     background='', shadow=False, align='left')


def validate_style(style):
    if not isinstance(style, dict) or set(style) - set(TEXT_DEFAULTS):
        raise ValueError('文字样式格式无效')
    values = {**TEXT_DEFAULTS, **style}
    if not isinstance(values['font'], str) or values['align'] not in ('left', 'center', 'right'):
        raise ValueError('文字字体或对齐无效')
    if not isinstance(values['outline'], (int, float)) or not math.isfinite(values['outline']) or not 0 <= values['outline'] <= 30:
        raise ValueError('文字描边必须在0到30之间')
    if any(not isinstance(values[key], bool) for key in ('bold', 'italic', 'shadow')):
        raise ValueError('文字样式开关无效')
    for key in ('stroke', 'background'):
        if not isinstance(values[key], str) or values[key] and not re.fullmatch(r'#[0-9a-fA-F]{6}', values[key]):
            raise ValueError('文字颜色请使用#RRGGBB格式')
