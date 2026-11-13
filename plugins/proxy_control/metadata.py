"""Subscription display names, without exposing URL tokens as a fallback."""
import base64
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urlsplit


def display_name(source, location, title='', disposition=''):
    if title.startswith('base64:'):
        try:
            encoded = title[7:]
            title = base64.b64decode(encoded + '=' * (-len(encoded) % 4), validate=True).decode('utf-8')
        except (ValueError, UnicodeError):
            title = ''
    if not title and disposition:
        message = Message()
        message['Content-Disposition'] = disposition
        title = message.get_filename() or ''
        if title.lower().endswith(('.yaml', '.yml', '.json', '.txt')):
            title = title.rsplit('.', 1)[0]
    title = unquote(title).strip()
    if title and not any(ord(char) < 32 for char in title):
        return title[:128]
    if source == 'url':
        try:
            return urlsplit(location).hostname or '订阅配置'
        except ValueError:
            return '订阅配置'
    return Path(location).stem[:128] if source == 'file' else '节点配置'
