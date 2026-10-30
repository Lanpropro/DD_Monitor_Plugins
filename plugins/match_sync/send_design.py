"""Scoped, opaque sending-workspace styling and recipient rendering."""
from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ddm import theme


def colors():
    return (dict(surface='#F5F5F3', inset='#FCFCFB', raised='#E7E7E1', input='#ECECE7', text='#282824',
                 secondary='#60605A', accent='#52524A', ink='#FCFCFB') if theme.is_light() else
            dict(surface='#151515', inset='#1A1A19', raised='#252523', input='#20201E', text='#EAEAE5',
                 secondary='#B3B3A9', accent='#D4D4CB', ink='#151515'))


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
        #MatchSyncSendConsole QPushButton[sendPrimary="true"] {{background: {c['accent']}; color: {c['ink']};}}
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
