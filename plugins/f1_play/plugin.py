"""Register the F1 Play workspace with the host."""
from pathlib import Path

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QToolButton

from ddm.plugins import Plugin

__path__ = [str(Path(__file__).parent)]
__package__ = __name__

from .workspace import RaceTools


class F1Play(Plugin):
    name = 'F1 Play'
    version = '0.2'

    def __init__(self):
        super().__init__()
        self.context = None
        self.workspace = None
        self.tools = None

    def on_load(self, context):
        if context.window is None:
            return
        required = ('register_workspace_control', 'show_workspace', 'create_plugin_widget')
        if not all(callable(getattr(context, name, None)) for name in required):
            raise RuntimeError('F1 Play 需要支持插件功能页的配套本体')
        self.context = context
        entry = QToolButton(context.window)
        entry.setProperty('dockMotion', 'slide-in-left')
        icon = QIcon(str(Path(__file__).with_name('icon-rail.svg')))
        action = QAction(icon, self.name, entry)
        action.triggered.connect(self.open_workspace)
        entry.setDefaultAction(action)
        if not context.register_workspace_control('entry', entry, priority=25):
            entry.deleteLater()

    def open_workspace(self):
        if self.workspace is None:
            self.workspace = self.context.create_plugin_widget('match_sync', 'match_workspace_v1', None)
            if self.workspace is None:
                self.context.notify('请安装并启用配套版本的比赛二路同步')
                return False
            self.tools = RaceTools(self.context, self.workspace)
        if not self.context.show_workspace(self.workspace, manages_content=True):
            return False
        if self.workspace.rows and not self.workspace.running:
            self.workspace.toggle_running()
        return True

    def on_unload(self):
        if self.workspace is not None:
            self.workspace.close()
            self.workspace.deleteLater()
            self.workspace = None
            self.tools = None
        if self.context is not None:
            self.context.unregister_workspace_control('entry')
            self.context = None


plugin = F1Play()
