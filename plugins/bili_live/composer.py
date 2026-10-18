"""Per-tile drafts and send2 composer, backed by captured credentials."""
import requests
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QStyle, QStyleOptionTab, QStylePainter, QTabBar, QToolButton,
    QVBoxLayout, QWidget, QWidgetAction,
)

from ddm import theme
from . import bili_api as bili
from .emoticons import EmoticonButton, EmoticonPopup, _style_button
from .workers import Request, start_worker, request_message, _active_requests, wait_for_requests

class DanmakuEdit(QLineEdit):
    """输入时接管单键快捷键，中文选词回车不触发发送。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._composing = False

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and not (
                event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            event.accept()
            return True
        return super().event(event)

    def inputMethodEvent(self, event):
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._composing:
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class DanmakuComposer(QWidget):
    deviceCookieReady = Signal()
    HEIGHT = 28

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._requests = set()
        self.session.changed.connect(self._session_changed)
        self.setObjectName("DanmakuComposer")
        self.setCursor(Qt.ArrowCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            #DanmakuComposer {{background: transparent; border: none;}}
            #DanmakuComposer QLineEdit {{padding: 3px 6px; font-size: {theme.FONT_CONTROL}px;}}
            #DanmakuSendFeedback {{background: palette(tooltip-base); color: palette(window-text);
                border: 1px solid palette(mid); border-radius: {theme.RADIUS_SM}px;
                padding: 6px 8px; font-size: {theme.FONT_CAPTION}px;}}
        """)
        self._room_id = ""
        self._closing = False
        self._generation = self._revision = 0
        self._auth = self._auth_identity(self._cookies())
        self._pending = self._catalog_pending = None
        self._catalog_key = None
        self._packages = None
        self.popup = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.edit = DanmakuEdit()
        self.edit.setAccessibleName("发送到当前直播间的弹幕")
        self.edit.setPlaceholderText("输入弹幕，Enter 发送")
        self.edit.setMinimumWidth(0)
        self.edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.edit.setFixedHeight(28)
        self.edit.textChanged.connect(self._text_changed)
        self.edit.returnPressed.connect(self.send_text)
        row.addWidget(self.edit, 1)
        self.emote_button = EmoticonButton()
        self.emote_button.setAccessibleName("选择直播间表情")
        self.emote_button.setToolTip("选择表情；大表情点击后直接发送")
        self.emote_button.setFixedSize(28, 28)
        self.emote_button.clicked.connect(self.open_emoticons)
        row.addWidget(self.emote_button)
        self.send_button = QPushButton("发送")
        self.send_button.setFixedSize(44, 28)
        self.send_button.clicked.connect(self.send_text)
        row.addWidget(self.send_button)
        for button in (self.emote_button, self.send_button):
            _style_button(button)
        self.feedback = QLabel("", self, Qt.ToolTip)
        self.feedback.setObjectName("DanmakuSendFeedback")
        self.feedback.setAttribute(Qt.WA_ShowWithoutActivating)
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.setMaximumWidth(380)
        self.feedback.setAccessibleName("弹幕发送状态")
        self.feedback.hide()
        self._update_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 小格保留输入和表情入口，回车仍可发送，不为单独按钮挤掉输入空间。
        self.send_button.setVisible(self.width() >= 144)

    def hideEvent(self, event):
        self.feedback.hide()
        if self.popup:
            self.popup.hide()
        super().hideEvent(event)

    @property
    def busy(self):
        return self._pending is not None

    def shutdown(self):
        self._closing = True
        self._cancel_requests()
        self._generation += 1
        self._pending = self._catalog_pending = None
        self._packages = self._catalog_key = None
        self.feedback.hide()
        if self.popup:
            self.popup.hide()
        self.setEnabled(False)

    @property
    def supported(self):
        return self._room_id.isascii() and self._room_id.isdigit() and bool(self._room_id.strip("0"))

    @staticmethod
    def _auth_identity(cookies):
        return cookies.get("SESSDATA", ""), cookies.get("bili_jct", "")

    def sync_credentials(self):
        cookies = self._cookies()
        identity = self._auth_identity(cookies)
        if identity != self._auth:
            self._cancel_requests()
            self._auth = identity
            self._generation += 1
            self._pending = self._catalog_pending = None
            self._packages = self._catalog_key = None
            self._feedback("")
            if self.popup:
                self.popup.set_loading()
                self.popup.hide()
            self._update_buttons()
        return cookies

    def set_room(self, room_id):
        room_id = str(room_id or "")
        if room_id != self._room_id:
            self._cancel_requests()
            self._generation += 1
            self._room_id = room_id
            self._pending = self._catalog_pending = None
            self._packages = self._catalog_key = None
            self.edit.clear()
            self._feedback("")
            if self.popup:
                self.popup.hide()
        self.setVisible(self.supported)
        self._update_buttons()

    def _text_changed(self):
        self._revision += 1
        self.feedback.hide()
        self._update_buttons()

    def _update_buttons(self):
        self.send_button.setEnabled(bool(self.edit.text().strip()) and not self.busy)
        self.send_button.setText("…" if self.busy else "发送")

    def _context(self):
        self.sync_credentials()
        # buvid3 的按需初始化不代表账号变化，不能使首次表情请求失效。
        return self._generation, self._room_id, self._auth

    def send_text(self):
        if self.edit.text().strip():
            self._send(self.edit.text(), 0)

    def _send(self, message, dm_type):
        cookies = self.sync_credentials()
        if self._closing or self.busy or not self.supported:
            return
        if not cookies.get("SESSDATA") or not cookies.get("bili_jct"):
            self._feedback("请先登录 B 站" if not cookies.get("SESSDATA") else
                           "登录缺少 bili_jct，请退出 B 站账号后重新登录")
            return
        context = (*self._context(), self._revision, dm_type)
        self._pending = context
        self._feedback("正在发送…")
        self._update_buttons()
        room_id = self._room_id
        worker = Request(context, lambda local: bili.send_danmaku(room_id, message, dm_type=dm_type, cookies=local, still_current=worker.still_current),
                         cookies=cookies, account_generation=self._account_generation(), require_device=True)
        worker.completed.connect(self._sent)
        worker.failed.connect(self._send_failed)
        self._start_request(worker)

    def _feedback(self, text):
        self.feedback.setText(text)
        self.feedback.setToolTip(text)
        self.feedback.setAccessibleDescription(text)
        self.edit.setToolTip(text)
        self.feedback.hide()
        if text and not self.busy and self.isVisible():
            self.feedback.adjustSize()
            anchor = self.edit.mapToGlobal(QPoint(0, 0))
            screen = self.screen().availableGeometry()
            x = max(screen.left(), min(anchor.x(), screen.right() - self.feedback.width() + 1))
            y = anchor.y() - self.feedback.height() - 4
            if y < screen.top():
                y = anchor.y() + self.edit.height() + 4
            self.feedback.move(x, min(y, screen.bottom() - self.feedback.height() + 1))
            self.feedback.show()

    def _finish_send(self, context):
        if context != self._pending:
            return False
        self._pending = None
        self._update_buttons()
        if context[:3] != self._context():
            self._feedback("")
            return False
        return True

    def _sent(self, context, _result):
        self.deviceCookieReady.emit()
        if self._finish_send(context):
            if context[4] == 0 and context[3] == self._revision:
                self.edit.clear()
            self._feedback("")

    def _send_failed(self, context, error):
        self.deviceCookieReady.emit()
        if self._finish_send(context):
            self._feedback(request_message(error, sending=True))

    def choose_emoticon(self, pkg_type, emote):
        if self._closing:
            return
        self.sync_credentials()
        if emote.get("perm") != 1:
            return
        if pkg_type == 3:
            self.edit.insert(str(emote.get("emoji") or ""))
        elif pkg_type in (1, 2, 5) and emote.get("emoticon_unique"):
            self._send(str(emote["emoticon_unique"]), 1)
        self.edit.setFocus(Qt.OtherFocusReason)

    def open_emoticons(self, _checked=False):
        cookies = self.sync_credentials()
        self.feedback.hide()
        if self._closing or not self.supported:
            return
        if not cookies.get("SESSDATA"):
            self._feedback("请先登录 B 站后选择表情")
            return
        if self.popup is None:
            self.popup = EmoticonPopup(self)
            self.popup.selected.connect(self._choose_cached_emoticon)
            self.popup.retryRequested.connect(self._reload_emoticons)
        self.popup.open_at(self.emote_button)
        context = self._context()
        if self._catalog_key == context and self._packages is not None:
            return
        if self._catalog_pending == context:
            return
        self.popup.set_loading()
        self._catalog_pending = context
        room_id = self._room_id
        worker = Request(context, lambda local: bili.get_emoticons(room_id, cookies=local),
                         cookies=cookies, account_generation=self._account_generation(), require_device=True)
        worker.completed.connect(self._emoticons_loaded)
        worker.failed.connect(self._emoticons_failed)
        self._start_request(worker)

    def _choose_cached_emoticon(self, kind, emote):
        # 菜单打开后可能退出/切换账号，旧的权限结果不能沿用。
        if self._catalog_key == self._context():
            self.choose_emoticon(kind, emote)

    def _reload_emoticons(self):
        self._catalog_key = None
        self.open_emoticons()

    def _emoticons_loaded(self, context, packages):
        self.deviceCookieReady.emit()
        if context != self._context():
            return
        self._catalog_pending = None
        self._catalog_key, self._packages = context, packages
        self.popup.set_packages(packages)

    def _emoticons_failed(self, context, error):
        self.deviceCookieReady.emit()
        if context != self._context():
            return
        self._catalog_pending = None
        self.popup.set_error(request_message(error))

    def _cookies(self):
        snapshot = self.session.snapshot()
        return dict(snapshot.values) if snapshot else {}

    def _account_generation(self):
        snapshot = self.session.snapshot()
        return snapshot.generation if snapshot else 0

    def _session_changed(self, _snapshot):
        if not self._closing:
            self.sync_credentials()

    def _cancel_requests(self):
        for worker in tuple(self._requests):
            worker.cancel()

    def _start_request(self, worker):
        self._requests.add(worker)
        worker.deviceReady.connect(self.session.publish_device)
        worker.finished.connect(lambda: self._requests.discard(worker))
        start_worker(worker)

    def interaction_active(self):
        return (self.edit.hasFocus() or self.edit._composing or
                bool(self.popup and self.popup.isVisible()))
