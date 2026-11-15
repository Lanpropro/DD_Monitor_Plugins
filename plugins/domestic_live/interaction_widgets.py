"""Shared interaction input and feedback widgets; Qt parents stay with callers."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
from ddm import theme


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

class SendDetailsDialog(QDialog):
    def __init__(self, room, message, parent=None):
        super().__init__(parent)
        platform = "抖音" if room.get("platform") == "douyin" else "虎牙"
        self.setWindowTitle(f"{platform}弹幕发送详情")
        self.setObjectName("SendDetails")
        self.setMinimumWidth(460)
        self.setMaximumWidth(620)
        self.setStyleSheet(f"""
            QDialog#SendDetails {{background:{theme.ELEVATED};}}
            QLabel {{color:{theme.TEXT1};font-size:{theme.FONT_CONTROL}px;}}
            QLabel#SendDetailsTitle {{font-size:20px;font-weight:600;}}
            QLabel#SendDetailsMuted {{color:{theme.TEXT3};}}
            QFrame#SendDetailsCard {{background:{theme.CONTENT};border:1px solid {theme.BORDER};border-radius:10px;}}
            QPushButton {{min-height:34px;min-width:88px;padding:0 14px;border:1px solid {theme.BORDER_STRONG};
                border-radius:8px;color:{theme.TEXT1};background:{theme.ELEVATED};}}
            QPushButton:hover {{background:{theme.CONTENT_HOVER};}}
            QPushButton#SendDetailsClose {{background:{theme.ACCENT};color:#04161f;border:none;}}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        def label(text, name=""):
            result = QLabel(str(text))
            result.setObjectName(name)
            result.setWordWrap(True)
            result.setTextFormat(Qt.PlainText)
            return result
        layout.addWidget(label("发送状态未确认", "SendDetailsTitle"))
        layout.addWidget(label(f"{platform} · {room.get('uname') or '当前直播间'}", "SendDetailsMuted"))
        card = QFrame()
        card.setObjectName("SendDetailsCard")
        details = QVBoxLayout(card)
        details.setContentsMargins(16, 14, 16, 14)
        details.setSpacing(8)
        details.addWidget(label("接口信息", "SendDetailsMuted"))
        self.message_label = label(message)
        self.message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details.addWidget(self.message_label)
        layout.addWidget(card)
        layout.addWidget(label("请先核对直播间内是否已经出现该条弹幕，再决定是否重新发送。", "SendDetailsMuted"))
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("复制详情")
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(str(message)))
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("SendDetailsClose")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
