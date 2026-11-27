"""Room entry, connection status and member roster in the right console."""
from PySide6.QtCore import Qt, QRegularExpression, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ddm import theme
from ddm.avatar_widgets import Avatar
from ddm.ui.labels import ElidedLabel

from .panels import ConsoleIconButton
from .send_design import colors
from .watch_profile import avatar_pixmap, display_name

WAITING = "#d39648"
LOCKED = "#42c685"
INTRO = "大家先把同一主播设为主画面。房内成员可见你当前平台的公开头像、昵称和 ID。"
PLATFORMS = {
    "bilibili": "B 站", "douyu": "斗鱼", "douyin": "抖音", "huya": "虎牙",
    "youtube": "YouTube", "twitch": "Twitch", "chzzk": "CHZZK",
}


def text_label(text="", object_name=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    if object_name:
        label.setObjectName(object_name)
    return label


class WatchPartyPanel(QWidget):
    activity_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("WatchPartyPanel")
        self.setAccessibleName("好友同看")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._phase = ""
        panel_layout = QVBoxLayout(self)
        panel_layout.setContentsMargins(12, 9, 12, 10)
        panel_layout.setSpacing(7)
        self.content_scroll = QScrollArea()
        self.content_scroll.setFrameShape(QFrame.NoFrame)
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        content = QWidget()
        self.content_scroll.setWidget(content)
        content.setAutoFillBackground(False)
        self.content_scroll.viewport().setAutoFillBackground(False)
        panel_layout.addWidget(self.content_scroll, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        self.entry = QWidget()
        entry = QVBoxLayout(self.entry)
        entry.setContentsMargins(0, 0, 0, 0)
        entry.setSpacing(5)
        intro = text_label(INTRO, "WatchSecondary")
        intro.setWordWrap(True)
        entry.addWidget(intro)
        self.mode = QComboBox()
        self.mode.setAccessibleName("建房方式")
        self.mode.addItem("本机建房", "local")
        self.mode.addItem("云端建房", "cloud")
        self.create = QPushButton("创建房间")
        self.create.setObjectName("WatchCreate")
        entry.addLayout(self.action_row(self.mode, self.create))
        mode_hint = text_label(object_name="WatchSecondary")
        mode_hint.setWordWrap(True)
        entry.addWidget(mode_hint)
        def update_mode_hint():
            mode_hint.setText("本机建房时，房主需要保持在线。" if self.mode.currentData() == "local"
                              else "云端服务协调房间和画面对齐。")
        self.mode.currentIndexChanged.connect(update_mode_hint)
        update_mode_hint()
        self.code = QLineEdit()
        self.code.setPlaceholderText("输入六位房间码")
        self.code.setAccessibleName("六位房间码")
        self.code.setMaxLength(6)
        self.code.setMinimumWidth(0)
        self.code.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9]{0,6}"), self.code))
        self.join = QPushButton("加入房间")
        entry.addLayout(self.action_row(self.code, self.join))
        layout.addWidget(self.entry)

        self.room_header = QWidget()
        header = QHBoxLayout(self.room_header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        invitation = QVBoxLayout()
        invitation.setSpacing(0)
        invitation.addWidget(text_label("房间码", "WatchSecondary"))
        self.room_number = text_label(object_name="WatchCode")
        self.room_number.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        invitation.addWidget(self.room_number)
        header.addLayout(invitation)
        self.copy = ConsoleIconButton("copy", "复制房间码")
        self.copy.setObjectName("WatchCopy")
        self.copy.setFocusPolicy(Qt.StrongFocus)
        self.copy.clicked.connect(self.copy_code)
        header.addWidget(self.copy)
        header.addStretch(1)
        occupancy = QVBoxLayout()
        occupancy.setSpacing(0)
        self.online_count = text_label()
        self.room_mode = text_label(object_name="WatchSecondary")
        for label in (self.online_count, self.room_mode):
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            occupancy.addWidget(label)
        header.addLayout(occupancy)
        layout.addWidget(self.room_header)

        self.status_block = QWidget()
        status_layout = QHBoxLayout(self.status_block)
        status_layout.setContentsMargins(0, 2, 0, 2)
        status_layout.setSpacing(7)
        self.status_dot = text_label()
        self.status_dot.setFixedSize(7, 7)
        status_layout.addWidget(self.status_dot, 0, Qt.AlignTop)
        description = QVBoxLayout()
        description.setSpacing(1)
        self.status = text_label(object_name="WatchStatusTitle")
        self.status_detail = text_label(object_name="WatchSecondary")
        for label in (self.status, self.status_detail):
            label.setWordWrap(True)
            description.addWidget(label)
        status_layout.addLayout(description, 1)
        layout.addWidget(self.status_block)

        self.member_list = QWidget()
        self.member_list.setAccessibleName("房间成员")
        self.roster = QVBoxLayout(self.member_list)
        self.roster.setContentsMargins(0, 0, 0, 0)
        self.roster.setSpacing(2)
        layout.addWidget(self.member_list)

        self.details_toggle = QToolButton()
        self.details_toggle.setObjectName("WatchDetailsToggle")
        self.details_toggle.setText("同步详情")
        self.details_toggle.setAccessibleName("同步详情")
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.RightArrow)
        layout.addWidget(self.details_toggle, 0, Qt.AlignLeft)
        self.details = QWidget()
        metrics = QGridLayout(self.details)
        metrics.setContentsMargins(13, 0, 0, 0)
        metrics.setVerticalSpacing(1)
        self.metrics = []
        for index, title in enumerate(("上次复核画面时差", "近期画面时差 P95", "最高网络往返延迟")):
            metrics.addWidget(text_label(title, "WatchSecondary"), index, 0)
            value = text_label()
            metrics.addWidget(value, index, 1, Qt.AlignRight)
            self.metrics.append(value)
        self.details_toggle.toggled.connect(self.details.setVisible)
        self.details_toggle.toggled.connect(
            lambda expanded: self.details_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow))
        layout.addWidget(self.details)

        self.room_actions = QWidget()
        actions = QHBoxLayout(self.room_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.manual = QPushButton("复核画面")
        self.exit_room = QPushButton("退出同看")
        self.exit_room.setObjectName("WatchLeave")
        actions.addWidget(self.manual, 1)
        actions.addWidget(self.exit_room, 1)
        panel_layout.addWidget(self.room_actions)
        self.copy_feedback = text_label("已复制房间码", "WatchSecondary")
        self.copy_feedback.hide()
        layout.addWidget(self.copy_feedback)
        layout.addStretch(1)
        self.refresh_design()
        self.set_phase("")

    @staticmethod
    def action_row(field, button):
        row = QHBoxLayout()
        row.setSpacing(6)
        field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        row.addWidget(field, 1)
        row.addWidget(button)
        return row

    def refresh_design(self):
        c = colors()
        style = f"""
            #WatchPartyPanel {{background: transparent;}}
            #WatchPartyPanel QLabel#WatchSecondary {{color: {c['secondary']};}}
            #WatchPartyPanel QLabel#WatchCode {{font-size: 17px; font-weight: 600;}}
            #WatchPartyPanel QLabel#WatchStatusTitle, #WatchPartyPanel QLabel#WatchMemberName {{font-weight: 600;}}
            #WatchPartyPanel QScrollArea, #WatchPartyPanel QScrollArea > QWidget,
            #WatchPartyPanel QScrollArea > QWidget > QWidget {{background: transparent;}}
            #WatchPartyPanel QPushButton#WatchCreate:enabled {{color: {c['accent']};
                background: {theme.ACCENT_SOFT}; border-color: {c['accent']};}}
            #WatchPartyPanel QPushButton#WatchLeave {{background: transparent;}}
            #WatchPartyPanel QPushButton:focus, #WatchPartyPanel QToolButton:focus {{
                border: 1px solid {c['accent']};}}
            #WatchPartyPanel QToolButton#WatchDetailsToggle {{background: transparent;
                color: {c['secondary']}; padding: 2px; border: 1px solid transparent;}}
            #WatchPartyPanel QToolButton#WatchDetailsToggle:focus {{border-color: {c['accent']};}}
        """
        if self.styleSheet() != style:
            self.setStyleSheet(style)
        copy_style = f"""
            QPushButton {{background: {c['raised']}; border: 1px solid transparent;
                padding: 0; border-radius: 6px;}}
            QPushButton:focus {{border-color: {c['accent']};}}
        """
        if self.copy.styleSheet() != copy_style:
            self.copy.setStyleSheet(copy_style)

    def set_phase(self, phase, title="", detail="", metrics=()):
        color = LOCKED if phase == "locked" else WAITING
        self.status_dot.setVisible(bool(phase))
        self.status_dot.setStyleSheet(f"background: {color}; border-radius: 3px;")
        self.status.setText(title)
        self.status_detail.setText(detail)
        self.status_detail.setVisible(bool(detail))
        self.status_block.setVisible(bool(title))
        self.manual.setEnabled(phase == "locked")
        if self._phase != phase:
            self._phase = phase
            self.activity_changed.emit(phase)
        if not metrics:
            self.details_toggle.setChecked(False)
        for label, value in zip(self.metrics, metrics):
            label.setText(f"{value} ms")
        self.details_toggle.setVisible(bool(metrics))
        self.details.setVisible(bool(metrics) and self.details_toggle.isChecked())

    def set_room(self, active, code, mode, count, *, reconnecting=False):
        self.entry.setVisible(not active)
        self.room_header.setVisible(bool(code))
        self.room_actions.setVisible(active)
        self.room_number.setText(code or "")
        self.room_mode.setText("本机房间" if mode == "local" else "云端房间")
        self.online_count.setText("连接中断" if reconnecting else f"{count}/3 在线")
        self.copy.setEnabled(bool(code) and not reconnecting)
        self.copy_feedback.hide()

    def copy_code(self):
        QApplication.clipboard().setText(self.room_number.text())
        self.copy_feedback.show()

    def set_members(self, members, own_side):
        while self.roster.count():
            row = self.roster.takeAt(0).widget()
            row.hide()
            row.deleteLater()
        for member in members:
            side, profile = member["side"], member["profile"]
            name = display_name(profile)
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(1, 1, 1, 1)
            layout.setSpacing(7)
            portrait = Avatar(name, side, 26, row)
            if not profile:
                portrait.set_color(theme.CONTENT_HOVER)
            portrait.set_pixmap_image(avatar_pixmap(profile))
            presence = text_label()
            presence.setParent(portrait)
            presence.setFixedSize(8, 8)
            presence.move(18, 18)
            presence.setStyleSheet(f"background: {LOCKED}; border: 1px solid {theme.CONTENT}; border-radius: 4px;")
            layout.addWidget(portrait)
            identity = QVBoxLayout()
            identity.setSpacing(0)
            platform = PLATFORMS.get(profile["platform"], profile["platform"]) if profile else "未登录"
            account = f"{platform} · {profile['id']}" if profile and profile.get("nickname") else platform
            for text, object_name in ((name, "WatchMemberName"), (account, "WatchSecondary")):
                label = ElidedLabel(text)
                label.setTextFormat(Qt.PlainText)
                label.setObjectName(object_name)
                label.setToolTip(text)
                label.setAccessibleName(text)
                identity.addWidget(label)
            layout.addLayout(identity, 1)
            role = ("房主" if side == 0 else "成员") + (" · 我" if side == own_side else "")
            layout.addWidget(text_label(role, "WatchSecondary"))
            self.roster.addWidget(row)
        self.member_list.setVisible(bool(members))
