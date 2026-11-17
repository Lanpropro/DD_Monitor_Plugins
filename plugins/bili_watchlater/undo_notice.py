"""Small in-window removal feedback; no extra native window or layout reflow."""
from PySide6.QtCore import QAbstractAnimation, QEvent, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QIcon, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from ddm import motion, theme
from ddm.ui import continuous_path
from .button_feedback import StableActionButton
from .page_shell import card_color
from .card_presentation import avatar_thumbnail
from .entry_icon import GalleryIcon
from .page_panels import GalleryToolButton
from .undo_entries import AvatarPile, RemovedEntries
from .gallery_controls import ElidedLabel


class UndoNotice(QWidget):
    undo_requested = Signal()

    def __init__(self, page):
        super().__init__(page)
        self.setProperty('customControlSurface', True)
        self.items = []
        self.expanded = False
        self.detail_height = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 12, 10)
        layout.setSpacing(8)
        self.details = RemovedEntries(page,self)
        self.details.setFixedHeight(0)
        self.details.hide()
        layout.addWidget(self.details)
        self.header = QWidget(self)
        layout.addWidget(self.header)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0,0,0,0)
        row.setSpacing(16)
        self.pile = AvatarPile(page,self.header)
        self.pile.hide()
        row.addWidget(self.pile)
        labels = QVBoxLayout()
        labels.setSpacing(3)
        self.title = ElidedLabel(self.header)
        self.title.hide()
        labels.addWidget(self.title)
        self.message = QLabel(self)
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        labels.addWidget(self.message)
        row.addLayout(labels, 1)
        self.undo = StableActionButton('撤回', self)
        self.undo.setAccessibleName('撤回移除，重新加入稍后再看')
        self.undo.clicked.connect(self.undo_requested)
        row.addWidget(self.undo)
        self.expand = GalleryToolButton(self.header)
        self.expand.clicked.connect(self.toggle_details)
        self.expand.hide()
        row.addWidget(self.expand)
        self.details_animation = QVariantAnimation(self)
        self.details_animation.setDuration(motion.CONTROL_FEEDBACK_MS)
        self.details_animation.setEasingCurve(motion.THEME_WAVE_EASING)
        self.details_animation.valueChanged.connect(self._expand_frame)
        self.details.verticalScrollBar().valueChanged.connect(self.refresh_images)
        page.covers.image_ready.connect(self._image_ready)
        self.progress = 1.
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(motion.CONTROL_FEEDBACK_MS)
        self.animation.setEasingCurve(motion.THEME_WAVE_EASING)
        self.animation.valueChanged.connect(self._advance)
        self.animation.finished.connect(self._animation_finished)
        page.installEventFilter(self)
        self.hide()

    def set_items(self, items):
        self.items = list(items)
        self.pile.set_items(self.items)
        self.title.setVisible(len(self.items) == 1)
        self.title.set_full_text(self.items[-1].title if self.items else '')
        self.undo.setText('全部撤回' if len(self.items) > 1 else '撤回')
        self.expand.setVisible(len(self.items) > 1)
        scroll = self.details.verticalScrollBar()
        position = scroll.value()
        self.details.model().replace(self.items)
        self.details.doItemsLayout()
        scroll.setValue(position)
        if len(self.items) < 2:
            self.expanded = False
            self.details_animation.stop()
        self._sync_expand_icon()
        self.reposition()
        self.details.sync_buttons()

    def _sync_expand_icon(self):
        self.expand.setIcon(QIcon(GalleryIcon('collapse' if self.expanded else 'expand')))
        self.expand.setAccessibleName('收起已移除的视频' if self.expanded else '展开已移除的视频')
        self.expand.setToolTip(self.expand.accessibleName())

    def expanded_height(self):
        if not self.expanded:
            return 0
        page = self.parentWidget()
        header_bottom = page.window_header.geometry().bottom()+16
        available = max(0,page.height()-24-header_bottom-self.header.sizeHint().height()-28)
        row = self.details.row_height()
        count = min(len(self.items),page.preferences['undo_expand_rows'],available//row)
        return count*row

    def toggle_details(self):
        self.expanded = not self.expanded
        self._sync_expand_icon()
        self.details_animation.stop()
        target = self.expanded_height()
        if motion.enabled():
            self.details_animation.setStartValue(self.detail_height)
            self.details_animation.setEndValue(target)
            self.details_animation.start()
        else:
            self._expand_frame(target)

    def _expand_frame(self, height):
        self.detail_height = round(height)
        self.reposition()
        self.refresh_images()

    def image_urls(self):
        if not self.isVisible():
            return []
        items = [*self.pile.items,*(self.details.visible_items() if self.expanded else [])]
        return list(dict.fromkeys(url for item in items if (url := avatar_thumbnail(item.owner_face))))

    def refresh_images(self):
        for url in self.image_urls():
            self.parentWidget().covers.get(url)
        self.pile.refresh_images()
        self.details.viewport().update()

    def _image_ready(self, url):
        if url in self.image_urls():
            self.pile.refresh_images()
            self.details.viewport().update()

    def present(self, message, *, enabled=False, action=True):
        entering = self.isHidden()
        self.message.setText(message)
        self.setAccessibleName(message)
        self.undo.setVisible(action)
        self.undo.setEnabled(enabled)
        self.details.set_undo_enabled(enabled)
        self.show()
        self.raise_()
        self.animation.stop()
        if motion.enabled():
            if entering:
                self.progress = 0.
            self.animation.setStartValue(self.progress)
            self.animation.setEndValue(1.)
            self.animation.start()
        else:
            self.progress = 1.
        self.reposition()
        self.refresh_images()

    def dismiss(self, *, animated=True):
        self.animation.stop()
        if animated and self.isVisible() and motion.enabled():
            self.animation.setStartValue(self.progress)
            self.animation.setEndValue(0.)
            self.animation.start()
        else:
            self.hide()
            self.set_items([])

    def _advance(self, progress):
        self.progress = progress
        self.reposition()

    def _animation_finished(self):
        if self.progress == 0.:
            self.hide()
            self.set_items([])

    def reposition(self):
        width = max(1, min(640, self.parentWidget().width()-48))
        if self.details_animation.state() != QAbstractAnimation.Running:
            self.detail_height = self.expanded_height()
        self.details.setFixedHeight(self.detail_height)
        self.details.setVisible(self.detail_height > 0)
        height = max(56,self.header.sizeHint().height()+20)+self.detail_height+(8 if self.detail_height else 0)
        self.setGeometry((self.parentWidget().width()-width)//2,
            self.parentWidget().height()-height-24+round((1-self.progress)*(height+24)),width,height)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.reposition()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(theme.qcolor(theme.BORDER_STRONG), 1))
        painter.setBrush(theme.qcolor(card_color()))
        painter.drawPath(continuous_path(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),12))
