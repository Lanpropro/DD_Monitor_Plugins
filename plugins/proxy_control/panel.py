"""Compact proxy overview, node cards and named service configuration pages."""
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from ddm import theme

from .configuration import Options
from .node_list import NodeDelegate, NodeModel, NodeView


class Panel(QWidget):
    applyRequested = Signal(object)
    stopRequested = Signal()
    prepareRequested = Signal()
    nodeRequested = Signal(str)
    testRequested = Signal(object)
    hideRequested = Signal()
    profileRequested = Signal(str)
    autoStartRequested = Signal(bool)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setObjectName('ProxyPanel')
        self.setMinimumSize(0, 0)
        self._testing = False
        self._busy = False
        self._importing = False
        self._loading = False
        self._saved_options = replace(options)
        self._profiles = []
        self._edit_id = options.profile_id
        self.model = NodeModel(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel('网络代理')
        title.setObjectName('ProxyTitle')
        self.state = QLabel('未启动')
        self.state.setObjectName('ProxyState')
        self.close_button = QPushButton('×')
        self.close_button.setObjectName('ProxyClose')
        self.close_button.setFixedSize(28, 28)
        self.close_button.setAccessibleName('收起网络代理')
        self.close_button.setToolTip('收起，代理继续运行')
        self.close_button.clicked.connect(self.hideRequested)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.state)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(14)
        self.navigation = QListWidget()
        self.navigation.setObjectName('ProxyNavigation')
        self.navigation.setFixedWidth(78)
        self.navigation.addItems(['总览', '代理', '配置'])
        body.addWidget(self.navigation)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        layout.addLayout(body, 1)
        self._build_dashboard(options)
        self._build_nodes()
        self._build_settings(options)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.status = QLabel('在“配置”中添加服务商订阅，即可启动代理')
        self.status.setObjectName('ProxyHint')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        theme.signals().changed.connect(self.refresh_theme)
        self.refresh_theme()

    def _build_dashboard(self, options):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        def card(title, row, column, span=1):
            frame = QFrame()
            frame.setObjectName('ProxyCard')
            box = QVBoxLayout(frame)
            box.setSpacing(9)
            label = QLabel(title)
            label.setObjectName('ProxyHint')
            box.addWidget(label)
            grid.addWidget(frame, row, column, 1, span)
            return box

        service = card('代理服务', 0, 0)
        self.service_state = QLabel('尚未启动')
        service.addWidget(self.service_state)
        actions = QHBoxLayout()
        self.apply = QPushButton('启动代理')
        self.apply.setObjectName('ProxyStart')
        self.apply.clicked.connect(self._start)
        self.stop = QPushButton('停止')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stopRequested)
        actions.addWidget(self.apply, 1)
        actions.addWidget(self.stop)
        service.addLayout(actions)
        settings = card('快捷设置', 0, 1)
        self.auto_start = QCheckBox('启动软件时恢复代理')
        self.auto_start.setToolTip('仅恢复退出前仍在运行的代理；点击停止后，下次不会自动连接')
        self.auto_start.setChecked(options.auto_start)
        self.auto_start.clicked.connect(self.autoStartRequested)
        settings.addWidget(self.auto_start)
        scope = QLabel('作用范围：仅本软件\n切换后重新播放直播间生效')
        scope.setObjectName('ProxyHint')
        scope.setWordWrap(True)
        settings.addWidget(scope)
        provider = card('服务商配置', 1, 0, 2)
        provider_row = QHBoxLayout()
        self.quick_profile = QComboBox()
        self.quick_profile.setPlaceholderText('先在配置页添加服务商')
        self.quick_profile.setMinimumWidth(0)
        self.quick_profile.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.activate_profile = QPushButton('使用配置')
        self.activate_profile.setEnabled(False)
        self.activate_profile.clicked.connect(lambda: self.profileRequested.emit(self.quick_profile.currentData()))
        provider_row.addWidget(self.quick_profile, 1)
        provider_row.addWidget(self.activate_profile)
        provider.addLayout(provider_row)
        node = card('当前节点', 2, 0, 2)
        node_row = QHBoxLayout()
        self.quick_node = QComboBox()
        self.quick_node.setPlaceholderText('启动服务后可选择节点')
        self.quick_node.setMinimumWidth(0)
        self.quick_node.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.quick_node.setEnabled(False)
        self.quick_node.activated.connect(lambda index: self.nodeRequested.emit(self.quick_node.itemText(index)))
        all_nodes = QPushButton('全部节点 / 测延迟')
        all_nodes.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        node_row.addWidget(self.quick_node, 1)
        node_row.addWidget(all_nodes)
        node.addLayout(node_row)
        resource = card('资源占用', 3, 0, 2)
        self.resources = QLabel('内核未运行')
        self.resources.setObjectName('ProxyHint')
        self.resources.setWordWrap(True)
        resource.addWidget(self.resources)
        grid.setRowStretch(4, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(content)
        self.pages.addWidget(scroll)

    def _build_nodes(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(9)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索节点名称')
        self.search.setClearButtonEnabled(True)
        box.addWidget(self.search)
        toolbar = QHBoxLayout()
        self.count = QLabel('0 个节点')
        self.count.setObjectName('ProxyHint')
        self.test = QPushButton('测延迟')
        self.test.setEnabled(False)
        self.test.setToolTip('测试当前搜索结果中的节点，最多同时测试 2 个')
        self.test.clicked.connect(lambda: self.testRequested.emit(list(self.model.visible)))
        toolbar.addWidget(self.count)
        toolbar.addStretch()
        toolbar.addWidget(self.test)
        box.addLayout(toolbar)
        self.nodes = NodeView()
        self.nodes.setModel(self.model)
        self.nodes.setItemDelegate(NodeDelegate(self.nodes))
        self.nodes.setUniformItemSizes(True)
        self.nodes.setMouseTracking(True)
        self.nodes.setFrameShape(QFrame.NoFrame)
        self.nodes.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nodes.setSelectionMode(QAbstractItemView.SingleSelection)
        self.nodes.clicked.connect(lambda index: self.nodeRequested.emit(index.data()))
        self.nodes.activated.connect(lambda index: self.nodeRequested.emit(index.data()))
        box.addWidget(self.nodes, 1)
        self.empty = QLabel('还没有节点\n先在配置页添加服务商订阅并启动')
        self.empty.setObjectName('ProxyHint')
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        box.addWidget(self.empty, 1)
        self.add_subscription = QPushButton('添加订阅')
        self.add_subscription.clicked.connect(lambda: self.navigation.setCurrentRow(2))
        box.addWidget(self.add_subscription)
        self.search.textChanged.connect(self._filter)
        self.pages.addWidget(page)
        self._filter('')

    def _build_settings(self, options):
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel('服务商配置'), 1)
        self.add_profile = QPushButton('＋ 添加配置')
        self.add_profile.clicked.connect(self._new_profile)
        profile_row.addWidget(self.add_profile)
        box.addLayout(profile_row)
        self.profiles_list = QListWidget()
        self.profiles_list.setObjectName('ProxyProfiles')
        self.profiles_list.setMaximumHeight(86)
        self.profiles_list.hide()
        self.profiles_list.currentRowChanged.connect(self._edit_profile)
        box.addWidget(self.profiles_list)
        self.profile_name = QLineEdit(options.profile_name if options.profile else '')
        self.profile_name.setPlaceholderText('配置名称，如服务商名称')
        box.addWidget(self.profile_name)
        box.addWidget(QLabel('导入方式'))
        self.source = QComboBox()
        for name, value in [('订阅地址', 'url'), ('本地 Clash／Mihomo 配置', 'file'), ('节点链接', 'nodes')]:
            self.source.addItem(name, value)
        self.source.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.source.setMinimumContentsLength(6)
        self.source.setCurrentIndex(self.source.findData(options.source))
        box.addWidget(self.source)
        self.location = QLineEdit(options.profile if options.source != 'nodes' else '')
        self.location.setMinimumWidth(0)
        self.location.returnPressed.connect(self._confirm_import)
        box.addWidget(self.location)
        self.profile = QPlainTextEdit(options.profile if options.source == 'nodes' else '')
        self.profile.setFixedHeight(130)
        box.addWidget(self.profile)
        self.browse_profile = QPushButton('选择配置文件…')
        self.browse_profile.clicked.connect(self._profile_file)
        box.addWidget(self.browse_profile)
        self.import_button = QPushButton()
        self.import_button.setObjectName('ProxyStart')
        self.import_button.clicked.connect(self._confirm_import)
        box.addWidget(self.import_button)
        self.import_status = QLabel('填写后点击上方按钮，下载并载入节点。')
        self.import_status.setObjectName('ProxyHint')
        self.import_status.setWordWrap(True)
        box.addWidget(self.import_status)
        hint = QLabel('成功后保存这份配置并进入代理页。订阅和节点仅保存在本机。')
        hint.setObjectName('ProxyHint')
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.advanced = QPushButton('内核设置 ▸')
        self.advanced.setCheckable(True)
        box.addWidget(self.advanced)
        self.runtime = QWidget()
        runtime = QVBoxLayout(self.runtime)
        runtime.setContentsMargins(0, 0, 0, 0)
        runtime.addWidget(QLabel('内核程序'))
        self.core = QLineEdit(options.core)
        self.core.setMinimumWidth(0)
        runtime.addWidget(self.core)
        choose = QPushButton('选择内核程序…')
        choose.clicked.connect(self._core_file)
        runtime.addWidget(choose)
        self.prepare = QPushButton('准备内核（首次下载）')
        self.prepare.clicked.connect(self.prepareRequested)
        runtime.addWidget(self.prepare)
        self.advanced.setChecked(not Path(options.core).is_file())
        self.advanced.toggled.connect(self._advanced)
        self._advanced(self.advanced.isChecked())
        box.addWidget(self.runtime)
        box.addStretch()
        scroll.setWidget(content)
        self.pages.addWidget(scroll)
        self.source.currentIndexChanged.connect(self._visibility)
        self.source.currentIndexChanged.connect(self._edited)
        self.location.textChanged.connect(self._edited)
        self.profile.textChanged.connect(self._edited)
        self.core.textChanged.connect(self._edited)
        self.profile_name.textChanged.connect(self._edited)
        self._visibility()

    def _filter(self, text):
        self.model.filter(text)
        total, found = len(self.model.records), len(self.model.visible)
        self.count.setText(f'{found} / {total} 个节点' if text else f'{total} 个节点')
        self.nodes.setVisible(bool(found))
        self.empty.setVisible(not found)
        self.empty.setText('没有匹配的节点' if total else '还没有节点\n先在配置页添加服务商订阅并启动')
        self.add_subscription.setVisible(not total)
        self.test.setEnabled(bool(found) or self._testing)

    def _advanced(self, checked):
        self.runtime.setVisible(checked)
        self.advanced.setText('内核设置 ▾' if checked else '内核设置 ▸')

    def _visibility(self):
        source = self.source.currentData()
        self.browse_profile.setVisible(source == 'file')
        self.location.setVisible(source != 'nodes')
        self.profile.setVisible(source == 'nodes')
        self.location.setPlaceholderText('粘贴完整的 HTTP(S) 订阅地址' if source == 'url' else '选择本地节点配置文件')
        self.profile.setPlaceholderText('每行一个节点链接，如 ss://、vmess://、vless://、trojan://')
        self.import_button.setText('更新订阅并启用' if source == 'url' and self._edit_id else
                                   {'url': '导入订阅并启动', 'file': '导入配置并启动',
                                    'nodes': '导入节点并启动'}[source])

    def _edited(self, *_args):
        if not self._busy and not self._loading:
            message = '修改尚未确认，请点击上方导入按钮。'
            self.import_status.setText(message)
            self.status.setText(message)

    def _profile_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择节点配置', '', '节点配置 (*.yaml *.yml *.json *.txt);;所有文件 (*)')
        if path:
            self.location.setText(path)

    def _core_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Mihomo 内核', '', '程序 (*.exe);;所有文件 (*)')
        if path:
            self.core.setText(path)

    def _start(self):
        if not self._saved_options.profile:
            self.navigation.setCurrentRow(2)
            (self.profile if self.source.currentData() == 'nodes' else self.location).setFocus()
            self.set_status('请先填写订阅地址、配置文件或节点链接，再点击导入按钮。')
            return
        self.applyRequested.emit(replace(self._saved_options, core=self.core.text().strip(),
                                        auto_start=self.auto_start.isChecked()))

    def _confirm_import(self):
        if self._busy:
            return
        options = self.options()
        try:
            options.validate()
        except ValueError as error:
            self.navigation.setCurrentRow(2)
            self.set_status(str(error))
            self.import_status.setText(str(error))
            return
        self._importing = True
        self.applyRequested.emit(options)

    def options(self):
        return Options(enabled=True, core=self.core.text().strip(), source=self.source.currentData(),
                       profile=(self.profile.toPlainText() if self.source.currentData() == 'nodes'
                                else self.location.text()).strip(),
                       node=(self.model.active if self._edit_id == self._saved_options.profile_id else
                             next((profile.node for profile in self._profiles if profile.id == self._edit_id), '')),
                       profile_id=self._edit_id, profile_name=self.profile_name.text().strip() or '我的配置',
                       auto_start=self.auto_start.isChecked())

    def _new_profile(self):
        self.profiles_list.setCurrentRow(-1)
        self._loading = True
        self._edit_id = ''
        self.profile_name.clear()
        self.location.clear()
        self.profile.clear()
        self.source.setCurrentIndex(0)
        self._loading = False
        self._visibility()
        self.import_status.setText('填写配置名称和订阅，点击导入即可保存并启用。')
        self.profile_name.setFocus()

    def _edit_profile(self, row):
        if row < 0:
            return
        profile = self._profiles[row]
        self._loading = True
        self._edit_id = profile.id
        self.profile_name.setText(profile.name)
        self.source.setCurrentIndex(self.source.findData(profile.source))
        self.location.setText(profile.profile if profile.source != 'nodes' else '')
        self.profile.setPlainText(profile.profile if profile.source == 'nodes' else '')
        self._loading = False
        self._visibility()
        self.import_status.setText('点击导入按钮，更新并启用此配置。')

    def set_profiles(self, profiles, active):
        self._profiles = list(profiles)
        self.profiles_list.blockSignals(True)
        self.profiles_list.clear()
        self.quick_profile.clear()
        active_row = -1
        for row, profile in enumerate(profiles):
            suffix = ' · 当前配置' if profile.id == active else ''
            item = QListWidgetItem(profile.name + suffix)
            self.profiles_list.addItem(item)
            self.quick_profile.addItem(profile.name, profile.id)
            if profile.id == active:
                active_row = row
                self._saved_options = replace(self._saved_options, profile_id=profile.id,
                                              profile_name=profile.name, source=profile.source,
                                              profile=profile.profile, node=profile.node)
        self.quick_profile.setCurrentIndex(active_row)
        self.profiles_list.setCurrentRow(active_row)
        self.profiles_list.blockSignals(False)
        if active_row >= 0:
            self._edit_profile(active_row)
        self.profiles_list.setVisible(bool(profiles))
        self.profiles_list.setFixedHeight(min(84, max(38, len(profiles) * 38)))
        self.activate_profile.setEnabled(bool(profiles) and not self._busy)

    def select_node(self, name):
        self.model.select(name)
        self.quick_node.setCurrentIndex(self.quick_node.findText(self.model.active))
        self._saved_options.node = self.model.active

    def set_status(self, message):
        self.status.setText(message)
        if self._importing:
            self.import_status.setText(message)
            if not self._busy:
                self._importing = False

    def set_nodes(self, nodes, selected, types=None):
        self.model.set_nodes(nodes, selected, types)
        self._filter(self.search.text())
        self.state.setText('运行中' if nodes else '未启动')
        self.state.setProperty('online', bool(nodes))
        self.stop.setEnabled(bool(nodes))
        self.apply.setText('重新载入' if nodes else '启动代理')
        self.service_state.setText('运行中' if nodes else '尚未启动')
        self.quick_node.clear()
        self.quick_node.addItems(nodes)
        self.quick_node.setCurrentIndex(self.quick_node.findText(selected))
        self.quick_node.setEnabled(bool(nodes))
        self._visibility()
        if nodes and self._importing:
            self.search.clear()
            self.navigation.setCurrentRow(1)
        self.refresh_theme()

    def set_testing(self, running):
        self._testing = running
        self.test.setText('停止测试' if running else '测延迟')
        self.test.setEnabled(running or bool(self.model.visible))

    def set_busy(self, busy):
        self._busy = busy
        self.apply.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        for widget in (self.source, self.location, self.profile, self.browse_profile, self.runtime,
                       self.profiles_list, self.profile_name, self.add_profile):
            widget.setEnabled(not busy)
        self.activate_profile.setEnabled(bool(self._profiles) and not busy)
        self.stop.setEnabled(busy or bool(self.model.records))
        if busy:
            self.state.setText('准备中…')
            self.service_state.setText('正在准备服务…')
        else:
            self.state.setText('运行中' if self.model.records else '未启动')
            self.service_state.setText('运行中' if self.model.records else '尚未启动')

    def refresh_theme(self, *_args):
        self.setStyleSheet(f'''
#ProxyPanel {{background: {theme.SIDEBAR};}}
#ProxyPanel QLabel#ProxyTitle {{font-size: {theme.FONT_HEADING}px; font-weight: 600;}}
#ProxyPanel QLabel#ProxyHint {{color: {theme.TEXT2}; font-size: {theme.FONT_CAPTION}px;}}
#ProxyPanel QLabel#ProxyState {{color: {theme.TEXT2};}}
#ProxyPanel QLabel#ProxyState[online="true"] {{color: {theme.ACCENT};}}
#ProxyPanel QFrame#ProxyCard {{background: {theme.CONTENT}; border: 1px solid {theme.BORDER}; border-radius: 9px;}}
#ProxyPanel QListWidget#ProxyNavigation {{background: transparent; border: none; outline: none;}}
#ProxyPanel QListWidget#ProxyNavigation::item {{padding: 12px 8px; margin-bottom: 6px; border-radius: 7px;}}
#ProxyPanel QListWidget#ProxyNavigation::item:selected {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}
#ProxyPanel QListWidget#ProxyProfiles::item {{padding: 8px; border: 1px solid {theme.BORDER}; border-radius: 7px;}}
#ProxyPanel QListWidget#ProxyProfiles::item:selected {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT}; border-color: {theme.ACCENT};}}
#ProxyPanel QPushButton {{background: {theme.CONTENT}; color: {theme.TEXT1};
    border: 1px solid {theme.BORDER}; border-radius: 7px; padding: 6px 10px;}}
#ProxyPanel QPushButton:hover {{background: {theme.CONTENT_HOVER}; border-color: {theme.BORDER_STRONG};}}
#ProxyPanel QPushButton:disabled {{color: {theme.TEXT3};}}
#ProxyPanel QPushButton#ProxyStart {{background: {theme.ACCENT_SOFT}; border-color: {theme.ACCENT}; color: {theme.ACCENT};}}
#ProxyPanel QPushButton#ProxyClose {{background: transparent; border: none; padding: 0; font-size: 20px;}}
#ProxyPanel QPlainTextEdit, #ProxyPanel QLineEdit {{background: {theme.CONTENT}; color: {theme.TEXT1}; border: 1px solid {theme.BORDER}; border-radius: 7px; padding: 8px;}}
#ProxyPanel QListView {{background: transparent; border: none; outline: none;}}
#ProxyPanel QScrollArea, #ProxyPanel QScrollArea > QWidget > QWidget {{background: transparent; border: none;}}
''')
        self.nodes.viewport().update()
