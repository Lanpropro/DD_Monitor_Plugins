"""DDCut Replay entry point for the host's single-file loader."""
import os
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMessageBox, QStyle, QToolBar, QToolButton

from ddm import config
from ddm import plugins as api

__path__ = [os.path.dirname(__file__)]
__package__ = __name__

from .media import TaskHub
from .preview_server import ArchiveServer
from .recorder import ReplayRecordingSession
from .workspace import Workspace


class DDCutReplay(api.Plugin):
    name = '录制与切片'
    version = '0.1.3'

    def __init__(self):
        super().__init__()
        self.service = self.hub = self.workspace = self.toolbar = self.timer = None
        self.entry = self.action = None
        self.preview_server = None
        self._ticks = 0

    def on_load(self, context):
        self.context = context
        if context.window is None:
            return
        self.service = getattr(context, 'recording', None)
        if self.service is not None and self.service.api_version != 1:
            self.service = None
        if self.service is not None and hasattr(self.service, 'register_backend'):
            self.service.register_backend(ReplayRecordingSession)
        action = self.action = QAction('录制切片', context.window)
        action.setCheckable(True)
        action.setToolTip('录制切片：回看正在录制的内容、时间轴与快速切片（Ctrl+Shift+R）')
        glyph = QPixmap(36, 36)
        glyph.setDevicePixelRatio(2)
        glyph.fill(Qt.transparent)
        painter = QPainter(glyph)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor('#dde3eb'), 1.5))
        painter.drawEllipse(2, 11, 4, 4)
        painter.drawEllipse(11, 11, 4, 4)
        painter.drawLine(5, 12, 13, 3)
        painter.drawLine(12, 12, 4, 3)
        painter.end()
        action.setIcon(QIcon(glyph))
        action.setShortcut(QKeySequence('Ctrl+Shift+R'))
        action.setShortcutContext(Qt.ApplicationShortcut)
        action.triggered.connect(self.toggle)
        context.window.addAction(action)
        entry = QToolButton(context.window)
        entry.setDefaultAction(action)
        entry.setAccessibleName('录制切片')
        register = getattr(context, 'register_workspace_control', None)
        if register is not None and register('entry', entry, priority=10):
            self.entry = entry
        else:
            entry.deleteLater()
            action.setIcon(context.window.style().standardIcon(QStyle.SP_DialogSaveButton))
            self.toolbar = QToolBar('录制与切片', context.window)
            self.toolbar.setObjectName('DDCutReplayToolbar')
            self.toolbar.setMovable(False)
            self.toolbar.addAction(action)
            context.window.addToolBar(self.toolbar)
        if self.service is not None:
            self.preview_server = ArchiveServer()
            self.hub = TaskHub(Path(config.data_root()) / 'ddcut_replay')
            self.timer = QTimer(action)
            self.timer.setInterval(250)
            self.timer.timeout.connect(self.poll)
            self.timer.start()

    def open(self, *_):
        if self.service is None:
            self.action.setChecked(False)
            QMessageBox.information(self.context.window, '需要配套开发版本',
                '本体缺少录制服务接口 v1。请使用 DDCut Replay 配套本体源码；仅安装插件不会增加宿主接口。')
            return
        show = getattr(self.context, 'show_workspace', None)
        if show is None:
            self.action.setChecked(False)
            QMessageBox.information(self.context.window, '需要配套开发版本', '本体缺少嵌入式工作区接口，请更新配套本体。')
            return
        if self.workspace is None:
            self.workspace = Workspace(self)
            self.workspace.closed.connect(self._workspace_closed)
        if show(self.workspace):
            self.action.setChecked(True)
            self.workspace.refresh_sources()
            self.workspace.open_current_recording()
        else:
            self.action.setChecked(False)

    def toggle(self, checked):
        if checked:
            self.open()
        elif self.workspace is not None:
            self.workspace.close()

    def _workspace_closed(self):
        if self.action is not None:
            self.action.setChecked(False)

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
                    self.workspace.prepare_review()
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
                self.workspace.refresh_live()

    def on_unload(self):
        if self.timer:
            self.timer.stop()
        if self.workspace:
            self.workspace.close()
            self.workspace.deleteLater()
            self.workspace = None
        if self.hub:
            self.hub.close()
        if self.preview_server:
            self.preview_server.close()
            self.preview_server = None
        if self.service:
            self.service.close()
        if self.entry is not None:
            self.context.unregister_workspace_control('entry')
            self.entry = None
        if self.toolbar:
            self.context.window.removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        if self.action is not None:
            self.context.window.removeAction(self.action)
            self.action.deleteLater()
            self.action = None


plugin = DDCutReplay()
