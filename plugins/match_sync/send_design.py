"""Scoped, opaque sending-workspace styling and recipient rendering."""
from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ddm import theme


def colors():
    return (dict(surface='#F5F6F8', inset='#FFFFFF', raised='#E7EAF0', text='#202631',
                 secondary='#57616F', accent='#006F8F', ink='#FFFFFF') if theme.is_light() else
            dict(surface='#25282E', inset='#1D2025', raised='#30343B', text='#F4F6FA',
                 secondary='#B4BBC6', accent='#48C8F0', ink='#10232A'))


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
        #MatchSyncSendConsole QTabBar {{background: {c['inset']}; border: none; border-radius: 8px;}}
        #MatchSyncSendConsole QTabBar::tab {{color: {c['secondary']}; background: transparent;
            border: none; border-radius: 6px; padding: 5px 8px; margin: 3px;}}
        #MatchSyncSendConsole QTabBar::tab:selected {{color: {c['text']}; background: {c['raised']};}}
        #MatchSyncSendConsole QTabBar::tab:hover {{color: {c['text']};}}
        #MatchSyncSendConsole QTabBar::tab:focus {{border: 1px solid {c['accent']};}}
        #MatchSyncSendConsole QLineEdit {{background: {c['inset']}; color: {c['text']};
            border: 1px solid {c['raised']}; border-radius: 8px; padding: 6px 8px;}}
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
        stripe = QColor(index.data(Qt.UserRole + 2) or c['accent'])
        painter.setPen(Qt.NoPen)
        painter.setBrush(stripe)
        painter.drawRoundedRect(QRectF(rect.left() + 7, rect.top() + 10, 3, rect.height() - 20), 1.5, 1.5)
        text = str(index.data(Qt.DisplayRole) or '').split('\n', 1)
        font = QFont(option.font)
        font.setWeight(QFont.DemiBold if selected else QFont.Normal)
        metrics = QFontMetrics(font)
        left, width = int(rect.left() + 18), max(0, int(rect.width() - 45))
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
            else:
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(QRectF(x, y, 12, 12), 3, 3)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(164, max(52, option.fontMetrics.height() * 2 + 12))
