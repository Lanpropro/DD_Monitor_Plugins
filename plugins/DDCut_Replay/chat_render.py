"""Deterministic replay lanes and bitmap overlays inside the existing libmpv."""
from bisect import bisect_left, bisect_right
from collections import OrderedDict
import ctypes

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication

from ddm import images, theme
from ddm.danmaku_content import content_runs
from ddm.danmaku_raster import CommentRaster
from ddm.super_chat import super_chat_palette, paint_sc_background, sc_message_document, sc_card_height


def layout_events(events, width, height, measure, font_size=36):
    lanes = [None] * max(1, min(32, int(height * .65 / (font_size + 10))))
    packets = []
    sc_ends = []
    previous_start = -1
    for event in events:
        if event.get('kind') == 'super_chat':
            start = event['time']
            end = start + max(0, float(event.get('duration', 60)))
            if end > start:
                lane = next((index for index, value in enumerate(sc_ends) if value <= start), len(sc_ends))
                if lane == len(sc_ends):
                    sc_ends.append(end)
                else:
                    sc_ends[lane] = end
                packets.append({'event': event, 'start': start, 'end': end, 'width': 540, 'speed': 0, 'lane': lane})
            continue
        start = max(event['time'], previous_start + .1)
        if start - event['time'] > 1:
            continue
        text_width = max(font_size, min(width * 2, measure(event))) + 12
        speed = (width + text_width) / 13
        lane = None
        for index, previous in enumerate(lanes):
            if previous is None or previous['end'] <= start:
                lane = index
                break
            right = width + previous['width'] - previous['speed'] * (start - previous['start'])
            gap = width - right - 20
            if gap >= 0 and (speed <= previous['speed'] or gap / (speed - previous['speed']) >= max(0, right / previous['speed'])):
                lane = index
                break
        if lane is None:
            continue
        end = start + 13
        if end <= start:
            continue
        packet = {'event': event, 'start': start, 'end': end, 'width': text_width, 'speed': speed, 'lane': lane}
        packets.append(packet)
        lanes[lane] = packet
        previous_start = start
    return sorted(packets, key=lambda packet: packet['start'])


def display_text(event):
    text = str(event.get('text') or '')
    return f"SC ¥{event.get('price', 0)} {event.get('uname') or ''}: {text}" if event.get('kind') == 'super_chat' else text


class ReplayChat(QObject):
    imagesChanged = Signal()

    def __init__(self, preview):
        super().__init__(preview)
        self.preview = preview
        self.events = []
        self.packets = []
        self.starts = []
        self.lifetime = 13
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
        scale = content_width / 1920
        pixels = max(12, round(self.font_size * scale))
        font = QFont(theme.FONT_DEFAULT)
        font.setPixelSize(pixels)
        metrics = QFontMetrics(font)
        signature = (content_width, content_height, self.font_size, self.opacity)
        if signature != self.signature:
            measure = lambda event: metrics.horizontalAdvance(display_text(event)) / scale
            self.packets = layout_events(self.events, 1920, content_height / scale, measure, pixels / scale)
            self.starts = [packet['start'] for packet in self.packets]
            self.lifetime = max((row['end'] - row['start'] for row in self.packets), default=13)
            self.signature = signature
            self.cache.clear()
        media_time = position - self.offset
        frame = (round(media_time, 3), signature, left, top)
        if frame == self.frame:
            return
        active = [(index, self.packets[index]) for index in range(bisect_left(self.starts, media_time - self.lifetime), bisect_right(self.starts, media_time))
                  if self.packets[index]['end'] > media_time][-64:]
        visible = set()
        sc_y = top + 8
        for slot, (index, packet) in enumerate(active):
            event = packet['event']
            for _text, url in content_runs(event):
                if url:
                    self._ensure_image(url, event)
            key = (index, signature)
            if key not in self.cache:
                text = display_text(event)
                color = QColor(str(event.get('color') or '#ffffff'))
                if not color.isValid():
                    color = QColor('#ffffff')
                raster = CommentRaster(font, max(1, pixels / 24), 1, content_width * 2, self.images)
                runs = content_runs(dict(event, text=text)) if event.get('kind') != 'super_chat' else [(text, '')]
                image = raster._render_content(text, color, runs).convertToFormat(QImage.Format_ARGB32_Premultiplied)
                if event.get('kind') == 'super_chat':
                    card_width = max(160, round(content_width * .30))
                    card_scale = card_width / 340
                    image = QImage(card_width, sc_card_height(event, card_width, card_scale), QImage.Format_ARGB32_Premultiplied)
                    image.fill(Qt.transparent)
                    painter = QPainter(image)
                    palette = super_chat_palette(event.get('price', 0))
                    paint_sc_background(painter, QRectF(image.rect()), palette['background_bottom_color'], palette['background_color'], 48 * card_scale)
                    painter.setPen(QColor('#20242c'))
                    painter.setFont(font)
                    painter.drawText(QRectF(8, 0, card_width - 16, 48 * card_scale), Qt.AlignVCenter,
                                     metrics.elidedText(f"{event.get('uname') or 'SC'}  ¥{event.get('price', 0)}", Qt.ElideRight, card_width - 16))
                    painter.translate(11 * card_scale, 56 * card_scale)
                    sc_message_document(event, card_width, card_scale).drawContents(painter)
                    painter.end()
                layer = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
                layer.fill(Qt.transparent)
                painter = QPainter(layer)
                painter.setOpacity(self.opacity / 100)
                painter.drawImage(0, 0, image)
                painter.end()
                buffer = ctypes.create_string_buffer(layer.constBits().tobytes())
                self.cache[key] = (buffer, layer.width(), layer.height(), layer.bytesPerLine())
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
            buffer, image_width, image_height, stride = self.cache[key]
            x = left + round((1920 - (media_time - packet['start']) * packet['speed']) * scale)
            y = top + round(packet['lane'] * (pixels + 10 * scale) + 8 * scale)
            if event.get('kind') == 'super_chat':
                x, y = left + content_width - image_width - 8, sc_y
                sc_y += image_height + 8
            core.overlay_add(slot, x, y, '&' + str(ctypes.addressof(buffer)), 0, 'bgra', image_width, image_height, stride)
            visible.add(slot)
        for slot in self.visible - visible:
            core.overlay_remove(slot)
        self.visible = visible
        self.frame = frame
