"""Auxiliary proxy controls; never replace the live wall."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from .configuration import Options


class Panel(QDialog):
    applyRequested = Signal(object)
    stopRequested = Signal()
    prepareRequested = Signal()
    nodeRequested = Signal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle('网络代理')
        self.setModal(False)
        self.resize(500, 520)
        layout = QVBoxLayout(self)
        description = QLabel('导入订阅或节点，启动内置代理。启动或切换节点后，请重新播放直播间。')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.managed = QGroupBox('导入节点')
        managed = QVBoxLayout(self.managed)
        self.source = QComboBox()
        for name, value in [('订阅地址', 'url'), ('本地 Clash／Mihomo 配置', 'file'), ('粘贴节点链接', 'nodes')]:
            self.source.addItem(name, value)
        self.source.setCurrentIndex(self.source.findData(options.source))
        managed.addWidget(self.source)
        self.profile = QPlainTextEdit(options.profile)
        self.profile.setMaximumHeight(120)
        managed.addWidget(self.profile)
        self.browse_profile = QPushButton('选择配置文件…')
        self.browse_profile.clicked.connect(self._profile_file)
        managed.addWidget(self.browse_profile)
        layout.addWidget(self.managed)
        self.runtime = QGroupBox('代理内核')
        runtime = QVBoxLayout(self.runtime)
        row = QHBoxLayout()
        self.core = QLineEdit(options.core)
        choose = QPushButton('选择…')
        choose.clicked.connect(self._core_file)
        row.addWidget(self.core)
        row.addWidget(choose)
        runtime.addLayout(row)
        self.prepare = QPushButton('准备内核（首次联网下载）')
        self.prepare.clicked.connect(self.prepareRequested)
        runtime.addWidget(self.prepare)
        layout.addWidget(self.runtime)
        row = QHBoxLayout()
        self.nodes = QComboBox()
        self.nodes.setEnabled(False)
        self.nodes.activated.connect(lambda: self.nodeRequested.emit(self.nodes.currentText()))
        row.addWidget(QLabel('当前节点'))
        row.addWidget(self.nodes, 1)
        layout.addLayout(row)
        self.status = QLabel('导入订阅或节点后，点击“启动代理”')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.resources = QLabel('内核未运行')
        self.resources.setWordWrap(True)
        layout.addWidget(self.resources)
        buttons = QHBoxLayout()
        self.apply = QPushButton('启动代理')
        self.apply.clicked.connect(lambda: self.applyRequested.emit(self.options()))
        self.stop = QPushButton('停止代理')
        self.stop.clicked.connect(self.stopRequested)
        hide = QPushButton('收起')
        hide.clicked.connect(self.hide)
        for button in (self.apply, self.stop, hide):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.source.currentIndexChanged.connect(self._visibility)
        self._visibility()

    def _visibility(self):
        self.browse_profile.setVisible(self.source.currentData() == 'file')
        self.profile.setPlaceholderText({'url': '粘贴服务商提供的订阅地址', 'file': '选择本地节点配置文件',
                                         'nodes': '每行一个节点链接，例如 ss://、vmess://、vless://、trojan://'}
                                        .get(self.source.currentData(), ''))

    def _profile_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择节点配置', '', '节点配置 (*.yaml *.yml *.json *.txt);;所有文件 (*)')
        if path:
            self.profile.setPlainText(path)

    def _core_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Mihomo 内核', '', '程序 (*.exe);;所有文件 (*)')
        if path:
            self.core.setText(path)

    def options(self):
        return Options(enabled=True, core=self.core.text().strip(), source=self.source.currentData(),
                       profile=self.profile.toPlainText().strip(), node=self.nodes.currentText())

    def set_nodes(self, nodes, selected):
        self.nodes.clear()
        self.nodes.addItems(nodes)
        if selected in nodes:
            self.nodes.setCurrentText(selected)
        self.nodes.setEnabled(bool(nodes))

    def set_busy(self, busy):
        self.apply.setEnabled(not busy)
        self.prepare.setEnabled(not busy)
