"""Use ordinary followed rooms to play imported IPTV channels and HLS sources."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from ddm import plugins as api

from .branding import channel_branding
from .playlist import Channel, PlaylistError, http_url, read_playlist
from .presets import PRESETS, PRESET_CHANNELS
from .source_status import source_is_live


INPUT_PREFIX = 'm3u:source:'
CHANNEL_ID = re.compile(r'm3u:[a-f0-9]{64}')
EXTENSIONS = ('.m3u', '.m3u8')


def _input_source(value):
    text = str(value or '').strip()
    explicit = text.startswith('m3u:')
    if text.startswith(INPUT_PREFIX):
        text = text[len(INPUT_PREFIX):]
    elif explicit:
        text = text[4:].strip()
    text = text.strip('"')
    if http_url(text):
        return text if explicit or urlsplit(text).path.lower().endswith(EXTENSIONS) else ''
    if '://' not in text and Path(text).suffix.lower() in EXTENSIONS:
        return text
    return ''


def _channel_id(channel):
    identity = [channel.url, channel.name, channel.group, channel.headers]
    return 'm3u:' + sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class PlaylistPlatform(api.Platform):
    kind = 'm3u'
    label = 'IPTV Play'
    room_input_hint = 'M3U／M3U8 文件或链接；无扩展名的 HLS 地址前加 m3u:'
    room_input_file_filter = '播放列表 (*.m3u *.m3u8)'
    capture_first_playback_cover = True

    def __init__(self, context):
        self.context = context
        saved = context.setting('channels', {})
        self.channels = {}
        if isinstance(saved, dict):
            for room_id, row in saved.items():
                if not CHANNEL_ID.fullmatch(room_id) or not isinstance(row, dict):
                    continue
                try:
                    self.channels[room_id] = Channel(**row)
                except TypeError:
                    continue

    def matches(self, room_id):
        text = str(room_id or '').strip()
        return bool(text in PRESET_CHANNELS or CHANNEL_ID.fullmatch(text) or _input_source(text))

    def normalize(self, room_id):
        text = str(room_id or '').strip()
        if text in PRESET_CHANNELS or CHANNEL_ID.fullmatch(text):
            return text
        source = _input_source(text)
        if not source:
            raise PlaylistError('请输入 M3U／M3U8 文件路径或 HTTP(S) 链接')
        return INPUT_PREFIX + source

    def _channel(self, room_id):
        channel = PRESET_CHANNELS.get(room_id) or self.channels.get(room_id)
        if channel is None:
            raise PlaylistError('频道来源已丢失，请重新导入播放列表')
        return channel

    def _room(self, room_id, channel):
        return api.RoomInfo(room_id=room_id, uname=channel.name, title=channel.group,
                            face=self._room_face(channel), platform=self.kind,
                            extra={'playback_mode': self.playback_mode, 'live_known': False,
                                   'live_from_playback': True, 'import_group': channel.group})

    @staticmethod
    def _room_face(channel):
        artwork = channel_branding(channel)
        return channel.logo if artwork.get('badge_from_avatar') or not artwork.get('avatar_path') else ''

    def room_card_branding(self, room_id):
        channel = PRESET_CHANNELS.get(room_id) or self.channels.get(room_id)
        return channel_branding(channel) if channel is not None else {}

    def restore_follow_rooms(self, rooms):
        for room in rooms:
            channel = PRESET_CHANNELS.get(room['room_id']) or self.channels.get(room['room_id'])
            if channel is not None:
                room.update(uname=channel.name, title=channel.group, face=self._room_face(channel),
                            live_from_playback=True, import_group=channel.group)
                room.setdefault('live_known', False)

    def room_info(self, room_id):
        return self._room(room_id, self._channel(room_id))

    def rooms_status(self, room_ids):
        channels = PRESET_CHANNELS | self.channels
        selected = {rid: channels[rid] for rid in room_ids if rid in channels}
        if not selected:
            return {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            return {
                rid: self._room(rid, channel).as_dict() | {'live': live, 'live_known': live}
                for (rid, channel), live in zip(selected.items(), pool.map(source_is_live, selected.values()))
            }

    def room_input_presets(self):
        return [self._room(key, channel) for key, channel in PRESETS.items()]

    def room_list(self, room_id, cancelled):
        normalized = self.normalize(room_id)
        if normalized in PRESET_CHANNELS or CHANNEL_ID.fullmatch(normalized):
            return api.RoomList([self.room_info(normalized)], choose=False)
        channels, hls, invalid = read_playlist(normalized[len(INPUT_PREFIX):], cancelled)
        by_id = {_channel_id(channel): channel for channel in channels}
        counts = Counter(channel.name for channel in by_id.values())
        seen = Counter()
        rooms = []
        for key, channel in by_id.items():
            room = self._room(key, channel)
            host = urlsplit(channel.url).hostname or '本地文件'
            seen[channel.name] += 1
            room.extra['import_source'] = (f'线路 {seen[channel.name]} · {host}'
                                           if counts[channel.name] > 1 else host)
            rooms.append(room)

        def store(selected):
            updated = self.channels | {key: by_id[key] for key in selected}
            self.context.set_setting('channels', {key: asdict(channel) for key, channel in updated.items()})
            self.channels = updated

        return api.RoomList(rooms, choose=not hls, store=store,
                            warning=f'已跳过 {invalid} 条无法识别的频道。' if invalid else '')

    def room_quality_options(self, room_id):
        return [{'qn': 0, 'desc': '源自适应'}]

    def play_url(self, room_id, quality=0):
        channel = self._channel(room_id)
        return channel.url, 0, self.kind, dict(channel.headers)
