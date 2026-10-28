"""Per-tile Douyu interaction controls, owned by domestic_live."""
import threading
import time
import weakref

import requests
from PySide6.QtCore import QEvent, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QTabBar, QVBoxLayout, QWidget)

from ddm import plugins as api, theme
from .douyu_interaction import DouyuAPI, GiftPanel, InteractionError, LoginRequired, format_yuchi, room_number
from .interaction_popup import AnchoredPopup, RefreshSpinner, picker_style
from .douyu_danmaku import relay_for


def style_button(button, *, diameter=None):
    # Match bili_live's TileCtrl buttons without requiring that plugin installed.
    button.setObjectName("TileCtrl")
    button.setStyleSheet("""
        QPushButton#TileCtrl:disabled {background:transparent; color:palette(tooltip-text);}
        QPushButton#TileCtrl:focus {border:1px solid palette(highlight);}
    """)
    if diameter is not None:
        button.setFixedSize(diameter, diameter)
        button.setStyleSheet(button.styleSheet() +
            f"QPushButton#TileCtrl {{border-radius:{diameter // 2}px; padding:0;}}")
    button.setCursor(Qt.PointingHandCursor)
    button.setAutoDefault(False)


class GiftButton(QPushButton):
    def __init__(self):
        super().__init__()
        style_button(self, diameter=24)
        self.setAccessibleName("选择直播间礼物")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(.875, .875)
        color = theme.TEXT2 if self.isEnabled() else theme.TEXT4
        if self.isEnabled() and self.underMouse():
            color = theme.ACCENT if theme.is_light() else theme.BG
        painter.setPen(QPen(theme.qcolor(color), 1.6))
        painter.drawRect(-7, -2, 14, 9)
        painter.drawRect(-8, -5, 16, 4)
        painter.drawLine(0, -5, 0, 7)
        painter.drawEllipse(-5, -9, 5, 4)
        painter.drawEllipse(0, -9, 5, 4)


class GiftCard(QPushButton):
    def __init__(self, gift):
        super().__init__()
        self.setObjectName("GiftCard")
        self.setAccessibleName(gift.name)
        self.setMinimumWidth(56)
        self.setFixedHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QVBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(1)
        self.image = QLabel()
        self.image.setFixedSize(42, 42)
        self.image.setAlignment(Qt.AlignCenter)
        row.addWidget(self.image, 0, Qt.AlignHCenter)
        for text in (gift.name, gift.cost()):
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            label.setTextFormat(Qt.PlainText)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(label)
        self.image.setAttribute(Qt.WA_TransparentForMouseEvents)


class Request(QThread):
    result = Signal(object, object, object)
    phase = Signal(object, str)

    def __init__(self, key, operation):
        super().__init__()
        self.key, self.operation = key, operation
        self.cancelled = threading.Event()
        self.started_at = time.monotonic()

    def run(self):
        try:
            value = self.operation(self.cancelled.is_set, lambda stage: self.phase.emit(self, stage))
        except Exception as error:
            room = self.key[-1] if isinstance(self.key, tuple) and self.key and isinstance(self.key[-1], dict) else {}
            platform = {"douyu": "斗鱼", "huya": "虎牙", "douyin": "抖音"}.get(room.get("platform"), "斗鱼")
            value = error if isinstance(error, InteractionError) else InteractionError(
                f"{platform}网络请求失败，请稍后重试" if isinstance(error, requests.RequestException)
                else f"{platform}互动请求失败，请刷新后重试")
        self.result.emit(self, self.key, value)


class DanmakuEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.composing = False

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and not event.modifiers() & (
                Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not self.composing and not event.isAutoRepeat() and not self.isReadOnly():
                self.returnPressed.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class GiftConfirmDialog(QDialog):
    def __init__(self, room, gift, count, wallet, parent=None):
        super().__init__(parent)
        self.setObjectName("DouyuGiftConfirm")
        self.setWindowTitle("确认赠送礼物")
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)
        self.setStyleSheet(f"""
            QDialog#DouyuGiftConfirm {{background:{theme.ELEVATED};}}
            QLabel {{color:{theme.TEXT1}; font-size:{theme.FONT_CONTROL}px;}}
            QLabel#GiftConfirmTitle {{font-size:18px; font-weight:600;}}
            QLabel#GiftConfirmRecipient {{font-size:15px; font-weight:600;}}
            QLabel#GiftConfirmMuted {{color:{theme.TEXT3};}}
            QLabel#GiftConfirmCost {{color:{theme.ACCENT}; font-size:18px; font-weight:600;}}
            QFrame#GiftConfirmDetails {{background:{theme.CONTENT}; border:1px solid {theme.BORDER}; border-radius:8px;}}
            QPushButton {{min-height:32px; min-width:84px; padding:0 14px;
                border:1px solid {theme.BORDER_STRONG}; border-radius:8px; color:{theme.TEXT1};}}
            QPushButton:hover {{background:{theme.CONTENT_HOVER};}}
            QPushButton#GiftConfirmSubmit {{background:{theme.ACCENT}; color:#04161f; border:none;}}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        def label(text, name=""):
            widget = QLabel(str(text))
            widget.setTextFormat(Qt.PlainText)
            widget.setWordWrap(True)
            widget.setObjectName(name)
            return widget
        layout.addWidget(label("确认赠送", "GiftConfirmTitle"))
        recipient = QVBoxLayout()
        recipient.setSpacing(4)
        recipient.addWidget(label("赠送给", "GiftConfirmMuted"))
        self.recipient = label(room.get("uname") or "主播", "GiftConfirmRecipient")
        recipient.addWidget(self.recipient)
        self.room_label = label("斗鱼房间 " + room_number(room.get("room_id")), "GiftConfirmMuted")
        recipient.addWidget(self.room_label)
        layout.addLayout(recipient)
        details = QFrame()
        details.setObjectName("GiftConfirmDetails")
        grid = QGridLayout(details)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(10)
        self.gift_label, self.quantity_label = label(gift.name), label(str(count))
        self.cost_label = label(gift.cost(count), "GiftConfirmCost")
        rows = [("礼物", self.gift_label), ("数量", self.quantity_label), ("本次消费", self.cost_label)]
        if wallet is not None:
            amount = f"{format_yuchi(wallet.yuchi)} 鱼翅" if gift.currency == "YUCHI" else f"{wallet.yuwan} 鱼丸"
            rows.append(("当前余额", label(amount)))
        for index, (title, value) in enumerate(rows):
            grid.addWidget(label(title, "GiftConfirmMuted"), index, 0)
            grid.addWidget(value, index, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(details)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel = QPushButton("取消")
        self.cancel.setDefault(True)
        self.cancel.clicked.connect(self.reject)
        self.confirm = QPushButton("确认赠送")
        self.confirm.setObjectName("GiftConfirmSubmit")
        self.confirm.setAutoDefault(False)
        self.confirm.clicked.connect(self.accept)
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.confirm)
        layout.addLayout(buttons)


class GiftPopup(AnchoredPopup):
    def __init__(self, composer):
        super().__init__(composer)
        self.composer, self.selected = composer, None
        self.wallet = None
        self._wallet_error = ""
        self._tabs = ()
        self.network = QNetworkAccessManager(self)
        self.images = set()
        self.setObjectName("DouyuGiftPopup")
        self.setWindowTitle("斗鱼礼物")
        self.resize(400, 340)
        self.setStyleSheet(picker_style())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        title = QHBoxLayout()
        self.wallet_label = QLabel("余额：读取中…")
        self.wallet_label.setTextFormat(Qt.PlainText)
        self.wallet_label.setWordWrap(True)
        self.wallet_label.setMinimumWidth(0)
        self.wallet_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title.addWidget(self.wallet_label, 1)
        self.spinner = RefreshSpinner()
        title.addWidget(self.spinner)
        refresh = QPushButton("刷新")
        style_button(refresh)
        refresh.setFixedSize(48, theme.TILE_CONTROL_HEIGHT)
        refresh.clicked.connect(composer.load_gifts)
        title.addWidget(refresh)
        layout.addLayout(title)
        self.status = QLabel("点击刷新获取当前直播间礼物")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs = QTabBar()
        self.tabs.setObjectName("GiftTabs")
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.addTab("礼物")
        layout.addWidget(self.tabs)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        cards = QWidget()
        cards.setObjectName("DouyuGiftCards")
        self.grid = QGridLayout(cards)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(3)
        self.grid.setAlignment(Qt.AlignTop)
        for column in range(5):
            self.grid.setColumnStretch(column, 1)
            self.grid.setColumnMinimumWidth(column, 56)
        scroll.setWidget(cards)
        layout.addWidget(scroll, 1)
        self.summary = QLabel("请选择礼物")
        self.summary.setTextFormat(Qt.PlainText)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        row.addWidget(QLabel("数量"))
        self.quantity = QSpinBox()
        self.quantity.setFixedSize(60, theme.TILE_CONTROL_HEIGHT)
        self.quantity.setButtonSymbols(QSpinBox.NoButtons)
        self.quantity.setAccessibleName("礼物数量")
        self.quantity.setToolTip("输入数量，或使用减号与加号调整")
        self.quantity.setRange(1, 9999)
        self.quantity.valueChanged.connect(self.update_total)
        self.decrease = QPushButton("−")
        self.increase = QPushButton("+")
        for button, title, step in ((self.decrease, "减少礼物数量", -1), (self.increase, "增加礼物数量", 1)):
            style_button(button)
            button.setFixedSize(26, theme.TILE_CONTROL_HEIGHT)
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.clicked.connect(lambda checked=False, step=step: self.quantity.stepBy(step))
        row.addWidget(self.decrease)
        row.addWidget(self.quantity)
        row.addWidget(self.increase)
        row.addStretch(1)
        self.send = QPushButton("确认赠送")
        style_button(self.send)
        self.send.setFixedSize(96, theme.TILE_CONTROL_HEIGHT)
        self.send.setEnabled(False)
        self.send.clicked.connect(self.confirm)
        row.addWidget(self.send)
        layout.addLayout(row)
        self.tabs.currentChanged.connect(self.select_tab)
        self.update_total()

    def set_panel(self, panel):
        if (panel.tabs != self._tabs or self.tabs.count() != len(panel.tabs) or
                not panel.tabs and self.grid.count()):
            previous = self.tabs.tabData(self.tabs.currentIndex())
            self._tabs = panel.tabs
            self.tabs.blockSignals(True)
            while self.tabs.count():
                self.tabs.removeTab(0)
            for tab in self._tabs:
                index = self.tabs.addTab(tab.name)
                self.tabs.setTabData(index, tab.id)
            selected = next((i for i, tab in enumerate(self._tabs) if tab.id == previous), 0)
            self.tabs.setCurrentIndex(selected)
            self.tabs.blockSignals(False)
            self.select_tab(selected)
        self.set_wallet(panel.wallet, panel.wallet_error)

    def set_wallet(self, wallet, error=""):
        self.wallet = wallet
        self.wallet_label.setText("余额：未知" if wallet is None else
            f"鱼丸：{wallet.yuwan}　鱼翅：{format_yuchi(wallet.yuchi)}")
        self.wallet_label.setToolTip(error)
        if error:
            self.status.setText(error)
        elif self._wallet_error and self.status.text() == self._wallet_error:
            self.status.setText("选择礼物后确认数量与消费金额")
        self._wallet_error = error
        self.update_total()

    def select_tab(self, index):
        self.set_gifts(self._tabs[index].items if 0 <= index < len(self._tabs) else ())

    def set_gifts(self, gifts):
        self.spinner.set_busy(False)
        self.stop_images()
        self.selected = None
        self.send.setEnabled(False)
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for index, gift in enumerate(gifts):
            button = GiftCard(gift)
            button.setCheckable(True)
            button.setProperty("gift_id", gift.id)
            button.setToolTip(gift.disabled or gift.name)
            button.setEnabled(not gift.disabled)
            button.clicked.connect(lambda checked=False, gift=gift: self.choose(gift))
            self.grid.addWidget(button, index // 5, index % 5)
            if gift.image:
                self.load_image(button, gift.image)
        self.status.setText("选择礼物后确认数量与消费金额" if gifts else "当前分类暂无礼物")
        self.summary.setText("请选择礼物")

    def load_image(self, button, url):
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(5000)
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        reply = self.network.get(request)
        self.images.add(reply)
        reply.readyRead.connect(lambda: reply.abort() if reply.bytesAvailable() > 1024 * 1024 else None)
        def finished():
            self.images.discard(reply)
            if reply.error() == QNetworkReply.NoError:
                image = QPixmap()
                if image.loadFromData(bytes(reply.readAll())):
                    button.image.setPixmap(image.scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            reply.deleteLater()
        reply.finished.connect(finished)

    def stop_images(self):
        for reply in tuple(self.images):
            reply.finished.disconnect()
            reply.readyRead.disconnect()
            reply.abort()
            reply.deleteLater()
        self.images.clear()

    def shutdown(self):
        self.stop_images()
        super().shutdown()

    def choose(self, gift):
        self.selected = gift
        for index in range(self.grid.count()):
            button = self.grid.itemAt(index).widget()
            button.setChecked(button.property("gift_id") == gift.id)
        self.quantity.setMaximum(gift.maximum)
        self.quantity.setValue(1)
        self.update_total()

    def update_total(self):
        gift = self.selected
        self.decrease.setEnabled(self.quantity.value() > self.quantity.minimum())
        self.increase.setEnabled(self.quantity.value() < self.quantity.maximum())
        self.summary.setText(f"{gift.name} × {self.quantity.value()} · {gift.cost(self.quantity.value())}" if gift else "请选择礼物")
        affordable = gift is None or self.wallet is None or gift.price * self.quantity.value() <= (
            self.wallet.yuchi if gift.currency == "YUCHI" else self.wallet.yuwan)
        self.send.setEnabled(gift is not None and not gift.disabled and affordable and self.composer.pending is None)
        self.send.setToolTip("余额不足" if not affordable else "核对主播、数量和金额后确认赠送")

    def confirm(self):
        gift = self.selected
        if gift is None or self.composer.pending is not None or not self.send.isEnabled():
            return
        count = self.quantity.value()
        key = self.composer.snapshot()
        question = GiftConfirmDialog(dict(self.composer.room), gift, count, self.wallet, self.composer.window())
        try:
            accepted = question.exec() == QDialog.Accepted
        finally:
            question.deleteLater()
        if accepted and key == self.composer.snapshot():
            self.composer.send_gift(gift, count, key)

class DouyuComposer(QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner, self.room = owner, {}
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
                from .douyu_interaction import ChatReceipt
                if isinstance(value, ChatReceipt):
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
        client = DouyuAPI(self.owner.platform._playback_cookies())
        self.start("text", lambda cancelled, progress: client.send_text(key[0], text, cancelled, progress=progress), key=key)

    def open_gifts(self):
        if self.popup is None:
            self.popup = GiftPopup(self)
        self.popup.open_at(self.gifts)
        self.load_gifts()

    def load_gifts(self):
        key = self.snapshot()
        client = DouyuAPI(self.owner.platform._playback_cookies())
        self.start("catalog", lambda cancelled, progress: client.gift_panel(key[0], cancelled), key=key)

    def refresh_wallet(self):
        key = self.snapshot()
        client = DouyuAPI(self.owner.platform._playback_cookies())
        self.start("wallet", lambda cancelled, progress: client.wallet(key[0], cancelled), key=key)

    def send_gift(self, gift, count, key):
        if key != self.snapshot():
            return
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
        dialog = QDialog(self)
        dialog.setWindowTitle("抖音弹幕发送详情" if self.room.get("platform") == "douyin" else "虎牙弹幕发送详情")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        label = QLabel(message, dialog)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        close = QPushButton("关闭", dialog)
        style_button(close)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
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
        from .douyin_interaction import DouyinAPI
        text = self.edit.text().strip()
        if not text:
            return
        key = self.snapshot()
        client = DouyinAPI(self.owner.platform, self.owner.platform._playback_cookies())
        self.start("text", lambda cancelled, progress: client.send_text(key[0], text, cancelled, progress=progress), key=key)


class DouyuInteraction:
    def __init__(self, context, platform, *, composer_class=DouyuComposer, control_id="douyu_interaction"):
        self.context, self.platform = context, platform
        self.composer_class = composer_class
        self.controls = weakref.WeakSet()
        self.requests = set()
        self.sending = False
        context.register_tile_control(control_id, self.make_control, priority=100)

    def make_control(self, context):
        class Control(api.TileControl):
            def set_room(self, room):
                self.widget.set_room(room)
                context.invalidate_layout()

            def interaction_active(self):
                return self.widget.interaction_active()

            def focus_input(self):
                if not self.widget.isVisible() or not self.widget.isEnabled():
                    return False
                self.widget.edit.setFocus(Qt.ShortcutFocusReason)
                return True

            def shutdown(self):
                self.widget.shutdown()

        widget = self.composer_class(self, context.parent)
        self.controls.add(widget)
        return Control(widget, minimum_width=90, preferred_width=240)

    def retire(self, request):
        request.wait()
        self.requests.discard(request)
        request.deleteLater()

    def shutdown(self):
        for widget in tuple(self.controls):
            widget.shutdown()
        for request in tuple(self.requests):
            request.cancelled.set()
        for request in tuple(self.requests):
            request.wait()
        self.requests.clear()
