"""Compact proxy overview, node cards and named service configuration pages."""
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem,
                               QScrollArea, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from ddm import theme
from ddm.ui import (ActionButton, DisclosureButton, HintLabel, SettingsSurface,
                    SegmentedControl, SelectControl, TitleLabel, ToggleSwitch)

from .configuration import Options, DEFAULT_USER_AGENT, USER_AGENTS
from .node_list import NodeChoiceDelegate, NodeDelegate, NodeModel, NodeSelect, NodeView
from .visuals import NavigationDelegate, ProfileDelegate, ProfileList, icon, surface, translucent
from .regions import display_node_name, flag_icon, region_code
from .connection_page import ConnectionPage
from .latency import MAX_CONCURRENT
from .traffic import quota, expiry_text
from .profile_editor import build_profile_editor


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
    targetRequested = Signal(str)

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
        brand = self.brand = QLabel()
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
        for label, name in [('总览', 'overview'), ('代理', 'nodes'), ('配置', 'folder'), ('连接', 'connections')]:
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
        self.title = TitleLabel('总览')
        self.state = QLabel()
        self.state.setObjectName('ProxyState')
        self.state.setFixedSize(28, 28)
        self.state.setAlignment(Qt.AlignCenter)
        self.close_button = ActionButton()
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
        self.connections = ConnectionPage()
        self.pages.addWidget(self.connections)
        self.navigation.currentRowChanged.connect(self._page_changed)
        self.navigation.itemClicked.connect(self._navigation_clicked)
        self.navigation.itemActivated.connect(self._navigation_clicked)
        self.navigation.setCurrentRow(0)
        self.status = HintLabel('在“配置”中添加服务商订阅，即可启动代理')
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
        self.dashboard_columns = [QVBoxLayout(), QVBoxLayout()]
        for index, column in enumerate(self.dashboard_columns):
            column.setSpacing(12)
            column.addStretch()
            grid.addLayout(column, 0, index)
        grid.setRowStretch(0, 1)
        self.card_icons = []

        def card(title, symbol):
            frame = SettingsSurface()
            frame.setObjectName('ProxyCard')
            frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
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
            label.setObjectName('SettingsSectionTitle')
            heading.addWidget(label, 1)
            box.addLayout(heading)
            return box

        service = card('代理服务', 'latency')
        self.service_state = TitleLabel('尚未启动')
        actions = QHBoxLayout()
        actions.addWidget(self.service_state, 1)
        self.apply = ActionButton()
        self.apply.setObjectName('ProxyPower')
        self.apply.setFixedSize(40, 40)
        self.apply.clicked.connect(self._start)
        actions.addWidget(self.apply)
        service.addLayout(actions)
        settings = card('快捷设置', 'settings')
        restore_row = QHBoxLayout()
        restore_row.addWidget(QLabel('启动软件时自动连接'), 1)
        self.auto_start = ToggleSwitch('启动软件时自动连接代理')
        self.auto_start.setToolTip('开启后，退出软件时代理仍在运行，下次启动才自动连接。手动暂停后不会自动连接。')
        self.auto_start.setChecked(options.auto_start)
        self.auto_start.clicked.connect(self.autoStartRequested)
        restore_row.addWidget(self.auto_start)
        settings.addLayout(restore_row)
        self.auto_start_hint = HintLabel('仅恢复退出前仍开启的代理；手动暂停后不恢复。')
        settings.addWidget(self.auto_start_hint)
        self.routing = SegmentedControl([('rule', '规则'), ('global', '全局'), ('direct', '直连')], options.routing)
        self.routing.setMinimumWidth(0)
        self.routing.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.routing.setToolTip('仅作用于本软件。规则模式：内置国内直播域名直连，其余经节点；不导入订阅路由规则。')
        self.routing.valueChanged.connect(self._request_routing)
        settings.addWidget(self.routing)
        self.routing_hint = HintLabel()
        settings.addWidget(self.routing_hint)
        self._routing_hint(options.routing)
        provider = card('当前配置', 'folder')
        self.quick_profile = SelectControl()
        self.quick_profile.setObjectName('ProxyInline')
        self.quick_profile.setPlaceholderText('先在配置页添加服务商')
        self.quick_profile.setMinimumWidth(0)
        self.quick_profile.setMinimumContentsLength(14)
        self.quick_profile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.quick_profile.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.activate_profile = ActionButton('使用配置')
        self.activate_profile.setEnabled(False)
        self.activate_profile.clicked.connect(lambda: self.profileRequested.emit(self.quick_profile.currentData()))
        provider.addWidget(self.quick_profile)
        provider.addWidget(self.activate_profile, 0, Qt.AlignLeft)
        node = card('当前节点', 'nodes')
        self.quick_node = NodeSelect()
        self.quick_node.setObjectName('ProxyInline')
        self.quick_node.setPlaceholderText('启动服务后可选择节点')
        self.quick_node.setMinimumWidth(0)
        self.quick_node.setMinimumContentsLength(14)
        self.quick_node.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.quick_node.setItemDelegate(NodeChoiceDelegate(self.model, self.quick_node))
        self.quick_node.setMaxVisibleItems(5)
        self.quick_node.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.quick_node.setEnabled(False)
        self.quick_node.activated.connect(lambda index: self.nodeRequested.emit(self.quick_node.itemData(index)))
        self.all_nodes = ActionButton('查看全部节点')
        self.all_nodes.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        node.addWidget(self.quick_node)
        node.addWidget(self.all_nodes, 0, Qt.AlignLeft)
        resource = card('资源占用', 'overview')
        self.resources = HintLabel('内核未运行')
        resource.addWidget(self.resources)
        self._arrange_dashboard(2)
        scroll.setWidget(content)
        self.pages.addWidget(scroll)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and watched in (self.source, self.agent):
            event.ignore()
            return True
        if watched is self.pages and event.type() == QEvent.Resize and hasattr(self, 'dashboard_cards'):
            columns = 1 if self.pages.width() < 460 else 2
            if columns != self._dashboard_columns:
                self._arrange_dashboard(columns)
        return super().eventFilter(watched, event)

    def _arrange_dashboard(self, columns):
        self._dashboard_columns = columns
        for card in self.dashboard_cards:
            for column in self.dashboard_columns:
                column.removeWidget(card)
        for index, card in enumerate(self.dashboard_cards):
            column = self.dashboard_columns[0 if columns == 1 or index in (0, 2, 4) else 1]
            column.insertWidget(column.count() - 1, card)
        self.dashboard_grid.setColumnStretch(0, 1)
        self.dashboard_grid.setColumnStretch(1, 1 if columns == 2 else 0)
        self.dashboard_grid.setHorizontalSpacing(12 if columns == 2 else 0)

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
        self.count = HintLabel('0 个节点')
        self.test = ActionButton('测延迟')
        self.test.setEnabled(False)
        self.test.setToolTip(f'测试当前搜索结果中的节点，最多同时测试 {MAX_CONCURRENT} 个，其余排队；收起后取消未完成项')
        self.test.clicked.connect(lambda: self.testRequested.emit(list(self.model.visible)))
        toolbar.addWidget(self.test)
        box.addLayout(toolbar)
        test_options = QHBoxLayout()
        test_options.addWidget(self.count, 1)
        test_options.addWidget(QLabel('检测地址'))
        self.test_target = SelectControl()
        self.test_target.setMaximumWidth(180)
        self.test_target.addItem('Google 204', 'google')
        self.test_target.addItem('YouTube 204', 'youtube')
        self.test_target.addItem('Cloudflare HTTP', 'cloudflare')
        self.test_target.setCurrentIndex(self.test_target.findData(self._saved_options.test_target))
        self.test_target.setToolTip('比较时请选择相同检测地址。FlClash 可优先使用订阅分组地址，未必是 Google。Google／YouTube 使用 HTTPS，Cloudflare 使用 HTTP；统一延迟，单节点共用 8 秒期限。')
        self.test_target.activated.connect(lambda index: self.targetRequested.emit(self.test_target.itemData(index)))
        test_options.addWidget(self.test_target)
        box.addLayout(test_options)
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
        self.empty = HintLabel('还没有节点\n先在配置页添加服务商订阅并启动')
        self.empty.setAlignment(Qt.AlignCenter)
        box.addWidget(self.empty, 1)
        self.add_subscription = ActionButton('添加订阅')
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
        self.profiles_hint = HintLabel('选择配置进行编辑')
        list_box.addWidget(self.profiles_hint)
        self.profiles_list = ProfileList()
        self.profiles_list.setObjectName('ProxyProfiles')
        self.profiles_list.setItemDelegate(ProfileDelegate(self.profiles_list))
        self.profiles_list.currentRowChanged.connect(self._edit_profile)
        self.profiles_list.itemClicked.connect(lambda item: self._edit_profile(self.profiles_list.row(item)))
        list_box.addWidget(self.profiles_list, 1)
        self.add_profile = ActionButton('添加配置')
        self.add_profile.setObjectName('PrimaryButton')
        self.add_profile.clicked.connect(self._new_profile)
        list_box.addWidget(self.add_profile, 0, Qt.AlignRight)
        self.configuration_views.addWidget(listing)
        self.configuration_views.addWidget(build_profile_editor(self, options))
        self.configuration_views.setCurrentIndex(1 if not options.profile else 0)
        self.pages.addWidget(page)
        self.source.currentIndexChanged.connect(self._visibility)
        self.source.currentIndexChanged.connect(self._edited)
        self.location.textChanged.connect(self._edited)
        self.profile.textChanged.connect(self._edited)
        self.core.textChanged.connect(self._edited)
        self.profile_name.textChanged.connect(self._edited)
        self.agent.editTextChanged.connect(self._edited)
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
        self.test.setEnabled(bool(found) or self._testing)

    def _node_action(self):
        if self._saved_options.profile:
            self.catalogRequested.emit()
        else:
            self.navigation.setCurrentRow(2)

    def _compatibility(self, checked):
        self.agent_area.setVisible(checked and self.source.currentData() == 'url')

    def _advanced(self, checked):
        self.runtime.setVisible(checked)

    def _page_changed(self, index):
        if index == 2:
            self.configuration_views.setCurrentIndex(0 if self._profiles else 1)
        self.pages.setCurrentIndex(index)
        self.title.setText(['总览', '代理', '配置', '连接'][index])

    def _navigation_clicked(self, item):
        if self.navigation.row(item) == 2:
            self.configuration_views.setCurrentIndex(0 if self._profiles else 1)

    def _visibility(self):
        source = self.source.currentData()
        self.compatibility.setVisible(source == 'url')
        self._compatibility(self.compatibility.isChecked())
        self.location_label.setText('订阅地址' if source == 'url' else '配置文件')
        self.location_label.setVisible(source != 'nodes')
        self.location_row.setVisible(source != 'nodes')
        self.profile_label.setVisible(source == 'nodes')
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
            message = '修改尚未确认，请点击下方导入按钮。'
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
        if self._running or self._busy:
            self.stopRequested.emit()
            return
        if not self._saved_options.profile:
            self.navigation.setCurrentRow(2)
            self.configuration_views.setCurrentIndex(1)
            (self.profile if self.source.currentData() == 'nodes' else self.location).setFocus()
            self.set_status('请先填写订阅地址、配置文件或节点链接，再点击导入按钮。')
            return
        self.applyRequested.emit(replace(self._saved_options, core=self.core.text().strip(),
                                        auto_start=self.auto_start.isChecked(), routing=self.routing.value(),
                                        test_target=self.test_target.currentData()))

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
                       auto_start=self.auto_start.isChecked(), routing=self.routing.value(),
                       test_target=self.test_target.currentData(), user_agent=self._user_agent())

    def _user_agent(self):
        text = self.agent.currentText().strip()
        return next((value for label, value in USER_AGENTS if text == label), text)

    def _set_user_agent(self, value):
        index = self.agent.findData(value)
        self.agent.setCurrentIndex(index)
        if index < 0:
            self.agent.setEditText(value)

    def _new_profile(self):
        self.configuration_views.setCurrentIndex(1)
        self.profiles_list.setCurrentRow(-1)
        self._loading = True
        self._edit_id = ''
        self.profile_name.clear()
        self.location.clear()
        self.profile.clear()
        self.source.setCurrentIndex(0)
        self._set_user_agent(DEFAULT_USER_AGENT)
        self._loading = False
        self._visibility()
        self.import_status.setText('填写订阅地址，名称可留空；导入后保存并启用。')
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
        self._set_user_agent(profile.user_agent)
        self._loading = False
        self._visibility()
        self.import_status.setText('编辑后点击下方按钮，更新并启用此配置。')

    def set_profiles(self, profiles, active):
        self._profiles = list(profiles)
        self.profiles_list.blockSignals(True)
        self.profiles_list.clear()
        self.quick_profile.clear()
        active_row = -1
        for row, profile in enumerate(profiles):
            suffix = ' · 当前配置' if profile.id == active else ''
            item = QListWidgetItem(profile.name + suffix)
            description = '，'.join(part for part in (profile.name, quota(profile.usage)[0], expiry_text(profile.usage)) if part)
            item.setToolTip(description)
            item.setData(Qt.AccessibleTextRole, description)
            item.setData(Qt.UserRole, {'name': profile.name, 'active': profile.id == active,
                                      'usage': profile.usage,
                                      'kind': {'url': '订阅链接', 'file': '本地配置', 'nodes': '节点链接'}[profile.source]})
            self.profiles_list.addItem(item)
            self.quick_profile.addItem(profile.name, profile.id)
            if profile.id == active:
                active_row = row
                self._saved_options = replace(self._saved_options, profile_id=profile.id,
                                              profile_name=profile.name, source=profile.source,
                                              profile=profile.profile, node=profile.node, user_agent=profile.user_agent)
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
        self.quick_node.setCurrentIndex(self.quick_node.findData(self.model.active))
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
        for name in nodes:
            self.quick_node.addItem(flag_icon(region_code(name)) if region_code(name) else icon('globe'),
                                    display_node_name(name), name)
            self.quick_node.setItemData(self.quick_node.count() - 1,
                                        display_node_name(name) + ' · ' + (types or {}).get(name, '代理节点'), Qt.ToolTipRole)
        self.quick_node.setCurrentIndex(self.quick_node.findData(selected))
        self.quick_node.setEnabled(bool(nodes) and not self._busy)
        self._visibility()
        if nodes and self._importing and self._busy:
            self.search.clear()
            self.navigation.setCurrentRow(1)
        self.refresh_theme()

    def set_results(self, results):
        for name, record in self.model.records.items():
            delay, state, detail = results.get(name, (None, 'idle', ''))
            if (record['delay'], record['state'], record.get('detail', '')) != (delay, state, detail):
                self.set_result(name, delay, state, detail)
        self.quick_node.view().viewport().update()

    def set_result(self, name, delay, state, detail):
        self.model.result(name, delay, state)
        self.model.set_detail(name, detail)
        self.quick_node.view().viewport().update()

    def set_running(self, running):
        self._running = running
        self.service_state.setText('运行中' if running else '尚未启动')
        self._filter(self.search.text())
        self.refresh_theme()

    def set_routing(self, mode):
        self._saved_options.routing = mode
        self.routing.blockSignals(True)
        self.routing.setValue(mode)
        self.routing.blockSignals(False)
        self._routing_hint(mode)

    def _routing_hint(self, mode):
        self.routing_hint.setText({'rule': '国内直播域名直连，其余代理',
                                  'global': '本软件接入流量全部经节点',
                                  'direct': '本软件接入流量直接连接'}[mode])

    def _request_routing(self, mode):
        self._routing_hint(mode)
        self.routingRequested.emit(mode)

    def set_testing(self, running):
        self._testing = running
        self.test.setText('停止测试' if running else '测延迟')
        self.test.setEnabled(running or bool(self.model.visible))
        self.test_target.setEnabled(not running and not self._busy)

    def set_test_target(self, target):
        self._saved_options.test_target = target
        self.test_target.setCurrentIndex(self.test_target.findData(target))

    def set_busy(self, busy):
        self._busy = busy
        self.apply.setEnabled(True)
        self._service_button()
        self.import_button.setEnabled(not busy)
        for widget in (self.source, self.location, self.profile, self.browse_profile, self.runtime,
                       self.profiles_list, self.profile_name, self.add_profile, self.agent):
            widget.setEnabled(not busy)
        self.activate_profile.setEnabled(bool(self._profiles) and not busy)
        self.routing.setEnabled(not busy)
        self.test_target.setEnabled(not busy and not self._testing)
        self.add_subscription.setEnabled(not busy)
        self.quick_node.setEnabled(not busy and bool(self.model.records))
        self.nodes.setEnabled(not busy)
        if busy:
            self.service_state.setText('正在准备服务…')
        else:
            self.service_state.setText('运行中' if self._running else '尚未启动')
        self._status_icon()

    def refresh_theme(self, *_args):
        stop_ink = (theme.qcolor(theme.ERROR).darker(120) if theme.is_light()
                    else theme.qcolor(theme.ERROR).lighter(110)).name()
        self.setStyleSheet(f'''
#ProxyPanel {{background: transparent; color: {theme.TEXT1}; font-family: {theme.FONT_FAMILY}; font-size: {theme.FONT_CONTROL}px;}}
#ProxyPanel QFrame#ProxyRail {{background: {translucent(theme.SIDEBAR, 135)}; border-right: 1px solid {theme.BORDER};}}
#ProxyPanel QWidget#ProxyContent {{background: transparent;}}
#ProxyPanel QWidget#ProxyConnections {{background: transparent;}}
#ProxyPanel QTreeWidget#ProxyConnectionList {{background: {surface()}; color: {theme.TEXT1}; border: 1px solid {theme.BORDER_STRONG}; border-radius: {theme.RADIUS_MD}px; outline: none;}}
#ProxyPanel QTreeWidget#ProxyConnectionList::item {{padding: 5px 3px;}}
#ProxyPanel QTreeWidget#ProxyConnectionList::item:selected {{background: {theme.ACCENT_SOFT}; color: {theme.TEXT1};}}
#ProxyPanel QHeaderView::section {{background: {surface()}; color: {theme.TEXT2}; border: none; border-bottom: 1px solid {theme.BORDER}; padding: 4px;}}
#ProxyPanel QLabel {{background: transparent; border: none;}}
#ProxyPanel QLabel#ProxyBrand {{background: {surface()}; border-radius: {theme.RADIUS_LG}px;}}
#ProxyPanel QListView {{background: transparent; border: none; outline: none;}}
#ProxyPanel QPushButton#ProxyPower {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT}; border: 1px solid {translucent(theme.ACCENT, 90)}; border-radius: 20px; padding: 0; min-height: 38px; max-height: 38px;}}
#ProxyPanel QPushButton#ProxyPower:hover {{border-color: {theme.ACCENT};}}
#ProxyPanel QPushButton#ProxyPower[serviceState="on"] {{background: {translucent(theme.ERROR, 28)}; color: {stop_ink}; border-color: {translucent(theme.ERROR, 120)};}}
#ProxyPanel QPushButton#ProxyPower[serviceState="on"]:hover {{background: {translucent(theme.ERROR, 46)}; border-color: {theme.ERROR};}}
#ProxyPanel QPushButton#ProxyPower[serviceState="busy"] {{background: {surface()}; color: {theme.TEXT2}; border-color: {theme.BORDER_STRONG};}}
#ProxyPanel QPushButton#ProxyClose {{background: transparent; border: none; padding: 0;}}
#ProxyPanel QComboBox QLineEdit {{background: transparent; border: none; padding: 0;}}
#ProxyPanel QScrollArea, #ProxyPanel QScrollArea > QWidget > QWidget {{background: transparent; border: none;}}
''')
        for label, name in self.card_icons:
            label.setPixmap(icon(name).pixmap(18, 18))
        for button, name in ((self.close_button, 'close'),
                             (self.test, 'latency'), (self.add_profile, 'plus'),
                             (self.back_to_profiles, 'back'), (self.all_nodes, 'right')):
            button.setIcon(icon(name, theme.TEXT2))
            button.setIconSize(QSize(16, 16))
        self._service_button()
        self._status_icon()
        self._advanced(self.advanced.isChecked())
        self.navigation.viewport().update()
        self.profiles_list.viewport().update()
        self.auto_start.update()
        self.nodes.viewport().update()

    def _status_icon(self):
        if self._busy:
            name, text, color = 'latency', '代理准备中', theme.ACCENT
        elif self._running:
            name, text = 'check', '代理运行中'
            color = (theme.qcolor(theme.SUCCESS).darker(145) if theme.is_light()
                     else theme.qcolor(theme.SUCCESS).lighter(115)).name()
        else:
            name, text, color = 'power', '代理未启动', theme.TEXT2
        self.state.setPixmap(icon(name, color).pixmap(22, 22))
        self.state.setToolTip(text)
        self.state.setAccessibleName(text)

    def _service_button(self):
        action = '取消准备' if self._busy else ('暂停代理' if self._running else '开启代理')
        state = 'busy' if self._busy else ('on' if self._running else 'off')
        self.apply.setAccessibleName(action)
        self.apply.setToolTip(action + ('：恢复软件原有网络环境，已有连接请重新播放' if state == 'on' else
                                       '：仅作用于本软件' if state == 'off' else '：停止本次准备'))
        self.apply.setProperty('serviceState', state)
        color = theme.TEXT2 if self._busy else (theme.ERROR if self._running else theme.ACCENT)
        if self._running and not self._busy:
            color = (theme.qcolor(color).darker(120) if theme.is_light() else theme.qcolor(color).lighter(110)).name()
        self.apply.setIcon(icon('close' if self._busy else 'power', color))
        self.apply.setIconSize(QSize(22, 22))
        self.apply.style().unpolish(self.apply)
        self.apply.style().polish(self.apply)
        self.apply.update()
