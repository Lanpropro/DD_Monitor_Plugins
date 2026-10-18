"""send2 emoticon tabs, permission-aware picker and lazy images."""
import requests
import hashlib
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QStyle, QStyleOptionTab, QStylePainter, QTabBar, QToolButton,
    QVBoxLayout, QWidget, QWidgetAction,
)

from pathlib import Path
from ddm import config, theme
from .gift_media import _fetch
from .popup import AnchoredPopup, picker_style
from .workers import start_worker as _start

def _style_button(button):
    button.setObjectName("TileCtrl")
    # 与主题注入的浅色局部样式放在同一层，并保留状态选择器的优先级。
    button.setStyleSheet("""
        QPushButton#TileCtrl:disabled {background: transparent; color: palette(tooltip-text);}
        QPushButton#TileCtrl:focus {border: 1px solid palette(highlight);}
    """)
    button.setCursor(Qt.PointingHandCursor)
    button.setAutoDefault(False)


class EmoticonButton(QPushButton):
    """与播放器控制同样使用矢量绘制，避免字体笑脸过小或被替换为彩色 emoji。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        color = theme.TEXT2 if self.isEnabled() else theme.TEXT4
        if self.isEnabled() and (self.underMouse() or self.property("hovered") is True):
            color = theme.ACCENT if theme.is_light() else theme.BG
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.translate(self.width() / 2, self.height() / 2)
        pen = QPen(theme.qcolor(color), 1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(-8, -8, 16, 16))
        smile = QPainterPath(QPointF(-3.6, 1.8))
        smile.quadTo(0, 6, 3.6, 1.8)
        painter.drawPath(smile)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(color))
        for x in (-3, 3):
            painter.drawEllipse(QPointF(x, -2.3), .9, .9)


def _load_image(url, subdir):
    legacy = Path(config.REPO) / 'cache' / subdir / (hashlib.md5(url.encode('utf-8')).hexdigest()+'.png')
    if legacy.is_file():
        image = QImage(str(legacy))
        if not image.isNull():return image
    return QImage(_fetch(url, Path(config.REPO) / 'cache' / subdir))


class _EmoticonImageLoader(QThread):
    """Bounded QThreads decode QImage; QPixmap stays on the GUI thread."""
    loaded = Signal(int, str, QImage)

    def __init__(self, generation, urls):
        super().__init__()
        self.generation, self.urls = generation, urls

    def run(self):
        for url in self.urls:
            if self.isInterruptionRequested():
                break
            try:
                pixmap = _load_image(url, "bili_live/emoticons")
            except Exception:
                continue
            if self.isInterruptionRequested():
                break
            if pixmap is not None and not pixmap.isNull():
                # 图标按 32 逻辑像素显示，保留 2x 分辨率，避免缓存整张原图。
                pixmap = pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.loaded.emit(self.generation, url, pixmap)


class EmoticonTabs(QTabBar):
    """包名和封面共用固定方形位置，异步换图时不重排分类。"""

    def tabSizeHint(self, index):
        return QSize(40, 40)

    def minimumTabSizeHint(self, index):
        return self.tabSizeHint(index)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        region = QRegion(self.rect())
        for name in ("ScrollLeftButton", "ScrollRightButton"):
            arrow = self.findChild(QToolButton, name)
            if arrow is not None and arrow.isVisible():
                region = region.subtracted(QRegion(arrow.geometry()))
        painter.setClipRegion(region)
        selected = self.currentIndex()
        # 与原生 Tab 一样最后绘制选中项，边框、焦点及悬停仍由主题样式负责。
        order = [index for index in range(self.count()) if index != selected]
        if selected >= 0:
            order.append(selected)
        for index in order:
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            icon = QIcon(option.icon)
            option.text = option.fontMetrics.elidedText(option.text, self.elideMode(), option.rect.width() - 8)
            option.icon = QIcon()
            painter.drawControl(QStyle.CE_TabBarTab, option)
            if not icon.isNull():
                # 原生图标布局会给旁边的文字留空，纯封面需单独按方形中心绘制。
                rect = QRect(QPoint(), self.iconSize())
                rect.moveCenter(option.rect.center())
                mode = QIcon.Normal if option.state & QStyle.State_Enabled else QIcon.Disabled
                icon.paint(painter, rect, Qt.AlignCenter, mode,
                           QIcon.On if index == selected else QIcon.Off)


class EmoticonPopup(AnchoredPopup):
    selected = Signal(int, dict)
    retryRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EmoticonPopup")
        self.setStyleSheet(picker_style() + f"""
            #EmoticonButton {{border: 1px solid transparent; border-radius: {theme.RADIUS_SM}px;
                color: {theme.TEXT1}; font-size: 11px; padding: 1px;}}
            #EmoticonButton:hover, #EmoticonButton:focus {{background: {theme.CONTENT_HOVER};
                border-color: {theme.ACCENT};}}
            #EmoticonButton:disabled {{color: {theme.TEXT3};}}
            #EmoticonTabs {{background: transparent; font-size: {theme.FONT_CAPTION}px;}}
            #EmoticonTabs::tab {{color: {theme.TEXT2}; background: transparent;
                padding: 0; margin: 2px; border: 1px solid {theme.BORDER};
                border-radius: {theme.RADIUS_SM}px;}}
            #EmoticonTabs::tab:selected {{color: {theme.TEXT1}; background: {theme.CONTENT_HOVER};
                border-color: {theme.ACCENT};}}
        """)
        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QHBoxLayout()
        self.status = QLabel("正在加载表情…")
        self.status.setWordWrap(True)
        header.addWidget(self.status, 1)
        self.retry_button = QPushButton("重试")
        _style_button(self.retry_button)
        self.retry_button.setFixedSize(48, theme.TILE_CONTROL_HEIGHT)
        self.retry_button.setToolTip("重新获取当前直播间的表情列表")
        self.retry_button.clicked.connect(self.retryRequested)
        self.retry_button.hide()
        header.addWidget(self.retry_button)
        layout.addLayout(header)
        self.tabs = EmoticonTabs()
        self.tabs.setObjectName("EmoticonTabs")
        self.tabs.setAccessibleName("表情包分类")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setIconSize(QSize(24, 24))
        self.tabs.currentChanged.connect(self._select_package)
        self.tabs.hide()
        layout.addWidget(self.tabs)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(self.scroll, 1)
        self._packages = []
        self._covers = {}
        self._pixmaps = {}
        self._generation = 0
        self._image_workers = set()
        self._pending_urls = {}
        self._build_timer = QTimer(self)
        self._build_timer.setInterval(8)
        self._build_timer.timeout.connect(self._build_batch)
        self._clear_body()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6,6,6,6)
        outer.addWidget(holder)
        self._holder = holder
        self._holder.setFixedSize(340, 300)

    def _clear_body(self):
        self._pause_loading()
        body = QWidget()
        self.grid = QGridLayout(body)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.scroll.setWidget(body)
        self._images = {}
        self._emotes = []
        self._next_emote = 0

    def _pause_loading(self):
        self._build_timer.stop()
        self._generation += 1
        self._pending_urls.clear()
        self._requested_urls = set()
        for loader in self._image_workers:
            loader.requestInterruption()

    def _clear_packages(self):
        self._clear_body()
        self._packages = []
        self._covers.clear()
        self._pixmaps.clear()
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.tabs.blockSignals(False)
        self.tabs.hide()

    def set_loading(self):
        self._clear_packages()
        self.status.setText("正在加载表情…")
        self.status.show()
        self.retry_button.setText("刷新")
        self.retry_button.setEnabled(False)
        self.retry_button.show()

    def set_error(self, message):
        self.status.setText(message)
        self.status.show()
        self.retry_button.setText("重试")
        self.retry_button.setEnabled(True)
        self.retry_button.show()

    def set_packages(self, packages):
        self._clear_packages()
        self.retry_button.setText("刷新")
        self.retry_button.setEnabled(True)
        self.retry_button.show()
        self._packages = [p for p in packages if p.get("pkg_type") in (1, 2, 3, 5)]
        self.tabs.blockSignals(True)
        for package in self._packages:
            name = str(package.get("pkg_name") or "表情")
            if package["pkg_type"] == 3 and name.lower() == "emoji":
                name = "表情"
            index = self.tabs.addTab(name)
            self.tabs.setTabToolTip(index, name)
            self.tabs.setAccessibleTabName(index, name)
            cover = package.get("current_cover")
            if package["pkg_type"] != 3 and isinstance(cover, str) and cover.startswith(("https://", "http://")):
                self._covers.setdefault(cover, []).append(index)
        self.tabs.blockSignals(False)
        self.tabs.setVisible(bool(self._packages))
        self._select_package(self.tabs.currentIndex())

    def _select_package(self, index):
        self._clear_body()
        if 0 <= index < len(self._packages):
            package = self._packages[index]
            self._kind = package["pkg_type"]
            self._emotes = [e for e in package.get("emoticons") or [] if isinstance(e, dict)]
        self.status.setText("表情" if self._emotes else "暂无可用表情")
        self.status.show()
        self._resume_loading()

    def _build_batch(self):
        # 每批两行，长包不会一次占用 GUI 线程；未选中的包不创建按钮。
        end = min(self._next_emote + 10, len(self._emotes))
        for index in range(self._next_emote, end):
            emote = self._emotes[index]
            text = str(emote.get("emoji") or "表情")
            button = QToolButton()
            button.setText(text)
            button.setAccessibleName(text)
            button.setObjectName("EmoticonButton")
            button.setFixedSize(56, 56)
            button.setIconSize(QSize(32, 32))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setCursor(Qt.PointingHandCursor)
            available = emote.get("perm") == 1 and bool(emote.get(
                "emoji" if self._kind == 3 else "emoticon_unique"))
            button.setEnabled(available)
            button.setToolTip(text if available else
                              f"{text}：{emote.get('unlock_show_text') or '当前账号尚未解锁'}")
            button.clicked.connect(lambda _checked=False, k=self._kind, e=dict(emote): self._choose(k, e))
            self.grid.addWidget(button, index // 5, index % 5)
            url = emote.get("url")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                self._images.setdefault(url, []).append(button)
                if url in self._pixmaps:
                    button.setIcon(QIcon(self._pixmaps[url]))
                elif url not in self._requested_urls:
                    self._requested_urls.add(url)
                    self._pending_urls[url] = None
        self._next_emote = end
        if end == len(self._emotes):
            self._build_timer.stop()
        self._start_images()

    def _start_images(self):
        if not self.isVisible() or not self._pending_urls:
            return
        urls = [url for url in self._pending_urls if url in self._images]
        if not urls:
            if self._next_emote < len(self._emotes):
                return  # 等下一批当前包按钮提供 URL，不让导航封面抢在前面。
            urls = list(self._pending_urls)
        for url in urls[:max(0,4-len(self._image_workers))]:
            del self._pending_urls[url]
            loader = _EmoticonImageLoader(self._generation, [url])
            self._image_workers.add(loader)
            loader.loaded.connect(self._image_loaded)
            loader.finished.connect(self._images_finished)
            _start(loader, QThread.LowPriority)

    def _images_finished(self):
        self._image_workers.discard(self.sender())
        self._start_images()

    def _image_loaded(self, generation, url, pixmap):
        if generation != self._generation:
            return
        pixmap = QPixmap.fromImage(pixmap)
        self._pixmaps[url] = pixmap
        for index in self._covers.get(url, []):
            self.tabs.setTabIcon(index, QIcon(pixmap))
            # 加载成功才移除文字，缺图或失败仍有可读的包名作为回退。
            self.tabs.setTabText(index, "")
        for button in self._images.get(url, []):
            button.setIcon(QIcon(pixmap))

    def hideEvent(self, event):
        self._pause_loading()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._resume_loading()

    def _resume_loading(self):
        if not self.isVisible():
            return
        # 分类封面只加载少量导航图片，包内表情仍仅按当前 Tab 构建。
        for url in (*self._covers, *self._images):
            if url not in self._pixmaps and url not in self._requested_urls:
                self._requested_urls.add(url)
                self._pending_urls[url] = None
        if self._next_emote < len(self._emotes):
            self._build_timer.start()
        self._start_images()

    def _choose(self, kind, emote):
        self.hide()
        self.selected.emit(kind, emote)

    def open_at(self, button):
        screen = button.screen().availableGeometry()
        self._holder.setFixedSize(min(340, screen.width() - 16), min(300, screen.height() - 16))
        self.adjustSize()
        super().open_at(button)
