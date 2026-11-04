"""Dedicated Qt recording, archive, review and task workspace."""
from datetime import datetime
from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QListWidget, QMessageBox, QPushButton, QSlider, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .media import ACTIVE, probe, scan_directory, prepare_snapshot, export_clip

STATES = {'idle': '未录制', 'recording': '录制中', 'buffering': '缓存中',
          'stopping': '收尾中', 'reconnecting': '等待重连', 'queued': '等待中',
          'running': '处理中', 'done': '已完成', 'failed': '失败',
          'cancelling': '正在取消', 'cancelled': '已取消', 'interrupted': '已中断'}


def clock(seconds):
    seconds = max(0, float(seconds or 0))
    return f'{int(seconds)//3600:02}:{int(seconds)%3600//60:02}:{seconds%60:06.3f}'


def button(text, callback, box):
    widget = QPushButton(text)
    widget.setMinimumHeight(30)
    widget.clicked.connect(callback)
    box.addWidget(widget)
    return widget


def table(headers):
    tree = QTreeWidget()
    tree.setHeaderLabels(headers)
    tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(True)
    tree.setSelectionMode(QAbstractItemView.SingleSelection)
    tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
    tree.header().setStretchLastSection(True)
    return tree


class Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 180)
        from PySide6.QtGui import QColor, QPalette
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor('#11151b'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WA_NativeWindow)
        self.core = None

    def load(self, path):
        self.stop()
        # Reuse the host's bundled libmpv, loader setup and safe embedded options.
        from ddm.player import EMBEDDED_OPTIONS
        import mpv
        self.core = mpv.MPV(wid=str(int(self.winId())), mute=True, volume=50,
                            keep_open=True, **EMBEDDED_OPTIONS)
        self.core.play(str(path))

    def stop(self):
        if self.core is not None:
            self.core.terminate()
            self.core = None

    def position(self):
        return float(self.core.time_pos or 0) if self.core is not None else 0

    def seek(self, seconds):
        if self.core is not None:
            self.core.seek(float(seconds), 'absolute+exact')


class Workspace(QWidget):
    def __init__(self, owner):
        super().__init__(owner.context.window, Qt.Window)
        self.owner = owner
        self.service = owner.service
        self.hub = owner.hub
        self.current = None
        self.pending_review = ''
        self.sources_data = {}
        self.setWindowTitle('DDCut Replay · 录制与切片')
        self.setMinimumSize(920, 600)
        self.resize(1180, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        header = QHBoxLayout()
        title = QLabel('DDCut Replay')
        font = title.font()
        font.setPointSize(font.pointSize() + 7)
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title)
        header.addWidget(QLabel('录制 · 回看 · 切片'))
        header.addStretch()
        button('导入录像', self.import_media, header)
        button('录制设置', self.settings, header)
        root.addLayout(header)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.build_sources()
        self.build_library()
        self.build_review()
        self.build_tasks()
        self.status = QLabel('关闭此窗口后，后台任务仍会继续；退出本体时收尾。')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(200)
        self.preview_timer.timeout.connect(self.tick_preview)
        self.preview_timer.start()
        self.refresh_sources()
        self.refresh_library()

    def page(self, name):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.tabs.addTab(page, name)
        return page, layout

    def build_sources(self):
        _, layout = self.page('录制中心')
        help_text = QLabel('沿用本体录制设置。回看只使用已封口分段；生成副本期间录制继续。')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.sources = table(['房间', '状态', '开始时间'])
        layout.addWidget(self.sources, 1)
        row = QHBoxLayout()
        for text, action in [('开始录制', 'record'), ('停止录制/收尾', 'stop'),
                             ('开启缓存', 'buffer'), ('停止缓存', 'stop_buffer'),
                             ('快速保存', 'save_replay')]:
            button(text, lambda _checked=False, key=action: self.control(key), row)
        layout.addLayout(row)
        row = QHBoxLayout()
        button('生成回看副本', self.prepare_review, row)
        button('刷新状态', self.refresh_sources, row)
        row.addStretch()
        layout.addLayout(row)

    def build_library(self):
        _, layout = self.page('录播档案')
        row = QHBoxLayout()
        row.addWidget(QLabel('本体成品、回看副本和手动导入的本地录像'))
        row.addStretch()
        button('扫描录制目录', self.scan, row)
        button('回看所选', self.open_selected, row)
        layout.addLayout(row)
        self.library = table(['录像', '日期', '时长', '位置'])
        self.library.itemDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.library, 1)
        row = QHBoxLayout()
        button('打开所在目录', self.open_library_folder, row)
        row.addStretch()
        layout.addLayout(row)

    def build_review(self):
        self.review_page, layout = self.page('回看与选区')
        self.review_title = QLabel('从录制中心生成回看副本，或在录播档案选择录像。')
        self.review_title.setWordWrap(True)
        layout.addWidget(self.review_title)
        split = QSplitter()
        layout.addWidget(split, 1)
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.addWidget(QLabel('素材标记'))
        self.markers = QListWidget()
        self.markers.itemDoubleClicked.connect(self.seek_marker)
        left_box.addWidget(self.markers, 1)
        button('添加标记 M', self.add_marker, left_box)
        button('移除所选标记', self.remove_marker, left_box)
        split.addWidget(left)
        middle = QWidget()
        middle_box = QVBoxLayout(middle)
        self.preview = Preview()
        middle_box.addWidget(self.preview, 1)
        self.position_label = QLabel('00:00:00.000 / 00:00:00.000')
        middle_box.addWidget(self.position_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName('回看位置')
        self.slider.setRange(0, 0)
        self.slider.sliderReleased.connect(self.seek_slider)
        middle_box.addWidget(self.slider)
        row = QHBoxLayout()
        button('播放/暂停 Space', self.toggle_play, row)
        self.mute_button = button('开启声音', self.toggle_mute, row)
        middle_box.addLayout(row)
        split.addWidget(middle)
        inspector = QWidget()
        inspector.setMinimumWidth(215)
        inspector_box = QVBoxLayout(inspector)
        inspector_box.addWidget(QLabel('导出选区'))
        form = QFormLayout()
        self.inpoint, self.outpoint = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self.inpoint, self.outpoint):
            spin.setDecimals(3)
            spin.setSuffix(' 秒')
            spin.setRange(0, 0)
            spin.setMinimumHeight(30)
            spin.valueChanged.connect(self.save_selection)
        form.addRow('入点', self.inpoint)
        form.addRow('出点', self.outpoint)
        inspector_box.addLayout(form)
        button('当前播放位置设为入点 I', lambda: self.inpoint.setValue(self.preview.position()), inspector_box)
        button('当前播放位置设为出点 O', lambda: self.outpoint.setValue(self.preview.position()), inspector_box)
        self.selection_label = QLabel('尚未选择素材')
        self.selection_label.setWordWrap(True)
        inspector_box.addWidget(self.selection_label)
        self.mode = QComboBox()
        self.mode.addItem('快速复制 · 保留原编码', 'copy')
        self.mode.addItem('精确剪辑 · H.264 / AAC', 'precise')
        self.mode.currentIndexChanged.connect(self.mode_changed)
        inspector_box.addWidget(self.mode)
        self.mode_help = QLabel()
        self.mode_help.setWordWrap(True)
        inspector_box.addWidget(self.mode_help)
        self.export_button = button('导出选区', self.export, inspector_box)
        self.export_button.setEnabled(False)
        inspector_box.addStretch()
        split.addWidget(inspector)
        split.setSizes([170, 570, 250])
        split.setStretchFactor(1, 1)
        self.mode_changed()
        # Review shortcuts do not consume keys in other pages.
        for key, callback in [('Space', self.toggle_play), ('I', lambda: self.inpoint.setValue(self.preview.position())),
                              ('O', lambda: self.outpoint.setValue(self.preview.position())), ('M', self.add_marker)]:
            shortcut = QShortcut(QKeySequence(key), middle)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        middle.setFocusPolicy(Qt.StrongFocus)

    def build_tasks(self):
        _, layout = self.page('任务中心')
        layout.addWidget(QLabel('关闭工作区后任务继续。取消不删除原录像或已有成品。'))
        self.tasks = table(['任务', '状态', '进度', '结果/说明'])
        layout.addWidget(self.tasks, 1)
        row = QHBoxLayout()
        button('取消所选任务', self.cancel_task, row)
        button('打开成品', self.open_result, row)
        row.addStretch()
        layout.addLayout(row)

    def report(self, message):
        self.status.setText(str(message))

    def settings(self):
        self.guard(self.service.open_settings)

    def guard(self, callback):
        try:
            return callback()
        except Exception as error:
            self.report(error)
            return None

    def refresh_sources(self):
        def refresh():
            selected = self.sources.currentItem()
            selected_id = selected.data(0, Qt.UserRole) if selected else ''
            data = self.service.sources()
            self.sources_data = {item['source_id']: item for item in data}
            self.sources.clear()
            for entry in data:
                room = entry['room']
                started = datetime.fromtimestamp(entry['created_at']).strftime('%H:%M:%S') if entry['created_at'] else '—'
                item = QTreeWidgetItem([str(room.get('uname') or room.get('room_id')),
                                       STATES[entry['state']], started])
                item.setData(0, Qt.UserRole, entry['source_id'])
                self.sources.addTopLevelItem(item)
                if entry['source_id'] == selected_id:
                    self.sources.setCurrentItem(item)
            if not data:
                self.report('画面墙尚无房间，请先加入要录制的直播间。')
        self.guard(refresh)

    def source(self):
        item = self.sources.currentItem()
        if item is None:
            self.report('请先选择一路房间。')
            return None
        return self.sources_data.get(item.data(0, Qt.UserRole))

    def control(self, action):
        entry = self.source()
        if entry:
            result = self.guard(lambda: self.service.control(entry['source_id'], action, session_id=entry['session_id']))
            self.report('操作已提交；录制/快速保存结果请查看本体通知。' if result else '未启动操作，请检查本体通知与录制设置。')
            self.refresh_sources()

    def prepare_review(self):
        entry = self.source()
        if not entry:
            return
        snapshot = self.guard(lambda: self.service.snapshot(entry['source_id'], entry['session_id']))
        if snapshot is None:
            return
        try:
            options = self.service.options()
            destination = Path(options['directory']) / 'ddcut_replay'
            job = self.hub.submit('生成回看副本', 'snapshot', prepare_snapshot,
                options['ffmpeg'], snapshot, destination, self.hub.root / 'work', lease=snapshot['lease'])
            self.pending_review = job.id
            self.tabs.setCurrentIndex(3)
            self.report('正在生成已封口分段的稳定副本；尾部未封口内容不在本次范围内。')
        except Exception as error:
            self.service.release(snapshot['lease'])
            self.report(error)

    def scan(self):
        def submit():
            options = self.service.options()
            if not options['directory']:
                self.report('尚未选择录制目录，请打开录制设置。')
                return
            self.hub.submit('扫描录制目录', 'scan', scan_directory, options['directory'])
        self.guard(submit)

    def refresh_library(self):
        selected = self.library.currentItem()
        selected_path = selected.data(0, Qt.UserRole) if selected else ''
        self.library.clear()
        for entry in self.hub.entries:
            date = datetime.fromtimestamp(entry.get('created_at', 0)).strftime('%m-%d %H:%M')
            item = QTreeWidgetItem([entry['title'], date,
                                   clock(entry['duration']) if entry.get('duration') else '待探测', entry['path']])
            item.setData(0, Qt.UserRole, entry['path'])
            self.library.addTopLevelItem(item)
            if entry['path'] == selected_path:
                self.library.setCurrentItem(item)

    def selected_entry(self):
        item = self.library.currentItem()
        if item is None:
            self.report('请先选择一份录像。')
            return None
        return next((entry for entry in self.hub.entries if entry['path'] == item.data(0, Qt.UserRole)), None)

    def import_media(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入本地录像', '', '视频 (*.mp4 *.mkv *.mov *.ts *.flv *.webm)')
        if path:
            self.load_entry({'path': str(Path(path).resolve()), 'title': Path(path).stem, 'created_at': time.time()})

    def open_selected(self):
        entry = self.selected_entry()
        if entry:
            self.load_entry(entry)

    def load_entry(self, entry):
        def submit():
            if not Path(entry['path']).is_file():
                raise FileNotFoundError('原录像已移动或删除，请重新导入')
            ffmpeg = self.service.options()['ffmpeg']

            def inspect(job):
                return {**entry, **probe(job, ffmpeg, entry['path'], self.hub.root / 'work')}

            job = self.hub.submit('打开：' + entry['title'], 'import', inspect)
            self.pending_review = job.id
            self.report('正在探测本地录像，完成后打开回看。')
        self.guard(submit)

    def review(self, entry):
        self.current = None
        self.preview.stop()
        self.review_title.setText(entry['title'] + ' · ' + entry['path'])
        duration = entry['duration']
        for spin in (self.inpoint, self.outpoint):
            spin.setRange(0, duration)
        saved = entry.get('selection', [0, duration])
        self.inpoint.setValue(min(duration, saved[0]))
        self.outpoint.setValue(min(duration, saved[1]))
        self.slider.setRange(0, int(duration * 1000))
        self.current = entry
        self.refresh_markers()
        self.save_selection()
        self.tabs.setCurrentIndex(2)
        self.preview.show()
        self.guard(lambda: self.preview.load(entry['path']))
        self.mute_button.setText('开启声音')
        self.report('已打开回看；默认静音。时间轴使用副本内媒体时间，断流间隔不计入时长。')

    def save_selection(self, *_):
        if not self.current:
            return
        start, end = self.inpoint.value(), self.outpoint.value()
        self.current['selection'] = [start, end]
        valid = end > start
        self.selection_label.setText(f'{clock(start)} → {clock(end)}\n选区 {max(0, end-start):.3f} 秒' if valid else '出点必须晚于入点')
        self.export_button.setEnabled(valid)
        self.hub.save()

    def mode_changed(self, *_):
        self.mode_help.setText('保留编码，输出 MKV。起止可能按关键帧扩展，成品时长可能超过选区。'
            if self.mode.currentData() == 'copy' else '输出 MP4，按选区重编码为 H.264/AAC；需要更多时间，画质可能变化。')

    def export(self):
        if not self.current:
            return
        mode = self.mode.currentData()
        suffix = '.mkv' if mode == 'copy' else '.mp4'
        name = Path(self.current['path']).stem + '-clip-' + datetime.now().strftime('%H%M%S') + suffix
        folder = Path(self.current['path']).parent
        target, _ = QFileDialog.getSaveFileName(self, '导出选区（使用新文件名）', str(folder / name), f'视频 (*{suffix})')
        if not target:
            return
        if Path(target).suffix.lower() != suffix:
            target += suffix
        def submit():
            job = self.hub.submit('导出：' + self.current['title'], 'export', export_clip,
                self.service.options()['ffmpeg'], dict(self.current), self.inpoint.value(),
                self.outpoint.value(), mode, target, self.hub.root / 'work')
            self.tabs.setCurrentIndex(3)
            self.report('导出已排队：' + job.title)
        self.guard(submit)

    def add_marker(self):
        if not self.current:
            return
        position = min(self.current['duration'], self.preview.position())
        title, accepted = QInputDialog.getText(self, '添加素材标记', '标记说明')
        if accepted:
            self.current.setdefault('markers', []).append({'time': position, 'title': title.strip() or '标记'})
            self.hub.save()
            self.refresh_markers()

    def refresh_markers(self):
        self.markers.clear()
        if self.current:
            for marker in self.current.get('markers', []):
                self.markers.addItem(clock(marker['time']) + '  ' + marker['title'])

    def seek_marker(self, _item):
        if self.current:
            marker = self.current.get('markers', [])[self.markers.currentRow()]
            self.guard(lambda: self.preview.seek(marker['time']))

    def remove_marker(self):
        index = self.markers.currentRow()
        if self.current and index >= 0:
            self.current['markers'].pop(index)
            self.hub.save()
            self.refresh_markers()

    def toggle_play(self):
        if self.preview.core:
            self.guard(lambda: setattr(self.preview.core, 'pause', not self.preview.core.pause))

    def toggle_mute(self):
        if self.preview.core:
            muted = not self.preview.core.mute
            self.preview.core.mute = muted
            self.mute_button.setText('开启声音' if muted else '静音')

    def seek_slider(self):
        self.guard(lambda: self.preview.seek(self.slider.value() / 1000))

    def tick_preview(self):
        if not self.isVisible() or not self.current:
            return
        try:
            position = self.preview.position()
            if not self.slider.isSliderDown():
                self.slider.setValue(int(position * 1000))
            self.position_label.setText(clock(position) + ' / ' + clock(self.current['duration']))
        except Exception as error:
            self.report('播放器：' + str(error))

    def refresh_tasks(self):
        scroll = self.tasks.verticalScrollBar().value()
        selected = self.tasks.currentItem()
        selected_id = selected.data(0, Qt.UserRole) if selected else ''
        self.tasks.clear()
        for job in reversed(self.hub.jobs):
            progress = f'{int(job.progress*100)}%' if job.kind == 'export' or job.state == 'done' else '—'
            item = QTreeWidgetItem([job.title, STATES.get(job.state, job.state), progress, job.output or job.message])
            item.setData(0, Qt.UserRole, job.id)
            item.setToolTip(3, job.message)
            self.tasks.addTopLevelItem(item)
            if job.id == selected_id:
                self.tasks.setCurrentItem(item)
        self.tasks.verticalScrollBar().setValue(scroll)
        active = sum(job.state in ACTIVE for job in self.hub.jobs)
        self.tabs.setTabText(3, f'任务中心 ({active})' if active else '任务中心')

    def selected_job(self):
        item = self.tasks.currentItem()
        return next((job for job in self.hub.jobs if item and job.id == item.data(0, Qt.UserRole)), None)

    def cancel_task(self):
        job = self.selected_job()
        if job:
            self.hub.cancel(job)

    def open_result(self):
        job = self.selected_job()
        if job and job.state == 'done' and job.output and Path(job.output).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(job.output))

    def open_library_folder(self):
        entry = self.selected_entry()
        if entry:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(entry['path']).parent)))

    def closeEvent(self, event):
        self.preview.stop()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.current and self.preview.core is None:
            self.guard(lambda: self.preview.load(self.current['path']))
            self.mute_button.setText('开启声音')
