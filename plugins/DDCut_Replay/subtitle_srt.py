"""Strict SRT text interchange; no GUI dependencies."""
import math
import re
from .subtitles import placed_cues


STAMP = r'(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})'


def parse_srt(text):
    rows = []
    for index, block in enumerate(re.split(r'\n\s*\n', text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()), 1):
        if not block.strip():
            continue
        lines = block.splitlines()
        if lines[0].strip().isdigit():
            lines.pop(0)
        match = re.fullmatch(STAMP+r'\s*-->\s*'+STAMP, lines[0].strip()) if lines else None
        if not match or len(lines) < 2:
            raise ValueError(f'SRT第{index}块格式无效')
        parts = [int(value) for value in match.groups()]
        start, end = [(parts[i]*3600+parts[i+1]*60+parts[i+2])+parts[i+3]/1000 for i in (0, 4)]
        if end <= start:
            raise ValueError(f'SRT第{index}块出点不晚于入点')
        rows.append(dict(start=start, end=end, text='\n'.join(lines[1:])))
    return rows


def srt_text(project, begin=0, end=math.inf):
    def stamp(value):
        total = max(0, round(value*1000))
        return f'{total//3600000:02}:{total//60000%60:02}:{total//1000%60:02},{total%1000:03}'
    visible = [(cue, max(start, begin)-begin, min(stop, end)-begin) for cue, start, stop in placed_cues(project)
               if cue['text'].strip() and stop > begin and start < end]
    rows = [f'{index}\n{stamp(start)} --> {stamp(stop)}\n{cue["text"]}'
            for index, (cue, start, stop) in enumerate(visible, 1)]
    return '\n\n'.join(rows)+('\n' if rows else '')
