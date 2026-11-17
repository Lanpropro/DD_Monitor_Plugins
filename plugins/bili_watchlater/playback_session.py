"""Account-scoped VOD recovery; host layout and live rooms remain host-owned."""
import hashlib
import math
import re
from PySide6.QtCore import QTimer
from .models import WatchLaterItem, clean_text, normalize_cover_url
from .preferences import read_preferences
from .acceptance_trace import record as trace_event

KEY = 'playback_session'


def account_key(cookies):
    uid=str(cookies.get('DedeUserID') or '')
    if uid:
        return uid  # Preserve snapshots from hosts that provide a public UID.
    session=str(cookies.get('SESSDATA') or '')
    if not session:
        return ''
    # The host's credential contract does not include DedeUserID. An opaque
    # login fingerprint scopes recovery without persisting the login secret.
    return 'session:'+hashlib.sha256(('bili_watchlater:'+session).encode()).hexdigest()


def seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return 0.
    return max(0., float(value))


class PlaybackSession:
    def __init__(self, plugin):
        self.plugin = plugin
        self.finished = False
        self.attempts = 0

    def _read(self):
        reader = getattr(self.plugin.context, 'setting', None)
        return reader(KEY, {}) if callable(reader) else {}

    def _write(self, value):
        setter = getattr(self.plugin.context, 'set_setting', None)
        if callable(setter) and self._read() != value:
            setter(KEY, value)

    def clear(self):
        self.finished = True
        self._write({})

    def capture(self):
        plugin = self.plugin
        if not read_preferences(plugin.context)['resume_session']:
            trace_event('session_capture',reason='disabled',count=0)
            self.clear()
            return
        wall = getattr(plugin._host_window(), 'wall', None)
        panes = getattr(wall, 'pane_danmaku', None)
        account = account_key(plugin._cookie_snapshot())
        if panes is None or not account:
            trace_event('session_capture',reason='no_layout' if panes is None else 'no_login',count=0)
            self.clear()
            return
        videos = []
        tiles = plugin._playback_tiles()
        for binding in plugin._bindings.values():
            tile = next((tile for tile in tiles if (getattr(tile, 'room', None) or {}).get('watchlater_bvid') == binding.bvid), None)
            if tile is None or binding.state == 'ended':
                continue
            cell = panes.cell_for_tile(tile)
            if cell is None:
                continue
            position = seconds(binding.progress)
            player = plugin._player_for_binding(binding)
            if player is not None:
                prop = getattr(player, '_prop', None)
                if callable(prop):
                    current = prop('time-pos', None)
                    if type(current) in (int, float) and math.isfinite(current):
                        position = seconds(current)
            item = binding.item
            videos.append(dict(cell=cell, bvid=binding.bvid, aid=item.aid, title=item.title,
                owner_name=item.owner_name, owner_mid=item.owner_mid, owner_face=item.owner_face,
                duration=int(binding.duration or item.duration or 0), videos=int(item.parts or 1), cid=item.cid,
                part=binding.part, progress=position, quality=int(binding.quality or 0),
                paused=bool(getattr(tile, 'paused', binding.state == 'paused')),
                expected_room=str((binding.prev_room or {}).get('room_id') or '')))
        self._write(dict(account=account, layout=wall.layout_id, videos=videos) if videos else {})
        trace_event('session_capture',reason='saved' if videos else 'no_active_video',count=len(videos))

    def restore(self):
        plugin = self.plugin
        if self.finished or plugin._shutting_down:
            return
        if not read_preferences(plugin.context)['resume_session']:
            self.clear()
            return
        saved = self._read()
        if not isinstance(saved, dict) or not saved.get('videos'):
            trace_event('session_restore',reason='no_snapshot',count=0)
            self.finished = True
            return
        account = account_key(plugin._cookie_snapshot())
        wall = getattr(plugin._host_window(), 'wall', None)
        panes = getattr(wall, 'pane_danmaku', None)
        if not account or panes is None or not plugin._host_tiles():
            self._retry()
            return
        if account != saved.get('account') or wall.layout_id != saved.get('layout'):
            trace_event('session_restore',reason='login_changed' if account != saved.get('account') else 'layout_changed',count=0)
            self.clear()
            plugin._notify_warning('账号或布局已变化，未自动恢复上次视频。')
            return
        cells = panes.cell_tiles()
        candidates = plugin._playback_tiles()
        records = saved.get('videos')
        if not isinstance(records, list):
            self.clear()
            return
        pending = []
        used_cells, used_videos = set(), set()
        for record in records[:len(cells)]:
            if not isinstance(record, dict):
                continue
            cell, bvid = record.get('cell'), record.get('bvid')
            if type(cell) is not int or not isinstance(bvid, str) or not re.fullmatch(r'BV[0-9A-Za-z]{10}', bvid):
                continue
            tile = cells.get(cell)
            if tile not in candidates or cell in used_cells or bvid in used_videos or bvid in plugin._bindings:
                continue
            if str((getattr(tile, 'room', None) or {}).get('room_id') or '') != record.get('expected_room'):
                continue  # Do not replace content changed after the snapshot.
            aid, parts, part, cid = (record.get(key, 0) for key in ('aid', 'videos', 'part', 'cid'))
            if any(type(value) is not int for value in (aid, parts, part, cid)) or not (aid > 0 and 1 <= parts <= 10000 and 0 <= part < parts):
                continue
            if plugin._resolve_cell_from_tile(tile) is None:
                self._retry()
                return
            title = record.get('title')
            item = WatchLaterItem(aid=aid, bvid=bvid, title=title[:512] if isinstance(title, str) else '稍后再看视频',
                                 duration=int(seconds(record.get('duration'))), videos=parts, cid=max(0, cid))
            item.owner_name = clean_text(record.get('owner_name'), '未知 UP 主')[:128]
            mid = record.get('owner_mid', 0)
            item.owner_mid = max(0, mid) if type(mid) is int else 0
            item.owner_face = normalize_cover_url(record.get('owner_face'))
            quality = record.get('quality', 0)
            resume = dict(part=part, progress=seconds(record.get('progress')),
                          quality=quality if type(quality) is int and 0 <= quality <= 1000 else 0,
                          paused=record.get('paused') is True)
            pending.append((tile, item, resume))
            used_cells.add(cell)
            used_videos.add(bvid)
        self.clear()
        for tile, item, resume in pending:
            plugin._start_vod_for_tile(tile, item, resume=resume)
        plugin._log(f'playback session restored count={len(pending)} skipped={len(records)-len(pending)}')
        trace_event('session_restore',reason='started',count=len(pending),skipped=len(records)-len(pending))

    def _retry(self):
        self.attempts += 1
        if self.attempts <= 10:
            trace_event('session_retry',attempt=self.attempts)
            QTimer.singleShot(400, self.restore)
        else:
            self.finished = True
            self.plugin._notify_warning('启动时账号或格子尚未就绪，未自动恢复上次视频。')
