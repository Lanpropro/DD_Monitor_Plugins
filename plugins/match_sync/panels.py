"""Shared appearance of the playback and sending consoles."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from ddm import theme


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
            #MatchSyncSettings QPushButton, #MatchSyncSettings QLineEdit,
            #MatchSyncSettings QComboBox, #MatchSyncSettings QDoubleSpinBox {{
                background: palette(button); color: palette(window-text);
                border: 1px solid palette(mid);
                border-radius: {theme.RADIUS_MD}px; padding: 4px 6px;
            }}
            #MatchSyncSettings QPushButton:hover {{
                background: palette(light); border-color: palette(highlight);
            }}
            #MatchSyncSettings QPushButton:checked {{
                background: rgba(251, 114, 153, 45); border-color: palette(highlight);
            }}
        """.replace("MatchSyncSettings", object_name))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), theme.RADIUS_LG, theme.RADIUS_LG)
        top = QPainterPath()
        top.addRoundedRect(QRectF(0, 0, self.width(), self.height() / 2),
                           theme.RADIUS_MD, theme.RADIUS_MD)
        path = path.united(top)
        tint = QLinearGradient(0, 0, 0, self.height())
        top_color, bottom_color = theme.qcolor(theme.CONTENT), theme.qcolor(theme.BG)
        top_color.setAlpha(round(255 * self.viewer.panel_alpha) if self.viewer.skin_visible else 238)
        bottom_color.setAlpha(round(255 * self.viewer.panel_alpha) if self.viewer.skin_visible else 245)
        tint.setColorAt(0, top_color)
        tint.setColorAt(1, bottom_color)
        painter.fillPath(path, tint)
