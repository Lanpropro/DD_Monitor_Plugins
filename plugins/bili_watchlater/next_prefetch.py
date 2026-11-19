"""One pending next item per playback binding; Qt owns the coordinator."""
import threading
import requests
from PySide6.QtCore import QObject, QThread, Signal
from .request_retirement import dispose_finished_request, retire_page_request
from .warm_media import AUDIO_BYTES, VIDEO_BYTES, PreparedMedia, fetch_prefix


class PrefetchRequest(QThread):
    completed = Signal(object, object)

    def __init__(self, token, key, cookies, cid):
        super().__init__()
        self.token, self.key, self.cookies, self.cid = token, key, dict(cookies), cid
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        result = None
        try:
            from .vod_stream import resolve_vod
            _generation, bvid, part, quality = self.key
            stream = resolve_vod(bvid, cid=self.cid, cookies=self.cookies, page=part, qn=quality,
                                 hq=True, cancelled=self.cancelled.is_set)
            if self.cancelled.is_set():
                return
            with requests.Session() as session:
                video = fetch_prefix(session, stream.url, stream.headers, VIDEO_BYTES, self.cancelled.is_set)
                audio = (fetch_prefix(session, stream.audio_url, stream.headers, AUDIO_BYTES, self.cancelled.is_set)
                         if stream.is_dash and stream.audio_url and video is not None else None)
            if video is not None and (not stream.is_dash or not stream.audio_url or audio is not None):
                result = PreparedMedia(stream, video, audio)
        except Exception:
            pass  # Prefetch failure is silent; foreground resolution remains available.
        finally:
            self.cookies.clear()
        if not self.cancelled.is_set():
            self.completed.emit(self.token, result)


class NextPrefetch(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.entries = {}
        self.workers = set()
        self.sequence = 0

    def prepare(self, owner, key, cookies, cid):
        if owner in self.entries and self.entries[owner]['key'] == key:
            return
        self.drop(owner)
        self.sequence += 1
        token = self.sequence
        worker = PrefetchRequest(token, key, cookies, cid)
        self.entries[owner] = dict(key=key, token=token, result=None)
        self.workers.add(worker)
        worker.completed.connect(self._completed)
        worker.finished.connect(self._finished)
        try:
            worker.start()
        except RuntimeError:
            self.entries.pop(owner, None)
            self.workers.remove(worker)
            dispose_finished_request(worker)

    def _completed(self, token, result):
        entry = next((entry for entry in self.entries.values() if entry['token'] == token), None)
        if entry is not None:
            entry['result'] = result

    def _finished(self):
        worker = self.sender()
        if worker in self.workers:
            self.workers.remove(worker)
            dispose_finished_request(worker)

    def take(self, owner, key):
        entry = self.entries.get(owner)
        if entry is None or entry['key'] != key:
            return None
        result = entry['result']
        self.drop(owner)
        return result

    def drop(self, owner):
        entry = self.entries.pop(owner, None)
        if entry is None:
            return
        for worker in tuple(self.workers):
            if worker.token == entry['token']:
                worker.completed.disconnect(self._completed)
                worker.finished.disconnect(self._finished)
                self.workers.remove(worker)
                retire_page_request(worker)

    def clear(self):
        for owner in tuple(self.entries):
            self.drop(owner)
