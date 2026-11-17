"""Uploader pile and visible-only rows for expanded removal feedback."""
from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate, QWidget
from ddm import theme
from .card_presentation import UploaderAvatar, avatar_thumbnail
from .order_model import OrderModel
from .page_panels import two_line_title
from .button_feedback import StableActionButton


class AvatarPile(QWidget):
    def __init__(self, page, parent):
        super().__init__(parent)
        self.page = page
        self.avatars = [UploaderAvatar(self) for _ in range(3)]
        self.items = []
        self.setFixedHeight(34)

    def set_items(self, items):
        self.items = list(items)[-3:]
        self.setFixedWidth(34+20*max(0,len(self.items)-1))
        self.setVisible(bool(self.items))
        for i, avatar in enumerate(self.avatars):
            avatar.setVisible(i < len(self.items))
            if i < len(self.items):
                item = self.items[i]
                avatar.set_author(item.owner_name,avatar_thumbnail(item.owner_face))
                avatar.move(i*20,0)
                avatar.raise_()
            else:
                avatar.set_author('','')
                avatar.set_pixmap(None)

    def refresh_images(self):
        for avatar,item in zip(self.avatars,self.items):
            avatar.set_pixmap(self.page.covers.cached(avatar_thumbnail(item.owner_face)))


class RemovedDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(160,max(56,self.parent().fontMetrics().height()*2+12))

    def paint(self, painter, option, index):
        item = index.data(Qt.UserRole)
        rect = option.rect.adjusted(2,6,-6,-6)
        face = QRectF(rect.left(),rect.top(),34,34)
        painter.save()
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addEllipse(face)
        painter.setClipPath(clip)
        pixmap = self.parent().page.covers.cached(avatar_thumbnail(item.owner_face))
        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(face,pixmap,QRectF(pixmap.rect()))
        else:
            painter.fillRect(face,theme.qcolor(theme.CONTENT_HOVER))
            painter.setPen(theme.qcolor(theme.TEXT2))
            painter.drawText(face,Qt.AlignCenter,(item.owner_name or '?')[0])
        painter.restore()
        painter.save()
        painter.setFont(self.parent().font())
        painter.setPen(theme.qcolor(theme.TEXT1))
        text_rect = rect.adjusted(48,0,-self.parent().undo_button_width()-12,0)
        painter.drawText(text_rect,Qt.AlignLeft|Qt.AlignVCenter,
                         two_line_title(item.title,self.parent().font(),text_rect.width()))
        painter.restore()


class RemovedEntries(QListView):
    undo_requested = Signal(object)

    def __init__(self, page, parent):
        super().__init__(parent)
        self.page = page
        self._undo_buttons = []
        self._undo_enabled = False
        self.setModel(OrderModel(self))
        self.setItemDelegate(RemovedDelegate(self))
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(20)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.NoFrame)
        self.setStyleSheet('QListView { background: transparent; border: 0; }')
        self.setAccessibleName('本批已移除的视频')
        self.model().modelReset.connect(self.sync_buttons)
        self.model().dataChanged.connect(self.sync_buttons)

    def undo_button_width(self):
        return max(64,self.fontMetrics().horizontalAdvance('撤回')+28)

    def set_undo_enabled(self, enabled):
        self._undo_enabled = enabled
        self.sync_buttons()

    def sync_buttons(self, *_args):
        visible = list(self.visible_indexes()) if self.isVisible() else []
        while len(self._undo_buttons) < len(visible):
            button = StableActionButton('撤回',self.viewport())
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(self._undo_row)
            self._undo_buttons.append(button)
        for position,button in enumerate(self._undo_buttons):
            button.setVisible(position < len(visible))
            if position < len(visible):
                index = visible[position]
                item = index.data(Qt.UserRole)
                rect = self.visualRect(index)
                width = self.undo_button_width()
                button.setGeometry(rect.right()-width-8,rect.center().y()-17,width,34)
                button.setProperty('itemKey',item.key)
                button.setAccessibleName('撤回移除：'+item.title)
                button.setEnabled(self._undo_enabled)
                button.raise_()
            else:
                button.setProperty('itemKey',None)
                button.setAccessibleName('撤回移除')

    def _undo_row(self):
        if self._undo_enabled:
            self.undo_requested.emit(self.sender().property('itemKey'))

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_buttons()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx,dy)
        self.sync_buttons()

    def row_height(self):
        return self.itemDelegate().sizeHint(None,self.model().index(0,0)).height()

    def visible_indexes(self):
        first = max(0,self.indexAt(QPoint(1,0)).row())
        for row in range(first,self.model().rowCount()):
            index = self.model().index(row,0)
            if self.visualRect(index).top() > self.viewport().height():
                break
            yield index

    def visible_items(self):
        for index in self.visible_indexes():
            yield index.data(Qt.UserRole)
