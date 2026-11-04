"""DDCut Replay entry point for the host's single-file loader."""
import os
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMessageBox, QToolBar

from ddm import config
from ddm import plugins as api

__path__ = [os.path.dirname(__file__)]
__package__ = __name__

from .media import TaskHub
from .workspace import Workspace


class DDCutReplay(api.Plugin):
    name = '录制与切片'
    version = '0.1.0'

    def __init__(self):
        super().__init__()
        self.service = self.hub = self.workspace = self.toolbar = self.timer = None
        self._ticks = 0

    def on_load(self, context):
        self.context = context
        if context.window is None:
            return
        self.service = getattr(context, 'recording', None)
        if self.service is not None and self.service.api_version != 1:
            self.service = None
        self.toolbar = QToolBar('录制与切片', context.window)
        self.toolbar.setObjectName('DDCutReplayToolbar')
        self.toolbar.setMovable(False)
        action = QAction('录制与切片', self.toolbar)
        action.setShortcut(QKeySequence('Ctrl+Shift+R'))
        action.setShortcutContext(Qt.ApplicationShortcut)
        action.triggered.connect(self.open)
        self.toolbar.addAction(action)
        context.window.addToolBar(self.toolbar)
        if self.service is not None:
            self.hub = TaskHub(Path(config.data_root()) / 'ddcut_replay')
            self.timer = QTimer(self.toolbar)
            self.timer.setInterval(250)
            self.timer.timeout.connect(self.poll)
            self.timer.start()

    def open(self, *_):
        if self.service is None:
            QMessageBox.information(self.context.window, '需要配套开发版本',
                '本体缺少录制服务接口 v1。请使用 DDCut Replay 配套本体源码；仅安装插件不会增加宿主接口。')
            return
        if self.workspace is None:
            self.workspace = Workspace(self)
        self.workspace.show()
        # User-triggered action only; background jobs never raise or activate it.
        self.workspace.raise_()
        self.workspace.activateWindow()

    def tile_actions(self, tile):
        return [('录制与切片…', lambda: self.open_for(tile))]

    def open_for(self, tile):
        self.open()
        if self.workspace:
            self.workspace.refresh_sources()
            for index in range(self.workspace.sources.topLevelItemCount()):
                item = self.workspace.sources.topLevelItem(index)
                if item.data(0, Qt.UserRole) == str(id(tile)):
                    self.workspace.sources.setCurrentItem(item)
                    self.workspace.tabs.setCurrentIndex(0)
                    break

    def poll(self):
        self._ticks += 1
        changed = False
        for job in self.hub.jobs:
            if job.delivered or job.future is None or not job.future.done():
                continue
            job.delivered = True
            changed = True
            if job.lease:
                self.service.release(job.lease)
            if job.state == 'done':
                if job.kind == 'scan':
                    pending = set(self.service.options().get('pending_outputs', []))
                    for entry in job.result:
                        if entry['path'] not in pending:
                            existing = next((item for item in self.hub.entries if item['path'] == entry['path']), None)
                            if existing:
                                existing.update({key: value for key, value in entry.items() if key not in existing})
                            else:
                                self.hub.entries.append(entry)
                elif job.kind in {'snapshot', 'import'}:
                    self.hub.add_entry(job.result)
                    entry = next(item for item in self.hub.entries if item['path'] == job.result['path'])
                    if self.workspace and self.workspace.isVisible() and self.workspace.pending_review == job.id:
                        self.workspace.review(entry)
                elif job.kind == 'export':
                    actual = job.result['actual_duration']
                    job.message = f"成品时长 {actual:.3f} 秒；方式 {job.result['mode']}；源 {job.result['source']}"
                    self.context.notify('切片已导出：' + job.output, on_click=self.open)
            elif job.state == 'failed':
                self.context.notify(job.title + '失败：' + job.message[:240], kind='warning', on_click=self.open)
        if changed:
            self.hub.save()
            if self.workspace:
                self.workspace.refresh_library()
        if self.workspace and self.workspace.isVisible():
            self.workspace.refresh_tasks()
            if self._ticks % 8 == 0:
                self.workspace.refresh_sources()

    def on_unload(self):
        if self.timer:
            self.timer.stop()
        if self.workspace:
            self.workspace.close()
            self.workspace.deleteLater()
            self.workspace = None
        if self.hub:
            self.hub.close()
        if self.service:
            self.service.close()
        if self.toolbar:
            self.context.window.removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None


plugin = DDCutReplay()
