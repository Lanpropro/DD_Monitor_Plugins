"""Bounded session/lift acceptance evidence; no account, media or credential values."""
import json
import os
from pathlib import Path
import tempfile
import time
from PySide6.QtCore import QObject,QTimer

_remaining=80


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
