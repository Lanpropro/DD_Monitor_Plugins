"""Responsive media cards using the host artwork cache and platform assets."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QSizePolicy, QVBoxLayout, QWidget

from ddm import images
from ddm.follow_folders import PLATFORM_NAMES
from ddm.room_artwork import add_platform_logos, room_platform
from .timeline import timecode
from .library_data import human_size


def picture(entry, key, room):
    path = entry.get(key, '')
    if path and Path(path).is_file():
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            return pixmap
    room_id = str(room.get('room_id') or '')
    if key == 'cover_path':
        return images.load_cached_image(room.get('cover_url', ''), 'covers') or images.load_room_cover(room_id)
    return images.load_cached_avatar(room.get('face', '')) or images.load_room_avatar(room_id)


def media_type(entry):
    if entry.get('kind') == 'clip':
        return '已生成切片'
    path = Path(entry.get('path', ''))
    if entry.get('index_format') == 'hls' or path.suffix.lower() in {'.m3u8', '.edl'}:
        return '切片素材'
    if entry.get('kind') == 'replay' or path.parent.name.casefold() == 'replays':
        return '即时重放'
    return '完整录像' if entry.get('kind') == 'recording' else '本地视频'


class MediaCard(QWidget):
    def __init__(self, entry):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        room = entry.get('room') or {}
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(6)
        self.image = QLabel('暂无封面')
        self.image.setObjectName('SettingsHint')
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.cover = picture(entry, 'cover_path', room)
        if self.cover is not None:
            self.image.setPixmap(self.cover.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        box.addWidget(self.image)
        self.title = QLabel(str(room.get('title') or entry.get('title') or '未命名录像'))
        self.title.setObjectName('PluginName')
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        box.addWidget(self.title)
        author = QHBoxLayout()
        avatar = QLabel()
        avatar.setFixedSize(22, 22)
        face = picture(entry, 'avatar_path', room)
        if face is not None:
            avatar.setPixmap(face.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        author.addWidget(avatar)
        if room:
            add_platform_logos(author, [room], self)
        platform = room_platform(room) if room else ''
        name = ' · '.join(filter(None, [PLATFORM_NAMES.get(platform, platform), str(room.get('uname') or '本地视频')]))
        label = QLabel(name)
        label.setObjectName('SettingsHint')
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        author.addWidget(label, 1)
        self.type_label = QLabel(entry.get('state_label') or media_type(entry))
        self.type_label.setObjectName('SettingsHint')
        author.addWidget(self.type_label)
        box.addLayout(author)
        details = QHBoxLayout()
        stamp = date_range(entry).split('\n')[0]
        date = QLabel(stamp)
        date.setObjectName('SettingsHint')
        date.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        details.addWidget(date, 1)
        duration = QLabel(timecode(entry['duration']) if entry.get('duration') else '待探测')
        duration.setObjectName('SettingsHint')
        details.addWidget(duration)
        box.addLayout(details)
        self.file_info = QLabel('')
        self.file_info.setObjectName('SettingsHint')
        box.addWidget(self.file_info)
        extension = Path(entry.get('path', '')).suffix.upper().lstrip('.')
        self.setToolTip('\n'.join([self.title.text(), name, media_type(entry) + (' · ' + extension if extension else ''),
                                  date_range(entry), str(entry.get('path') or '')]))
        self.setAccessibleName(self.title.text() + ' · ' + name + ' · ' + self.type_label.text())

    def set_file_info(self, info):
        self.file_info.setText(('文件缺失或不完整 · ' if info.get('missing') else '') + human_size(info.get('bytes', 0)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(1, self.width() - 20)
        height = round(width * 9 / 16)
        self.image.setFixedHeight(height)
        if self.cover is not None:
            scaled = self.cover.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.image.setPixmap(scaled.copy((scaled.width() - width) // 2, (scaled.height() - height) // 2, width, height))


class MediaGrid(QListWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName('replayMediaGrid')
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWrapping(True)
        self.setSpacing(6)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        available = max(1, self.viewport().width() - 12)
        columns = max(1, available // 288)
        width = min(360, max(240, available // columns - 12))
        size = QSize(width, round((width - 20) * 9 / 16) + 134)
        if self.gridSize() != size + QSize(12, 12):
            self.setGridSize(size + QSize(12, 12))
        for index in range(self.count()):
            self.item(index).setSizeHint(size)


def date_range(entry):
    start = entry.get('created_at') or 0
    if not start:
        return '时间未知'
    text = datetime.fromtimestamp(start).strftime('%Y-%m-%d %H:%M:%S')
    if entry.get('ended_at'):
        text += '\n至 ' + datetime.fromtimestamp(entry['ended_at']).strftime('%Y-%m-%d %H:%M:%S')
    return text
