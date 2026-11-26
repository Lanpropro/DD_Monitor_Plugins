"""Translation-specific visual surfaces; values stay in Panel's existing controls."""
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ddm import theme
from ddm.ui import HintLabel, SettingsSurface, continuous_path
from .music_detection import MUSIC_MARKER


def paint_symbol(painter, name):
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(theme.qcolor(theme.TEXT2), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    if name in ('stream', 'captions'):
        painter.drawRoundedRect(QRectF(3, 5, 18, 14), 3, 3)
        if name == 'stream':
            painter.drawLine(10, 9, 15, 12)
            painter.drawLine(15, 12, 10, 15)
        else:
            painter.drawLine(7, 10, 17, 10)
            painter.drawLine(7, 14, 13, 14)
    elif name == 'audio':
        painter.drawRoundedRect(QRectF(9, 3, 6, 12), 3, 3)
        painter.drawArc(QRectF(6, 7, 12, 12), 180 * 16, 180 * 16)
        painter.drawLine(12, 19, 12, 22)
    elif name == 'music':
        painter.drawLine(10, 17, 10, 5)
        painter.drawLine(10, 5, 20, 3)
        painter.drawLine(20, 3, 20, 15)
        painter.drawEllipse(QRectF(4, 16, 6, 4))
        painter.drawEllipse(QRectF(14, 14, 6, 4))
    elif name == 'folder':
        path = QPainterPath()
        path.moveTo(3, 7)
        for point in ((3, 5), (9, 5), (12, 8), (21, 8), (21, 19), (3, 19), (3, 7)):
            path.lineTo(*point)
        painter.drawPath(path)
    elif name == 'stop':
        painter.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)


def panel_icon(name):
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.scale(2, 2)
    paint_symbol(painter, name)
    painter.end()
    return QIcon(pixmap)


class ControlSymbol(QWidget):
    def __init__(self, name, description, parent=None):
        super().__init__(parent)
        self.name = name
        self.setFixedSize(22, 22)
        self.setToolTip(description)
        self.setAccessibleName(description)
        theme.signals().changed.connect(self.update)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.scale(self.width() / 24, self.height() / 24)
        paint_symbol(painter, self.name)


class TranslationGlyph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self.setProperty('customControlSurface', True)
        theme.signals().changed.connect(self.update)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.scale(self.width() / 42, self.height() / 42)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(theme.CONTENT))
        painter.drawRoundedRect(QRectF(0, 0, 42, 42), 14, 14)
        painter.setPen(QPen(theme.qcolor(theme.TEXT2), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(9, 9, 23, 19), 6, 6)
        painter.drawLine(14, 28, 14, 33)
        painter.drawLine(14, 33, 20, 28)
        for x, height in ((15, 5), (20, 11), (25, 7)):
            painter.drawLine(x, 19-height//2, x, 19+height//2)


class LanguageRouteLayout(QBoxLayout):
    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return 140 if width < 300 else 64

    def minimumHeightForWidth(self, width):
        return self.heightForWidth(width)


class LanguageRoute(QWidget):
    def __init__(self, source, target, parent=None):
        super().__init__(parent)
        self.setProperty('customControlSurface', True)
        self.box = box = LanguageRouteLayout(QBoxLayout.LeftToRight, self)
        self.setMinimumHeight(64)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)
        for symbol, title, control in (('audio', '原声语言', source), ('captions', '字幕语言', target)):
            if box.count():
                self.arrow = QLabel('→')
                self.arrow.setAlignment(Qt.AlignCenter)
                self.arrow.setObjectName('TranslateRouteArrow')
                box.addWidget(self.arrow)
            column = QHBoxLayout()
            column.setSpacing(6)
            column.addWidget(ControlSymbol(symbol, title))
            control.setMinimumWidth(0)
            control.setFixedHeight(theme.CONTROL_HEIGHT)
            control.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            control.setToolTip(title)
            column.addWidget(control, 1)
            box.addLayout(column, 1)
        theme.signals().changed.connect(self.update)

    def sizeHint(self):
        return QSize(300, 64)

    def minimumSizeHint(self):
        return QSize(0, 64)

    def resizeEvent(self, event):
        compact = self.width() < 300
        self.box.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
        self.arrow.setText('↓' if compact else '→')
        self.arrow.setFixedHeight(20 if compact else 24)
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        path = continuous_path(rect, 14)
        painter.fillPath(path, theme.qcolor(theme.CONTENT))
        painter.setPen(QPen(theme.qcolor(theme.BORDER), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


class PanelSurface(SettingsSurface):
    """Use opaque settings colors without the fallback translucent sheen."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = continuous_path(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), theme.RADIUS_LG)
        painter.fillPath(path, theme.qcolor(theme.CONTENT))
        painter.setPen(QPen(theme.qcolor(theme.BORDER), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


class CaptionPreview(PanelSurface):
    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(ControlSymbol('captions', '当前字幕'))
        self.heading = HintLabel('字幕')
        heading.addWidget(self.heading)
        heading.addStretch()
        box.addLayout(heading)
        self.original = QLabel('等待语音…')
        self.original.setObjectName('TranslateOriginal')
        self.translated = QLabel('')
        self.translated.hide()
        self.translated.setObjectName('TranslateTranslated')
        for label in (self.original, self.translated):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            box.addWidget(label)
        self.setMinimumHeight(112)

    def set_caption(self, original, translated):
        # Bound presentation independently of the six-block legacy text editor.
        self.original.setText(original[:600] if original else '等待语音…')
        self.translated.setText(translated[:600])
        self.translated.setVisible(bool(translated) and original != MUSIC_MARKER)


def apply_panel_theme(panel):
    panel.setStyleSheet(f'''
        #TranslatePanel {{background: transparent; border: none;}}
        #TranslatePanel QScrollArea, #TranslatePage {{background: transparent; border: none;}}
        #TranslatePanel #TranslateBadge {{color: {theme.TEXT2}; background: transparent;
            border: none; padding: 2px 4px;
            font-size: {theme.FONT_CAPTION}px;}}
        #TranslatePanel #TranslateOriginal {{color: {theme.TEXT2}; font-size: {theme.FONT_CONTROL}px;}}
        #TranslatePanel #TranslateTranslated {{color: {theme.TEXT1}; font-size: {theme.FONT_BODY+1}px;
            font-weight: 600;}}
        #TranslatePanel #TranslateRouteArrow {{color: {theme.TEXT2}; font-size: 18px;}}
        #TranslatePanel #SettingsSection {{background: transparent; border: none;}}
    ''')
