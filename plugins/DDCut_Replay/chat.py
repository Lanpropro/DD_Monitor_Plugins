"""Recorded chat data, media-time alignment and portable exports."""
from bisect import bisect_right
import json
import hashlib
from pathlib import Path
import shutil


class ChatReader:
    def __init__(self):
        self.offset = 0
        self.records = []
        self.signature = None
        self.video_burned = False

    def read(self, path):
        path = Path(path)
        info = path.stat()
        if path.suffix.lower() == '.json':
            signature = (info.st_size, info.st_mtime_ns)
            if signature != self.signature:
                data = json.loads(path.read_text(encoding='utf-8'))
                self.video_burned = isinstance(data, dict) and data.get('video_burned', False)
                self.records = data['events'] if isinstance(data, dict) else data
                for row in self.records:
                    row['image_paths'] = {url: str((path.parent / local).resolve())
                                          for url, local in row.get('image_paths', {}).items()}
                self.signature = signature
            return self.records
        if info.st_size < self.offset:
            self.offset = 0
            self.records.clear()
        with path.open('rb') as handle:
            handle.seek(self.offset)
            while line := handle.readline():
                if not line.endswith(b'\n'):
                    break
                record = json.loads(line)
                self.records.append(record)
                self.offset = handle.tell()
        return self.records


def align_events(records, segments):
    starts, intervals = [], []
    elapsed = 0
    for segment in segments:
        if segment.get('wall_start') is not None:
            starts.append(segment['wall_start'])
            intervals.append((elapsed, segment['duration']))
        elapsed += segment['duration']
    result, chats = [], {}
    for record in records:
        row = dict(record)
        if 'received_at' in row:
            index = bisect_right(starts, row['received_at']) - 1
            if index < 0:
                continue
            offset, duration = intervals[index]
            delta = row['received_at'] - starts[index]
            if not 0 <= delta < duration:
                continue
            row['time'] = offset + delta
        if 'time' not in row:
            continue
        kind = row.get('kind') or 'danmaku'
        row['kind'] = kind
        identity = str(row.get('id') or '')
        if kind == 'super_chat_delete':
            for identity in map(str, row.get('ids') or []):
                if identity in chats:
                    chat = chats[identity]
                    chat['duration'] = max(0, min(chat.get('duration', 60), row['time'] - chat['time']))
            continue
        if kind not in ('danmaku', 'super_chat'):
            continue
        if kind == 'super_chat' and identity:
            if identity in chats:
                continue
            chats[identity] = row
        result.append(row)
    return sorted(result, key=lambda row: row['time'])


def load_events(source):
    path = source.get('danmaku_path')
    return align_events(ChatReader().read(path), source.get('segments', [])) if path else []


def trim_events(events, start, end, *, offset=0, shift=0):
    result = []
    for event in events:
        time = event['time'] + shift
        duration = max(0, float(event.get('duration', 60))) if event.get('kind') == 'super_chat' else 0
        overlaps = time < end and time + duration > start if duration else start <= time < end
        if overlaps:
            row = {key: value for key, value in event.items() if key not in ('received_at', 'start_time', 'end_time')}
            row['time'] = max(start, time) - start + offset
            if duration:
                row['duration'] = min(end, time + duration) - max(start, time)
            result.append(row)
    return result


def write_json(path, events, *, video_burned=False):
    path = Path(path)
    portable = []
    for event in events:
        row = dict(event)
        pictures = {}
        for url, local in event.get('image_paths', {}).items():
            source = Path(local)
            if source.is_file():
                destination = path.parent / (path.stem + '.images') / (hashlib.sha256(source.read_bytes()).hexdigest() + source.suffix)
                destination.parent.mkdir(exist_ok=True)
                if not destination.exists():
                    shutil.copyfile(source, destination)
                pictures[url] = destination.relative_to(path.parent).as_posix()
        if pictures:
            row['image_paths'] = pictures
        else:
            row.pop('image_paths', None)
        portable.append(row)
    data = {'video_burned': True, 'events': portable} if video_burned else portable
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def ass_time(seconds):
    centiseconds = max(0, round(seconds * 100))
    seconds, cs = divmod(centiseconds, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02}:{seconds:02}.{cs:02}'


def ass_text(text):
    return str(text).replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\r', '').replace('\n', r'\N')


def write_ass(path, events, *, shift=0, font_size=36, opacity=80):
    """ASS is text-only and uses the same deterministic lane schedule as preview."""
    from .chat_scene import layout_events, display_text
    from PySide6.QtGui import QFont, QFontMetrics
    font = QFont('Microsoft YaHei')
    font.setPixelSize(font_size)
    metrics = QFontMetrics(font)
    packets = layout_events(events, 1920, 1080, lambda row: metrics.horizontalAdvance(display_text(row)), font_size)
    alpha = round(255 * (1 - max(0, min(100, opacity)) / 100))
    lines = ['[Script Info]', 'ScriptType: v4.00+', 'PlayResX: 1920', 'PlayResY: 1080',
             'WrapStyle: 2', '[V4+ Styles]',
             'Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding',
             f'Style: Chat,Microsoft YaHei,{font_size},&H{alpha:02X}FFFFFF,&H00FFFFFF,&H80000000,&H80000000,1,0,0,0,100,100,0,0,1,1.5,0,7,0,0,0,1',
             '[Events]', 'Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text']
    for packet in packets:
        start, end = packet['start'] + shift, packet['end'] + shift
        if end <= 0:
            continue
        event = packet['event']
        width, y = packet['width'], packet['lane'] * (font_size + 10) + 10
        text = ass_text(event.get('text') or '')
        color = str(event.get('color') or '#ffffff').lstrip('#')
        if len(color) != 6 or any(c not in '0123456789abcdefABCDEF' for c in color):
            color = 'ffffff'
        bgr = color[4:6] + color[2:4] + color[0:2]
        if event.get('kind') == 'super_chat':
            prefix = '{' + f"\\an9\\pos(1900,{10 + packet['lane'] * (font_size + 16)})\\c&H00D7FF&" + '}'
            text = f"SC ¥{event.get('price', 0)} {ass_text(event.get('uname') or '')}: {text}"
        else:
            x = 1920 - max(0, -start) / 13 * (1920 + width)
            prefix = '{' + f'\\move({round(x)},{y},{-width},{y})\\c&H{bgr}&' + '}'
        lines.append(f'Dialogue: 0,{ass_time(max(0, start))},{ass_time(end)},Chat,,0,0,0,,{prefix}{text}')
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
