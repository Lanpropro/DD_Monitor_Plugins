"""Auxiliary proxy controls; never replace the live wall."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
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
        description = QLabel('为本软件选择连接方式。切换后请重新播放直播间，让已有连接使用新设置。')
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.mode = QComboBox()
        for name, value in [('跟随系统代理', 'system'), ('直接连接', 'direct'),
                            ('连接现有代理', 'external'), ('内置代理：订阅／节点', 'managed')]:
            self.mode.addItem(name, value)
        self.mode.setCurrentIndex(self.mode.findData(options.mode))
        form.addRow('连接方式', self.mode)
        layout.addLayout(form)
        self.external = QGroupBox('已有代理')
        external = QFormLayout(self.external)
        self.address = QLineEdit(options.address)
        self.address.setPlaceholderText('http://127.0.0.1:端口 或 socks5://主机:端口')
        self.address.setToolTip('支持 user:password@主机:端口；凭据仅保存在本机插件数据目录')
        external.addRow('代理地址', self.address)
        layout.addWidget(self.external)
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
        self.status = QLabel('请选择连接方式。订阅、节点与代理设置保留在本机，不写入日志。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.resources = QLabel('内核未运行')
        self.resources.setWordWrap(True)
        layout.addWidget(self.resources)
        buttons = QHBoxLayout()
        self.apply = QPushButton('应用')
        self.apply.clicked.connect(lambda: self.applyRequested.emit(self.options()))
        self.stop = QPushButton('停止并恢复原设置')
        self.stop.clicked.connect(self.stopRequested)
        hide = QPushButton('收起')
        hide.clicked.connect(self.hide)
        for button in (self.apply, self.stop, hide):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.mode.currentIndexChanged.connect(self._visibility)
        self.address.textChanged.connect(self._visibility)
        self.source.currentIndexChanged.connect(self._visibility)
        self._visibility()

    def _visibility(self):
        mode = self.mode.currentData()
        self.external.setVisible(mode == 'external')
        self.managed.setVisible(mode == 'managed')
        self.runtime.setVisible(mode == 'managed' or (mode == 'external' and
                                                    self.address.text().lower().startswith('socks')))
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
        return Options(mode=self.mode.currentData(), address=self.address.text().strip(),
                       core=self.core.text().strip(), source=self.source.currentData(),
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
