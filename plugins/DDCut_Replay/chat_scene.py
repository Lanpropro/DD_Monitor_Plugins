"""Shared, media-clock chat scene for preview and encoded video; QImage only."""
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
from ddm import theme
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


class ChatScene:
    def __init__(self, events, width, height, font_size=36, opacity=80, images=None):
        self.width, self.height = width, height
        self.font_size, self.opacity = font_size, opacity
        self.images = images if images is not None else {}
        self.cache = OrderedDict()
        scale = width / 1920
        pixels = max(12, round(font_size * scale))
        self.font = QFont(theme.danmaku_font_family())
        self.font.setPixelSize(pixels)
        metrics = QFontMetrics(self.font)
        self.packets = layout_events(events, 1920, height / scale,
            lambda event: metrics.horizontalAdvance(display_text(event)) / scale, pixels / scale)
        self.starts = [packet['start'] for packet in self.packets]
        self.lifetime = max((packet['end'] - packet['start'] for packet in self.packets), default=13)

    def picture(self, event):
        content_width = self.width
        scale = content_width / 1920
        font = self.font
        pixels = font.pixelSize()
        metrics = QFontMetrics(font)
        for _text, url in content_runs(event):
            if url and url not in self.images:
                path = event.get('image_paths', {}).get(url)
                picture = QImage(path) if path else QImage()
                if not picture.isNull():
                    self.images[url] = picture
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
            document = sc_message_document(event, card_width, card_scale)
            body_font = document.defaultFont()
            body_font.setFamily(theme.danmaku_font_family())
            document.setDefaultFont(body_font)
            document.drawContents(painter)
            painter.end()
        layer = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        painter.setOpacity(self.opacity / 100)
        painter.drawImage(0, 0, image)
        painter.end()
        return layer

    def layers_at(self, position, ensure_image=None):
        active = [(index, self.packets[index]) for index in range(
            bisect_left(self.starts, position - self.lifetime), bisect_right(self.starts, position))
            if self.packets[index]['end'] > position][-64:]
        scale = self.width / 1920
        sc_y = 8
        for index, packet in active:
            event = packet['event']
            if ensure_image:
                for _text, url in content_runs(event):
                    if url:
                        ensure_image(url, event)
            if index not in self.cache:
                self.cache[index] = self.picture(event)
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
            image = self.cache[index]
            x = round((1920 - (position - packet['start']) * packet['speed']) * scale)
            y = round(packet['lane'] * (self.font.pixelSize() + 10 * scale) + 8 * scale)
            if event.get('kind') == 'super_chat':
                x, y = self.width - image.width() - 8, sc_y
                sc_y += image.height() + 8
            yield image, x, y

    def frame_at(self, position):
        frame = QImage(self.width, self.height, QImage.Format_ARGB32_Premultiplied)
        frame.fill(Qt.transparent)
        painter = QPainter(frame)
        for image, x, y in self.layers_at(position):
            painter.drawImage(x, y, image)
        painter.end()
        return frame
