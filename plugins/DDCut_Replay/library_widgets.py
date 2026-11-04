"""Recording identity cells using the host artwork cache and platform assets."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ddm import images
from ddm.follow_folders import PLATFORM_NAMES
from ddm.room_artwork import add_platform_logos, room_platform
from .timeline import timecode


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


class IdentityCell(QWidget):
    def __init__(self, entry, *, author=False, compact=False):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        room = entry.get('room') or {}
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        self.image = QLabel()
        self.image.setFixedSize(36, 36) if author else self.image.setFixedSize(128, 72)
        self.image.setAlignment(Qt.AlignCenter)
        pixmap = picture(entry, 'avatar_path' if author else 'cover_path', room)
        if pixmap is not None:
            self.image.setPixmap(pixmap.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.image.setText('头像' if author else '暂无封面')
            self.image.setObjectName('SettingsHint')
        row.addWidget(self.image)
        text = QVBoxLayout()
        text.setSpacing(5)
        platform = room_platform(room) if room else ''
        name = str(room.get('uname') or '本地视频') if author else str(room.get('title') or entry.get('title') or '未命名录像')
        self.title = QLabel(name)
        self.title.setObjectName('PluginName')
        self.title.setToolTip(name)
        self.title.setMinimumWidth(0)
        from PySide6.QtWidgets import QSizePolicy
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text.addWidget(self.title)
        details = QHBoxLayout()
        if author and platform:
            add_platform_logos(details, [room], self)
        if compact and not author:
            avatar = QLabel()
            avatar.setFixedSize(18, 18)
            face = picture(entry, 'avatar_path', room)
            if face is not None:
                avatar.setPixmap(face.scaled(18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            details.addWidget(avatar)
            if platform:
                add_platform_logos(details, [room], self)
        subtitle = (PLATFORM_NAMES.get(platform, platform) + ' · ' + str(room.get('room_id') or '')) if author and platform else ('本地导入' if author else (Path(entry['path']).name if entry.get('path') else '直播录制'))
        if compact and not author:
            subtitle = PLATFORM_NAMES.get(platform, platform or '本地视频') + ' · ' + str(room.get('uname') or '本地导入')
        hint = QLabel(subtitle)
        hint.setObjectName('SettingsHint')
        hint.setToolTip(subtitle)
        hint.setMinimumWidth(0)
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        details.addWidget(hint, 1)
        text.addLayout(details)
        if compact and not author:
            stamp = QLabel(date_range(entry).split('\n')[0] + (' · ' + timecode(entry['duration']) if entry.get('duration') else ''))
            stamp.setObjectName('SettingsHint')
            stamp.setMinimumWidth(0)
            stamp.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            stamp.setToolTip(date_range(entry))
            text.addWidget(stamp)
        row.addLayout(text, 1)


def date_range(entry):
    start = entry.get('created_at') or 0
    if not start:
        return '时间未知'
    text = datetime.fromtimestamp(start).strftime('%Y-%m-%d %H:%M:%S')
    if entry.get('ended_at'):
        text += '\n至 ' + datetime.fromtimestamp(entry['ended_at']).strftime('%Y-%m-%d %H:%M:%S')
    return text
