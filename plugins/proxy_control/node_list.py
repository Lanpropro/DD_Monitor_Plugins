"""A virtualized node list; paint visible rows without per-node widgets."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QListView, QStyledItemDelegate, QStyle

from ddm import theme
from .visuals import surface, symbol
from .regions import display_node_name, flag_icon, region_code

NODE_ROLE = Qt.UserRole + 1
ACTIVE_ROLE = Qt.UserRole + 2


class NodeView(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(1, self.viewport().width())
        self.setGridSize(QSize(max(1, (width - 2) // (2 if width >= 400 else 1)), 96))


class NodeModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = {}
        self.visible = []
        self.active = ''
        self.query = ''

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.visible)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.visible):
            return None
        name = self.visible[index.row()]
        record = self.records[name]
        if role == Qt.DisplayRole:
            return name
        if role == Qt.ToolTipRole:
            return display_node_name(name) + ('\n' + record['detail'] if record.get('detail') else '')
        if role == Qt.AccessibleTextRole:
            return f'{display_node_name(name)}，{record["type"]}，{delay_text(record)}' + ('，已选择' if name == self.active else '')
        if role == NODE_ROLE:
            return record
        if role == ACTIVE_ROLE:
            return name == self.active
        return None

    def set_nodes(self, nodes, active, types=None):
        self.beginResetModel()
        self.records = {name: {'name': name, 'type': (types or {}).get(name, '代理节点'),
                               'region': region_code(name), 'delay': None, 'state': 'idle'} for name in nodes}
        self.active = active
        self.visible = [name for name in nodes if self.query in name.casefold()]
        self.endResetModel()

    def filter(self, text):
        self.beginResetModel()
        self.query = text.strip().casefold()
        self.visible = [name for name in self.records if self.query in name.casefold()]
        self.endResetModel()

    def select(self, name):
        if name not in self.records:
            return
        self.active = name
        if self.visible:
            self.dataChanged.emit(self.index(0), self.index(len(self.visible) - 1), [ACTIVE_ROLE])

    def result(self, name, delay, state):
        if name not in self.records:
            return
        self.records[name].update(delay=delay, state=state)
        if state in ('testing', 'idle', 'ready'):
            self.records[name].pop('detail', None)
        if name in self.visible:
            index = self.index(self.visible.index(name))
            self.dataChanged.emit(index, index, [NODE_ROLE, Qt.AccessibleTextRole])

    def set_detail(self, name, detail):
        if name in self.records:
            self.records[name]['detail'] = detail


def delay_text(record):
    if record['state'] == 'ready':
        return f'{record["delay"]} ms'
    return {'idle': '未测试', 'testing': '测试中…', 'failed': record.get('detail', '测试失败'),
            'timeout': '超时'}.get(record['state'], '未测试')


class NodeDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(max(1, self.parent().gridSize().width()), 96)

    def paint(self, painter, option, index):
        record = index.data(NODE_ROLE)
        if record is None:
            return
        selected = index.data(ACTIVE_ROLE)
        rect = QRectF(option.rect).adjusted(4, 4, -4, -4)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        background = theme.ACCENT_SOFT if selected else (
            theme.CONTENT_HOVER if option.state & QStyle.State_MouseOver else surface())
        painter.setBrush(theme.qcolor(background))
        painter.setPen(QPen(theme.qcolor(theme.ACCENT if selected else theme.BORDER_STRONG), 1))
        painter.drawRoundedRect(rect, 15, 15)
        title = QFont(option.font)
        title.setPixelSize(theme.FONT_BODY)
        title.setWeight(QFont.DemiBold if selected else QFont.Medium)
        painter.setFont(title)
        painter.setPen(theme.qcolor(theme.TEXT1))
        flag_rect = QRectF(rect.left() + 14, rect.top() + 13, 24, 18)
        if record['region']:
            flag_icon(record['region']).paint(painter, flag_rect.toRect())
        else:
            symbol(painter, 'globe', flag_rect, theme.TEXT3)
        text_rect = rect.adjusted(46, 10, -35, -56)
        text = painter.fontMetrics().elidedText(display_node_name(record['name']), Qt.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        if selected:
            symbol(painter, 'check', QRectF(rect.right() - 29, rect.top() + 12, 17, 17), theme.ACCENT)
        caption = QFont(option.font)
        caption.setPixelSize(theme.FONT_CAPTION)
        painter.setFont(caption)
        painter.setPen(theme.qcolor(theme.TEXT2))
        protocol = painter.fontMetrics().elidedText(record['type'], Qt.ElideRight, int(rect.width() - 24))
        painter.drawText(rect.adjusted(14, 37, -14, -30), Qt.AlignLeft | Qt.AlignVCenter, protocol)
        color = theme.ERROR if record['state'] in ('failed', 'timeout') else (
            theme.SUCCESS if record['state'] == 'ready' and record['delay'] < 250 else theme.TEXT2)
        color = theme.qcolor(color)
        if theme.is_light() and record['state'] in ('failed', 'timeout', 'ready'):
            color = color.darker(125)
        painter.setPen(color)
        painter.drawText(rect.adjusted(14, 62, -14, -7), Qt.AlignLeft | Qt.AlignVCenter, delay_text(record))
        if option.state & QStyle.State_HasFocus:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(theme.qcolor(theme.ACCENT), 1, Qt.DotLine))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 6, 6)
        painter.restore()
