"""Dedicated Qt recording, archive, review and task workspace."""
from datetime import datetime
from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QLineEdit,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QListWidget, QPushButton, QScrollArea, QScrollBar, QSizePolicy, QSplitter, QStackedWidget, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ddm import theme

from .media import ACTIVE, probe, scan_directory, prepare_snapshot, export_clip
from .hls import preview_options
from .timeline import Timeline, TimeInput, timecode

STATES = {'idle': '未录制', 'recording': '录制中', 'buffering': '缓存中',
          'stopping': '收尾中', 'reconnecting': '等待重连', 'queued': '等待中',
          'running': '处理中', 'done': '已完成', 'failed': '失败',
          'cancelling': '正在取消', 'cancelled': '已取消', 'interrupted': '已中断'}


def clock(seconds):
    return timecode(seconds, milliseconds=True)


def button(text, callback, box):
    widget = QPushButton(text)
    widget.setObjectName('IconButton')
    widget.setMinimumHeight(theme.CONTROL_HEIGHT)
    widget.clicked.connect(callback)
    box.addWidget(widget)
    return widget


def table(headers):
    tree = QTreeWidget()
    tree.setHeaderLabels(headers)
    tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(False)
    tree.setUniformRowHeights(True)
    tree.setMouseTracking(True)
    tree.setTextElideMode(Qt.ElideRight)
    tree.setSelectionBehavior(QAbstractItemView.SelectRows)
    tree.setSelectionMode(QAbstractItemView.SingleSelection)
    tree.header().setSectionResizeMode(QHeaderView.Interactive)
    tree.header().setDefaultSectionSize(160)
    tree.header().setMinimumSectionSize(110)
    tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
    tree.header().setStretchLastSection(False)
    return tree


class Preview(QWidget):
    def __init__(self, source_url):
        super().__init__()
        self.source_url = source_url
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
                            keep_open=True, **EMBEDDED_OPTIONS, **preview_options(path))
        self.core.play(self.source_url(path))

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
    closed = Signal()

    def __init__(self, owner):
        super().__init__(owner.context.window)
        self.owner = owner
        self.service = owner.service
        self.hub = owner.hub
        self.current = None
        self.pending_review = ''
        self.sources_data = {}
        self.live_source = None
        self.pending_live = None
        self.loaded_duration = 0
        self.resume_position = None
        self.setWindowTitle('DDCut Replay · 录制与切片')
        self.selection_playing = False
        self.task_signature = None
        self.setObjectName('replayWorkspace')
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel('录制与切片')
        title.setObjectName('SettingsTitle')
        header.addWidget(title)
        header.addStretch()
        self.back_button = button('返回画面墙', self.close, header)
        button('录制设置', self.settings, header)
        self.task_button = button('后台任务', self.toggle_tasks, header)
        self.task_button.setCheckable(True)
        root.addLayout(header)
        self.content = QSplitter(Qt.Horizontal)
        root.addWidget(self.content, 1)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('PluginTabs')
        self.content.addWidget(self.tabs)
        self.build_library()
        self.build_review()
        self.build_tasks()
        self.content.setStretchFactor(0, 1)
        self.status = QLabel()
        self.status.setObjectName('replayStatus')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)
        self.status.hide()
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(200)
        self.preview_timer.timeout.connect(self.tick_preview)
        self.preview_timer.start()
        self.refresh_sources()
        self.refresh_library()
        self.apply_theme()
        theme.signals().changed.connect(self.apply_theme)

    def apply_theme(self, *_):
        # Buttons, tabs, hints, inputs and marker lists use the host's components.
        # Only the media table and time input need styles absent from host QSS.
        self.setStyleSheet(f'''
            #replayWorkspace QTreeWidget {{
                background: {theme.CONTENT}; color: {theme.TEXT1};
                border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_MD}px;
                font-size: {theme.FONT_BODY}px; outline: none;
            }}
            #replayWorkspace QTreeWidget::item {{
                height: 44px; padding: 0 12px; color: {theme.TEXT2};
                border-bottom: 1px solid {theme.BORDER};
            }}
            #replayWorkspace QTreeWidget::item:hover {{background: {theme.CONTENT_HOVER}; color: {theme.TEXT1};}}
            #replayWorkspace QTreeWidget::item:selected {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}
            #replayWorkspace QHeaderView::section {{
                background: {theme.CONTENT}; color: {theme.TEXT3};
                border: none; border-bottom: 1px solid {theme.BORDER}; padding: 10px 12px;
                font-size: {theme.FONT_CONTROL}px; font-weight: 600;
            }}
            #replayWorkspace QDoubleSpinBox {{
                background: {theme.CONTENT}; color: {theme.TEXT1};
                border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_MD}px; padding: 5px 8px;
            }}
            #replayWorkspace QDoubleSpinBox:focus {{border-color: {theme.ACCENT}; background: {theme.CONTENT_HOVER};}}
            #replayWorkspace QDoubleSpinBox::up-button, #replayWorkspace QDoubleSpinBox::down-button {{
                width: 18px; border: none; background: transparent;
            }}
            #replayWorkspace #replayStatus {{color: {theme.TEXT3}; font-size: {theme.FONT_CAPTION}px;}}
            #replayWorkspace #replayTasks {{background: {theme.CONTENT}; border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_MD}px;}}
        ''')

        # The host's light-mode primary-button rule otherwise makes disabled
        # labels white on the disabled white background.
        for primary in (self.open_button, self.export_button):
            primary.setStyleSheet(f'QPushButton#PrimaryButton[updateAction="true"]:disabled {{color: {theme.TEXT3};}}')

    def page(self, name):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.tabs.addTab(page, name)
        return page, layout

    def build_library(self):
        self.library_page, layout = self.page('录像库')
        filters = QFrame()
        filters.setObjectName('NotificationSettingsGroup')
        row = QHBoxLayout(filters)
        row.setContentsMargins(12, 10, 12, 10)
        self.category = QComboBox()
        for title, kind in [('正在录制', 'live'), ('历史录播', 'archive'), ('已生成切片', 'clip')]:
            self.category.addItem(title, kind)
        self.category.setAccessibleName('录像分类')
        self.category.currentIndexChanged.connect(self.refresh_library)
        row.addWidget(self.category)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索房间、录像或文件名')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_library)
        row.addWidget(self.search, 1)
        button('导入录像', self.import_media, row)
        button('刷新', self.refresh_all, row)
        layout.addWidget(filters)
        self.library_stack = QStackedWidget()
        layout.addWidget(self.library_stack, 1)
        self.sources = table(['房间', '状态', '开始时间'])
        self.sources.setAccessibleName('正在录制的房间')
        self.sources.itemDoubleClicked.connect(lambda *_: self.prepare_review())
        self.library_stack.addWidget(self.sources)
        self.library = table(['录像', '日期', '时长', '位置'])
        self.library.setAccessibleName('本地录像与切片')
        self.library.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.library_stack.addWidget(self.library)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_card = QFrame()
        empty_card.setObjectName('NotificationSettingsGroup')
        empty_card.setFixedHeight(240)
        empty_box = QVBoxLayout(empty_card)
        empty_box.setContentsMargins(24, 24, 24, 24)
        empty_box.setSpacing(12)
        empty_box.addStretch()
        self.library_empty = QLabel()
        self.library_empty.setObjectName('SettingsTitle')
        self.library_empty.setWordWrap(True)
        self.library_empty.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(self.library_empty)
        self.library_empty_hint = QLabel()
        self.library_empty_hint.setObjectName('SettingsHint')
        self.library_empty_hint.setWordWrap(True)
        self.library_empty_hint.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(self.library_empty_hint)
        empty_box.addStretch()
        empty_layout.addWidget(empty_card)
        empty_layout.addStretch()
        self.library_stack.addWidget(empty)
        row = QHBoxLayout()
        self.open_button = button('回看并切片', self.open_library_selection, row)
        self.open_button.setObjectName('PrimaryButton')
        self.open_button.setProperty('updateAction', True)
        self.folder_button = button('打开所在目录', self.open_library_folder, row)
        row.addStretch()
        layout.addLayout(row)
        self.sources.itemSelectionChanged.connect(self.update_library_actions)
        self.library.itemSelectionChanged.connect(self.update_library_actions)

    def build_review(self):
        self.review_page, layout = self.page('回看与切片')
        row = QHBoxLayout()
        self.review_title = QLabel('尚未打开录像')
        self.review_title.setMinimumWidth(0)
        self.review_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.review_title, 1)
        self.review_state = QLabel('请先从录像库选择素材')
        row.addWidget(self.review_state)
        button('切换录像', lambda: self.tabs.setCurrentWidget(self.library_page), row)
        layout.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        layout.addWidget(split, 1)
        middle = QWidget()
        middle_box = QVBoxLayout(middle)
        middle_box.setContentsMargins(0, 0, 8, 0)
        middle_box.setSpacing(5)
        self.preview = Preview(self.owner.preview_server.source_url)
        middle_box.addWidget(self.preview, 1)
        row = QHBoxLayout()
        self.play_button = button('播放 / 暂停', self.toggle_play, row)
        button('−5秒', lambda: self.seek_position(self.preview.position() - 5), row).setFixedWidth(64)
        button('+5秒', lambda: self.seek_position(self.preview.position() + 5), row).setFixedWidth(64)
        self.mute_button = button('开启声音', self.toggle_mute, row)
        row.addStretch()
        self.live_button = button('跳到最新', self.seek_live_edge, row)
        middle_box.addLayout(row)
        self.position_label = QLabel('00:00:00.000 / 00:00:00.000')
        self.timeline = Timeline()
        self.timeline.seekRequested.connect(self.seek_position)
        self.timeline.selectionChanged.connect(self.set_selection)
        middle_box.addWidget(self.timeline)
        row = QHBoxLayout()
        row.addWidget(self.position_label)
        button('−', lambda: self.timeline.set_zoom(self.timeline.zoom / 2), row).setFixedWidth(40)
        self.zoom_label = QLabel('1×')
        row.addWidget(self.zoom_label)
        button('+', lambda: self.timeline.set_zoom(self.timeline.zoom * 2), row).setFixedWidth(40)
        button('完整范围', lambda: self.timeline.set_zoom(1), row)
        row.addStretch()
        button('添加标记 M', self.add_marker, row)
        middle_box.addLayout(row)
        self.timeline_scroll = QScrollBar(Qt.Horizontal)
        self.timeline_scroll.setAccessibleName('时间轴可见范围')
        self.timeline_scroll.valueChanged.connect(self.timeline.set_view_start)
        self.timeline.viewChanged.connect(self.update_timeline_view)
        middle_box.addWidget(self.timeline_scroll)
        split.addWidget(middle)
        self.tools = tools = QTabWidget()
        tools.setObjectName('PluginTabs')
        tools.setMinimumWidth(300)
        inspector = QWidget()
        inspector_box = QVBoxLayout(inspector)
        inspector_box.setSpacing(6)
        form = QFormLayout()
        self.inpoint, self.outpoint = TimeInput(), TimeInput()
        for spin in (self.inpoint, self.outpoint):
            spin.setRange(0, 0)
            spin.valueChanged.connect(self.save_selection)
        form.addRow('入点', self.inpoint)
        form.addRow('出点', self.outpoint)
        inspector_box.addLayout(form)
        row = QHBoxLayout()
        button('设为入点 I', lambda: self.inpoint.setValue(self.preview.position()), row)
        button('设为出点 O', lambda: self.outpoint.setValue(self.preview.position()), row)
        inspector_box.addLayout(row)
        self.selection_label = QLabel('尚未选择素材')
        self.selection_label.setWordWrap(True)
        inspector_box.addWidget(self.selection_label)
        self.selection_button = button('预览选区', self.play_selection, inspector_box)
        self.selection_button.setEnabled(False)
        self.mode = QComboBox()
        self.mode.addItem('快速复制 · 保留原编码', 'copy')
        self.mode.addItem('精确切片 · H.264 / AAC', 'precise')
        self.mode.currentIndexChanged.connect(self.mode_changed)
        inspector_box.addWidget(self.mode)
        self.mode_help = QLabel()
        self.mode_help.setObjectName('SettingsHint')
        self.mode_help.setWordWrap(True)
        inspector_box.addWidget(self.mode_help)
        inspector_box.addWidget(QLabel('成品文件名'))
        self.filename = QLineEdit()
        self.filename.setPlaceholderText('打开录像后自动填写')
        self.filename.setAccessibleName('成品文件名')
        inspector_box.addWidget(self.filename)
        self.export_button = button('生成切片', self.export, inspector_box)
        self.export_button.setObjectName('PrimaryButton')
        self.export_button.setProperty('updateAction', True)
        self.export_button.setEnabled(False)
        inspector_box.addStretch()
        inspector_scroll = QScrollArea()
        inspector_scroll.setObjectName('PluginScroll')
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inspector_scroll.setFrameShape(QFrame.NoFrame)
        inspector_scroll.setWidget(inspector)
        tools.addTab(inspector_scroll, '选区')
        markers_page = QWidget()
        markers_box = QVBoxLayout(markers_page)
        self.markers = QListWidget()
        self.markers.setObjectName('FollowList')
        self.markers.itemDoubleClicked.connect(self.seek_marker)
        markers_box.addWidget(self.markers, 1)
        button('添加标记 M', self.add_marker, markers_box)
        button('移除所选标记', self.remove_marker, markers_box)
        tools.addTab(markers_page, '标记')
        split.addWidget(tools)
        split.setSizes([680, 300])
        split.setStretchFactor(0, 1)
        self.mode_changed()
        for key, callback in [('Space', self.toggle_play), ('I', lambda: self.inpoint.setValue(self.preview.position())),
                              ('O', lambda: self.outpoint.setValue(self.preview.position())), ('M', self.add_marker)]:
            shortcut = QShortcut(QKeySequence(key), middle)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        middle.setFocusPolicy(Qt.StrongFocus)

    def build_tasks(self):
        # A shared side pane resizes the native video surface instead of covering it.
        self.task_panel = QFrame()
        self.task_panel.setObjectName('replayTasks')
        self.task_panel.setMinimumWidth(270)
        self.task_panel.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self.task_panel)
        row = QHBoxLayout()
        row.addWidget(QLabel('后台任务'), 1)
        button('关闭', lambda: self.toggle_tasks(False), row)
        layout.addLayout(row)
        note = QLabel('关闭面板或返回画面墙，任务仍会继续。')
        note.setObjectName('SettingsHint')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tasks = table(['任务', '状态', '进度', '结果/说明'])
        layout.addWidget(self.tasks, 1)
        button('取消所选任务', self.cancel_task, layout)
        button('打开成品', self.open_result, layout)
        self.content.addWidget(self.task_panel)
        self.task_panel.hide()

    def toggle_tasks(self, checked=None):
        if checked is None:
            checked = self.task_panel.isHidden()
        self.task_panel.setVisible(checked)
        self.task_button.setChecked(checked)
        self.tools.setVisible(not checked or self.width() >= 1100)
        if checked:
            self.refresh_tasks()
            self.content.setSizes([max(480, self.width() - 350), 320])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'task_panel'):
            self.tools.setVisible(self.task_panel.isHidden() or self.width() >= 1100)

    def refresh_all(self):
        self.refresh_sources()
        self.refresh_library()
        self.scan()

    def filter_library(self, *_):
        query = self.search.text().strip().casefold()
        tree = self.sources if self.category.currentData() == 'live' else self.library
        visible = 0
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            match = query in ' '.join(item.text(col) for col in range(tree.columnCount())).casefold()
            item.setHidden(not match)
            visible += match
        kind = self.category.currentData()
        title, hint = {
            'live': ('暂无正在录制的房间', '在画面墙点击录制按钮，开始后可在这里回看。'),
            'archive': ('暂无历史录播', '导入本地录像，或刷新录制目录。'),
            'clip': ('暂无已生成切片', '打开录像并选择时间范围，生成的切片会显示在这里。'),
        }[kind]
        self.library_empty.setText('没有找到匹配的录像' if query else title)
        self.library_empty_hint.setText('试试其他关键词，或切换录像分类。' if query else hint)
        self.library_stack.setCurrentIndex((0 if kind == 'live' else 1) if visible else 2)
        self.update_library_actions()

    def update_library_actions(self):
        if not hasattr(self, 'open_button'):
            return
        live = self.category.currentData() == 'live'
        item = (self.sources if live else self.library).currentItem()
        selected = item is not None and not item.isHidden()
        self.open_button.setEnabled(selected)
        self.folder_button.setEnabled(selected and not live)

    def open_library_selection(self):
        if self.category.currentData() == 'live':
            self.prepare_review()
        else:
            self.open_selected()

    def set_selection(self, start, end):
        self.inpoint.blockSignals(True)
        self.outpoint.blockSignals(True)
        self.inpoint.setValue(start)
        self.outpoint.setValue(end)
        self.inpoint.blockSignals(False)
        self.outpoint.blockSignals(False)
        self.save_selection()

    def update_timeline_view(self):
        self.timeline_scroll.blockSignals(True)
        self.timeline_scroll.setRange(0, round(max(0, self.timeline.duration - self.timeline.span) * 1000))
        self.timeline_scroll.setPageStep(round(self.timeline.span * 1000))
        self.timeline_scroll.setValue(round(self.timeline.view_start * 1000))
        self.timeline_scroll.blockSignals(False)
        self.zoom_label.setText(f'{self.timeline.zoom:g}×')

    def play_selection(self):
        if self.current and self.outpoint.value() > self.inpoint.value():
            self.seek_position(self.inpoint.value())
            if self.preview.core:
                self.preview.core.pause = False
                if self.resume_position is not None:
                    self.resume_position = (self.inpoint.value(), False)
                self.selection_playing = True

    def report(self, message):
        self.status.setText(str(message))
        self.status.setVisible(bool(message))

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
            self.active_paths = set()
            self.sources.clear()
            for entry in data:
                if entry['state'] not in ('recording', 'reconnecting', 'stopping'):
                    continue
                if hasattr(self.service, 'live_recording'):
                    try:
                        self.active_paths.add(self.service.live_recording(entry['source_id'], entry['session_id'])['path'])
                    except RuntimeError:
                        pass  # The first segment may still be sealing.
                room = entry['room']
                started = datetime.fromtimestamp(entry['created_at']).strftime('%H:%M:%S') if entry['created_at'] else '—'
                item = QTreeWidgetItem([str(room.get('uname') or room.get('room_id')),
                                       STATES[entry['state']], started])
                item.setData(0, Qt.UserRole, entry['source_id'])
                self.sources.addTopLevelItem(item)
                if entry['source_id'] == selected_id:
                    self.sources.setCurrentItem(item)
            if self.sources.currentItem() is None and self.sources.topLevelItemCount():
                self.sources.setCurrentItem(self.sources.topLevelItem(0))
            self.refresh_library()
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
            try:
                result = self.service.control(entry['source_id'], action, session_id=entry['session_id'])
            except Exception as error:
                self.report(error)
                return
            self.report('操作已提交；录制/快速保存结果请查看本体通知。' if result else '未启动操作，请检查本体通知与录制设置。')
            self.refresh_sources()

    def prepare_review(self):
        entry = self.source()
        if not entry:
            return
        if hasattr(self.service, 'live_recording'):
            self.open_recording(entry)
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
            self.toggle_tasks(True)
            self.report('正在生成已封口分段的稳定副本；尾部未封口内容不在本次范围内。')
        except Exception as error:
            self.service.release(snapshot['lease'])
            self.report(error)

    def open_current_recording(self):
        entry = self.source() if self.sources.topLevelItemCount() else None
        if entry is not None:
            if self.live_source != (entry['source_id'], entry['session_id']):
                self.open_recording(entry)
            else:
                self.tabs.setCurrentWidget(self.review_page)
        elif self.current is None:
            self.tabs.setCurrentWidget(self.library_page)

    def open_recording(self, entry):
        info = self.guard(lambda: self.service.live_recording(entry['source_id'], entry['session_id']))
        if info is None:
            self.pending_live = (entry['source_id'], entry['session_id'])
            return
        self.hub.add_entry(info)
        info = next(item for item in self.hub.entries if item['path'] == info['path'])
        self.review(info)
        self.live_source = (entry['source_id'], entry['session_id'])
        self.review_state.setText('● 录制中 · 时间轴持续增长')
        self.report(f"录制正在继续（{info.get('format', '').upper()}）；时间轴随可读分段增长，未封口尾段暂不显示。")

    def refresh_live(self):
        if self.pending_live is not None:
            source_id, session_id = self.pending_live
            entry = self.sources_data.get(source_id)
            if entry and entry['session_id'] == session_id:
                self.open_recording(entry)
            else:
                self.pending_live = None
            return
        if self.live_source is None or self.current is None:
            return
        try:
            info = self.service.live_recording(*self.live_source)
        except RuntimeError:
            # Stop/rotation closes an archive. Pick up its final short segment
            # without silently switching the user's selection to a new archive.
            metadata = Path(self.current['path']).with_name('recording.json')
            if metadata.is_file():
                import json
                info = json.loads(metadata.read_text(encoding='utf-8'))
            else:
                return
            self.live_source = None
            self.review_state.setText('录制已结束 · 本地录像')
        if info['path'] != self.current['path']:
            self.live_source = None
            self.review_state.setText('本段录制已保存')
            self.report('本段录制已保存。可从录像库打开正在录制的新分段。')
            return
        if info['duration'] > self.current['duration']:
            self.current.update(info)
            for spin in (self.inpoint, self.outpoint):
                spin.setMaximum(info['duration'])
            self.timeline.segments = info.get('segments', [])
            self.timeline.set_duration(info['duration'])
            self.hub.save()
            self.refresh_library()
        core = self.preview.core
        if self.current.get('index_format') == 'hls':
            # EVENT playlists grow inside the demuxer. Playback and seeking use
            # the same instance, rather than restarting it on every new range.
            self.loaded_duration = self.current['duration']
        elif core is not None and core.eof_reached and self.current['duration'] > self.loaded_duration + .05:
            self.reload_preview(self.preview.position(), bool(core.pause))

    def reload_preview(self, position, paused):
        muted = bool(self.preview.core.mute) if self.preview.core else True
        self.preview.load(self.current['path'])
        self.loaded_duration = self.current['duration']
        self.preview.core.mute = muted
        self.preview.core.pause = True
        self.resume_position = (position, paused)

    def seek_live_edge(self):
        if self.current:
            self.refresh_live()
            self.seek_position(max(0, self.current['duration'] - 1))

    def seek_position(self, position):
        if not self.current:
            return
        self.selection_playing = False
        position = max(0, min(self.current['duration'], position))
        if self.current.get('index_format') != 'hls' and self.current['duration'] > self.loaded_duration + .05:
            self.guard(lambda: self.reload_preview(position, bool(self.preview.core.pause) if self.preview.core else False))
        else:
            self.guard(lambda: self.preview.seek(position))

    def scan(self):
        def submit():
            options = self.service.options()
            if not options['directory']:
                self.report('尚未选择录制目录，请打开录制设置。')
                return
            self.hub.submit('扫描录制目录', 'scan', scan_directory, options['directory'])
        self.guard(submit)

    def refresh_library(self, *_):
        if not hasattr(self, 'library'):
            return
        kind = self.category.currentData()
        self.library_stack.setCurrentIndex(0 if kind == 'live' else 1)
        self.folder_button.setEnabled(kind != 'live')
        selected = self.library.currentItem()
        selected_path = selected.data(0, Qt.UserRole) if selected else ''
        self.library.clear()
        for entry in self.hub.entries:
            if (entry.get('kind') == 'clip') != (kind == 'clip') or entry['path'] in getattr(self, 'active_paths', set()):
                continue
            date = datetime.fromtimestamp(entry.get('created_at', 0)).strftime('%m-%d %H:%M')
            item = QTreeWidgetItem([entry['title'], date,
                                   clock(entry['duration']) if entry.get('duration') else '待探测', entry['path']])
            item.setData(0, Qt.UserRole, entry['path'])
            self.library.addTopLevelItem(item)
            if entry['path'] == selected_path:
                self.library.setCurrentItem(item)
        self.filter_library()

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
        if entry.get('segments'):
            self.review(entry)
            return
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
        self.live_source = None
        self.pending_live = None
        self.resume_position = None
        self.current = None
        self.preview.stop()
        self.selection_playing = False
        self.review_title.setText(entry['title'])
        self.review_title.setToolTip(entry['path'])
        self.review_state.setText('本地切片' if entry.get('kind') == 'clip' else '本地录像')
        self.filename.setText(Path(entry['path']).stem + '-clip-' + datetime.now().strftime('%H%M%S'))
        duration = entry['duration']
        for spin in (self.inpoint, self.outpoint):
            spin.setRange(0, duration)
        saved = entry.get('selection', [0, duration])
        self.inpoint.setValue(min(duration, saved[0]))
        self.outpoint.setValue(min(duration, saved[1]))
        self.timeline.segments = entry.get('segments', [])
        self.timeline.set_duration(duration, reset=True)
        self.timeline.set_position(0)
        self.current = entry
        self.loaded_duration = duration
        self.position_label.setText(clock(0) + ' / ' + clock(duration))
        self.refresh_markers()
        self.save_selection()
        self.tabs.setCurrentWidget(self.review_page)
        self.preview.show()
        self.report('已打开回看；默认静音。时间轴使用媒体时间，断流间隔不计入时长。')
        self.guard(lambda: self.preview.load(entry['path']))
        self.mute_button.setText('开启声音')

    def save_selection(self, *_):
        if not self.current:
            return
        start, end = self.inpoint.value(), self.outpoint.value()
        self.current['selection'] = [start, end]
        self.timeline.set_selection(start, end)
        valid = end > start
        self.selection_label.setText(f'{clock(start)} → {clock(end)}\n选区 {max(0, end-start):.3f} 秒' if valid else '出点必须晚于入点')
        self.export_button.setEnabled(valid)
        self.selection_button.setEnabled(valid)
        self.hub.save()

    def mode_changed(self, *_):
        self.mode_help.setText('保留编码，输出 MKV。起止可能按关键帧扩展，成品时长可能超过选区。'
            if self.mode.currentData() == 'copy' else '输出 MP4，按选区重编码为 H.264/AAC；需要更多时间，画质可能变化。')

    def export(self):
        if not self.current:
            return
        mode = self.mode.currentData()
        suffix = '.mkv' if mode == 'copy' else '.mp4'
        name = Path(self.filename.text().strip() or 'clip').stem + suffix
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
            self.toggle_tasks(True)
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
        self.timeline.markers = self.current.get('markers', []) if self.current else []
        self.timeline.update()
        if self.current:
            for marker in self.current.get('markers', []):
                self.markers.addItem(clock(marker['time']) + '  ' + marker['title'])

    def seek_marker(self, _item):
        if self.current:
            marker = self.current.get('markers', [])[self.markers.currentRow()]
            self.seek_position(marker['time'])

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

    def tick_preview(self):
        if not self.isVisible() or not self.current:
            return
        try:
            if self.resume_position is not None and self.preview.core is not None and self.preview.core.time_pos is not None:
                position, paused = self.resume_position
                self.preview.seek(position)
                self.preview.core.pause = paused
                self.resume_position = None
            position = self.preview.position()
            if self.selection_playing and position >= self.outpoint.value() and self.preview.core:
                self.preview.core.pause = True
                self.preview.seek(self.outpoint.value())
                self.selection_playing = False
            self.timeline.set_position(position)
            if self.preview.core:
                self.play_button.setText('播放' if self.preview.core.pause else '暂停')
            self.position_label.setText(clock(position) + ' / ' + clock(self.current['duration']))
        except Exception as error:
            self.report('播放器：' + str(error))

    def refresh_tasks(self):
        active = sum(job.state in ACTIVE for job in self.hub.jobs)
        self.task_button.setText(f'后台任务 ({active})' if active else '后台任务')
        signature = [(job.id, job.state, job.progress, job.output, job.message) for job in self.hub.jobs]
        if signature == self.task_signature:
            return
        self.task_signature = signature
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
        self.closed.emit()

    def showEvent(self, event):
        super().showEvent(event)
        if self.current and self.preview.core is None:
            self.guard(lambda: self.preview.load(self.current['path']))
            self.mute_button.setText('开启声音')
