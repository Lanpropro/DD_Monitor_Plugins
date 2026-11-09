"""Independent left-rail entry for process-local proxy preferences."""
import os
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QToolButton
from ddm.plugins import Plugin
from ddm import config

__path__ = [os.path.dirname(__file__)]
__package__ = __name__

from .controller import Controller
from .panel import Panel
from .popover import Popover


class ProxyControl(Plugin):
    name = '网络代理'
    version = '0.1'
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
        entry.setText(self.name)
        entry.setToolTip('导入订阅和节点，启动内置代理')
        entry.setIcon(QIcon(str(Path(__file__).with_name('proxy.svg'))))
        entry.clicked.connect(self.toggle)
        context.register_workspace_control('proxy', entry, priority=35)
        # Restore saved routing before the host begins resolving live rooms.
        if self.controller.options.enabled and self.controller.options.auto_start:
            self.controller.apply(self.controller.options)

    def toggle(self):
        if self.popover is not None and self.popover.isVisible():
            self.popover.hide()
            return
        if self.panel is None:
            self.panel = Panel(self.controller.options)
            self.popover = Popover(self.panel, self.context.window, self.entry)
            self.panel.hideRequested.connect(self.popover.hide)
            self.popover.hidden.connect(self.controller.latency.stop)
            self.panel.applyRequested.connect(self.controller.apply)
            self.panel.stopRequested.connect(self.controller.stop)
            self.panel.prepareRequested.connect(self.controller.prepare)
            self.panel.nodeRequested.connect(self.controller.select)
            self.panel.testRequested.connect(self.controller.test_nodes)
            self.panel.profileRequested.connect(self.controller.activate_profile)
            self.panel.autoStartRequested.connect(self.controller.set_auto_start)
            self.controller.status.connect(self.panel.set_status)
            self.controller.resources.connect(self.panel.resources.setText)
            self.controller.busy.connect(self.panel.set_busy)
            self.controller.nodes.connect(lambda nodes, selected: self.panel.set_nodes(
                nodes, selected, self.controller.core.types))
            self.controller.core.selected.connect(self.panel.select_node)
            self.controller.profilesChanged.connect(self.panel.set_profiles)
            self.controller.latency.result.connect(self.panel.model.result)
            self.controller.latency.running.connect(self.panel.set_testing)
            self.controller.prepared.connect(self.panel.core.setText)
            self.panel.set_profiles(self.controller.profiles, self.controller.options.profile_id)
            self.panel.set_nodes(self.controller.core.nodes, self.controller.core.current, self.controller.core.types)
            self.panel.set_status(self.controller.message)
            self.panel.set_busy(self.controller.working)
            self.controller._resources()
        self.popover.show()

    def on_unload(self):
        if self.controller is not None:
            self.controller.stop(announce=False)
        if self.popover is not None:
            self.popover.hide()
        for widget in (self.popover, self.controller):
            if widget is not None:
                widget.deleteLater()
        self.panel = self.controller = None
        self.popover = None


plugin = ProxyControl()
