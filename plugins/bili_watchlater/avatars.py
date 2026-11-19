"""PiP avatars share the cancellable, cookie-free Qt image loader."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from .covers import CoverLoader
from .models import normalize_cover_url


class AvatarLoader(QObject):
    ready = Signal(str, QPixmap)

    def __init__(self):
        super().__init__()
        self._urls = {}
        self._covers = CoverLoader(self)
        self._covers.image_ready.connect(self._delivered)
        self._covers.resume()
        self._shutdown = False

    def get(self, bvid, url):
        url = normalize_cover_url(url)
        if self._shutdown or not url:
            return
        self._urls[bvid] = url
        pixmap = self._covers.get(url)
        if pixmap is not None:
            self.ready.emit(bvid, pixmap)

    def _delivered(self, url):
        if self._shutdown:
            return
        pixmap = self._covers.cached(url)
        if pixmap is not None:
            for bvid, expected in tuple(self._urls.items()):
                if expected == url:
                    self.ready.emit(bvid, pixmap)

    def shutdown(self):
        self._shutdown = True
        self._urls.clear()
        self._covers.shutdown()
