"""Scoped, opaque sending-workspace styling and recipient rendering."""
from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QWidget

from ddm import theme


def colors():
    surface = theme.qcolor(theme.CONTENT).name()
    text = theme.qcolor(theme.TEXT1).name()
    secondary = theme.qcolor(theme.TEXT2)
    background = QColor(surface)
    secondary = QColor(*(round(secondary.alphaF() * value + (1 - secondary.alphaF()) * base)
                         for value, base in zip(secondary.getRgb()[:3], background.getRgb()[:3])))
    return dict(surface=surface, inset=theme.mix(surface, theme.qcolor(theme.BG).name(), .35),
                raised=theme.qcolor(theme.ELEVATED).name(), input=theme.qcolor(theme.CONTENT_HOVER).name(),
                text=text, secondary=secondary.name(), accent=theme.qcolor(theme.ACCENT).name(), ink=text)


INPUT_HEIGHT = 32
SEND_WIDTH = 124
ACCESSORY_WIDTH = 56


def style_compose(editor, send):
    c = colors()
    editor.setMinimumWidth(0)
    editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    editor.setFixedHeight(INPUT_HEIGHT)
    editor.setStyleSheet(f'''QLineEdit {{background: {c['input']}; color: {c['text']};
        border: 1px solid transparent; border-radius: 8px; padding: 4px 8px;
        font-size: {theme.FONT_CONTROL}px;}}
        QLineEdit:focus {{border-color: {c['accent']};}}''')
    send.setFixedSize(SEND_WIDTH, INPUT_HEIGHT)
    send.setStyleSheet(f'''QPushButton {{background: {c['input']}; color: {c['text']};
        border: none; border-radius: 8px; padding: 4px 8px; font-size: {theme.FONT_CONTROL}px;}}
        QPushButton:hover {{background: {c['raised']};}}
        QPushButton:focus {{border: 1px solid {c['accent']};}}
        QPushButton:pressed {{background: {c['inset']};}}
        QPushButton:disabled {{color: {c['secondary']}; background: {c['raised']};}}''')


def align_composer(widget):
    """Arrange the existing platform widgets without replacing their send handlers."""
    layout = widget.layout()
    if not isinstance(layout, QHBoxLayout):
        return
    editors = [w for w in widget.findChildren(QLineEdit) if w.window() is widget.window()]
    sends = [w for w in widget.findChildren(QPushButton)
             if w.window() is widget.window() and (w.text() == '发送' or w.property('sendPrimary'))]
    if len(editors) != 1 or len(sends) != 1:
        return
    editor, send = editors[0], sends[0]
    send.setProperty('sendPrimary', True)
    if not widget.property('sendRowAligned'):
        widgets = [layout.itemAt(index).widget() for index in range(layout.count())]
        if editor not in widgets or send not in widgets:
            return
        for child in widgets:
            if child is not None:
                layout.removeWidget(child)
        accessories = QWidget(widget)
        accessories.setFixedWidth(ACCESSORY_WIDTH)
        extras = QHBoxLayout(accessories)
        extras.setContentsMargins(0, 0, 0, 0)
        extras.setSpacing(4)
        extras.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        for child in widgets:
            if child is not None and child not in (editor, send):
                hidden = child.isHidden()
                extras.addWidget(child)
                child.setVisible(not hidden)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(editor, 1)
        layout.addWidget(accessories)
        layout.addWidget(send)
        widget.setFixedHeight(40)
        widget.setProperty('sendRowAligned', True)
    style_compose(editor, send)


def stylesheet():
    c = colors()
    return f'''
        #MatchSyncSendConsole {{background: transparent; border: none;}}
        #MatchSyncSendConsole QLabel {{color: {c['text']}; background: transparent; border: none;}}
        #MatchSyncSendConsole #SendTitle {{font-weight: 600;}}
        #MatchSyncSendConsole #SendContext {{font-weight: 600;}}
        #MatchSyncSendConsole #SendFeedback {{color: {c['secondary']};}}
        #MatchSyncSendConsole QStackedWidget, #MatchSyncSendConsole QFrame {{border: none;}}
        #MatchSyncSendConsole QScrollArea, #MatchSyncSendConsole QScrollArea > QWidget,
        #MatchSyncSendConsole QScrollArea > QWidget > QWidget {{background: transparent; border: none;}}
        #MatchSyncSendConsole QListWidget {{background: {c['inset']}; border: none; border-radius: 8px;}}
        #MatchSyncSendConsole QLineEdit {{background: {c['input']}; color: {c['text']};
            border: 1px solid transparent; border-radius: 8px; padding: 6px 8px;}}
        #MatchSyncSendConsole QLineEdit:focus {{border-color: {c['accent']};}}
        #MatchSyncSendConsole QPushButton {{background: {c['raised']}; color: {c['text']};
            border: none; border-radius: 8px; padding: 5px 10px; min-height: 18px;}}
        #MatchSyncSendConsole QPushButton:hover {{border: 1px solid {c['secondary']};}}
        #MatchSyncSendConsole QPushButton:pressed {{background: {c['inset']};}}
        #MatchSyncSendConsole QPushButton:focus {{border: 1px solid {c['accent']};}}
        #MatchSyncSendConsole QPushButton[sendPrimary="true"] {{background: {c['input']}; color: {c['text']};}}
        #MatchSyncSendConsole QPushButton:disabled {{background: {c['raised']}; color: {c['secondary']};}}
        #MatchSyncSendConsole #SendMinimize {{background: transparent;}}
    '''


class RecipientDelegate(QStyledItemDelegate):
    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton
                and index.flags() & Qt.ItemIsEnabled and index.flags() & Qt.ItemIsUserCheckable
                and option.rect.contains(event.position().toPoint())):
            checked = index.data(Qt.CheckStateRole) == Qt.Checked.value
            return model.setData(index, (Qt.Unchecked if checked else Qt.Checked).value, Qt.CheckStateRole)
        return super().editorEvent(event, model, option, index)

    def paint(self, painter, option, index):
        c = colors()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect.adjusted(4, 3, -4, -3))
        checked = index.data(Qt.CheckStateRole)
        selected = (checked == Qt.Checked.value if checked is not None else
                    bool(option.state & QStyle.State_Selected))
        painter.setPen(Qt.NoPen)
        if selected or option.state & QStyle.State_MouseOver:
            painter.setBrush(QColor(c['raised']))
            painter.drawRoundedRect(rect, 6, 6)
        if option.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor(c['accent']), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        avatar = QRectF(rect.left() + 8, rect.center().y() - 16, 32, 32)
        pixmap = index.data(Qt.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(avatar)
            painter.setClipPath(clip)
            painter.drawPixmap(avatar, pixmap, QRectF(pixmap.rect()))
            painter.restore()
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(c['raised']))
            painter.drawEllipse(avatar)
            painter.setPen(QColor(c['text']))
            painter.drawText(avatar, Qt.AlignCenter, str(index.data(Qt.DisplayRole) or '?')[:1])
        painter.setPen(Qt.NoPen)
        if selected:
            painter.setPen(QPen(QColor(c['accent']), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(avatar.adjusted(-2, -2, 2, 2))
        text = str(index.data(Qt.DisplayRole) or '').split('\n', 1)
        font = QFont(option.font)
        font.setWeight(QFont.DemiBold if selected else QFont.Normal)
        metrics = QFontMetrics(font)
        left, width = int(rect.left() + 48), max(0, int(rect.width() - 74))
        line_height = metrics.height()
        top = int(rect.top() + (rect.height() - 2 * line_height) / 2)
        painter.setFont(font)
        painter.setPen(QColor(c['text']))
        painter.drawText(left, top + metrics.ascent(), metrics.elidedText(text[0], Qt.ElideRight, width))
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        painter.setPen(QColor(c['secondary']))
        painter.drawText(left, top + line_height + metrics.ascent(),
                         metrics.elidedText(text[1] if len(text) > 1 else '', Qt.ElideRight, width))
        if checked is not None:
            painter.setPen(QPen(QColor(c['accent'] if selected else c['secondary']), 1.5))
            x, y = int(rect.right() - 21), int(rect.center().y() - 6)
            if selected:
                painter.drawLine(x, y + 6, x + 4, y + 10)
                painter.drawLine(x + 4, y + 10, x + 12, y + 1)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(164, max(52, option.fontMetrics.height() * 2 + 12))
