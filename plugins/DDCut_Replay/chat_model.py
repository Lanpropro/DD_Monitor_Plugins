"""Virtual chat list: keep recorded events as data, rather than one widget per row."""
from bisect import bisect_left, bisect_right

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ddm.danmaku_content import content_runs
from ddm.search import matches_query
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
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1))

    def filter(self, query='', kind='all', offset=0):
        rows = [row for row in self.events if (kind == 'all' or row['kind'] == kind)
                and matches_query(str(row.get('uname') or '') + ' ' + str(row.get('text') or ''), query)]
        if rows == self.rows and offset == self.offset:
            return
        self.beginResetModel()
        self.offset = offset
        self.rows = rows
        self.times = [row['time'] + offset for row in rows]
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
        return None

    def runs(self, index):
        event = self.rows[index.row()]
        user = str(event.get('uname') or '')
        if event['kind'] == 'super_chat':
            user = f"SC ¥{event.get('price', 0)} {user}"
        yield f"{timecode(self.times[index.row()])}  {user}: ", None
        for text, url in content_runs(event):
            if url:
                self.renderer._ensure_image(url, event)
            yield text, self.renderer.images.get(url) if url else None

    def index_for_event(self, event):
        time = event['time'] + self.offset
        for row in range(bisect_left(self.times, time), bisect_right(self.times, time)):
            if self.rows[row] == event:
                return self.index(row)
        return QModelIndex()

    def index_at(self, position):
        index = bisect_right(self.times, position) - 1
        return self.index(index) if index >= 0 else QModelIndex()


class ChatDelegate(QStyledItemDelegate):
    IMAGE_HEIGHT = 26

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), self.IMAGE_HEIGHT + 8))

    def paint(self, painter, option, index):
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        item.text = ''
        style = item.widget.style() if item.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, item, painter, item.widget)
        painter.save()
        painter.setClipRect(item.rect)
        painter.setFont(item.font)
        selected = item.state & QStyle.State_Selected
        painter.setPen(item.palette.highlightedText().color() if selected else item.palette.text().color())
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)
        x, right = item.rect.left() + 8, item.rect.right() - 8
        for text, image in index.model().runs(index):
            if x >= right:
                break
            if image is not None and not image.isNull():
                size = image.size().scaled(64, self.IMAGE_HEIGHT, Qt.KeepAspectRatio)
                painter.drawImage(QRect(x, item.rect.center().y() - size.height() // 2,
                                        size.width(), size.height()), image)
                x += size.width() + 2
            else:
                label = item.fontMetrics.elidedText(text, Qt.ElideRight, right - x)
                painter.drawText(QRect(x, item.rect.top(), right - x, item.rect.height()), Qt.AlignVCenter, label)
                x += item.fontMetrics.horizontalAdvance(label)
        painter.restore()
