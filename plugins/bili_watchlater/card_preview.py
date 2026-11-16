"""One active, silent storyboard preview per browsing page."""
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from .vod_preview import VodPreviewProvider


class PreviewRetirement(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.providers = []
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.collect)

    def retire(self, provider):
        provider.release()
        provider.setParent(self)
        self.providers.append(provider)
        self.collect()
        if self.providers:
            self.timer.start()

    def collect(self):
        for provider in tuple(self.providers):
            if provider._pool.activeThreadCount() == 0:
                self.providers.remove(provider)
                provider.deleteLater()
        if not self.providers:
            self.timer.stop()


_retirement = None


def retire_preview(provider):
    global _retirement
    if _retirement is None:
        _retirement = PreviewRetirement(QCoreApplication.instance())
    _retirement.retire(provider)


class CardPreview(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.target = None
        self.provider = None
        self.moment = 0.
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.setInterval(450)
        self.delay.timeout.connect(self.begin)
        self.frames = QTimer(self)
        self.frames.setInterval(700)
        self.frames.timeout.connect(self.advance)

    def watch(self, cover):
        cover.setMouseTracking(True)
        cover.installEventFilter(self)

    def eventFilter(self, cover, event):
        kind = event.type()
        if kind == QEvent.Enter and self.page.preferences['video_preview'] and self.page._visible:
            self.stop()
            item = cover._item
            if item is not None and item.cid and item.duration > 0:
                self.target = cover
                self.moment = min(max(0, item.progress), item.duration - 1)
                self.delay.start()
        elif cover is self.target:
            if kind in (QEvent.Leave, QEvent.Hide, QEvent.MouseButtonPress, QEvent.Wheel):
                self.stop()
            elif kind == QEvent.MouseMove and self.provider is not None:
                self.moment = max(0., min(.99, event.position().x() / max(1, cover.width()))) * cover._item.duration
                self.draw()
        return False

    def begin(self):
        if self.target is None or not self.target.isVisible() or not self.page.preferences['video_preview']:
            self.stop()
            return
        item = self.target._item
        self.provider = VodPreviewProvider(item.bvid, item.cid, self)
        self.provider.preview_ready.connect(self.draw)
        self.frames.start()
        self.draw()

    def advance(self):
        if self.target is not None:
            self.moment = (self.moment + 3) % max(1, self.target._item.duration)
            self.draw()

    def draw(self):
        if self.provider is None or self.target is None:
            return
        frame, _stamp = self.provider.frame_at(self.moment)
        if frame is not None:
            self.target.set_preview_frame(frame)

    def stop(self):
        self.delay.stop()
        self.frames.stop()
        if self.target is not None:
            self.target.set_preview_frame(None)
        self.target = None
        if self.provider is not None:
            provider, self.provider = self.provider, None
            provider.preview_ready.disconnect(self.draw)
            retire_preview(provider)
