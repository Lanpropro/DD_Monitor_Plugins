"""Library controls and background accounting; TaskHub owns persistent entries/jobs."""
from copy import deepcopy
from pathlib import Path
import time

from PySide6.QtCore import QDate, QObject, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QWidget)

from .library_calendar import ArchiveDay
from .library_data import collect_stats, deletion_plan, delete_files, human_size, identity, on_date
from .media import ACTIVE, Job
from .timeline import timecode


class LibraryTools(QObject):
    def __init__(self, workspace, layout):
        super().__init__(workspace)
        self.view, self.hub = workspace, workspace.hub
        self.future, self.stats_job, self.stats, self.next_check = None, None, None, 0
        self.batch = QCheckBox('批量选择')
        self.batch.toggled.connect(self.batch_changed)
        self.sort = QComboBox()
        for title, key in [('最新录制', 'created_at'), ('标题', 'title'), ('时长', 'duration'), ('文件大小', 'size')]:
            self.sort.addItem(title, key)
        self.sort.currentIndexChanged.connect(workspace.refresh_library)
        self.date_filter = QCheckBox('按日期')
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('yyyy-MM-dd')
        self.date.setAccessibleName('录像日期')
        self.date.dateChanged.connect(workspace.filter_library)
        self.date_filter.toggled.connect(workspace.filter_library)
        self.calendar_toggle = QCheckBox('录播日历')
        self.calendar_toggle.toggled.connect(self.toggle_calendar)
        self.bar = QWidget()
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.batch, self.sort, self.date_filter, self.date, self.calendar_toggle):
            row.addWidget(widget)
        row.addStretch()
        layout.insertWidget(0, self.bar)
        self.summary = QLabel('空间统计尚未刷新')
        self.summary.setWordWrap(True)
        self.summary.setObjectName('SettingsHint')
        layout.insertWidget(1, self.summary)
        self.calendar = ArchiveDay()
        self.calendar.selected.connect(self.calendar_selected)
        self.calendar_scroll = QScrollArea()
        self.calendar_scroll.setWidgetResizable(True)
        self.calendar_scroll.setWidget(self.calendar)
        workspace.library_stack.addWidget(self.calendar_scroll)
        self.actions = QWidget()
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.count = QLabel()
        row.addWidget(self.count)
        for title, callback in [('全选当前结果', self.select_all), ('清除选择', self.clear),
                                ('从列表移除', lambda: self.remove(False)), ('删除文件', lambda: self.remove(True))]:
            button = QPushButton(title)
            button.setObjectName('IconButton')
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()
        layout.addWidget(self.actions)
        self.actions.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        workspace.library.itemSelectionChanged.connect(self.update_count)

    def selected(self):
        paths = {item.data(Qt.UserRole) for item in self.view.library.selectedItems() if not item.isHidden()}
        return [entry for entry in self.hub.entries if entry['path'] in paths]

    def batch_changed(self, checked):
        from PySide6.QtWidgets import QAbstractItemView
        self.view.library.setSelectionMode(QAbstractItemView.MultiSelection if checked else QAbstractItemView.SingleSelection)
        self.view.library.clearSelection()
        self.actions.setVisible(checked)
        self.update_count()

    def select_all(self):
        for index in range(self.view.library.count()):
            item = self.view.library.item(index)
            item.setSelected(not item.isHidden())

    def clear(self):
        self.view.library.clearSelection()

    def update_count(self):
        entries = self.selected()
        duration = sum(entry.get('duration') or 0 for entry in entries)
        self.count.setText(f'已选 {len(entries)} 项 · {timecode(duration)}')
        self.calendar.selected_paths = {entry['path'] for entry in entries}
        self.calendar.update()
        self.view.update_library_actions()

    def sorted_entries(self):
        key = self.sort.currentData()
        def value(entry):
            if key == 'title':
                return str((entry.get('room') or {}).get('title') or entry.get('title') or '').casefold()
            if key == 'size' and self.stats:
                return self.stats['entries'].get(identity(entry['path']), {}).get('bytes', 0)
            return entry.get(key) or 0
        return sorted(self.hub.entries, key=value, reverse=key != 'title')

    def matches(self, entry):
        return not self.date_filter.isChecked() or on_date(entry, self.date.date().toPython())

    def toggle_calendar(self, checked):
        if checked:
            if not self.date_filter.isChecked():
                stamps = [entry.get('created_at') or 0 for entry in self.hub.entries]
                if any(stamps):
                    from datetime import datetime
                    self.date.setDate(QDate(datetime.fromtimestamp(max(stamps)).date()))
            self.date_filter.setChecked(True)
        self.view.filter_library()

    def refresh(self):
        live = self.view.category.currentData() == 'live'
        self.bar.setVisible(not live)
        self.actions.setVisible(not live and self.batch.isChecked())
        paths = {self.view.library.item(i).data(Qt.UserRole) for i in range(self.view.library.count())
                 if not self.view.library.item(i).isHidden()}
        self.calendar.set_entries([entry for entry in self.hub.entries if entry['path'] in paths], self.date.date().toPython())
        self.update_count()

    def calendar_selected(self, path, activate):
        grid = self.view.library
        for index in range(grid.count()):
            item = grid.item(index)
            if item.data(Qt.UserRole) == path:
                if self.batch.isChecked():
                    item.setSelected(not item.isSelected())
                    from PySide6.QtCore import QItemSelectionModel
                    grid.setCurrentItem(item, QItemSelectionModel.NoUpdate)
                else:
                    grid.setCurrentItem(item)
                grid.scrollToItem(item)
                if activate and not self.batch.isChecked():
                    self.view.open_selected()
                break

    def request_stats(self):
        if self.future is None:
            self.stats_job = Job('录像空间统计', 'library_stats')
            folder = self.view.service.options().get('directory', '')
            entries = deepcopy(self.hub.entries)
            sources = self.view.service.sources()
            self.source_counts = {state: sum(item['state'] == state for item in sources)
                                  for state in ('recording', 'buffering', 'reconnecting', 'stopping')}
            for source in sources:
                if source['state'] == 'buffering' and hasattr(self.view.service, 'live_recording'):
                    try:
                        entry = self.view.service.live_recording(source['source_id'], source['session_id'])
                        entries.append({**entry, '_buffer': True})
                    except RuntimeError:
                        pass
            self.future = self.hub.pool.submit(collect_stats, self.stats_job, entries, folder)
            self.next_check = time.monotonic() + 30

    def tick(self):
        if self.future is not None and self.future.done():
            future, self.future = self.future, None
            try:
                self.stats = future.result()
                parts = []
                for kind, label in [('recording', '录像/回放'), ('cache', '切片归档'), ('clip', '成品'), ('buffer', '即时缓存')]:
                    group = self.stats['categories'].get(kind, {})
                    parts.append(f"{label} {group.get('count', 0)}项 · {human_size(group.get('bytes', 0))} · {timecode(group.get('duration', 0))}"
                                 + (f"（{group['unknown']}项时长待探测）" if group.get('unknown') else ''))
                if self.stats['disk']:
                    parts.append('录制磁盘可用 ' + human_size(self.stats['disk']['free']))
                parts.append(f"录制 {self.source_counts['recording']} · 缓存 {self.source_counts['buffering']} · 重连/收尾 {self.source_counts['reconnecting'] + self.source_counts['stopping']}")
                missing = sum(row['missing'] for row in self.stats['entries'].values())
                if missing:
                    parts.append(f'{missing}项缺失或读取不完整')
                self.summary.setText('  |  '.join(parts))
                self.summary.setToolTip('已索引录像及插件即时缓存的实际文件和侧车大小，重复路径只计一次；时长不含未探测项。原版即时缓存未开放文件统计，不计入占用。\n' + '\n'.join(self.stats['errors'][:8]))
                for index in range(self.view.library.count()):
                    item = self.view.library.item(index)
                    info = self.stats['entries'].get(identity(item.data(Qt.UserRole)))
                    if info:
                        self.view.library.itemWidget(item).set_file_info(info)
                if self.sort.currentData() == 'size':
                    self.view.refresh_library()
            except Exception as error:
                self.summary.setText('空间统计失败：' + str(error))
        if self.view.isVisible() and time.monotonic() >= self.next_check and not self.hub.closed:
            self.request_stats()

    def remove(self, delete):
        entries = self.selected()
        if not entries:
            return
        try:
            if any(job.state in ACTIVE for job in self.hub.jobs):
                raise ValueError('请等待后台任务完成后再管理录像')
            protected = set(getattr(self.view, 'active_paths', set()))
            protected.update(self.view.service.options().get('pending_outputs', []))
            for source in self.view.service.sources():
                if source['state'] != 'idle' and hasattr(self.view.service, 'live_recording'):
                    try:
                        protected.add(self.view.service.live_recording(source['source_id'], source['session_id'])['path'])
                    except RuntimeError:
                        pass  # A new archive with no readable segments cannot be selected yet.
            if self.view.current:
                if self.view.library_review.currentWidget() is self.view.listing:
                    self.view.preview.stop()
                    self.view.current, self.view.live_source = None, None
                else:
                    protected.add(self.view.current['path'])
            plans = None
            if delete:
                from .project import read_project
                paths = set((self.hub.root / 'projects').glob('*.ddcut.json'))
                paths.update(Path(path) for path in self.hub.preferences.get('project_paths', []) if Path(path).is_file())
                editor = self.view.project_editor
                references = []
                if editor.document:
                    references.extend(editor.document.data['media'])
                for path in paths:
                    references.extend(read_project(path)['media'])
                plans = deletion_plan(entries, self.hub.entries, protected, references)
            elif any(identity(entry['path']) in {identity(path) for path in protected} for entry in entries):
                raise ValueError('请先停止录制或返回录像库，再移除记录')
            title = '删除录像文件' if delete else '从录像库移除'
            message = (f'永久删除 {len(entries)} 项录像及其弹幕/归档文件，无法撤销。' if delete else
                       f'移除 {len(entries)} 项索引，保留磁盘文件；刷新不再自动加入，可重新导入恢复。')
            message += '\n\n' + '\n'.join(entry['path'] for entry in entries[:8])
            if QMessageBox.question(self.view, title, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            if delete:
                self.hub.submit(title, 'library_delete', delete_files, plans)
                self.view.report('正在删除所选录像；结果见后台任务。')
            else:
                self.hub.forget_entries([entry['path'] for entry in entries])
                self.view.refresh_library()
            self.next_check = 0
        except Exception as error:
            self.view.report(error)

    def stop(self):
        self.timer.stop()
        if self.stats_job:
            self.stats_job.cancel.set()
