"""Shared appearance of the playback and sending consoles."""
import math
import ctypes
import sys
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ddm import theme


class ChatConnectionIcon(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.connections = 0
        self.setFixedSize(20, 20)

    def set_connections(self, count):
        if self.connections != count:
            self.connections = count
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(10, 10)
        painter.rotate(90)
        painter.translate(-10, -10)
        for level, radius in enumerate((5, 9), 1):
            color = theme.qcolor(theme.PINK if self.connections >= level else theme.TEXT3)
            painter.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(QRectF(10 - radius, 15 - radius, radius * 2, radius * 2), 45 * 16, 90 * 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(theme.PINK if self.connections else theme.TEXT3))
        painter.drawEllipse(QPointF(10, 15), 1.4, 1.4)


class LinkCursor(QObject):
    def eventFilter(self, widget, event):
        if (event.type() in (QEvent.Enter, QEvent.HoverMove, QEvent.MouseMove)
                and widget.isEnabled() and sys.platform == 'win32'
                and QApplication.platformName() == 'windows'
                and QApplication.widgetAt(QCursor.pos()) is widget):
            # Use the live system link cursor, including animated Windows themes.
            user32 = ctypes.windll.user32
            user32.LoadCursorW.restype = ctypes.c_void_p
            user32.SetCursor.argtypes = (ctypes.c_void_p,)
            user32.SetCursor(user32.LoadCursorW(None, 32649))  # IDC_HAND
        return False


def use_link_cursor(widget):
    widget.setCursor(Qt.PointingHandCursor)
    if not hasattr(widget, '_match_link_cursor'):
        widget._match_link_cursor = LinkCursor(widget)
        widget.installEventFilter(widget._match_link_cursor)


def style_checkbox(checkbox):
    use_link_cursor(checkbox)
    checkbox.setAttribute(Qt.WA_Hover)
    checkbox.setMouseTracking(True)
    checkbox.setStyleSheet(f'''QCheckBox {{color: {theme.TEXT1}; border: none; border-radius: 5px; padding: 3px;}}
        QCheckBox:hover {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}''')


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
        use_link_cursor(self)
        self.setAttribute(Qt.WA_Hover)
        self.setMouseTracking(True)
        self.setStyleSheet(f'''QPushButton {{background: transparent; border: none; padding: 0; border-radius: 6px;
            min-width: 28px; max-width: 28px; min-height: 26px; max-height: 26px;}}''')

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.isEnabled() and QApplication.widgetAt(QCursor.pos()) is self
        if hovered or self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(theme.ACCENT_SOFT))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        painter.translate(self.width() / 2, self.height() / 2)
        color = theme.TEXT4 if not self.isEnabled() else theme.ACCENT if hovered else theme.TEXT1
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
        elif self.kind == 'independent':
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(-8, -7, 16, 14), 2, 2)
            painter.drawLine(QPointF(-2, -4), QPointF(4, 0))
            painter.drawLine(QPointF(4, 0), QPointF(-2, 4))
            painter.drawLine(QPointF(-2, 4), QPointF(-2, -4))
        elif self.kind == 'resync':
            painter.drawArc(QRectF(-7, -7, 14, 14), 35 * 16, 285 * 16)
            painter.drawLine(QPointF(6, -6), QPointF(7, -1))
            painter.drawLine(QPointF(7, -1), QPointF(2, -2))
        elif self.kind == 'minimize':
            painter.drawLine(QPointF(-6, 3), QPointF(6, 3))
        elif self.kind == 'emote':
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(-7, -7, 14, 14))
            painter.drawPoint(QPointF(-2.5, -2))
            painter.drawPoint(QPointF(2.5, -2))
            painter.drawArc(QRectF(-4, -2, 8, 6), 200 * 16, 140 * 16)
        elif self.kind == 'gift':
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(-6, -1, 12, 8), 1, 1)
            painter.drawRect(QRectF(-7, -4, 14, 3))
            painter.drawLine(QPointF(0, -4), QPointF(0, 7))
            painter.drawEllipse(QRectF(-5, -8, 5, 4))
            painter.drawEllipse(QRectF(0, -8, 5, 4))
        elif self.kind == 'save':
            painter.setBrush(Qt.NoBrush)
            bookmark = QPainterPath()
            bookmark.moveTo(-5, -7)
            bookmark.lineTo(5, -7)
            bookmark.lineTo(5, 7)
            bookmark.lineTo(0, 3)
            bookmark.lineTo(-5, 7)
            bookmark.closeSubpath()
            painter.drawPath(bookmark)
        else:
            painter.drawLine(QPointF(-7, 0), QPointF(4, 0))
            painter.drawLine(QPointF(0, -4), QPointF(4, 0))
            painter.drawLine(QPointF(0, 4), QPointF(4, 0))
            painter.drawLine(QPointF(7, -6), QPointF(7, 6))


class MatchSettingsDialog(QDialog):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.setAttribute(Qt.WA_DontShowOnScreen, viewer.testAttribute(Qt.WA_DontShowOnScreen))
        self.setWindowTitle('比赛二路设置')
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow('缓存余量', viewer.buffer_reserve)
        form.addRow('画中画画质', viewer.crop_quality)
        viewer.crop_quality.show()
        layout.addLayout(form)
        note = QLabel('自动对齐和对照微调保留在外部控制栏。\n重放最多回看十分钟，实际范围取主画面可用缓存。')
        note.setWordWrap(True)
        layout.addWidget(note)
        quality_note = QLabel('两种模式共用来源，副路优先720P，否则使用中间档；主画面不降档。\n'
                              '轻量增强只处理摄像头选区，切换模式不重连来源。\n'
                              '处理跟不上时，本次观看保持普通缩放；重新选择增强可重试。')
        quality_note.setWordWrap(True)
        layout.addWidget(quality_note)
        self.quality_status = QLabel()
        layout.addWidget(self.quality_status)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(500)
        self.status_timer.timeout.connect(self.refresh_quality_status)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.close)
        layout.addWidget(close)

    def refresh_quality_status(self):
        crops = list(self.viewer.floating_crops.values())
        if self.viewer.crop_quality.currentData() != 'light':
            self.quality_status.setText('')
        elif not crops:
            self.quality_status.setText('轻量增强：等待添加画中画')
        else:
            enhanced = sum(not crop.enhanced.isNull() for crop in crops)
            fallback = sum(crop.enhance_state == 'fallback' for crop in crops)
            self.quality_status.setText(f'轻量增强：{enhanced} 路增强，{fallback} 路已回退，{len(crops) - enhanced - fallback} 路准备中')

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_quality_status()
        self.status_timer.start()

    def hideEvent(self, event):
        self.status_timer.stop()
        super().hideEvent(event)


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
