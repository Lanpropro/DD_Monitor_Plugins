"""Coordinate independent room sessions with one local translation service."""
from collections import deque
from dataclasses import replace
import weakref
from PySide6.QtCore import QObject, Signal
from .embedded_recognition import EmbeddedRecognition
from .local_translation import LocalTranslation
from .session import Session
from .glossary import room_terms


class SessionGroup(QObject):
    caption = Signal(object, str, str)
    status = Signal(str)
    metrics = Signal(object)
    running = Signal(bool)
    stopping = Signal(object)

    def __init__(self, primary, parent=None):
        super().__init__(parent)
        self.primary = primary
        self.primary._recognition_group = self
        self.sessions = weakref.WeakKeyDictionary()
        self.samples = deque(maxlen=300)
        self.local = None
        self._active = set()
        self._resources = {}
        self._names = {}
        self._loading = self._starting = False
        self._pending = None
        self._glossaries = {}
        self._connect(primary)

    @property
    def is_running(self):
        return bool(self._loading or self._active)

    def _connect(self, session):
        session.caption.connect(lambda original, translated: self._caption(session, original, translated))
        session.stopping.connect(lambda: self.stopping.emit(session.tile() if session.tile else None))
        session.status.connect(lambda text: self._status(session, text))
        session.running.connect(lambda active: self._running(session, active))
        session.metrics.connect(lambda value: self._metrics(session, value))

    def start(self, options, key, sources, ffmpeg, *, glossaries=None):
        options.validate()
        self.stop()
        self._glossaries = glossaries or {}
        EmbeddedRecognition.ensure_available()
        sources = list(sources)
        if not sources:
            raise ValueError('请选择至少一个正在播放的直播间')
        self.samples.clear()
        for index, (tile, source) in enumerate(sources):
            session = self.primary if index == 0 else Session(self, recognition_group=self)
            if index:
                self._connect(session)
            self.sessions[tile] = session
            self._names[session] = source.uname or source.room_id
        self._pending = (options, key, [(weakref.ref(tile), source) for tile, source in sources], ffmpeg)
        if options.translation_mode == 'local':
            self._loading = True
            self.running.emit(True)
            self.status.emit('正在加载本地翻译模型…')
            local = self.local = LocalTranslation(self)
            local.ready.connect(lambda endpoint: self._ready(local, endpoint))
            local.failed.connect(lambda message: self._failed(local, message))
            try:
                local.start(options)
            except Exception:
                self.stop()
                raise
        else:
            self._start_rooms()

    def _ready(self, local, endpoint):
        if self.local is local and self._pending is not None:
            self._start_rooms((endpoint, local.api_key))

    def _start_rooms(self, shared_translation=None):
        if self._pending is None:
            return
        options, key, sources, ffmpeg = self._pending
        self._pending = None
        self._starting = True
        self._loading = False
        try:
            for reference, source in sources:
                tile = reference()
                session = self.sessions.get(tile) if tile is not None else None
                if session is None:
                    continue
                try:
                    room_options = replace(options, glossary_language=options.target_language,
                                           glossary=room_terms(self._glossaries, source, options.target_language))
                    session.start(room_options, key, source, tile, ffmpeg, shared_translation=shared_translation)
                except (ValueError, RuntimeError, OSError) as error:
                    session.stop()
                    self.status.emit(f'{source.uname or source.room_id}：{error}')
        finally:
            self._starting = False
        self._idle()
        self.running.emit(self.is_running)

    def _failed(self, local, message):
        if self.local is local:
            self.stop()
            self.status.emit(message)

    def _caption(self, session, original, translated):
        if session not in self._active:
            return
        tile = session.tile() if session.tile else None
        if tile is not None and self.sessions.get(tile) is session:
            self.caption.emit(tile, original, translated)

    def _status(self, session, text):
        if session is self.primary or session in self.sessions.values():
            name = self._names.get(session)
            self.status.emit(f'{name}：{text}' if name and len(self.sessions) > 1 else text)

    def _running(self, session, active):
        if active:
            self._active.add(session)
        else:
            self._active.discard(session)
            self._resources.pop(session, None)
        self._idle()
        self.running.emit(self.is_running)

    def _idle(self):
        if not self._active and not self._loading and not self._starting and self.local is not None:
            local, self.local = self.local, None
            local.stop()
            local.deleteLater()

    def _metrics(self, session, value):
        value = dict(value)
        if value['event'] == 'resources':
            self._resources[session] = dict(value)
            totals = list(self._resources.values())
            rss = (sum(item['child_rss_bytes'] for item in totals)
                   if all(item['child_rss_bytes'] is not None for item in totals) else None)
            if self.local is not None and self.local.process.processId():
                try:
                    import psutil
                    local_rss = psutil.Process(self.local.process.processId()).memory_info().rss
                    rss = rss + local_rss if rss is not None else None
                except (ImportError, OSError):
                    rss = None
                except psutil.Error:
                    rss = None
            value.update(child_rss_bytes=rss, active_sources=len(self._active),
                         pending_pcm_bytes=sum(item['pending_pcm_bytes'] for item in totals),
                         dropped_audio_seconds=round(sum(item['dropped_audio_seconds'] for item in totals), 2))
        elif session._source is not None:
            value['room_id'] = session._source.room_id
        self.samples.append(value)
        self.metrics.emit(value)

    def session_for(self, tile):
        session = self.sessions.get(tile)
        return session if session in self._active else None

    def stop_tile(self, tile):
        session = self.sessions.get(tile)
        if session is not None:
            session.stop()
            self.sessions.pop(tile, None)
            self._names.pop(session, None)
            if session is not self.primary:
                session.deleteLater()
            if not self.sessions and self._loading:
                self.stop()

    def switch_source(self, tile, source):
        session = self.session_for(tile)
        if session is not None:
            terms = room_terms(self._glossaries, source, session._options.target_language)
            return session.switch_source(source, glossary=terms)
        if self._pending is not None:
            options, key, sources, ffmpeg = self._pending
            self._pending = (options, key, [(ref, source if ref() is tile else previous)
                                          for ref, previous in sources], ffmpeg)
        return False

    def stop(self):
        self._pending = None
        self._glossaries = {}
        self._loading = False
        self._starting = True
        sessions = list(dict.fromkeys([self.primary, *self.sessions.values()]))
        released = [session.stop() for session in sessions]
        for session in sessions:
            if session is not self.primary:
                session.deleteLater()
        self.sessions.clear()
        self._active.clear()
        self._resources.clear()
        self._names.clear()
        self._starting = False
        self._idle()
        self.running.emit(False)
        return all(released)
