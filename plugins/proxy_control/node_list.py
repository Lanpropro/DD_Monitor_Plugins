"""A virtualized node list; paint visible rows without per-node widgets."""
from PySide6.QtCore import QAbstractListModel, QElapsedTimer, QModelIndex, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QListView, QStyledItemDelegate, QStyle

from ddm import theme
from ddm.motion import enabled as motion_enabled
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
        self.clock = QElapsedTimer()
        self.clock.start()
        self.animation = QTimer(self)
        self.animation.setInterval(50)
        self.animation.timeout.connect(self.viewport().update)

    def setModel(self, model):
        super().setModel(model)
        model.dataChanged.connect(self._animate)
        model.modelReset.connect(self._animate)

    def _animate(self, *_args):
        moving = self.isVisible() and motion_enabled() and any(record['state'] == 'testing' for record in self.model().records.values())
        if moving:
            self.animation.start()
        else:
            self.animation.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._animate()

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)

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
    return {'idle': '未测试', 'queued': '排队中', 'testing': '测试中…', 'failed': record.get('detail', '测试失败'),
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
        loading = record['state'] in ('testing', 'queued')
        if loading:
            arc = QRectF(rect.left() + 14, rect.top() + 64, 12, 12)
            angle = (self.parent().clock.elapsed() % 900) * 360 // 900 if record['state'] == 'testing' else 0
            painter.setPen(QPen(theme.qcolor(theme.ACCENT if record['state'] == 'testing' else theme.TEXT3),
                                1.6, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(arc, -angle * 16, 270 * 16)
        painter.drawText(rect.adjusted(33 if loading else 14, 62, -14, -7),
                         Qt.AlignLeft | Qt.AlignVCenter, delay_text(record))
        if option.state & QStyle.State_HasFocus:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(theme.qcolor(theme.ACCENT), 1, Qt.DotLine))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 6, 6)
        painter.restore()


class NodeChoiceDelegate(QStyledItemDelegate):
    """Use the node page's records in the compact overview selector."""
    def __init__(self, model, parent):
        super().__init__(parent)
        self.nodes = model

    def sizeHint(self, option, index):
        return QSize(280, 58)

    def paint(self, painter, option, index):
        record = self.nodes.records.get(index.data(Qt.UserRole))
        if record is None:
            return super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect)
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            painter.fillRect(rect, theme.qcolor(theme.ACCENT_SOFT))
        flag = QRectF(rect.left() + 10, rect.top() + 11, 24, 18)
        if record['region']:
            flag_icon(record['region']).paint(painter, flag.toRect())
        else:
            symbol(painter, 'globe', flag, theme.TEXT3)
        painter.setFont(option.font)
        painter.setPen(theme.qcolor(theme.TEXT1))
        text = painter.fontMetrics().elidedText(display_node_name(record['name']), Qt.ElideRight, int(rect.width() - 52))
        painter.drawText(rect.adjusted(44, 7, -8, -28), Qt.AlignVCenter, text)
        font = QFont(option.font)
        font.setPixelSize(theme.FONT_CAPTION)
        painter.setFont(font)
        painter.setPen(theme.qcolor(theme.TEXT2))
        text = record['type'] + ' · ' + delay_text(record)
        painter.drawText(rect.adjusted(44, 30, -8, -7), Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight, int(rect.width() - 52)))
        painter.restore()
