"""Embedded RTMP room controls, using the host's forms and buttons."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QHeaderView, QMessageBox, QPushButton, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)


class RtmpPage(QWidget):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        self.inputs = getattr(workspace.owner,'inputs',None)
        self.signature = None
        box = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel('外部输入')
        title.setObjectName('SettingsTitle')
        heading.addWidget(title)
        heading.addStretch()
        back = QPushButton('返回录像库')
        back.setObjectName('IconButton')
        back.clicked.connect(workspace.show_library)
        heading.addWidget(back)
        box.addLayout(heading)
        hint = QLabel('OBS选择自定义服务，复制服务器地址和密钥。推荐H.264 / AAC、2秒关键帧；每次推流保存一份切片格式录像，可边录边看。不采集平台弹幕。')
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(['房间','状态','端口','已录时长 / 提示'])
        self.list.setRootIsDecorated(False)
        self.list.header().setStretchLastSection(False)
        for index in range(4):
            self.list.header().setSectionResizeMode(index,QHeaderView.Stretch if index in (0,3) else QHeaderView.ResizeToContents)
        self.list.itemSelectionChanged.connect(self.select)
        box.addWidget(self.list,1)
        actions = QHBoxLayout()
        for label,method in [('启用',lambda:self.enable(True)),('停用',lambda:self.enable(False)),('移除输入',self.remove),('回看本次推流',self.review)]:
            button = QPushButton(label)
            button.setObjectName('IconButton')
            button.clicked.connect(method)
            actions.addWidget(button)
        box.addLayout(actions)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.address, self.key = QLineEdit(), QLineEdit()
        self.address.setReadOnly(True)
        self.key.setReadOnly(True)
        self.key.setEchoMode(QLineEdit.Password)
        for label,field in [('OBS服务器',self.address),('推流密钥',self.key)]:
            row = QHBoxLayout()
            row.addWidget(field)
            copy = QPushButton('复制')
            copy.setObjectName('IconButton')
            copy.clicked.connect(lambda _checked=False,field=field:QApplication.clipboard().setText(field.text()))
            row.addWidget(copy)
            form.addRow(label,row)
        self.name = QLineEdit()
        self.name.setPlaceholderText('新输入名称')
        self.port = QSpinBox()
        self.port.setRange(0,65535)
        self.port.setSpecialValueText('自动分配')
        self.lan = QCheckBox('接收局域网推流')
        self.lan.setToolTip('开启后监听所有IPv4网卡，OBS地址需替换为本机局域网IP；系统防火墙由用户管理。')
        row = QHBoxLayout()
        row.addWidget(self.name,1)
        row.addWidget(self.port)
        row.addWidget(self.lan)
        add = QPushButton('添加输入')
        add.setObjectName('PrimaryButton')
        add.clicked.connect(self.add)
        row.addWidget(add)
        form.addRow(row)
        box.addLayout(form)
        self.links = getattr(workspace.owner,'links',None)
        shortcuts = QHBoxLayout()
        self.link_enabled = QCheckBox('启用ddcut://浏览器快捷添加')
        self.link_enabled.setChecked(bool(self.links and self.links.server))
        self.link_enabled.setEnabled(self.links is not None)
        self.link_enabled.setToolTip(self.links.error if self.links and self.links.error else '仅为当前Windows用户注册ddcut://；停用会移除本安装的协议关联。')
        self.link_enabled.toggled.connect(self.enable_links)
        shortcuts.addWidget(self.link_enabled)
        self.link = QLineEdit()
        self.link.setPlaceholderText('ddcut://live.bilibili.com/123')
        shortcuts.addWidget(self.link,1)
        open_link = QPushButton('打开添加窗口')
        open_link.setObjectName('IconButton')
        open_link.setEnabled(self.links is not None)
        open_link.clicked.connect(lambda:workspace.guard(lambda:self.links.request(self.link.text())))
        shortcuts.addWidget(open_link)
        box.addLayout(shortcuts)
        self.setEnabled(self.inputs is not None)

    def enable_links(self, value):
        self.workspace.guard(lambda:self.links.set_enabled(value))
        self.link_enabled.blockSignals(True)
        self.link_enabled.setChecked(self.links.server is not None)
        self.link_enabled.blockSignals(False)

    def current(self):
        item = self.list.currentItem()
        return item.data(0,Qt.UserRole) if item else ''

    def refresh(self):
        if self.inputs is None:
            return
        rows = self.inputs.rows()
        signature = str(rows)
        if signature == self.signature:
            return
        self.signature = signature
        selected = self.current()
        self.list.blockSignals(True)
        self.list.clear()
        names = dict(starting='启动中',waiting='等待推流',recording='正在录制',disabled='已停用',error='启动失败')
        for definition,state in rows:
            info = state['info']
            detail = state['error'] or (f"{info['duration']:.1f} 秒" if info else '等待首个可读分段' if state['state']=='recording' else '')
            item = QTreeWidgetItem([definition['name'],names[state['state']],str(definition['port']),detail])
            item.setData(0,Qt.UserRole,definition['id'])
            self.list.addTopLevelItem(item)
            if definition['id']==selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.currentItem() is None and self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))
        self.select()

    def select(self):
        definition = next((row for row in self.inputs.definitions if row['id']==self.current()),None) if self.inputs else None
        self.address.setText(f"rtmp://127.0.0.1:{definition['port']}/live" if definition else '')
        self.key.setText(definition['key'] if definition else '')

    def add(self):
        definition = self.workspace.guard(lambda:self.inputs.add(self.name.text(),port=self.port.value(),lan=self.lan.isChecked()))
        if definition:
            self.name.clear()
            self.refresh()

    def enable(self, value):
        if self.current():
            self.workspace.guard(lambda:self.inputs.set_enabled(self.current(),value))
            self.refresh()

    def remove(self):
        identity = self.current()
        if identity and QMessageBox.question(self,'移除RTMP输入','停止并移除此输入？已录制的录像文件会保留。') == QMessageBox.Yes:
            self.workspace.guard(lambda:self.inputs.remove(identity))
            self.refresh()

    def review(self):
        source = next((row for row in self.inputs.sources() if row['source_id']=='rtmp:'+self.current()),None)
        if source:
            self.workspace.open_recording(source)
        else:
            self.workspace.report('当前没有可回看的推流，请等待首段封口；历史录像在录像库中。')
