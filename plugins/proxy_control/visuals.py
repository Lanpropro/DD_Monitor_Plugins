"""Paint the compact client navigation, switches and service profile cards."""
from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QListWidget, QStyle, QStyledItemDelegate

from ddm import theme
from .traffic import quota, expiry_text


def surface():
    return theme.CONTENT if theme.is_light() else theme.mix(theme.BG, theme.CONTENT, .3)


def translucent(color, alpha):
    value = theme.qcolor(color)
    return f'rgba({value.red()}, {value.green()}, {value.blue()}, {alpha / 255:.3f})'


def symbol(painter, name, rect, color):
    painter.save()
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 24, rect.height() / 24)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(theme.qcolor(color), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    if name == 'overview':
        for x, y, w, h in ((3, 3, 7, 18), (14, 3, 7, 7), (14, 14, 7, 7)):
            painter.drawRoundedRect(QRectF(x, y, w, h), 1.5, 1.5)
    elif name == 'folder':
        path = QPainterPath()
        path.moveTo(3, 7)
        for x, y in ((3, 5), (9, 5), (12, 8), (21, 8), (21, 20), (3, 20), (3, 7)):
            path.lineTo(x, y)
        painter.drawPath(path)
    elif name == 'nodes':
        for y in (4, 12):
            painter.drawRoundedRect(QRectF(4, y, 16, 6), 1.5, 1.5)
            painter.drawPoint(7, y + 3)
            painter.drawLine(11, y + 3, 17, y + 3)
    elif name == 'connections':
        painter.drawRoundedRect(QRectF(2, 8, 7, 8), 2, 2)
        painter.drawRoundedRect(QRectF(15, 8, 7, 8), 2, 2)
        painter.drawLine(9, 10, 15, 10)
        painter.drawLine(9, 14, 15, 14)
    elif name == 'check':
        path = QPainterPath()
        path.moveTo(5, 12)
        path.lineTo(10, 17)
        path.lineTo(19, 7)
        painter.drawPath(path)
    elif name in ('close', 'plus'):
        if name == 'close':
            painter.drawLine(6, 6, 18, 18)
            painter.drawLine(18, 6, 6, 18)
        else:
            painter.drawLine(5, 12, 19, 12)
            painter.drawLine(12, 5, 12, 19)
    elif name in ('back', 'down', 'right'):
        path = QPainterPath()
        points = {'back': [(14, 5), (7, 12), (14, 19)],
                  'right': [(9, 5), (16, 12), (9, 19)],
                  'down': [(5, 9), (12, 16), (19, 9)]}[name]
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        painter.drawPath(path)
    elif name == 'play':
        path = QPainterPath()
        path.moveTo(8, 5)
        path.lineTo(19, 12)
        path.lineTo(8, 19)
        path.closeSubpath()
        painter.setBrush(theme.qcolor(color))
        painter.drawPath(path)
    elif name == 'stop':
        painter.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
    elif name == 'settings':
        for x, y in ((5, 8), (12, 16), (19, 10)):
            painter.drawLine(x, 3, x, 21)
            painter.setBrush(theme.qcolor(surface()))
            painter.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))
    elif name == 'latency':
        painter.drawArc(QRectF(3, 3, 18, 18), 25 * 16, 310 * 16)
        painter.drawLine(12, 12, 17, 7)
    elif name == 'globe':
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.drawEllipse(QRectF(8, 3, 8, 18))
        painter.drawLine(3, 12, 21, 12)
    painter.restore()


def icon(name, color=None):
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    symbol(painter, name, QRectF(0, 0, 48, 48), color or theme.TEXT2)
    painter.end()
    return QIcon(pixmap)


class NavigationDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(60, 76)

    def paint(self, painter, option, index):
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if selected or option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(theme.ACCENT_SOFT if selected else theme.CONTENT_HOVER))
            painter.drawRoundedRect(QRectF(rect.center().x() - 25, rect.top() + 7, 50, 32), 16, 16)
        color = theme.ACCENT if selected else theme.TEXT2
        symbol(painter, index.data(Qt.UserRole), QRectF(rect.center().x() - 11, rect.top() + 12, 22, 22), color)
        font = QFont(option.font)
        font.setPixelSize(theme.FONT_CONTROL)
        painter.setFont(font)
        painter.setPen(theme.qcolor(color))
        painter.drawText(rect.adjusted(0, 42, 0, -9), Qt.AlignCenter, index.data())
        painter.restore()


class ProfileList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.LeftToRight)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWrapping(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QEvent.Resize:
            width = max(1, self.viewport().width() - 2)
            self.setGridSize(QSize(width // (2 if width >= 440 else 1), 126))
        return result


class ProfileDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(max(1, self.parent().gridSize().width()), 126)

    def paint(self, painter, option, index):
        record = index.data(Qt.UserRole)
        rect = QRectF(option.rect).adjusted(4, 4, -4, -4)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(theme.qcolor(theme.ACCENT if record['active'] else theme.BORDER_STRONG), 1))
        painter.setBrush(theme.qcolor(theme.ACCENT_SOFT if record['active'] else surface()))
        painter.drawRoundedRect(rect, 15, 15)
        font = QFont(option.font)
        font.setPixelSize(theme.FONT_BODY)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(theme.qcolor(theme.TEXT1))
        text = painter.fontMetrics().elidedText(record['name'], Qt.ElideRight, int(rect.width() - 55))
        painter.drawText(QRectF(rect.left() + 16, rect.top() + 12, rect.width() - 51, 24), Qt.AlignVCenter, text)
        if record['active']:
            symbol(painter, 'check', QRectF(rect.right() - 30, rect.top() + 14, 17, 17), theme.ACCENT)
        font.setPixelSize(theme.FONT_CAPTION)
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        painter.setPen(theme.qcolor(theme.TEXT2))
        usage = record.get('usage') or {}
        kind = record['kind'] + (' · ' + expiry_text(usage) if expiry_text(usage) else '')
        painter.drawText(QRectF(rect.left() + 16, rect.top() + 40, rect.width() - 32, 18), Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(kind, Qt.ElideRight, int(rect.width() - 32)))
        text, fraction = quota(usage)
        painter.drawText(QRectF(rect.left() + 16, rect.top() + 66, rect.width() - 32, 18), Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight, int(rect.width() - 32)))
        if fraction is not None:
            bar = QRectF(rect.left() + 16, rect.bottom() - 18, rect.width() - 32, 5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor(theme.BORDER_STRONG))
            painter.drawRoundedRect(bar, 2.5, 2.5)
            if fraction > 0:
                painter.setBrush(theme.qcolor(theme.ACCENT))
                painter.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * fraction, bar.height()), 2.5, 2.5)
        painter.restore()
