"""Gift browsing, confirmation and image reply lifecycle; no platform requests."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabBar, QVBoxLayout, QWidget
from ddm import theme
from .interaction_models import format_yuchi, room_number
from .interaction_popup import AnchoredPopup, RefreshSpinner, picker_style
from .interaction_widgets import style_button


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
