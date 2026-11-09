"""Connection destinations, selected route, and bounded session event display."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
                               QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)


class ConnectionPage(QWidget):
    activeChanged = Signal(bool)
    refreshRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ProxyConnections')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        row = QHBoxLayout()
        self.state = QLabel('代理未启动')
        self.state.setObjectName('ProxyHint')
        self.state.setWordWrap(True)
        row.addWidget(self.state, 1)
        refresh = QPushButton('刷新')
        refresh.clicked.connect(self._refresh)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.connections = QTreeWidget()
        self.connections.setObjectName('ProxyConnectionList')
        self.connections.setHeaderLabels(['目标 / 协议', '代理链 / 规则', '流量'])
        self.connections.setRootIsDecorated(False)
        self.connections.setUniformRowHeights(True)
        self.connections.setSelectionMode(QAbstractItemView.SingleSelection)
        self.connections.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.connections.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.connections.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.connections.setMinimumHeight(70)
        layout.addWidget(self.connections, 3)
        label = QLabel('最近事件与错误（仅本次会话）')
        label.setObjectName('ProxyHint')
        layout.addWidget(label)
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setMaximumBlockCount(64)
        self.events.setPlaceholderText('启动、测速和连接错误将显示在这里')
        self.events.setMinimumHeight(60)
        layout.addWidget(self.events, 2)

    def _refresh(self):
        self.activeChanged.emit(True)
        self.refreshRequested.emit()

    def set_snapshot(self, rows, _total):
        self.connections.clear()
        for target, network, chain, traffic, rule in rows:
            item = QTreeWidgetItem([f'{target} · {network}', f'{chain}\n{rule}', traffic])
            item.setToolTip(0, f'{target}\n{network}')
            item.setToolTip(1, f'{chain}\n{rule}')
            self.connections.addTopLevelItem(item)

    def set_events(self, events):
        self.events.setPlainText('\n'.join(events))
        self.events.verticalScrollBar().setValue(self.events.verticalScrollBar().maximum())

    def showEvent(self, event):
        super().showEvent(event)
        self.activeChanged.emit(True)

    def hideEvent(self, event):
        self.activeChanged.emit(False)
        super().hideEvent(event)
