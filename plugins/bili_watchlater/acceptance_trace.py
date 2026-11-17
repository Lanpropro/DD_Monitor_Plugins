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
