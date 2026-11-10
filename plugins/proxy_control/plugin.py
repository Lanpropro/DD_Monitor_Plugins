"""Independent left-rail entry for process-local proxy preferences."""
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QToolButton
from ddm.plugins import Plugin
from ddm import config, theme

__path__ = [os.path.dirname(__file__)]
__package__ = __name__

from .controller import Controller
from .panel import Panel
from .popover import Popover
from .entry_icon import EntryIcon


class ProxyControl(Plugin):
    name = '网络代理'
    version = '1.0'
    description = '使用内置代理内核导入订阅和节点'

    def __init__(self):
        super().__init__()
        self.controller = self.panel = None
        self.popover = None

    def on_load(self, context):
        self.context = context
        if context.window is None:
            return
        self.controller = Controller(Path(config.data_root()) / 'utils/proxy_control', context.window)
        entry = QToolButton(context.window)
        self.entry = entry
        self.action = QAction(self.name, context.window)
        self.action.setCheckable(True)
        self.action.setIcon(QIcon(str(Path(__file__).with_name('proxy.svg'))))
        entry.setDefaultAction(self.action)
        entry.setToolTip('导入订阅和节点，启动内置代理')
        self.action.triggered.connect(self.toggle)
        context.register_workspace_control('proxy', entry, priority=35)
        self.action.toggled.connect(self._entry_icon)
        self.controller.runningChanged.connect(self._service_icon)
        theme.signals().changed.connect(self._entry_icon)
        self._entry_icon()
        if not Path(self.controller.options.core).is_file():
            self.controller.prepared.connect(self._restore_session, Qt.SingleShotConnection)
            self.controller.prepare()
        else:
            self._restore_session()

    def _restore_session(self, *_args):
        # Restore saved routing before the host begins resolving live rooms.
        if self.controller.options.enabled and self.controller.options.auto_start:
            self.controller.apply(self.controller.options)
        elif self.controller.options.profile and not self.controller.node_snapshot()[0]:
            self.controller.load_nodes()

    def toggle(self):
        if self.popover is not None and self.popover.isVisible():
            self.popover.hide()
            return
        if self.panel is None:
            self.panel = Panel(self.controller.options)
            self.popover = Popover(self.panel, self.context.window, self.entry)
            self.panel.hideRequested.connect(self.popover.hide)
            self.popover.hidden.connect(lambda: self.controller.menu_visible(False))
            self.popover.hidden.connect(self.controller.cancel_tests)
            self.popover.hidden.connect(lambda: self.action.setChecked(False))
            self.panel.applyRequested.connect(self.controller.apply)
            self.panel.stopRequested.connect(self.controller.stop)
            self.panel.prepareRequested.connect(self.controller.prepare)
            self.panel.nodeRequested.connect(self.controller.select)
            self.panel.testRequested.connect(self.controller.test_nodes)
            self.panel.profileRequested.connect(self.controller.activate_profile)
            self.panel.autoStartRequested.connect(self.controller.set_auto_start)
            self.panel.catalogRequested.connect(self.controller.load_nodes)
            self.panel.routingRequested.connect(self.controller.set_mode)
            self.panel.targetRequested.connect(self.controller.set_test_target)
            self.controller.status.connect(self.panel.set_status)
            self.controller.resources.connect(self.panel.resources.setText)
            self.controller.busy.connect(self.panel.set_busy)
            self.controller.runningChanged.connect(self.panel.set_running)
            self.controller.routingChanged.connect(self.panel.set_routing)
            self.controller.targetChanged.connect(self.panel.set_test_target)
            self.controller.nodes.connect(self._nodes_changed)
            self.controller.selectionChanged.connect(self.panel.select_node)
            self.controller.profilesChanged.connect(self.panel.set_profiles)
            self.controller.resultsChanged.connect(self.panel.set_results)
            self.controller.resultChanged.connect(self.panel.set_result)
            self.controller.testingChanged.connect(self.panel.set_testing)
            self.controller.prepared.connect(self.panel.core.setText)
            monitor, page = self.controller.connections, self.panel.connections
            page.activeChanged.connect(monitor.set_active)
            page.refreshRequested.connect(monitor.refresh)
            monitor.snapshot.connect(page.set_snapshot)
            monitor.status.connect(page.state.setText)
            self.controller.eventsChanged.connect(page.set_events)
            page.set_events(list(self.controller.events))
            self.panel.set_profiles(self.controller.profiles, self.controller.options.profile_id)
            self.panel.set_nodes(*self.controller.node_snapshot())
            self.panel.set_results(self.controller.result_snapshot())
            self.panel.set_running(bool(self.controller.endpoint))
            self.panel.set_status(self.controller.message)
            self.panel.set_busy(self.controller.working)
            self.controller._resources()
            self._entry_icon()
        self.popover.show()
        self.action.setChecked(True)
        self.controller.menu_visible(True)

    def _nodes_changed(self, nodes, selected):
        self.panel.set_nodes(nodes, selected, self.controller.node_snapshot()[2])
        self.panel.set_results(self.controller.result_snapshot())

    def _service_icon(self, running):
        self.entry.setProperty('workspaceRunning', running)
        self.entry.update()
        self._entry_icon()

    def _entry_icon(self, *_args):
        self.action.setIcon(QIcon(EntryIcon(self.entry)))
        if self.panel is not None:
            self.panel.brand.setPixmap(self.action.icon().pixmap(QSize(28, 28)))
        self.entry.setToolTip('网络代理：运行中（仅本软件）' if self.entry.property('workspaceRunning') else '网络代理：未启动')

    def on_unload(self):
        if self.controller is not None:
            theme.signals().changed.disconnect(self._entry_icon)
            self.controller.stop(announce=False)
        if self.popover is not None:
            self.popover.hide()
        for widget in (self.popover, self.controller):
            if widget is not None:
                widget.deleteLater()
        self.panel = self.controller = None
        self.popover = None
        if getattr(self, 'action', None) is not None:
            self.action.deleteLater()
        self.action = self.entry = None


plugin = ProxyControl()
