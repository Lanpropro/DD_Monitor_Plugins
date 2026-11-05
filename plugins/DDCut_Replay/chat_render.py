"""Deterministic replay lanes and bitmap overlays inside the existing libmpv."""
from collections import OrderedDict
import ctypes
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from ddm import images, theme
from .chat_scene import ChatScene


class ReplayChat(QObject):
    imagesChanged = Signal()

    def __init__(self, preview):
        super().__init__(preview)
        # Register the host's bundled chat font on the GUI thread before export workers start.
        theme.danmaku_font_family()
        self.preview = preview
        self.events = []
        self.packets = []
        self.offset = 0
        self.font_size = 36
        self.opacity = 80
        self.enabled = True
        self.images = {}
        self.loaders = {}
        self.cache = OrderedDict()
        self.visible = set()
        self.signature = None
        self.frame = None

    def set_events(self, events):
        self.events = events
        self.signature = None
        self.frame = None

    def clear(self):
        core = self.preview.core
        if core is not None:
            for index in self.visible:
                core.overlay_remove(index)
        self.visible.clear()
        self.cache.clear()
        self.scene = None
        self.signature = None
        self.frame = None

    def _loaded(self, url, pixmap):
        if not pixmap.isNull():
            self.images[url] = pixmap.toImage()
            self.cache.clear()
            self.signature = None
            self.frame = None
            self.imagesChanged.emit()

    def _ensure_image(self, url, event):
        if url in self.images or url in self.loaders:
            return
        path = (event.get('image_paths') or {}).get(url)
        picture = QImage(path) if path else None
        if picture is None or picture.isNull():
            cached = images.load_cached_emoticon(url)
            picture = cached.toImage() if cached is not None else None
        if picture is not None and not picture.isNull():
            self.images[url] = picture
            return
        if len(self.loaders) >= 16:
            return
        loader = images.AvatarLoader({url: url}, QApplication.instance(), subdir='emoticons')
        self.loaders[url] = loader
        loader.loaded.connect(self._loaded)
        loader.finished.connect(lambda: self.loaders.pop(url, None))
        loader.finished.connect(loader.deleteLater)
        QApplication.instance().aboutToQuit.connect(loader.wait)
        loader.start()

    def update(self, position):
        core = self.preview.core
        if core is None:
            return
        if not self.enabled or not self.events:
            if self.visible:
                self.clear()
            return
        dimensions = core.osd_dimensions or {}
        width, height = dimensions.get('w', 0), dimensions.get('h', 0)
        if not width or not height:
            return
        left, top = dimensions.get('ml', 0), dimensions.get('mt', 0)
        content_width = width - left - dimensions.get('mr', 0)
        content_height = height - top - dimensions.get('mb', 0)
        if content_width <= 0 or content_height <= 0:
            return
        signature = (content_width, content_height, self.font_size, self.opacity)
        if signature != self.signature:
            self.scene = ChatScene(self.events, content_width, content_height,
                                   self.font_size, self.opacity, self.images)
            self.packets = self.scene.packets
            self.signature = signature
            self.cache.clear()
        media_time = position - self.offset
        frame = (round(media_time, 3), signature, left, top)
        if frame == self.frame:
            return
        visible = set()
        for slot, (layer, x, y) in enumerate(self.scene.layers_at(media_time, self._ensure_image)):
            key = layer.cacheKey()
            if key not in self.cache:
                self.cache[key] = ctypes.create_string_buffer(layer.constBits().tobytes())
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
            buffer = self.cache[key]
            core.overlay_add(slot, left + x, top + y, '&' + str(ctypes.addressof(buffer)), 0,
                             'bgra', layer.width(), layer.height(), layer.bytesPerLine())
            visible.add(slot)
        for slot in self.visible - visible:
            core.overlay_remove(slot)
        self.visible = visible
        self.frame = frame
