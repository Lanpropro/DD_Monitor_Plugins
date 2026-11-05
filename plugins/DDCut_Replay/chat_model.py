"""Virtual chat list: keep recorded events as data, rather than one widget per row."""
from bisect import bisect_right

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtGui import QPixmap

from ddm.danmaku_content import content_runs
from .timeline import timecode
from .chat_scene import display_text


class ChatModel(QAbstractListModel):
    def __init__(self, renderer):
        super().__init__()
        self.renderer = renderer
        self.events = []
        self.rows = []
        self.times = []
        self.offset = 0
        renderer.imagesChanged.connect(self.refresh_images)

    def refresh_images(self):
        if self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1), [Qt.DecorationRole])

    def filter(self, query='', kind='all', offset=0):
        self.beginResetModel()
        self.offset = offset
        query = query.casefold().strip()
        self.rows = [row for row in self.events if (kind == 'all' or row['kind'] == kind)
                     and query in (str(row.get('uname') or '') + ' ' + str(row.get('text') or '')).casefold()]
        self.times = [row['time'] + offset for row in self.rows]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        event = self.rows[index.row()]
        if role == Qt.UserRole:
            return event
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            name = '' if event['kind'] == 'super_chat' else str(event.get('uname') or '') + ': '
            return f"{timecode(event['time'] + self.offset)}  {name}{display_text(event)}"
        if role == Qt.DecorationRole:
            for _text, url in content_runs(event):
                if url:
                    self.renderer._ensure_image(url, event)
                picture = self.renderer.images.get(url)
                if picture is not None:
                    return QPixmap.fromImage(picture).scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return None

    def index_at(self, position):
        index = bisect_right(self.times, position) - 1
        return self.index(index) if index >= 0 else QModelIndex()
