"""Validated room-specific terminology; no account discovery or model state."""
import re


def room_key(source):
    return f'{source.platform}:{source.room_id}'


def validate_terms(entries):
    if len(entries) > 20:
        raise ValueError('每个直播间、每种字幕语言最多保存 20 条术语')
    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise ValueError('术语格式为：原词 = 译法')
        source, target = (str(value).strip() for value in entry)
        if not source or not target or max(len(source), len(target)) > 40 or any(c in source + target for c in '\r\n'):
            raise ValueError('术语原词和译法各需 1–40 字，不能换行')
        if source.casefold() in seen:
            raise ValueError('同一原词只能指定一种译法')
        seen.add(source.casefold())
        result.append((source, target))
    return tuple(result)


def parse_terms(text):
    entries = []
    for line in text.splitlines():
        if line.strip():
            if '=' not in line:
                raise ValueError('每行填写：原词 = 译法')
            entries.append(tuple(line.split('=', 1)))
    return validate_terms(entries)


def room_terms(rooms, source, language):
    return validate_terms(rooms.get(room_key(source), {}).get(language, ()))


def matched_terms(entries, text):
    result = []
    for source, target in entries:
        pattern = re.escape(source)
        if re.match(r'[A-Za-z0-9]', source):
            pattern = r'(?<![A-Za-z0-9])' + pattern
        if re.search(r'[A-Za-z0-9]$', source):
            pattern += r'(?![A-Za-z0-9])'
        if re.search(pattern, text, re.I):
            result.append((source, target))
    return tuple(result)
