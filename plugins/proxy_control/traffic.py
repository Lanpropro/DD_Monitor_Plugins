"""Subscription response metadata and truthful quota display."""
from datetime import datetime


def parse_userinfo(header):
    values = {}
    for field in header[:4096].split(';'):
        key, separator, value = field.strip().partition('=')
        value = value.strip()
        if separator and key in ('upload', 'download', 'total', 'expire') and value.isascii() and value.isdecimal():
            number = int(value) if len(value) <= 19 else -1
            if 0 <= number <= 2**63 - 1:
                values[key] = number
    return values


def bytes_text(value):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024


def quota(usage):
    if not all(type(usage.get(key)) is int and usage[key] >= 0 for key in ('upload', 'download', 'total')):
        return '服务商未提供流量统计', None
    used, total = usage['upload'] + usage['download'], usage['total']
    if not total:
        return f'已用 {bytes_text(used)} · 未提供上限', None
    remaining = total - used
    text = (f'剩余 {bytes_text(remaining)} / {bytes_text(total)}' if remaining >= 0 else
            f'已超额 {bytes_text(-remaining)} / {bytes_text(total)}')
    return text, max(0, min(1, remaining / total))


def expiry_text(usage):
    expiry = usage.get('expire')
    if type(expiry) is not int or not 0 < expiry < 253402300800:
        return ''
    try:
        date = datetime.fromtimestamp(expiry)
        return ('已到期 ' if date < datetime.now() else '到期 ') + date.strftime('%Y-%m-%d')
    except (OSError, OverflowError, ValueError):
        return ''
