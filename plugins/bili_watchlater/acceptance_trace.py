"""Bounded session/lift/audio evidence; no account, media or credential values."""
import json
import os
from pathlib import Path
import tempfile
import time
from PySide6.QtCore import QCoreApplication,QEvent,QObject,QTimer
from shiboken6 import isValid

_remaining=80
_audio_swaps=0


def trace_path():
    return Path(tempfile.gettempdir())/f'ddm-watchlater-acceptance-{os.getpid()}.jsonl'


def record(phase,**values):
    global _remaining
    if _remaining<=0:
        return
    _remaining-=1
    row=dict(phase=phase,time=time.strftime('%Y-%m-%d %H:%M:%S'),**values)
    try:
        with trace_path().open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(row,ensure_ascii=False)+'\n')
    except OSError:
        pass  # Diagnostics must not block shutdown or playback.


class LiftTrace(QObject):
    def __init__(self,view):
        super().__init__(view)
        self.pressed=None
        self.started=None
        self.last=0.
        self.count=0
        self.timings={}

    def press(self):
        self.pressed=time.perf_counter()

    def begin(self):
        if self.count>=8:
            return
        self.count+=1
        self.started=self.last=time.perf_counter()
        self.timings={'attempt':self.count,'press_to_drag_ms':round((self.started-self.pressed)*1000,3) if self.pressed is not None else 0.}

    def mark(self,phase):
        if self.started is None:
            return
        now=time.perf_counter()
        self.timings[phase]=round((now-self.last)*1000,3)
        self.last=now

    def end(self):
        if self.started is None:
            return
        finished=time.perf_counter()
        values=dict(self.timings,total_ms=round((finished-self.started)*1000,3))
        self.started=None
        # Keep diagnostic file I/O outside drag image creation and repaint.
        QTimer.singleShot(0,lambda:record('drag_lift',**values,
            loop_return_ms=round((time.perf_counter()-finished)*1000,3)))


def playback_sample(binding, player):
    """Sample a few startup/pause transitions; never serialize track URLs or IDs."""
    tick = getattr(binding, '_trace_tick', 0) + 1
    binding._trace_tick = tick
    paused = bool(getattr(binding.tile, 'paused', False))
    changed = paused != getattr(binding, '_trace_paused', None)
    binding._trace_paused = paused
    count = getattr(binding, '_trace_count', 0)
    if count >= 8 or not (tick in (1, 4, 12, 40) or changed):
        return
    binding._trace_count = count + 1
    prop = player._prop
    tracks = prop('track-list', []) or []
    audio = [track for track in tracks if isinstance(track, dict) and track.get('type') == 'audio']
    def number(name):
        value = prop(name, None)
        return round(float(value), 3) if isinstance(value, (int, float)) else None
    record('playback_sample', tick=tick, paused=paused,
           dash=bool(getattr(binding.stream, 'is_dash', False)),
           audio_url=bool(getattr(binding.stream, 'audio_url', '')),
           pending_audio=bool(binding.pending_audio), attempts=binding.audio_attempts,
           audio_tracks=len(audio), selected_audio=sum(bool(t.get('selected')) for t in audio),
           audio_params=bool(prop('audio-params', None)),
           audio_output=bool(prop('audio-out-params', None)),
           audio_pts=number('audio-pts'), position=number('time-pos'),
           mute=bool(prop('mute', False)), volume=number('volume'),
           core_paused=bool(prop('pause', False)),
           video_ready=bool(prop('vo-configured', False)),
           idle=bool(prop('idle-active', False)),
           cache_paused=bool(prop('paused-for-cache', False)))


class SwapAudioTrace(QObject):
    """Observe real drop state and paint delivery, without changing audio or UI."""
    def __init__(self, plugin, source, target, path, attempt):
        super().__init__(QCoreApplication.instance())
        self.plugin, self.tiles = plugin, [source, target]
        self.path, self.attempt = path, attempt
        self.paints = {}
        for tile in self.tiles:
            if tile is not None and isValid(tile):
                button = getattr(tile, 'volume_button', None)
                if button is not None:
                    self.paints[id(button)] = 0
                    button.installEventFilter(self)
        self.sample('before')
        QTimer.singleShot(0, self.after_dispatch)
        QTimer.singleShot(300, self.finish)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Paint:
            self.paints[id(watched)] = self.paints.get(id(watched), 0) + 1
        return False

    def after_dispatch(self):
        self.sample('after_dispatch')

    def sample(self, stage):
        for member, tile in enumerate(self.tiles):
            if tile is None or not isValid(tile):
                record('swap_audio', stage=stage, attempt=self.attempt, member=member, valid=False)
                continue
            player = self.plugin._host_player(tile)
            prop = getattr(player, '_prop', None)
            core_mute = prop('mute', None) if callable(prop) else None
            button = tile.volume_button
            positions = self.plugin._host_tiles()
            record('swap_audio', stage=stage, attempt=self.attempt, path=self.path,
                member=member, valid=True,
                position=next((i for i,t in enumerate(positions) if t is tile),-1),
                video=bool((tile.room or {}).get('watchlater_bvid')),
                tile_muted=bool(tile.muted), button_muted=bool(button.muted),
                player_muted=bool(player.muted) if player is not None else None,
                core_muted=bool(core_mute) if core_mute is not None else None,
                volume=int(tile.volume), button_volume=int(button.level),
                bar_visible=tile.bottom.isVisible(), button_visible=button.isVisible(),
                bar_updates=tile.bottom.updatesEnabled(), button_updates=button.updatesEnabled(),
                paints=self.paints.get(id(button),0))

    def finish(self):
        self.sample('settled')
        for tile in self.tiles:
            if tile is not None and isValid(tile):
                tile.volume_button.removeEventFilter(self)
        self.deleteLater()


def trace_audio_drop(plugin, target, event):
    global _audio_swaps
    if _audio_swaps >= 4:
        return
    source = plugin._drag_source_tile(event)
    mime = event.mimeData()
    if source is None and mime.hasFormat('application/x-ddm-room'):
        room_id = bytes(mime.data('application/x-ddm-room')).decode('utf-8', errors='replace')
        source = next((tile for tile in plugin._host_tiles()
                       if str((tile.room or {}).get('room_id') or '') == room_id),None)
    if not (plugin._binding_for_tile(target) or plugin._binding_for_tile(source)):
        return
    _audio_swaps += 1
    path = 'tile' if mime.hasFormat('application/x-ddm-tile') else 'room-or-video'
    SwapAudioTrace(plugin, source, target, path, _audio_swaps)
