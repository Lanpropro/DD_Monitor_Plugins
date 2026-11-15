"""Per-tile interaction coordination; one owner for room, requests and cancellation."""
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QWidget
from ddm import theme
from .interaction_models import GiftPanel, InteractionError, LoginRequired, room_number
from .interaction_widgets import DanmakuEdit, SendDetailsDialog, style_button
from .interaction_gifts import GiftButton, GiftPopup
from .interaction_requests import Request


class DouyuComposer(QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner, self.room = owner, {}
        self.chat_relay = None
        if owner.platform.kind == "douyu":
            from .douyu_danmaku import relay_for
            self.chat_relay = relay_for(owner.platform)
        self.pending = self.popup = None
        self.closing = False
        self.identity_timer = QTimer(self)
        self.identity_timer.setInterval(250)
        self.identity_timer.timeout.connect(self.check_identity)
        self.setFixedHeight(26)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setObjectName("DanmakuComposer")
        self.setStyleSheet(f"QLineEdit {{padding:3px 6px; font-size:{theme.FONT_CONTROL}px; "
                          f"color:{theme.TEXT1}; placeholder-text-color:{theme.TEXT3};}}")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.edit = DanmakuEdit()
        self.edit.setPlaceholderText("输入斗鱼弹幕，Enter 发送")
        self.edit.setAccessibleName("发送到当前斗鱼直播间的弹幕")
        self.edit.setMaxLength(100)
        self.edit.setMinimumWidth(0)
        self.edit.returnPressed.connect(self.send_text)
        row.addWidget(self.edit, 1)
        self.gifts = GiftButton()
        self.gifts.setToolTip("选择斗鱼礼物并确认赠送")
        self.gifts.clicked.connect(self.open_gifts)
        row.addWidget(self.gifts)
        self.send = QPushButton("发送")
        style_button(self.send)
        self.send.setFocusPolicy(Qt.NoFocus)
        self.send.setFixedSize(44, 26)
        self.send.clicked.connect(self.send_text)
        row.addWidget(self.send)
        self.edit.textChanged.connect(self.update_controls)
        self.update_controls()
        self.hide()

    def update_controls(self):
        busy = self.pending is not None
        self.edit.setReadOnly(busy)
        self.send.setEnabled(not busy and bool(self.edit.text().strip()))
        self.send.setText("…" if busy else "发送")
        self.gifts.setEnabled(not busy)

    def resizeEvent(self, event):
        self.send.setVisible(self.width() >= 176)
        super().resizeEvent(event)

    def snapshot(self):
        jar = self.owner.platform._playback_cookies()
        identity = tuple(sorted((c.domain, c.path, c.name, c.value) for c in jar))
        return self.room.get("room_id", ""), identity

    def set_room(self, room):
        if room != self.room:
            if self.pending:
                self.pending.cancelled.set()
            if self.popup:
                self.popup.hide()
                self.popup.set_panel(GiftPanel((), ()))
            self.edit.clear()
        self.room = dict(room)
        supported = room.get("platform") == "douyu"
        try:
            room_number(room.get("room_id"))
        except InteractionError:
            supported = False
        self.setVisible(supported)

    def feedback(self, message, *, login_required=False):
        room = dict(self.room)
        action = None
        if login_required:
            action = lambda: self.owner.context.request_login("douyu", reauthenticate=True)
        self.owner.context.notify(f"【{room.get('uname') or '斗鱼'}（{room.get('room_id', '')}）】{message}",
                                  rooms=(room,), kind="warning" if login_required else "info", on_click=action)
        self.edit.setToolTip(message)
        if self.popup:
            self.popup.status.setText(message)

    def start(self, kind, operation, *, key=None):
        if self.closing or self.pending is not None:
            return
        if self.owner.sending and kind in ("text", "gift"):
            self.feedback("已有发送请求正在处理，请等待结果")
            return
        key = key or self.snapshot()
        if kind in ("text", "gift"):
            self.owner.sending = True
        request = Request((key, kind, self.edit.text(), dict(self.room)), operation)
        self.pending = request
        self.owner.requests.add(request)
        self.update_controls()
        if self.popup:
            self.popup.send.setEnabled(False)
            self.popup.spinner.set_busy(kind in ("catalog", "wallet"))
            if kind == "catalog":
                self.popup.wallet = None
                self.popup.wallet_label.setText("余额：读取中…")
        request.result.connect(self.completed)
        request.phase.connect(self.progress)
        request.finished.connect(lambda: self.owner.retire(request), Qt.QueuedConnection)
        self.identity_timer.start()
        request.start()

    def progress(self, request, stage):
        if self.closing or self.pending is not request:
            return
        log = getattr(self.owner.context, "log", None)
        if callable(log):
            room = request.key[0][0]
            log(f"[{self.room.get('platform', 'douyu')}互动] 房间={room} 阶段={stage} 耗时={time.monotonic() - request.started_at:.2f}s")
        label = {"account": "确认账号", "gateway": "获取网关", "connect": "连接网关",
                 "login": "确认互动身份", "wait_ack": "等待发送回执", "ack": "收到发送回执", "wait_broadcast": "接口已受理，等待弹幕广播", "broadcast": "已收到弹幕广播"}.get(stage)
        if label:
            self.edit.setToolTip(label)
            self.send.setText("等待" if stage in ("wait_ack", "wait_broadcast") else "…")

    def check_identity(self):
        if self.pending is not None and self.pending.key[0] != self.snapshot():
            self.pending.cancelled.set()

    def completed(self, request, info, value):
        key, kind, draft, room = info
        if kind in ("text", "gift"):
            self.owner.sending = False
        if self.pending is request:
            self.pending = None
            self.identity_timer.stop()
        if self.closing:
            return
        self.update_controls()
        if self.popup:
            self.popup.spinner.set_busy(False)
        if key != self.snapshot():
            if kind in ("text", "gift"):
                self.owner.context.notify(f"【{room.get('room_id', '')}】账号或房间已变化，请在原直播间核对发送结果", rooms=(room,))
            return
        if kind == "wallet":
            if self.popup:
                self.popup.set_wallet(None, str(value)) if isinstance(value, Exception) else self.popup.set_wallet(value)
        elif isinstance(value, Exception):
            log = getattr(self.owner.context, "log", None)
            if callable(log):
                log(f"[{self.room.get('platform', 'douyu')}互动] 房间={key[0]} 阶段=failed 耗时={time.monotonic() - request.started_at:.2f}s")
            self.feedback(str(value), login_required=isinstance(value, LoginRequired))
            if kind == "catalog" and self.popup:
                self.popup.wallet_label.setText("余额：未知")
        elif kind == "catalog":
            self.popup.set_panel(value)
        else:
            if kind == "text":
                from .interaction_models import ChatReceipt
                if isinstance(value, ChatReceipt) and self.chat_relay is not None:
                    self.chat_relay.confirmed.emit(key[0], value)
                if self.edit.text() == draft:
                    self.edit.clear()
            self.edit.setToolTip("")
            if kind == "gift" and self.popup:
                self.popup.status.setText("礼物已赠送")
        if self.popup:
            self.popup.update_total()
            if kind == "gift" and not isinstance(value, Exception) and self.popup.isVisible():
                self.refresh_wallet()

    def send_text(self):
        text = self.edit.text().strip()
        if not text:
            return
        key = self.snapshot()
        from .douyu_interaction import DouyuAPI
        client = DouyuAPI(self.owner.platform._playback_cookies())
        self.start("text", lambda cancelled, progress: client.send_text(key[0], text, cancelled, progress=progress), key=key)

    def open_gifts(self):
        if self.popup is None:
            self.popup = GiftPopup(self)
        self.popup.open_at(self.gifts)
        self.load_gifts()

    def load_gifts(self):
        key = self.snapshot()
        from .douyu_interaction import DouyuAPI
        client = DouyuAPI(self.owner.platform._playback_cookies())
        self.start("catalog", lambda cancelled, progress: client.gift_panel(key[0], cancelled), key=key)

    def refresh_wallet(self):
        key = self.snapshot()
        from .douyu_interaction import DouyuAPI
        client = DouyuAPI(self.owner.platform._playback_cookies())
        self.start("wallet", lambda cancelled, progress: client.wallet(key[0], cancelled), key=key)

    def send_gift(self, gift, count, key):
        if key != self.snapshot():
            return
        from .douyu_interaction import DouyuAPI
        client = DouyuAPI(self.owner.platform._playback_cookies())
        self.start("gift", lambda cancelled, progress: client.send_gift(key[0], gift, count, cancelled), key=key)

    def interaction_active(self):
        return self.edit.hasFocus() or bool(self.popup and self.popup.isVisible())

    def hideEvent(self, event):
        if self.popup:
            self.popup.hide()
        super().hideEvent(event)

    def shutdown(self):
        self.closing = True
        self.identity_timer.stop()
        if self.pending:
            self.pending.cancelled.set()
        if self.popup:
            self.popup.shutdown()
            self.popup = None

class HuyaComposer(DouyuComposer):
    def __init__(self, owner, parent=None):
        super().__init__(owner, parent)
        self.edit.setPlaceholderText("输入虎牙弹幕，Enter 发送")
        self.edit.setAccessibleName("发送到当前虎牙直播间的弹幕")
        self.gifts.hide()

    def set_room(self, room):
        super().set_room(room)
        supported = room.get("platform") == "huya"
        try:
            self.owner.platform.normalize(room.get("room_id", ""))
        except ValueError:
            supported = False
        self.setVisible(supported)

    def feedback(self, message, *, login_required=False):
        room = dict(self.room)
        action = (lambda: self.owner.context.request_login("huya", reauthenticate=True)) if login_required else lambda: self.show_failure_details(message)
        self.owner.context.notify(f"【{room.get('uname') or '虎牙'}（{room.get('room_id', '')}）】{message}",
            rooms=(room,), kind="warning" if login_required else "info", on_click=action)
        self.edit.setToolTip(message)

    def show_failure_details(self, message):
        dialog = SendDetailsDialog(dict(self.room), message, self)
        dialog.exec()

    def send_text(self):
        from .huya_interaction import HuyaAPI
        text = self.edit.text().strip()
        if not text:
            return
        key = self.snapshot()
        client = HuyaAPI(self.owner.platform, self.owner.platform._playback_cookies())
        self.start("text", lambda cancelled, progress: client.send_text(key[0], text, cancelled, progress=progress), key=key)

class DouyinComposer(HuyaComposer):
    def __init__(self, owner, parent=None):
        super().__init__(owner, parent)
        self.edit.setPlaceholderText("输入抖音弹幕，Enter 发送")
        self.edit.setAccessibleName("发送到当前抖音直播间的弹幕")
        self.edit.setMaxLength(50)
        self.browser_chat = None

    def set_room(self, room):
        DouyuComposer.set_room(self, room)
        self.setVisible(room.get("platform") == "douyin")

    def feedback(self, message, *, login_required=False):
        room = dict(self.room)
        action = (lambda: self.owner.context.request_login("douyin", reauthenticate=True)) if login_required else lambda: self.show_failure_details(message)
        self.owner.context.notify(f"【{room.get('uname') or '抖音'}（{room.get('room_id', '')}）】{message}",
            rooms=(room,), kind="warning" if login_required else "info", on_click=action)
        self.edit.setToolTip(message)

    def send_text(self):
        from .douyin_interaction import DouyinAPI, DeliveryUnconfirmed
        text = self.edit.text().strip()
        if not text:
            return
        key = self.snapshot()
        if self.browser_chat is None:
            from .douyin_browser import BrowserChat
            self.browser_chat = BrowserChat(self.owner.platform, self.owner.context, self)
        client = DouyinAPI(self.owner.platform, self.owner.platform._playback_cookies(),
                           transport=self.browser_chat.request)
        bridge = self.browser_chat
        def send(cancelled, progress):
            unconfirmed = False
            try:
                return client.send_text(key[0], text, cancelled, progress=progress)
            except DeliveryUnconfirmed:
                unconfirmed = True
                raise
            finally:
                bridge.finish(cancelled, delivery_unconfirmed=unconfirmed)
        self.start("text", send, key=key)


    def shutdown(self):
        if self.browser_chat is not None:
            self.browser_chat.shutdown()
        super().shutdown()
