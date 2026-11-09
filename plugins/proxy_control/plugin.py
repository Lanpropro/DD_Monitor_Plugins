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


class ProxyControl(Plugin):
    name = '网络代理'
    version = '0.1'
    description = '连接现有代理，或使用内置内核导入订阅和节点'

    def __init__(self):
        super().__init__()
        self.controller = self.panel = None

    def on_load(self, context):
        self.context = context
        if context.window is None:
            return
        self.controller = Controller(Path(config.data_root()) / 'utils/proxy_control', context.window)
        entry = QToolButton(context.window)
        entry.setText(self.name)
        entry.setToolTip('设置本软件的代理连接')
        entry.setIcon(QIcon(str(Path(__file__).with_name('proxy.svg'))))
        entry.clicked.connect(self.toggle)
        context.register_workspace_control('proxy', entry, priority=35)
        # Restore saved routing before the host begins resolving live rooms.
        self.controller.apply(self.controller.options)

    def toggle(self):
        if self.panel is not None and self.panel.isVisible():
            self.panel.hide()
            return
        if self.panel is None:
            self.panel = Panel(self.controller.options, self.context.window)
            self.panel.applyRequested.connect(self.controller.apply)
            self.panel.stopRequested.connect(self.controller.stop)
            self.panel.prepareRequested.connect(self.controller.prepare)
            self.panel.nodeRequested.connect(self.controller.select)
            self.controller.status.connect(self.panel.status.setText)
            self.controller.resources.connect(self.panel.resources.setText)
            self.controller.busy.connect(self.panel.set_busy)
            self.controller.nodes.connect(self.panel.set_nodes)
            self.controller.prepared.connect(self.panel.core.setText)
            self.panel.set_nodes(self.controller.core.nodes, self.controller.core.current)
            self.panel.status.setText(self.controller.message)
            self.panel.set_busy(self.controller.working)
            self.controller._resources()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def on_unload(self):
        if self.controller is not None:
            self.controller.stop(announce=False)
        for widget in (self.panel, self.controller):
            if widget is not None:
                widget.deleteLater()
        self.panel = self.controller = None


plugin = ProxyControl()
