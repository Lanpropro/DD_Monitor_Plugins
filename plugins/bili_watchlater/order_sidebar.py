"""Virtualized large cards: one drag works both inside the queue and on wall tiles."""
from PySide6.QtCore import QMimeData, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QLabel, QListView,
                              QStyledItemDelegate, QVBoxLayout, QWidget)
from ddm import motion, theme
from .models import VOD_MIME, format_duration
from .card_presentation import avatar_thumbnail
from .widgets import relative_font
from .motion_trace import MotionTrace, measure
from .order_feedback import OrderFeedback
from .order_model import OrderModel

ORDER_MIME = 'application/x-bili-watchlater-order'


class OrderDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(160, max(100, round((self.parent().viewport().width()-12)*9/16))+12)

    def paint(self, painter, option, index):
        view = self.parent()
        item = index.data(Qt.UserRole)
        rect = QRectF(option.rect).adjusted(4, 4, -4, -8)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setClipPath(path)
        painter.fillRect(rect, theme.qcolor(theme.CONTENT))
        pixmap = view.page.covers.cached(item.cover_url)
        if pixmap is not None and not pixmap.isNull():
            source = QRectF(pixmap.rect())
            ratio = rect.width()/max(1,rect.height())
            if source.width()/source.height() > ratio:
                source.setWidth(source.height()*ratio)
            else:
                source.setHeight(source.width()/ratio)
            source.moveCenter(QRectF(pixmap.rect()).center())
            painter.drawPixmap(rect, pixmap, source)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, QColor(0,0,0,150))
        gradient.setColorAt(.6, QColor(0,0,0,25))
        gradient.setColorAt(1, QColor(0,0,0,180))
        painter.fillRect(rect, gradient)
        opacity = view.feedback.weights.get(index.row(), 0.)
        if opacity:
            painter.fillRect(rect, QColor(255,255,255,round(14*opacity)))
        if view.feedback.pressed == index.row():
            painter.fillRect(rect, QColor(0,0,0,32))
        avatar_rect = QRectF(rect.left()+10,rect.top()+10,30,30)
        painter.save()
        avatar_path = QPainterPath()
        avatar_path.addEllipse(avatar_rect)
        painter.setClipPath(avatar_path)
        painter.fillRect(avatar_rect, theme.qcolor(theme.ELEVATED))
        avatar = view.page.covers.cached(avatar_thumbnail(item.owner_face))
        if avatar is not None and not avatar.isNull():
            painter.drawPixmap(avatar_rect,avatar,QRectF(avatar.rect()))
        painter.restore()
        painter.setPen(QColor('white'))
        painter.setFont(relative_font(view.font(), 1, bold=True))
        title = painter.fontMetrics().elidedText(item.title, Qt.ElideRight, int(rect.width()-60))
        painter.drawText(rect.adjusted(48,10,-10,-10), Qt.AlignTop|Qt.AlignLeft, title)
        painter.setFont(relative_font(view.font(), .88))
        owner = painter.fontMetrics().elidedText(item.owner_name, Qt.ElideRight, int(rect.width()-60))
        painter.drawText(rect.adjusted(48,32,-10,-10), Qt.AlignTop|Qt.AlignLeft, owner)
        painter.drawText(rect.adjusted(10,10,-10,-10), Qt.AlignBottom|Qt.AlignLeft, str(index.row()+1))
        painter.drawText(rect.adjusted(10,10,-10,-10), Qt.AlignBottom|Qt.AlignRight, format_duration(item.duration))
        painter.setClipping(False)
        if view.currentIndex() == index:
            painter.setPen(QPen(theme.qcolor(theme.ACCENT), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect,8,8)
        elif opacity:
            painter.setPen(QPen(QColor(255,255,255,round(115*opacity)),1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect,8,8)
        painter.restore()


class OrderList(QListView):
    reordered = Signal(str, object)
    chosen = Signal(object)
    items_replaced = Signal()

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.setModel(OrderModel(self))
        self._pending_items = None
        self._press_pos = None
        self.setFrameShape(QListView.NoFrame)
        self.setStyleSheet('QListView { background: transparent; border: 0; }')
        self.setItemDelegate(OrderDelegate(self))
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.doubleClicked.connect(lambda row: self.chosen.emit(row.data(Qt.UserRole)))
        self.verticalScrollBar().valueChanged.connect(self.request_covers)
        self.cover_timer = QTimer(self)
        self.cover_timer.setSingleShot(True)
        self.cover_timer.setInterval(80)
        self.cover_timer.timeout.connect(self.load_covers)
        self.drag_scroll = QTimer(self)
        self.drag_scroll.setInterval(30)
        self.drag_scroll.timeout.connect(self.scroll_drag)
        self.drag_y = 0
        page.covers.image_ready.connect(self.cover_ready)
        self.motion_trace = MotionTrace(page, areas=[self], visible=self.isVisible,
                                        parent=self, prefix='order timing')
        self.feedback = OrderFeedback(self)
        self.drop_y = None

    @measure('replace')
    def replace(self, items):
        if self.feedback.dragging:
            self._pending_items = list(items)
            return
        self.feedback.clear()
        current = self.currentIndex()
        selected = current.data(Qt.UserRole).bvid if current.isValid() else None
        value = self.verticalScrollBar().value()
        self.model().replace(items)
        for position, item in enumerate(self.model().items):
            if item.bvid == selected:
                self.setCurrentIndex(self.model().index(position,0))
                break
        self.verticalScrollBar().setValue(value)
        self.items_replaced.emit()
        self.request_covers()

    def mousePressEvent(self, event):
        self._press_pos = event.position().toPoint() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def drag_mime(self, item):
        mime = QMimeData()
        mime.setData(VOD_MIME, item.bvid.encode('utf-8'))
        mime.setData(ORDER_MIME, item.bvid.encode('utf-8'))
        mime.setText(item.title)
        return mime

    def prepare_drag(self, index):
        item = index.data(Qt.UserRole)
        rect = self.visualRect(index)
        position = self._press_pos if self._press_pos is not None else rect.center()
        hotspot = position - rect.topLeft()
        hotspot = QPoint(max(0,min(hotspot.x(),rect.width()-1)),
                         max(0,min(hotspot.y(),rect.height()-1)))
        pixmap, hotspot = motion.lifted_drag(self.viewport().grab(rect),hotspot)
        drag = QDrag(self)
        drag.setMimeData(self.drag_mime(item))
        drag.setPixmap(pixmap)
        drag.setHotSpot(hotspot)
        return drag

    def startDrag(self, actions):
        index = self.currentIndex()
        if not index.isValid() or self.feedback.dragging:
            return
        self.feedback.clear()
        drag = self.prepare_drag(index)
        self.feedback.dragging = True
        try:
            drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
        finally:
            self.drag_scroll.stop()
            self.set_drop_y(None)
            self.feedback.dragging = False
            self._press_pos = None
            drag.deleteLater()
            if self._pending_items is not None:
                items, self._pending_items = self._pending_items, None
                self.replace(items)
            self.feedback.sync_pointer()

    def dragEnterEvent(self, event):
        if event.source() is self and event.mimeData().hasFormat(ORDER_MIME):
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)
        if event.isAccepted():
            self.drag_y = event.position().y()
            _index, y = self.insertion_at(event.position().toPoint())
            self.set_drop_y(y)
            self.drag_scroll.start()

    def scroll_drag(self):
        edge, height = 36, self.viewport().height()
        step = -12 if self.drag_y < edge else 12 if self.drag_y > height-edge else 0
        bar = self.verticalScrollBar()
        bar.setValue(bar.value()+step)
        _index, y = self.insertion_at(QPoint(2,round(self.drag_y)))
        self.set_drop_y(y)

    def dragLeaveEvent(self, event):
        self.drag_scroll.stop()
        self.set_drop_y(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drag_scroll.stop()
        self.set_drop_y(None)
        if event.source() is not self or not event.mimeData().hasFormat(ORDER_MIME):
            event.ignore()
            return
        index, _y = self.insertion_at(event.position().toPoint())
        before = self.model().index(index,0).data(Qt.UserRole).bvid if index < self.model().rowCount() else None
        key = bytes(event.mimeData().data(ORDER_MIME)).decode('utf-8')
        self.reordered.emit(key, before)
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def insertion_at(self, point):
        row = self.indexAt(point)
        if not row.isValid():
            return self.model().rowCount(), min(self.viewport().height()-2,point.y())
        rect = self.visualRect(row)
        after = point.y() > rect.center().y()
        return row.row() + int(after), rect.bottom()-2 if after else rect.top()+2

    def set_drop_y(self, y):
        for value in (self.drop_y,y):
            if value is not None:
                self.viewport().update(0,round(value)-5,self.viewport().width(),11)
        self.drop_y = y

    def request_covers(self, *_):
        if self.isVisible():
            self.cover_timer.start()

    @measure('load_covers')
    def load_covers(self):
        if not self.isVisible():
            return
        if self.page.covers._paused:
            self.page.covers.resume()
        for url in self.visible_urls():
            self.page.covers.get(url)

    @measure('visible_urls')
    def visible_urls(self):
        if not self.isVisible():
            return []
        covers, avatars = [], []
        for row in self.visible_items():
            covers.append(row.data(Qt.UserRole).cover_url)
            avatars.append(avatar_thumbnail(row.data(Qt.UserRole).owner_face))
        return [url for url in covers+avatars if url]

    def visible_items(self):
        start = max(0,self.indexAt(QPoint(1,0)).row())
        for index in range(start,self.model().rowCount()):
            row = self.model().index(index,0)
            if self.isRowHidden(index):
                continue
            rect = self.visualRect(row)
            if rect.top() >= self.viewport().height():
                break
            if rect.bottom() >= 0:
                yield row

    @measure('image_callback')
    def cover_ready(self, url):
        if self.isVisible():
            for row in self.visible_items():
                item = row.data(Qt.UserRole)
                if url in (item.cover_url, avatar_thumbnail(item.owner_face)):
                    self.viewport().update(self.visualRect(row))

    @measure('resize_layout')
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.doItemsLayout()
        self.request_covers()

    @measure('list_paint')
    def paintEvent(self, event):
        super().paintEvent(event)
        if self.drop_y is not None:
            painter = QPainter(self.viewport())
            painter.setPen(QPen(theme.qcolor(theme.ACCENT),2))
            painter.drawLine(6,round(self.drop_y),self.viewport().width()-6,round(self.drop_y))
            painter.end()

    def keyPressEvent(self, event):
        self.feedback.clear()
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self.feedback.clear()
        self.motion_trace.timer.stop()
        self.set_drop_y(None)
        super().hideEvent(event)


class OrderSidebar(QWidget):
    order_changed = Signal()
    def __init__(self, page, order, play, parent=None):
        super().__init__(parent)
        self.page, self.order = page, order
        box = QVBoxLayout(self)
        box.setContentsMargins(0,0,0,0)
        hint = QLabel('双击播放 · 拖动排序或拖入格子', self)
        hint.setWordWrap(True)
        hint.setObjectName('SectionLabel')
        box.addWidget(hint)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.list = OrderList(page, self)
        page.external_cover_urls = self.list.visible_urls
        def release_cover_source():
            if page.external_cover_urls == self.list.visible_urls:
                page.external_cover_urls = lambda: []
        self.destroyed.connect(release_cover_source)
        box.addWidget(self.list,1)
        self.list.chosen.connect(play)
        self.list.reordered.connect(self.reorder)
        self.query = ''
        self.list.items_replaced.connect(lambda: self.set_filter(self.query))

    def refresh(self):
        items = self.order.snapshot()
        self.list.replace(items)
        self.status.setText('' if items else '列表为空；打开稍后再看页面可刷新或登录。')
        self.status.setVisible(not items)

    def reorder(self, key, before):
        if self.order.move(key, before):
            self.refresh()
            self.order_changed.emit()

    def set_filter(self, text):
        self.query = str(text).strip().lower()
        for index in range(self.list.model().rowCount()):
            row = self.list.model().index(index,0)
            item = row.data(Qt.UserRole)
            self.list.setRowHidden(index,self.query not in f'{item.title} {item.owner_name} {item.bvid}'.lower())
        self.list.request_covers()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        if not self.page._cache_fresh():
            self.page.refresh_from_wall()
        self.list.request_covers()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.list.cover_timer.stop()
        self.list.drag_scroll.stop()
        if not self.page._visible:
            self.page.covers.pause()


class DeferredOrderSidebar(QWidget):
    """Do not build the browser or start account requests during host startup."""
    def __init__(self, factory):
        super().__init__()
        self.factory = factory
        self.content = None
        self.query = ''
        QVBoxLayout(self).setContentsMargins(0,0,0,0)

    def showEvent(self, event):
        super().showEvent(event)
        if self.content is None:
            self.content = self.factory()
            if self.content is not None:
                self.layout().addWidget(self.content)
                self.content.set_filter(self.query)

    def set_filter(self, text):
        self.query = text
        if self.content is not None:
            self.content.set_filter(text)
