"""Cover annotations and uploader details; no playback or network ownership."""
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from ddm import theme
from .models import format_count, format_duration, normalize_cover_url


def cover_statistics(item):
    return [(kind, format_count(value)) for kind, value in
            (('play', item.view_count), ('danmaku', item.danmaku_count)) if value is not None]


def cover_description(item):
    labels = {'play': '播放量', 'danmaku': '弹幕数'}
    parts = [labels[kind] + text for kind, text in cover_statistics(item)]
    if item.duration > 0:
        parts.append('时长' + format_duration(item.duration))
    if item.is_finished:
        parts.append('已看完')
    return '，'.join(parts)


def avatar_thumbnail(url):
    url = normalize_cover_url(url)
    if not url:
        return ''
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if (host == 'hdslb.com' or host.endswith('.hdslb.com')) and '@' not in parts.path:
        return urlunsplit(parts._replace(path=parts.path + '@64w_64h_1c.webp'))
    return url


def publication_text(item):
    stamp = item.pubdate or item.add_at
    if stamp <= 0:
        return '', ''
    try:
        date = datetime.fromtimestamp(stamp)
    except (ValueError, OSError, OverflowError):
        return '', ''
    return date.strftime('%m-%d'), ('发布于 ' if item.pubdate else '加入稍后再看于 ') + date.strftime('%Y-%m-%d')


def _stat_icon(painter, kind, rect):
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 16, rect.height() / 16)
    painter.setPen(QPen(QColor('white'), 1.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath()
    if kind == 'play':
        painter.drawEllipse(QRectF(1.4, 1.4, 13.2, 13.2))
        path.moveTo(6.3, 4.8)
        path.lineTo(10.4, 8)
        path.lineTo(6.3, 11.2)
        path.closeSubpath()
    else:
        path.moveTo(2, 2)
        path.lineTo(14, 2)
        path.lineTo(14, 11)
        path.lineTo(9, 11)
        path.lineTo(6, 14)
        path.lineTo(6, 11)
        path.lineTo(2, 11)
        path.closeSubpath()
        path.moveTo(5, 5)
        path.lineTo(11, 5)
        path.moveTo(5, 8)
        path.lineTo(9, 8)
    painter.drawPath(path)
    painter.restore()


def cover_footer_layout(rect, item, font):
    metrics = QFontMetrics(font)
    height = max(16, metrics.height())
    y = rect.bottom() - height - 8
    duration = format_duration(item.duration) if item.duration > 0 else ''
    time_width = metrics.horizontalAdvance(duration)
    time_rect = QRectF(rect.right() - time_width - 9, y, time_width, height)
    limit = time_rect.left() - 12 if duration else rect.right() - 9
    x, stats = rect.left() + 9, []
    for kind, text in cover_statistics(item):
        width = 18 + metrics.horizontalAdvance(text)
        if x + width > limit:
            break
        stats.append((kind, text, QRectF(x, y, 14, height), QRectF(x + 18, y, width - 18, height)))
        x += width + 10
    return stats, duration, time_rect


def draw_cover_footer(painter, rect, item, font):
    painter.save()
    painter.setFont(font)
    stats, duration, time_rect = cover_footer_layout(rect, item, font)
    gradient = QLinearGradient(rect.left(), max(rect.top(), rect.bottom() - 48), rect.left(), rect.bottom())
    gradient.setColorAt(0, QColor(0, 0, 0, 0))
    gradient.setColorAt(1, QColor(0, 0, 0, 190))
    painter.fillRect(QRectF(rect.left(), max(rect.top(), rect.bottom() - 48), rect.width(), min(48, rect.height())), gradient)
    painter.setPen(QColor('white'))
    for kind, text, icon, label in stats:
        _stat_icon(painter, kind, QRectF(icon.x(), icon.center().y() - 7, 14, 14))
        painter.drawText(label, Qt.AlignVCenter | Qt.AlignLeft, text)
    painter.drawText(time_rect, Qt.AlignVCenter | Qt.AlignRight, duration)
    painter.restore()


def finished_icon_rect(rect):
    side, margin = (24, 8) if rect.height() >= 72 else (16, 4)
    return QRectF(rect.right() - side - margin, rect.top() + margin, side, side)


def draw_finished_icon(painter, rect):
    badge = finished_icon_rect(rect)
    painter.save()
    painter.translate(badge.topLeft())
    painter.scale(badge.width() / 24, badge.height() / 24)
    badge = QRectF(0, 0, 24, 24)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, 170))
    painter.drawEllipse(badge)
    painter.setPen(QPen(QColor('white'), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(badge.adjusted(4, 4, -4, -4))
    path = QPainterPath()
    path.moveTo(badge.x() + 7.5, badge.y() + 12)
    path.lineTo(badge.x() + 10.5, badge.y() + 15)
    path.lineTo(badge.x() + 16.5, badge.y() + 9)
    painter.drawPath(path)
    painter.restore()


class UploaderAvatar(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._name, self._url, self._pixmap = '', '', None
        theme.signals().changed.connect(self.update)

    def set_author(self, name, url):
        if url != self._url:
            self._pixmap = None
        self._name, self._url = name, url
        self.setAccessibleName(name + '的头像')
        self.update()

    def set_pixmap(self, pixmap):
        if pixmap is self._pixmap:
            return
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        clip = QPainterPath()
        clip.addEllipse(rect)
        painter.setClipPath(clip)
        if self._pixmap is not None and not self._pixmap.isNull():
            source = QRectF(self._pixmap.rect())
            side = min(source.width(), source.height())
            painter.drawPixmap(rect, self._pixmap, QRectF(source.center().x() - side/2, source.center().y() - side/2, side, side))
        else:
            painter.fillRect(rect, theme.qcolor(theme.CONTENT_HOVER))
            painter.setPen(theme.qcolor(theme.TEXT2))
            painter.drawText(rect, Qt.AlignCenter, (self._name or '?')[0])
