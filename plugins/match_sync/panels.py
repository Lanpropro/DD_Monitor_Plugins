"""Shared appearance of the playback and sending consoles."""
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ddm import theme


class ConsoleIconButton(QPushButton):
    def __init__(self, kind, tooltip, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(28, 26)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f'''QPushButton {{background: transparent; border: none; padding: 0; border-radius: 6px;
            min-width: 28px; max-width: 28px; min-height: 26px; max-height: 26px;}}''')

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.isEnabled() and (self.underMouse() or self.isDown()):
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(theme.ACCENT_SOFT))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        painter.translate(self.width() / 2, self.height() / 2)
        color = theme.ACCENT if self.underMouse() else theme.TEXT1 if self.isEnabled() else theme.TEXT4
        pen = QPen(theme.qcolor(color), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        if self.kind == 'settings':
            gear = QPainterPath()
            for index in range(24):
                angle = math.radians(index * 15 - 7.5)
                radius = 7 if index % 4 in (0, 1) else 5.3
                point = QPointF(math.cos(angle) * radius, math.sin(angle) * radius)
                gear.moveTo(point) if index == 0 else gear.lineTo(point)
            gear.closeSubpath()
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(gear)
            painter.drawEllipse(QRectF(-2, -2, 4, 4))
        elif self.kind == 'minimize':
            painter.drawLine(QPointF(-6, 3), QPointF(6, 3))
        else:
            painter.drawLine(QPointF(-7, 0), QPointF(4, 0))
            painter.drawLine(QPointF(0, -4), QPointF(4, 0))
            painter.drawLine(QPointF(0, 4), QPointF(4, 0))
            painter.drawLine(QPointF(7, -6), QPointF(7, 6))


class MatchSettingsDialog(QDialog):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.setAttribute(Qt.WA_DontShowOnScreen, viewer.testAttribute(Qt.WA_DontShowOnScreen))
        self.setWindowTitle('比赛二路设置')
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow('缓存余量', viewer.buffer_reserve)
        layout.addLayout(form)
        note = QLabel('自动对齐和对照微调保留在外部控制栏。\n重放最多回看十分钟，实际范围取主画面可用缓存。')
        note.setWordWrap(True)
        layout.addWidget(note)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.close)
        layout.addWidget(close)


def console_button_style(selector):
    return f"""
        {selector} {{
            background: palette(button); color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: {theme.RADIUS_MD}px; padding: 4px 6px;
        }}
        {selector}:hover {{ background: palette(light); border-color: palette(highlight); }}
        {selector}:checked {{ background: rgba(251, 114, 153, 45); border-color: palette(highlight); }}
    """


class SettingsPanel(QWidget):
    def __init__(self, viewer, object_name="MatchSyncSettings"):
        super().__init__(viewer)
        self.viewer = viewer
        self.setObjectName(object_name)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumHeight(130)
        self.setMaximumHeight(240)
        self.setStyleSheet(f"""
            #MatchSyncSettings, #MatchSyncRows {{
                background: transparent; border: none;
            }}
            #MatchSyncSettings QLabel, #MatchSyncSettings QCheckBox {{ color: palette(window-text); }}
            #MatchSyncSettings QScrollArea, #MatchSyncSettings QScrollArea > QWidget {{
                background: transparent; border: none;
            }}
            #MatchSyncSettings QLineEdit,
            #MatchSyncSettings QComboBox, #MatchSyncSettings QDoubleSpinBox {{
                background: palette(button); color: palette(window-text);
                border: 1px solid palette(mid);
                border-radius: {theme.RADIUS_MD}px; padding: 4px 6px;
            }}
        """.replace("MatchSyncSettings", object_name)
            + console_button_style(f'#{object_name} QPushButton'))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        # Match the host's Tile and DanmakuPanel outer corners.
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        if self.objectName() == 'MatchSyncSendConsole':
            path.setFillRule(Qt.WindingFill)
            path.addRect(QRectF(0, 0, self.width(), 10))
        tint = QLinearGradient(0, 0, 0, self.height())
        top_color, bottom_color = theme.qcolor(theme.CONTENT), theme.qcolor(theme.BG)
        top_color.setAlpha(round(255 * self.viewer.panel_alpha) if self.viewer.skin_visible else 238)
        bottom_color.setAlpha(round(255 * self.viewer.panel_alpha) if self.viewer.skin_visible else 245)
        tint.setColorAt(0, top_color)
        tint.setColorAt(1, bottom_color)
        painter.fillPath(path, tint)
