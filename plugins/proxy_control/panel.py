"""Compact proxy overview, node cards and named service configuration pages."""
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from ddm import theme

from .configuration import Options
from .node_list import NodeDelegate, NodeModel, NodeView
from .visuals import NavigationDelegate, ProfileDelegate, ProfileList, RestoreSwitch, icon, surface


class Panel(QWidget):
    applyRequested = Signal(object)
    stopRequested = Signal()
    prepareRequested = Signal()
    nodeRequested = Signal(str)
    testRequested = Signal(object)
    hideRequested = Signal()
    profileRequested = Signal(str)
    autoStartRequested = Signal(bool)
    catalogRequested = Signal()
    routingRequested = Signal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setObjectName('ProxyPanel')
        self.setMinimumSize(0, 0)
        self._testing = False
        self._busy = False
        self._running = False
        self._importing = False
        self._loading = False
        self._saved_options = replace(options)
        self._profiles = []
        self._edit_id = options.profile_id
        self.model = NodeModel(self)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        rail = QFrame()
        rail.setObjectName('ProxyRail')
        rail.setFixedWidth(76)
        rail_box = QVBoxLayout(rail)
        rail_box.setContentsMargins(8, 16, 8, 12)
        rail_box.setSpacing(16)
        brand = QLabel()
        brand.setObjectName('ProxyBrand')
        brand.setPixmap(QIcon(str(Path(__file__).with_name('proxy.svg'))).pixmap(28, 28))
        brand.setFixedSize(44, 44)
        brand.setAlignment(Qt.AlignCenter)
        rail_box.addWidget(brand, 0, Qt.AlignHCenter)
        self.navigation = QListWidget()
        self.navigation.setObjectName('ProxyNavigation')
        self.navigation.setItemDelegate(NavigationDelegate(self.navigation))
        self.navigation.setMouseTracking(True)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for label, name in [('总览', 'overview'), ('代理', 'nodes'), ('配置', 'folder')]:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)
            self.navigation.addItem(item)
        rail_box.addWidget(self.navigation, 1)
        root.addWidget(rail)
        content = QWidget()
        content.setObjectName('ProxyContent')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 12)
        layout.setSpacing(14)
        root.addWidget(content, 1)
        header = QHBoxLayout()
        self.title = QLabel('总览')
        self.title.setObjectName('ProxyTitle')
        self.state = QLabel('未启动')
        self.state.setObjectName('ProxyState')
        self.close_button = QPushButton()
        self.close_button.setObjectName('ProxyClose')
        self.close_button.setFixedSize(28, 28)
        self.close_button.setAccessibleName('收起网络代理')
        self.close_button.setToolTip('收起，代理继续运行')
        self.close_button.clicked.connect(self.hideRequested)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.state)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.pages = QStackedWidget()
        self.pages.installEventFilter(self)
        layout.addWidget(self.pages, 1)
        self._build_dashboard(options)
        self._build_nodes()
        self._build_settings(options)
        self.navigation.currentRowChanged.connect(self._page_changed)
        self.navigation.setCurrentRow(0)
        self.status = QLabel('在“配置”中添加服务商订阅，即可启动代理')
        self.status.setObjectName('ProxyFooter')
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(36)
        layout.addWidget(self.status)
        theme.signals().changed.connect(self.refresh_theme)
        self.refresh_theme()

    def _build_dashboard(self, options):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        grid = QGridLayout(content)
        self.dashboard_grid = grid
        self.dashboard_cards = []
        self._dashboard_columns = 2
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.card_icons = []

        def card(title, symbol, row, column, span=1):
            frame = QFrame()
            frame.setObjectName('ProxyCard')
            self.dashboard_cards.append(frame)
            box = QVBoxLayout(frame)
            box.setContentsMargins(14, 12, 14, 12)
            box.setSpacing(10)
            heading = QHBoxLayout()
            heading.setSpacing(7)
            picture = QLabel()
            picture.setFixedSize(18, 18)
            self.card_icons.append((picture, symbol))
            heading.addWidget(picture)
            label = QLabel(title)
            label.setObjectName('ProxyHint')
            heading.addWidget(label, 1)
            box.addLayout(heading)
            grid.addWidget(frame, row, column, 1, span)
            return box

        service = card('代理服务', 'latency', 0, 0)
        self.service_state = QLabel('尚未启动')
        self.service_state.setObjectName('ProxyValue')
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
        settings = card('快捷设置', 'settings', 0, 1)
        self.auto_start = RestoreSwitch('启动时恢复')
        self.auto_start.setToolTip('仅恢复退出前仍在运行的代理；点击停止后，下次不会自动连接')
        self.auto_start.setChecked(options.auto_start)
        self.auto_start.clicked.connect(self.autoStartRequested)
        settings.addWidget(self.auto_start)
        self.routing = QComboBox()
        self.routing.setObjectName('ProxyInline')
        self.routing.setMinimumWidth(0)
        self.routing.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for label, mode in [('规则 · 国内直播直连', 'rule'), ('全局 · 全部经节点', 'global'),
                            ('直连 · 不经节点', 'direct')]:
            self.routing.addItem(label, mode)
        self.routing.setCurrentIndex(self.routing.findData(options.routing))
        self.routing.setToolTip('仅作用于本软件。规则模式：内置国内直播域名直连，其余经节点；不导入订阅路由规则。')
        self.routing.activated.connect(lambda index: self.routingRequested.emit(self.routing.itemData(index)))
        settings.addWidget(self.routing)
        provider = card('当前配置', 'folder', 1, 0)
        self.quick_profile = QComboBox()
        self.quick_profile.setObjectName('ProxyInline')
        self.quick_profile.setPlaceholderText('先在配置页添加服务商')
        self.quick_profile.setMinimumWidth(0)
        self.quick_profile.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.activate_profile = QPushButton('使用配置')
        self.activate_profile.setEnabled(False)
        self.activate_profile.clicked.connect(lambda: self.profileRequested.emit(self.quick_profile.currentData()))
        provider.addWidget(self.quick_profile)
        provider.addWidget(self.activate_profile, 0, Qt.AlignLeft)
        node = card('当前节点', 'nodes', 1, 1)
        self.quick_node = QComboBox()
        self.quick_node.setObjectName('ProxyInline')
        self.quick_node.setPlaceholderText('启动服务后可选择节点')
        self.quick_node.setMinimumWidth(0)
        self.quick_node.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.quick_node.setEnabled(False)
        self.quick_node.activated.connect(lambda index: self.nodeRequested.emit(self.quick_node.itemText(index)))
        self.all_nodes = QPushButton('查看全部节点')
        self.all_nodes.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        node.addWidget(self.quick_node)
        node.addWidget(self.all_nodes, 0, Qt.AlignLeft)
        resource = card('资源占用', 'overview', 2, 0, 2)
        self.resources = QLabel('内核未运行')
        self.resources.setObjectName('ProxyHint')
        self.resources.setWordWrap(True)
        resource.addWidget(self.resources)
        grid.setRowStretch(3, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(content)
        self.pages.addWidget(scroll)

    def eventFilter(self, watched, event):
        if watched is self.pages and event.type() == QEvent.Resize and hasattr(self, 'dashboard_cards'):
            columns = 1 if self.pages.width() < 460 else 2
            if columns != self._dashboard_columns:
                self._dashboard_columns = columns
                for index, card in enumerate(self.dashboard_cards):
                    self.dashboard_grid.removeWidget(card)
                    if columns == 1:
                        self.dashboard_grid.addWidget(card, index, 0)
                    elif index < 4:
                        self.dashboard_grid.addWidget(card, index // 2, index % 2)
                    else:
                        self.dashboard_grid.addWidget(card, 2, 0, 1, 2)
                self.dashboard_grid.setColumnStretch(1, 1 if columns == 2 else 0)
                self.dashboard_grid.setRowStretch(3, 0 if columns == 1 else 1)
                self.dashboard_grid.setRowStretch(5, 1 if columns == 1 else 0)
        return super().eventFilter(watched, event)

    def _build_nodes(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(9)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索节点名称')
        self.search.setClearButtonEnabled(True)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.search, 1)
        self.count = QLabel('0 个节点')
        self.count.setObjectName('ProxyHint')
        self.test = QPushButton('测延迟')
        self.test.setEnabled(False)
        self.test.setToolTip('测试当前搜索结果中的节点，最多同时测试 2 个')
        self.test.clicked.connect(lambda: self.testRequested.emit(list(self.model.visible)))
        toolbar.addWidget(self.test)
        box.addLayout(toolbar)
        box.addWidget(self.count)
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
        self.add_subscription.clicked.connect(self._node_action)
        box.addWidget(self.add_subscription)
        self.search.textChanged.connect(self._filter)
        self.pages.addWidget(page)
        self._filter('')

    def _build_settings(self, options):
        page = QWidget()
        page_box = QVBoxLayout(page)
        page_box.setContentsMargins(0, 0, 0, 0)
        self.configuration_views = QStackedWidget()
        page_box.addWidget(self.configuration_views)
        listing = QWidget()
        list_box = QVBoxLayout(listing)
        list_box.setContentsMargins(0, 0, 0, 0)
        list_box.setSpacing(12)
        self.profiles_hint = QLabel('选择配置进行编辑')
        self.profiles_hint.setObjectName('ProxyHint')
        list_box.addWidget(self.profiles_hint)
        self.profiles_list = ProfileList()
        self.profiles_list.setObjectName('ProxyProfiles')
        self.profiles_list.setItemDelegate(ProfileDelegate(self.profiles_list))
        self.profiles_list.currentRowChanged.connect(self._edit_profile)
        self.profiles_list.itemClicked.connect(lambda item: self._edit_profile(self.profiles_list.row(item)))
        list_box.addWidget(self.profiles_list, 1)
        self.add_profile = QPushButton('添加配置')
        self.add_profile.setObjectName('ProxyStart')
        self.add_profile.clicked.connect(self._new_profile)
        list_box.addWidget(self.add_profile, 0, Qt.AlignRight)
        self.configuration_views.addWidget(listing)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        self.back_to_profiles = QPushButton('配置列表')
        self.back_to_profiles.setObjectName('ProxyLink')
        self.back_to_profiles.clicked.connect(lambda: self.configuration_views.setCurrentIndex(0))
        box.addWidget(self.back_to_profiles, 0, Qt.AlignLeft)
        name_label = QLabel('配置名称')
        name_label.setObjectName('ProxyHint')
        box.addWidget(name_label)
        self.profile_name = QLineEdit(options.profile_name if options.profile else '')
        self.profile_name.setPlaceholderText('留空自动识别服务商名称')
        self.profile_name.setToolTip('优先读取订阅提供的名称；未提供时显示订阅域名，也可手动命名')
        box.addWidget(self.profile_name)
        source_label = QLabel('导入方式')
        source_label.setObjectName('ProxyHint')
        box.addWidget(source_label)
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
        box.addWidget(self.import_button, 0, Qt.AlignRight)
        self.import_status = QLabel('填写后点击上方按钮，下载并载入节点。')
        self.import_status.setObjectName('ProxyHint')
        self.import_status.setWordWrap(True)
        box.addWidget(self.import_status)
        self.advanced = QPushButton('内核设置')
        self.advanced.setObjectName('ProxyLink')
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
        self.configuration_views.addWidget(scroll)
        self.configuration_views.setCurrentIndex(1 if not options.profile else 0)
        self.pages.addWidget(page)
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
        self.empty.setText('没有匹配的节点' if total else
                           ('已保存配置，但尚未载入节点\n点击下方“载入节点”，无需启动代理' if self._saved_options.profile
                            else '还没有节点\n先在配置页添加服务商订阅'))
        self.add_subscription.setVisible(not total)
        self.add_subscription.setText('载入节点' if self._saved_options.profile else '添加订阅')
        self.test.setEnabled((bool(found) and self._running) or self._testing)

    def _node_action(self):
        if self._saved_options.profile:
            self.catalogRequested.emit()
        else:
            self.navigation.setCurrentRow(2)

    def _advanced(self, checked):
        self.runtime.setVisible(checked)
        self.advanced.setIcon(icon('down' if checked else 'right'))

    def _page_changed(self, index):
        self.pages.setCurrentIndex(index)
        self.title.setText(['总览', '代理', '配置'][index])

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
            self.configuration_views.setCurrentIndex(1)
            (self.profile if self.source.currentData() == 'nodes' else self.location).setFocus()
            self.set_status('请先填写订阅地址、配置文件或节点链接，再点击导入按钮。')
            return
        self.applyRequested.emit(replace(self._saved_options, core=self.core.text().strip(),
                                        auto_start=self.auto_start.isChecked(), routing=self.routing.currentData()))

    def _confirm_import(self):
        if self._busy:
            return
        options = self.options()
        try:
            options.validate()
        except ValueError as error:
            self.navigation.setCurrentRow(2)
            self.configuration_views.setCurrentIndex(1)
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
                       auto_start=self.auto_start.isChecked(), routing=self.routing.currentData())

    def _new_profile(self):
        self.configuration_views.setCurrentIndex(1)
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

    def _edit_profile(self, row, show=True):
        if row < 0:
            return
        if show:
            self.configuration_views.setCurrentIndex(1)
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
            item.setData(Qt.UserRole, {'name': profile.name, 'active': profile.id == active,
                                      'kind': {'url': '订阅链接', 'file': '本地配置', 'nodes': '节点链接'}[profile.source]})
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
            self._edit_profile(active_row, show=False)
        self.profiles_hint.setText(f'{len(profiles)} 份配置 · 点击卡片编辑' if profiles else '还没有配置，点击下方添加')
        self.configuration_views.setCurrentIndex(0 if profiles else 1)
        self.activate_profile.setEnabled(bool(profiles) and not self._busy)
        self._filter(self.search.text())

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
        self.quick_node.clear()
        self.quick_node.addItems(nodes)
        self.quick_node.setCurrentIndex(self.quick_node.findText(selected))
        self.quick_node.setEnabled(bool(nodes) and not self._busy)
        self._visibility()
        if nodes and self._importing and self._busy:
            self.search.clear()
            self.navigation.setCurrentRow(1)
        self.refresh_theme()

    def set_running(self, running):
        self._running = running
        self.state.setText('运行中' if running else '未启动')
        self.state.setProperty('online', running)
        self.service_state.setText('运行中' if running else '尚未启动')
        self.stop.setEnabled(running or self._busy)
        self.apply.setText('重新载入' if running else '启动代理')
        self._filter(self.search.text())
        self.refresh_theme()

    def set_routing(self, mode):
        self._saved_options.routing = mode
        self.routing.setCurrentIndex(self.routing.findData(mode))

    def set_testing(self, running):
        self._testing = running
        self.test.setText('停止测试' if running else '测延迟')
        self.test.setEnabled(running or (self._running and bool(self.model.visible)))

    def set_busy(self, busy):
        self._busy = busy
        self.apply.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        for widget in (self.source, self.location, self.profile, self.browse_profile, self.runtime,
                       self.profiles_list, self.profile_name, self.add_profile):
            widget.setEnabled(not busy)
        self.activate_profile.setEnabled(bool(self._profiles) and not busy)
        self.routing.setEnabled(not busy)
        self.add_subscription.setEnabled(not busy)
        self.quick_node.setEnabled(not busy and bool(self.model.records))
        self.nodes.setEnabled(not busy)
        self.stop.setEnabled(busy or self._running)
        if busy:
            self.state.setText('准备中…')
            self.service_state.setText('正在准备服务…')
        else:
            self.state.setText('运行中' if self._running else '未启动')
            self.service_state.setText('运行中' if self._running else '尚未启动')

    def refresh_theme(self, *_args):
        self.setStyleSheet(f'''
#ProxyPanel {{background: {theme.BG}; color: {theme.TEXT1}; font-family: {theme.FONT_FAMILY}; font-size: {theme.FONT_CONTROL}px;}}
#ProxyPanel QFrame#ProxyRail {{background: {theme.SIDEBAR}; border-right: 1px solid {theme.BORDER};}}
#ProxyPanel QWidget#ProxyContent {{background: {theme.BG};}}
#ProxyPanel QLabel {{background: transparent; border: none; font-weight: 400;}}
#ProxyPanel QLabel#ProxyBrand {{background: {surface()}; border-radius: 14px;}}
#ProxyPanel QLabel#ProxyTitle {{font-size: 22px; font-weight: 600;}}
#ProxyPanel QLabel#ProxyValue {{font-size: 20px; font-weight: 500;}}
#ProxyPanel QLabel#ProxyHint {{color: {theme.TEXT2}; font-size: {theme.FONT_CAPTION}px;}}
#ProxyPanel QLabel#ProxyFooter {{color: {theme.TEXT3}; font-size: 11px;}}
#ProxyPanel QLabel#ProxyState {{color: {theme.TEXT3}; background: {surface()}; border-radius: 10px; padding: 4px 8px; font-size: 11px;}}
#ProxyPanel QLabel#ProxyState[online="true"] {{color: {theme.SUCCESS};}}
#ProxyPanel QFrame#ProxyCard {{background: {surface()}; border: 1px solid {theme.BORDER_STRONG}; border-radius: 16px;}}
#ProxyPanel QListView {{background: transparent; border: none; outline: none;}}
#ProxyPanel QPushButton {{background: {surface()}; color: {theme.TEXT1}; border: 1px solid {theme.BORDER_STRONG}; border-radius: 13px; padding: 5px 11px; min-height: 18px;}}
#ProxyPanel QPushButton:hover {{background: {theme.CONTENT_HOVER};}}
#ProxyPanel QPushButton:disabled {{color: {theme.TEXT3};}}
#ProxyPanel QPushButton#ProxyStart {{background: {theme.ACCENT_SOFT}; border: 1px solid transparent; color: {theme.ACCENT}; border-radius: 15px; padding: 6px 13px;}}
#ProxyPanel QPushButton#ProxyStart:hover {{border-color: {theme.ACCENT};}}
#ProxyPanel QPushButton#ProxyClose {{background: transparent; border: none; padding: 0;}}
#ProxyPanel QPushButton#ProxyLink {{background: transparent; border: none; color: {theme.TEXT2}; padding: 3px 0;}}
#ProxyPanel QPlainTextEdit, #ProxyPanel QLineEdit {{background: {surface()}; color: {theme.TEXT1}; border: 1px solid {theme.BORDER_STRONG}; border-radius: 12px; padding: 8px 10px;}}
#ProxyPanel QPlainTextEdit:focus, #ProxyPanel QLineEdit:focus {{border-color: {theme.ACCENT};}}
#ProxyPanel QComboBox {{background: {surface()}; color: {theme.TEXT1}; border: 1px solid {theme.BORDER_STRONG}; border-radius: 12px; padding: 6px 8px; min-height: 18px;}}
#ProxyPanel QComboBox#ProxyInline {{background: transparent; border: none; padding: 2px 0;}}
#ProxyPanel QComboBox::drop-down {{border: none; width: 20px;}}
#ProxyPanel QScrollArea, #ProxyPanel QScrollArea > QWidget > QWidget {{background: transparent; border: none;}}
''')
        for label, name in self.card_icons:
            label.setPixmap(icon(name).pixmap(18, 18))
        for button, name in ((self.close_button, 'close'), (self.apply, 'play'), (self.stop, 'stop'),
                             (self.test, 'latency'), (self.add_profile, 'plus'),
                             (self.back_to_profiles, 'back'), (self.all_nodes, 'right')):
            button.setIcon(icon(name, theme.ACCENT if button is self.apply else theme.TEXT2))
            button.setIconSize(QSize(16, 16))
        self._advanced(self.advanced.isChecked())
        self.navigation.viewport().update()
        self.profiles_list.viewport().update()
        self.auto_start.update()
        self.nodes.viewport().update()
