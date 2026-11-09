"""Dedicated Qt recording, archive, review and task workspace."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer, QUrl, QSize, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QLineEdit,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QListView, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QScrollBar, QSizePolicy, QSlider, QSpinBox, QDoubleSpinBox, QSplitter, QStackedWidget, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QMessageBox)

from ddm import theme

from .media import ACTIVE, probe, scan_directory, prepare_snapshot, export_clip, export_ranges, thumbnail
from .hls import preview_options
from .timeline import Timeline, TimeInput, timecode
from .library_widgets import MediaCard, MediaGrid, media_type
from .library_tools import LibraryTools
from .highlights import HighlightReader
from .selections import restore_ranges, enabled_ranges
from .chat import ChatReader, trim_events, write_json, write_ass
from .chat_render import ReplayChat
from .chat_model import ChatModel
from .encoding_widgets import EncodingOptions

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
    def __init__(self, source_url, interactive=False):
        super().__init__()
        self.source_url = source_url
        self.interactive = interactive
        self.setMinimumSize(280, 100)
        from PySide6.QtGui import QColor, QPalette
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor('#05070a'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WA_NativeWindow)
        self.core = None
        self.chat = ReplayChat(self)
        self.chat_error = ''
        self.chat_timer = QTimer(self)
        self.chat_timer.setInterval(33)
        self.chat_timer.timeout.connect(self.tick_chat)

    def tick_chat(self):
        if self.core is not None and self.isVisible():
            try:
                self.chat.update(self.position())
                self.chat_error = ''
            except Exception as error:
                self.chat_error = str(error)

    def load(self, path):
        self.stop()
        from .preview_runtime import retain_qt_ui_library
        retain_qt_ui_library()
        # Reuse the host's bundled libmpv, loader setup and safe embedded options.
        from ddm.player import EMBEDDED_OPTIONS
        import mpv
        self.core = mpv.MPV(wid=str(int(self.winId())), mute=True, volume=50,
                            keep_open=True, **EMBEDDED_OPTIONS, **preview_options(path),
                            **({'input_default_bindings': False, 'input_vo_keyboard': True,
                                'input_cursor': True, 'input_builtin_dragging': False} if self.interactive else {}))
        self.core.play(self.source_url(path))
        self.chat_timer.start()

    def stop(self):
        self.chat_timer.stop()
        if self.core is not None:
            self.chat.clear()
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
        self.heat_future = None
        self.heat_reader = None
        self.heat_signature = None
        self.heat_next_check = 0
        self.pending_project = None
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
        self.projects_button = button('剪辑工程', self.show_projects, header)
        button('封面制作', self.open_cover, header)
        button('RTMP输入', self.show_inputs, header)
        self.task_button.setCheckable(True)
        self.capture_switch = QCheckBox('仅录制切片素材')
        self.capture_switch.setChecked(getattr(owner, 'capture_mode', 'clip') == 'clip')
        self.capture_switch.setToolTip('开启：只保存可边录边看的切片素材；关闭：使用本体完整录像与即时重放。停止当前录制和缓存后可切换。')
        self.capture_switch.toggled.connect(self.change_capture_mode)
        header.insertWidget(1, self.capture_switch)
        root.addLayout(header)
        self.content = QSplitter(Qt.Horizontal)
        root.addWidget(self.content, 1)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('PluginTabs')
        self.tabs.tabBar().hide()
        self.content.addWidget(self.tabs)
        self.build_library()
        self.build_review()
        self.build_projects()
        from .rtmp_page import RtmpPage
        self.rtmp_page = RtmpPage(self)
        self.library_review.addWidget(self.rtmp_page)
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
        # Media cards, the task table and time input need styles absent from host QSS.
        panel = theme.CONTENT if theme.is_light() else '#171c24'
        stage = theme.BG if theme.is_light() else '#0e1219'
        self.setStyleSheet(f'''
            #replayReview {{background: {stage};}}
            #replayInspector, #replaySelection {{background: {panel};}}
            #replayInspector {{border-left: 1px solid {theme.BORDER};}}
            #replayReview QPushButton#IconButton {{border-radius: 8px;}}
            #replayReview QPushButton#PrimaryButton {{border-radius: 8px; background: #148cff;}}
            #replayReview QPushButton#PrimaryButton:disabled {{background: {theme.CONTENT_HOVER}; color: {theme.TEXT3};}}
            #replayReview QTabWidget::pane {{border: none; background: {panel};}}
            #replayReview QListWidget#FollowList, #replayReview QListView#FollowList {{border-radius: 8px;}}
            #replayWorkspace QTreeWidget {{
                background: {theme.CONTENT}; color: {theme.TEXT1};
                border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_MD}px;
                font-size: {theme.FONT_BODY}px; outline: none;
            }}
            #replayWorkspace QTreeWidget::item {{
                height: 44px; padding: 0 12px; color: {theme.TEXT2};
                border-bottom: 1px solid {theme.BORDER};
            }}
            #replayWorkspace QListWidget#replayMediaGrid {{
                background: transparent; border: none; outline: none;
                font-size: {theme.FONT_BODY}px;
            }}
            #replayWorkspace QListWidget#replayMediaGrid::item {{
                background: {theme.CONTENT}; border: 1px solid {theme.BORDER};
                border-radius: {theme.RADIUS_MD}px; padding: 0;
            }}
            #replayWorkspace QListWidget#replayMediaGrid::item:hover {{background: {theme.CONTENT_HOVER};}}
            #replayWorkspace QListWidget#replayMediaGrid::item:selected {{
                background: {theme.ACCENT_SOFT}; border-color: {theme.ACCENT};
            }}
            #replayWorkspace QTreeWidget::item:hover {{background: {theme.CONTENT_HOVER}; color: {theme.TEXT1};}}
            #replayWorkspace QTreeWidget::item:selected {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}
            #replayWorkspace QHeaderView::section {{
                background: {theme.CONTENT}; color: {theme.TEXT3};
                border: none; border-bottom: 1px solid {theme.BORDER}; padding: 10px 12px;
                font-size: {theme.FONT_CONTROL}px; font-weight: 600;
            }}
            #replayWorkspace QDoubleSpinBox, #replayWorkspace QDateEdit {{
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
        from PySide6.QtGui import QPalette
        palette = self.timeline.palette()
        palette.setColor(QPalette.Text, theme.qcolor(theme.TEXT1))
        palette.setColor(QPalette.AlternateBase, theme.qcolor(theme.CONTENT_HOVER if theme.is_light() else '#0c1016'))
        palette.setColor(QPalette.Highlight, theme.qcolor('#148cff'))
        self.timeline.setPalette(palette)

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
        self.library_page, layout = self.page('录像与回看')
        self.filters = filters = QFrame()
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
        self.library_review = QStackedWidget()
        # Hidden browse/review pages must not impose their size on the active view.
        self.library_review.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        layout.addWidget(self.library_review, 1)
        self.listing = QWidget()
        self.listing.setMinimumWidth(300)
        list_box = QVBoxLayout(self.listing)
        list_box.setContentsMargins(0, 0, 0, 0)
        self.library_review.addWidget(self.listing)
        self.library_stack = QStackedWidget()
        list_box.addWidget(self.library_stack, 1)
        self.sources = MediaGrid()
        self.sources.setAccessibleName('正在录制的房间')
        self.sources.itemClicked.connect(lambda *_: self.prepare_review())
        self.sources.itemActivated.connect(lambda *_: self.prepare_review())
        self.library_stack.addWidget(self.sources)
        self.library = MediaGrid()
        self.library.setAccessibleName('本地录像与切片')
        self.library.itemClicked.connect(lambda *_: None if self.library_tools.batch.isChecked() else self.open_selected())
        self.library.itemActivated.connect(lambda *_: None if self.library_tools.batch.isChecked() else self.open_selected())
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
        list_box.addLayout(row)
        self.sources.itemSelectionChanged.connect(self.update_library_actions)
        self.library.itemSelectionChanged.connect(self.update_library_actions)
        self.library_tools = LibraryTools(self, list_box)

    def build_review(self):
        self.review_page = self.library_page
        self.review_panel = QWidget()
        self.review_panel.setObjectName('replayReview')
        self.review_panel.setAttribute(Qt.WA_StyledBackground)
        layout = QVBoxLayout(self.review_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.library_review.addWidget(self.review_panel)
        self.review_panel.hide()
        row = QHBoxLayout()
        self.review_title = QLabel('尚未打开录像')
        self.review_title.setObjectName('SettingsTitle')
        self.review_title.setMinimumWidth(0)
        self.review_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.review_title, 1)
        self.review_state = QLabel('请先从录像库选择素材')
        self.review_state.setObjectName('SettingsHint')
        row.addWidget(self.review_state)
        button('录像列表', self.show_library, row)
        self.edit_button = button('收起检查器', self.toggle_editor, row)
        self.edit_button.setCheckable(True)
        button('弹幕亮点', self.show_highlights, row)
        layout.addLayout(row)
        self.review_split = split = QSplitter(Qt.Horizontal)
        layout.addWidget(split, 1)
        middle = QWidget()
        middle_box = QVBoxLayout(middle)
        middle_box.setContentsMargins(0, 0, 8, 0)
        middle_box.setSpacing(8)
        self.preview = Preview(self.owner.preview_server.source_url)
        middle_box.addWidget(self.preview, 1)
        row = QHBoxLayout()
        self.play_button = button('播放', self.toggle_play, row)
        self.play_button.setFixedWidth(58)
        button('−5秒', lambda: self.seek_position(self.preview.position() - 5), row).setFixedWidth(58)
        button('+5秒', lambda: self.seek_position(self.preview.position() + 5), row).setFixedWidth(58)
        self.mute_button = button('开启声音', self.toggle_mute, row)
        self.position_label = QLabel('00:00:00.000 / 00:00:00.000')
        self.position_label.setMinimumWidth(0)
        self.position_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.position_label, 1)
        self.live_button = button('跳到最新', self.seek_live_edge, row)
        middle_box.addLayout(row)
        self.heat_status = QLabel('弹幕热度：尚未打开录像')
        self.heat_status.setObjectName('SettingsHint')
        self.heat_status.setWordWrap(True)
        middle_box.addWidget(self.heat_status)
        self.timeline = Timeline()
        self.timeline.seekRequested.connect(self.seek_position)
        self.timeline.selectionChanged.connect(self.set_selection)
        self.timeline.rangeRequested.connect(self.select_range)
        middle_box.addWidget(self.timeline)
        row = QHBoxLayout()
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
        self.inspector = QFrame()
        self.inspector.setObjectName('replayInspector')
        self.inspector.setMinimumWidth(300)
        inspector_layout = QVBoxLayout(self.inspector)
        inspector_layout.setContentsMargins(12, 12, 12, 12)
        inspector_layout.setSpacing(10)
        heading = QLabel('片段检查器')
        heading.setObjectName('SettingsTitle')
        inspector_layout.addWidget(heading)
        self.tools = tools = QTabWidget()
        tools.setObjectName('PluginTabs')
        inspector_layout.addWidget(tools, 1)
        inspector = QWidget()
        inspector.setObjectName('replaySelection')
        inspector_box = QVBoxLayout(inspector)
        inspector_box.setContentsMargins(4, 10, 4, 10)
        inspector_box.setSpacing(8)
        form = QFormLayout()
        self.range_list = QListWidget()
        self.range_list.setObjectName('FollowList')
        self.range_list.setAccessibleName('切片选区列表')
        self.range_list.setMinimumHeight(68)
        self.range_list.setMaximumHeight(150)
        self.range_list.currentRowChanged.connect(self.select_range)
        self.range_list.itemChanged.connect(self.range_toggled)
        inspector_box.addWidget(self.range_list)
        row = QHBoxLayout()
        button('新建选区', self.add_range, row)
        button('删除', self.remove_range, row)
        button('↑', lambda: self.move_range(-1), row).setFixedWidth(36)
        button('↓', lambda: self.move_range(1), row).setFixedWidth(36)
        inspector_box.addLayout(row)
        self.ranges_all = QCheckBox('全选 / 启用全部选区')
        self.ranges_all.clicked.connect(self.toggle_all_ranges)
        inspector_box.addWidget(self.ranges_all)
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
        self.selection_button = button('预览选区', self.play_selection, inspector_box)
        self.selection_button.setEnabled(False)
        self.heat_heading = QLabel('亮点推荐')
        self.heat_heading.setObjectName('SettingsTitle')
        inspector_box.addWidget(self.heat_heading)
        button('全部添加亮点', self.add_all_highlights, inspector_box)
        hint = QLabel('基于弹幕热度发现候选片段。每5秒统计30秒窗口，选择后请回看确认。')
        hint.setObjectName('SettingsHint')
        hint.setWordWrap(True)
        inspector_box.addWidget(hint)
        self.heat_keywords = QLineEdit()
        self.heat_keywords.setPlaceholderText('关键词，逗号分隔；留空统计全部弹幕')
        self.heat_keywords.setAccessibleName('亮点关键词过滤')
        self.heat_keywords.editingFinished.connect(self.save_heat_options)
        inspector_box.addWidget(self.heat_keywords)
        self.heat_threshold_label = QLabel('峰值阈值 80%')
        inspector_box.addWidget(self.heat_threshold_label)
        self.heat_threshold = QSlider(Qt.Horizontal)
        self.heat_threshold.setRange(50, 100)
        self.heat_threshold.setValue(80)
        self.heat_threshold.setAccessibleName('弹幕峰值阈值百分比')
        self.heat_threshold.valueChanged.connect(self.save_heat_options)
        inspector_box.addWidget(self.heat_threshold)
        self.heat_results = QListWidget()
        self.heat_results.setObjectName('FollowList')
        self.heat_results.setAccessibleName('弹幕亮点推荐区间')
        self.heat_results.setWordWrap(True)
        self.heat_results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.heat_results.setMinimumHeight(68)
        self.heat_results.setMaximumHeight(220)
        self.heat_results.itemClicked.connect(lambda *_: self.apply_highlight())
        self.heat_results.itemActivated.connect(lambda *_: self.apply_highlight())
        inspector_box.addWidget(self.heat_results)
        button('应用所选亮点为选区', self.apply_highlight, inspector_box)
        inspector_box.addWidget(QLabel('导出设置'))
        self.export_scope = QComboBox()
        self.export_scope.addItem('当前选区', 'current')
        self.export_scope.addItem('启用选区：每段单独导出', 'separate')
        self.export_scope.addItem('启用选区：按列表顺序拼接', 'merge')
        self.export_scope.addItem('完整录像：冻结当前可读范围', 'full')
        self.export_scope.currentIndexChanged.connect(self.export_scope_changed)
        inspector_box.addWidget(self.export_scope)
        self.mode = QComboBox()
        self.mode.addItem('快速复制 · 保留原编码', 'copy')
        self.mode.addItem('精确切片 · 重编码 MP4', 'precise')
        self.mode.currentIndexChanged.connect(self.mode_changed)
        inspector_box.addWidget(self.mode)
        self.mode_help = QLabel()
        self.mode_help.setObjectName('SettingsHint')
        self.mode_help.setWordWrap(True)
        inspector_box.addWidget(self.mode_help)
        self.encoding = EncodingOptions()
        self.encoding.changed.connect(self.save_encoding_options)
        inspector_box.addWidget(self.encoding)
        self.burn_chat = QCheckBox('将弹幕压入视频')
        self.burn_chat.setToolTip('将文字、表情和SC合成进MP4，沿用弹幕页字号、透明度及时间偏移；需要重编码。画面开关仅影响预览。')
        self.burn_chat.toggled.connect(self.save_burn_options)
        inspector_box.addWidget(self.burn_chat)
        button('用启用选区新建工程', self.project_from_ranges, inspector_box)
        self.filename = QLineEdit()
        self.filename.setPlaceholderText('成品文件名')
        self.filename.setAccessibleName('成品文件名')
        inspector_box.addWidget(self.filename)
        inspector_box.addStretch()
        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setObjectName('PluginScroll')
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inspector_scroll.setFrameShape(QFrame.NoFrame)
        self.inspector_scroll.setWidget(inspector)
        tools.addTab(self.inspector_scroll, '选区')
        markers_page = QWidget()
        markers_box = QVBoxLayout(markers_page)
        self.markers = QListWidget()
        self.markers.setObjectName('FollowList')
        self.markers.itemDoubleClicked.connect(self.seek_marker)
        markers_box.addWidget(self.markers, 1)
        button('添加标记 M', self.add_marker, markers_box)
        button('移除所选标记', self.remove_marker, markers_box)
        tools.addTab(markers_page, '标记')
        self.selection_label = QLabel('尚未选择素材')
        self.selection_label.setWordWrap(True)
        inspector_layout.addWidget(self.selection_label)
        self.export_button = button('生成切片', self.export, inspector_layout)
        self.export_button.setObjectName('PrimaryButton')
        self.export_button.setProperty('updateAction', True)
        self.export_button.setEnabled(False)
        chat_page = QWidget()
        chat_box = QVBoxLayout(chat_page)
        self.chat_enabled = QCheckBox('画面弹幕')
        self.chat_enabled.setChecked(True)
        self.chat_enabled.toggled.connect(self.save_chat_options)
        chat_box.addWidget(self.chat_enabled)
        form = QFormLayout()
        self.chat_offset = QDoubleSpinBox()
        self.chat_offset.setRange(-120, 120)
        self.chat_offset.setDecimals(1)
        self.chat_offset.setSuffix(' 秒')
        self.chat_offset.setKeyboardTracking(False)
        self.chat_offset.setToolTip('正值延后显示弹幕，负值提前；同时作用于热度、列表、回放和导出。')
        self.chat_size = QSpinBox()
        self.chat_size.setRange(18, 72)
        self.chat_size.setValue(36)
        self.chat_opacity = QSpinBox()
        self.chat_opacity.setRange(10, 100)
        self.chat_opacity.setValue(80)
        self.chat_opacity.setSuffix('%')
        for label, widget in [('时间偏移', self.chat_offset), ('字号（1080p）', self.chat_size), ('不透明度', self.chat_opacity)]:
            widget.setMinimumHeight(theme.CONTROL_HEIGHT)
            widget.valueChanged.connect(self.save_chat_options)
            form.addRow(label, widget)
        chat_box.addLayout(form)
        self.chat_search = QLineEdit()
        self.chat_search.setPlaceholderText('搜索弹幕 / SC / 发送者')
        self.chat_search.textChanged.connect(self.filter_chat)
        chat_box.addWidget(self.chat_search)
        self.chat_kind = QComboBox()
        for label, kind in [('全部弹幕', 'all'), ('普通弹幕', 'danmaku'), ('SC 醒目留言', 'super_chat')]:
            self.chat_kind.addItem(label, kind)
        self.chat_kind.currentIndexChanged.connect(self.filter_chat)
        chat_box.addWidget(self.chat_kind)
        self.chat_count = QLabel('暂无弹幕记录')
        self.chat_count.setObjectName('SettingsHint')
        self.chat_count.setWordWrap(True)
        chat_box.addWidget(self.chat_count)
        self.chat_model = ChatModel(self.preview.chat)
        self.chat_list = QListView()
        self.chat_list.setObjectName('FollowList')
        self.chat_list.setAccessibleName('录像弹幕与醒目留言')
        self.chat_list.setUniformItemSizes(True)
        self.chat_list.setModel(self.chat_model)
        self.chat_list.clicked.connect(self.seek_chat)
        chat_box.addWidget(self.chat_list, 1)
        self.chat_follow = QCheckBox('列表跟随播放')
        self.chat_follow.setChecked(True)
        chat_box.addWidget(self.chat_follow)
        self.chat_scope = QComboBox()
        self.chat_scope.addItem('导出整份录像弹幕', 'all')
        self.chat_scope.addItem('导出当前选区弹幕', 'selection')
        chat_box.addWidget(self.chat_scope)
        row = QHBoxLayout()
        button('导出 JSON', lambda: self.export_chat('json'), row)
        button('导出 ASS', lambda: self.export_chat('ass'), row)
        chat_box.addLayout(row)
        tools.insertTab(1, chat_page, '弹幕')
        self.playback_rate = QComboBox()
        for rate in (.5, 1, 1.5, 2, 5):
            self.playback_rate.addItem(f'{rate:g}倍', rate)
        self.playback_rate.setCurrentIndex(1)
        self.playback_rate.setAccessibleName('回看播放速度')
        self.playback_rate.currentIndexChanged.connect(self.change_playback_rate)
        row = middle_box.itemAt(1).layout()
        row.insertWidget(row.count() - 1, self.playback_rate)
        split.addWidget(self.inspector)
        split.setSizes([800, 340])
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        self.mode_changed()
        for key, callback in [('Space', self.toggle_play), ('I', lambda: self.inpoint.setValue(self.preview.position())),
                              ('O', lambda: self.outpoint.setValue(self.preview.position())), ('M', self.add_marker)]:
            shortcut = QShortcut(QKeySequence(key), middle)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        middle.setFocusPolicy(Qt.StrongFocus)

    def build_projects(self):
        from .project_editor import ProjectEditor
        self.project_page = QWidget()
        box = QVBoxLayout(self.project_page)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        title = QLabel('剪辑工程')
        title.setObjectName('SettingsTitle')
        row.addWidget(title)
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText('搜索工程标题或备注')
        self.project_search.textChanged.connect(self.filter_projects)
        row.addWidget(self.project_search, 1)
        button('新建工程', self.new_project, row)
        button('打开工程文件', self.import_project, row)
        button('返回录像', self.show_library, row)
        box.addLayout(row)
        hint = QLabel('工程引用外部文件；在编辑器中编排片段、图文、画中画与音频。素材由外部软件管理。')
        hint.setObjectName('SettingsHint')
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.project_list = table(['工程', '最近编辑', '时长', '片段', '备注'])
        self.project_list.setAccessibleName('剪辑工程列表')
        self.project_list.setIconSize(QSize(72, 40))
        self.project_list.itemDoubleClicked.connect(lambda *_: self.open_project_selection())
        box.addWidget(self.project_list, 1)
        row = QHBoxLayout()
        button('打开工程', self.open_project_selection, row)
        button('删除工程文件', self.delete_project, row)
        row.addStretch()
        box.addLayout(row)
        self.library_review.addWidget(self.project_page)
        self.project_editor = ProjectEditor(self)
        self.project_editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.project_editor.backRequested.connect(self.show_projects)
        self.library_review.addWidget(self.project_editor)
        from .cover_editor import CoverEditor
        self.cover_editor = CoverEditor(self)
        self.cover_editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.cover_editor.backRequested.connect(self.return_from_cover)
        self.library_review.addWidget(self.cover_editor)

    def open_cover(self):
        if self.library_review.currentWidget() is self.cover_editor:
            return
        self.cover_origin = self.library_review.currentWidget()
        project_document = None
        source = None
        if self.cover_origin is self.project_editor and self.project_editor.document:
            project_document = self.project_editor.document
            source = {'project': deepcopy(project_document.data)}
        elif self.cover_origin is self.review_panel and self.current:
            source = deepcopy(self.current)
        def open_page():
            self.cover_editor.open_source(source, project_document)
            if source:
                self.cover_editor.time.setValue(self.project_editor.position if project_document else self.preview.position())
            self.project_editor.shutdown_preview()
            self.cover_review_pause = self.preview.core.pause if self.preview.core else True
            if self.preview.core:
                self.preview.core.pause = True
            self.filters.hide()
            self.library_review.setCurrentWidget(self.cover_editor)
            if source and not self.cover_editor.document.data['background']:
                self.cover_editor.capture()
        self.guard(open_page)

    def return_from_cover(self):
        origin = getattr(self, 'cover_origin', self.listing)
        self.library_review.setCurrentWidget(origin)
        if origin is self.project_editor:
            self.project_editor.refresh()
            self.project_editor.request_preview()
        elif origin is self.review_panel and self.current:
            if self.preview.core:
                self.preview.core.pause = self.cover_review_pause
            else:
                self.review(self.current)
        elif origin is self.listing:
            self.show_library()

    def show_inputs(self):
        self.cover_editor.shutdown()
        self.preview.stop()
        self.current, self.live_source = None, None
        self.project_editor.shutdown_preview()
        self.filters.hide()
        self.library_review.setCurrentWidget(self.rtmp_page)
        self.rtmp_page.refresh()

    def show_projects(self):
        self.cover_editor.shutdown()
        self.preview.stop()
        self.current, self.live_source = None, None
        self.project_editor.shutdown_preview()
        self.filters.hide()
        self.library_review.setCurrentWidget(self.project_page)
        self.refresh_projects()

    def refresh_projects(self):
        from .project import read_project, duration
        folder = self.hub.root / 'projects'
        paths = list(folder.glob('*.ddcut.json')) if folder.is_dir() else []
        paths += [Path(path) for path in self.hub.preferences.get('project_paths', [])]
        rows = []
        for path in dict.fromkeys(paths):
            try:
                data = read_project(path)
                rows.append((path, data))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        rows.sort(key=lambda row: row[1].get('updated_at', 0), reverse=True)
        self.project_list.clear()
        for path, data in rows:
            item = QTreeWidgetItem([data['title'], datetime.fromtimestamp(data.get('updated_at', 0)).strftime('%Y-%m-%d %H:%M'),
                                   timecode(duration(data)), str(len(data['clips'])), data.get('note', '')])
            item.setData(0, Qt.UserRole, str(path))
            item.setToolTip(0, str(path))
            if data['clips']:
                source = data['media'][data['clips'][0]['source']]
                cover = source.get('cover_path')
                if cover and Path(cover).is_file():
                    item.setIcon(0, QIcon(cover))
            self.project_list.addTopLevelItem(item)
        self.filter_projects()

    def filter_projects(self, *_):
        query = self.project_search.text().casefold()
        for index in range(self.project_list.topLevelItemCount()):
            item = self.project_list.topLevelItem(index)
            item.setHidden(query not in (item.text(0)+' '+item.text(4)).casefold())

    def new_project(self, *_):
        from .project import new_project, ProjectDocument
        data = new_project()
        path = self.hub.root / 'projects' / (data['id'] + '.ddcut.json')
        document = ProjectDocument(path, data)
        self.guard(document.save)
        self.open_project_document(document)

    def open_project_document(self, document):
        self.preview.stop()
        self.current, self.live_source = None, None
        self.filters.hide()
        self.library_review.setCurrentWidget(self.project_editor)
        self.project_editor.open_document(document)

    def open_project_selection(self):
        from .project import ProjectDocument
        item = self.project_list.currentItem()
        if item:
            self.guard(lambda: self.open_project_document(ProjectDocument(item.data(0, Qt.UserRole))))

    def import_project(self):
        from .project import ProjectDocument
        path, _ = QFileDialog.getOpenFileName(self, '打开剪辑工程', '', '剪辑工程 (*.ddcut.json *.json)')
        if path:
            def load():
                document = ProjectDocument(path)
                paths = self.hub.preferences.setdefault('project_paths', [])
                if str(Path(path).resolve()) not in paths:
                    paths.append(str(Path(path).resolve()))
                    self.hub.save()
                self.open_project_document(document)
            self.guard(load)

    def delete_project(self):
        item = self.project_list.currentItem()
        if not item:
            return
        path = Path(item.data(0, Qt.UserRole))
        if QMessageBox.question(self, '删除工程', '删除此工程JSON文件？引用的视频、图片与音频会保留。') == QMessageBox.Yes:
            self.project_editor.discard_document(path)
            self.guard(lambda: path.unlink())
            paths = self.hub.preferences.get('project_paths', [])
            self.hub.preferences['project_paths'] = [entry for entry in paths if Path(entry) != path]
            self.hub.save()
            self.refresh_projects()

    def project_from_ranges(self):
        from .project_media import inspect_file
        if not self.current:
            return
        ranges = enabled_ranges(self.current)
        if not ranges or any(item['end'] <= item['start'] for item in ranges):
            self.report('请先启用至少一个有效选区。')
            return
        source = deepcopy(self.current)
        ffmpeg = self.service.options()['ffmpeg']
        def inspect(job):
            info = inspect_file(job, ffmpeg, source['path'], self.hub.root / 'work')
            return {**info, **source, 'type': 'video', 'has_audio': info['has_audio']}
        job = self.hub.submit('从选区创建工程', 'project_source', inspect)
        self.pending_project = job, ranges
        self.report('正在读取来源文件信息…')

    def finish_project_source(self):
        if not self.pending_project or not self.pending_project[0].future.done():
            return
        from .project import new_project, append_clip, ProjectDocument
        job, ranges = self.pending_project
        self.pending_project = None
        if job.state != 'done':
            self.report(job.message)
            return
        info = job.result
        data = new_project(info.get('title', '录像')+' · 剪辑工程')
        data['media'][info['path']] = info
        for item in ranges:
            append_clip(data, info['path'], item['start'], item['end'])
        document = ProjectDocument(self.hub.root / 'projects' / (data['id']+'.ddcut.json'), data)
        document.save()
        self.open_project_document(document)

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
        visible = self.edit_button.isChecked() and not checked
        self.inspector.setVisible(visible)
        self.tools.setVisible(visible)
        if checked:
            self.refresh_tasks()
            self.content.setSizes([max(480, self.width() - 350), 320])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'task_panel'):
            self.update_media_layout()

    def change_capture_mode(self, checked):
        try:
            self.owner.set_capture_mode('clip' if checked else 'original')
        except Exception as error:
            self.capture_switch.blockSignals(True)
            self.capture_switch.setChecked(getattr(self.owner, 'capture_mode', 'clip') == 'clip')
            self.capture_switch.blockSignals(False)
            self.report(error)
        else:
            self.report('录制方式：仅切片素材' if checked else '录制方式：原版完整录像与即时重放')

    def show_library(self):
        self.cover_editor.shutdown()
        self.project_editor.shutdown_preview()
        self.library_review.setCurrentWidget(self.listing)
        self.filters.show()
        self.inspector.hide()
        self.edit_button.setChecked(False)
        self.tools.hide()
        self.update_media_layout()
        if self.preview.core:
            self.preview.core.pause = True

    def toggle_editor(self, checked):
        visible = checked and self.task_panel.isHidden()
        self.inspector.setVisible(visible)
        self.tools.setVisible(visible)
        self.edit_button.setText('收起检查器' if checked else '展开检查器')

    def show_highlights(self):
        self.edit_button.setChecked(True)
        self.toggle_tasks(False)
        self.toggle_editor(True)
        self.tools.setCurrentIndex(0)
        self.inspector_scroll.ensureWidgetVisible(self.heat_heading)
        self.heat_next_check = 0
        self.refresh_highlights()

    def save_heat_options(self, *_):
        self.heat_threshold_label.setText(f'峰值阈值 {self.heat_threshold.value()}%')
        if self.current:
            self.current['heat_threshold'] = self.heat_threshold.value()
            self.current['heat_keywords'] = self.heat_keywords.text()
            self.hub.save()
            self.heat_next_check = 0

    def refresh_highlights(self):
        if not self.current:
            return
        if self.current.get('danmaku_error'):
            self.heat_status.setText('弹幕记录未完整保存：' + self.current['danmaku_error'])
            return
        if self.heat_future is not None:
            if not self.heat_future.done():
                return
            future, reader = self.heat_future, self.heat_requested_reader
            self.heat_future = None
            try:
                result = future.result()
            except Exception as error:
                self.heat_status.setText('弹幕分析失败：' + str(error))
                return
            options = (self.heat_threshold.value(), self.heat_keywords.text(), self.chat_offset.value())
            if reader is self.heat_reader and options == self.heat_requested_options:
                if result.get('video_burned') and not self.current.get('chat_burned'):
                    self.current['chat_burned'] = True
                    self.chat_enabled.setChecked(False)
                    self.burn_chat.setChecked(False)
                    self.burn_chat.setEnabled(False)
                    self.review_state.setText(media_type(self.current) + ' · 画面已含弹幕')
                    self.hub.save()
                self.chat_model.events = result.get('events', [])
                self.preview.chat.set_events(self.chat_model.events)
                self.filter_chat()
                self.timeline.heat_points = result['points']
                self.timeline.heat_threshold = result['threshold']
                self.timeline.update()
                self.heat_results.clear()
                for peak in result['peaks']:
                    item = QListWidgetItem(f"{timecode(peak['start'])} → {timecode(peak['end'])}\n峰值 {peak['count']} 条 / 30秒 · 点击设为选区")
                    item.setData(Qt.UserRole, peak)
                    self.heat_results.addItem(item)
                row_height = max(56, self.heat_results.fontMetrics().lineSpacing() * 2 + 20)
                self.heat_results.setFixedHeight(max(68, min(220, self.heat_results.count() * row_height + 4)))
                status = (f"弹幕热度：{result['total']}条，匹配{result['matched']}条 · {len(result['peaks'])}个候选亮点"
                          if result['total'] else '暂无弹幕数据；请确认本体已连接该直播间弹幕。')
                if self.current['duration'] < 30:
                    status += ' · 可读录像满30秒后生成推荐。'
                self.heat_status.setText(status)
        if time.monotonic() < self.heat_next_check:
            return
        self.heat_next_check = time.monotonic() + 2
        path = self.current.get('danmaku_path')
        if not path:
            self.heat_status.setText('此录像没有弹幕记录。仅新录制的切片素材支持热度分析；旧录像和原版录制无法补回。')
            return
        try:
            info = Path(path).stat()
        except OSError as error:
            self.heat_status.setText('无法读取弹幕记录：' + str(error))
            return
        options = (self.heat_threshold.value(), self.heat_keywords.text(), self.chat_offset.value())
        signature = (path, info.st_size, info.st_mtime_ns, self.current['duration'], options)
        if signature == self.heat_signature:
            return
        self.heat_signature = signature
        self.heat_requested_reader = self.heat_reader
        self.heat_requested_options = options
        keywords = options[1].replace('，', ',').split(',')
        self.heat_future = self.hub.pool.submit(self.heat_reader.analyze, path,
            list(self.current.get('segments', [])), self.current['duration'], options[0],
            [word.strip() for word in keywords if word.strip()], options[2])

    def apply_highlight(self):
        item = self.heat_results.currentItem()
        if self.current and item is not None:
            peak = item.data(Qt.UserRole)
            self.add_range(peak['start'], min(self.current['duration'], peak['end']))
            self.seek_position(peak['start'])
            self.report('已将推荐亮点设为选区；可调整入出点，再生成切片。')

    def fill_identity(self, grid, item, entry):
        room = entry.get('room') or {}
        search = ' '.join(str(value) for value in (entry.get('title', ''), entry.get('path', ''),
                          media_type(entry), *[room.get(key, '') for key in ('platform', 'uname', 'title', 'room_id')]))
        item.setData(Qt.UserRole + 1, search)
        card = MediaCard(entry)
        tools = getattr(self, 'library_tools', None)
        if tools and tools.stats:
            from .library_data import identity
            info = tools.stats['entries'].get(identity(entry.get('path', '')))
            if info:
                card.set_file_info(info)
        item.setToolTip(card.toolTip())
        grid.setItemWidget(item, card)

    def update_media_layout(self):
        for grid in (self.sources, self.library):
            grid.reflow()

    def refresh_all(self):
        self.library_tools.next_check = 0
        self.refresh_sources()
        self.refresh_library()
        self.scan()

    def filter_library(self, *_):
        query = self.search.text().strip().casefold()
        tree = self.sources if self.category.currentData() == 'live' else self.library
        visible = 0
        entries = {entry['path']: entry for entry in self.hub.entries}
        for index in range(tree.count()):
            item = tree.item(index)
            content = str(item.data(Qt.UserRole + 1) or '')
            match = query in content.casefold()
            if self.category.currentData() != 'live':
                match = match and self.library_tools.matches(entries.get(item.data(Qt.UserRole), {}))
            item.setHidden(not match)
            if not match:
                item.setSelected(False)
            visible += match
        kind = self.category.currentData()
        title, hint = {
            'live': ('暂无正在录制的房间', '在画面墙点击录制按钮，开始后可在这里回看。'),
            'archive': ('暂无历史录播', '导入本地录像，或刷新录制目录。'),
            'clip': ('暂无已生成切片', '打开录像并选择时间范围，生成的切片会显示在这里。'),
        }[kind]
        self.library_empty.setText('没有找到匹配的录像' if query else title)
        self.library_empty_hint.setText('试试其他关键词，或切换录像分类。' if query else hint)
        self.library_stack.setCurrentIndex(3 if kind != 'live' and self.library_tools.calendar_toggle.isChecked()
            else (0 if kind == 'live' else 1) if visible else 2)
        self.update_library_actions()
        self.library_tools.refresh()

    def update_library_actions(self):
        if not hasattr(self, 'open_button'):
            return
        live = self.category.currentData() == 'live'
        item = (self.sources if live else self.library).currentItem()
        selected = item is not None and not item.isHidden()
        if not live and hasattr(self, 'library_tools') and self.library_tools.batch.isChecked():
            selected = len(self.library_tools.selected()) == 1
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
            scroll = self.sources.verticalScrollBar().value()
            selected = self.sources.currentItem()
            selected_id = selected.data(Qt.UserRole) if selected else ''
            data = self.service.sources()
            self.sources_data = {item['source_id']: item for item in data}
            self.capture_switch.setEnabled(not any(item['state'] != 'idle' for item in data))
            if hasattr(self.service, 'options'):
                pending = set(self.service.options().get('pending_outputs', []))
                if getattr(self, '_pending_outputs', set()) - pending:
                    self.scan()
                self._pending_outputs = pending
            self.active_paths = set()
            self.sources.clear()
            for entry in data:
                if entry['state'] not in ('recording', 'reconnecting', 'stopping'):
                    continue
                info = {}
                if hasattr(self.service, 'live_recording'):
                    try:
                        info = self.service.live_recording(entry['source_id'], entry['session_id'])
                        self.active_paths.add(info['path'])
                        self.hub.add_entry(info)
                    except RuntimeError:
                        pass  # The first segment may still be sealing.
                room = entry['room']
                identity = {**info, 'room': room, 'title': room.get('title') or room.get('uname') or '直播', 'created_at': entry['created_at']}
                identity['state_label'] = STATES[entry['state']]
                item = QListWidgetItem()
                item.setData(Qt.UserRole, entry['source_id'])
                self.sources.addItem(item)
                self.fill_identity(self.sources, item, identity)
                if entry['source_id'] == selected_id:
                    self.sources.setCurrentItem(item)
            if self.sources.currentItem() is None and self.sources.count():
                self.sources.setCurrentItem(self.sources.item(0))
            self.sources.reflow()
            self.sources.doItemsLayout()
            self.sources.verticalScrollBar().setValue(scroll)
            self.refresh_library()
        self.guard(refresh)

    def source(self):
        item = self.sources.currentItem()
        if item is None:
            self.report('请先选择一路房间。')
            return None
        return self.sources_data.get(item.data(Qt.UserRole))

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
        if getattr(self.owner, 'capture_mode', 'clip') == 'original' and not entry['source_id'].startswith('rtmp:'):
            self.report('原版录制中；停止后可回看完整录像。')
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
        if self.library_review.currentWidget() in (self.project_page, self.project_editor, self.cover_editor,self.rtmp_page):
            return
        entry = self.source() if self.sources.count() else None
        if entry is not None and getattr(self.owner, 'capture_mode', 'clip') == 'original' and not entry['source_id'].startswith('rtmp:'):
            self.report('原版录制中；停止后可在本页回看完整录像。')
            return
        if entry is not None:
            if self.live_source != (entry['source_id'], entry['session_id']):
                self.open_recording(entry)
            else:
                self.tabs.setCurrentWidget(self.library_page)
                self.library_review.setCurrentWidget(self.review_panel)
                self.filters.hide()
                self.edit_button.setChecked(True)
                self.toggle_editor(True)
                self.update_media_layout()
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
        if info['duration'] > self.timeline.duration or info.get('ended_at'):
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
        self.change_playback_rate()
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
        scroll = self.library.verticalScrollBar().value()
        selected = self.library.currentItem()
        selected_path = selected.data(Qt.UserRole) if selected else ''
        selected_paths = {item.data(Qt.UserRole) for item in self.library.selectedItems()}
        self.library.blockSignals(True)
        self.library.clear()
        for entry in self.library_tools.sorted_entries():
            if (entry.get('kind') == 'clip') != (kind == 'clip') or entry['path'] in getattr(self, 'active_paths', set()):
                continue
            item = QListWidgetItem()
            item.setData(Qt.UserRole, entry['path'])
            self.library.addItem(item)
            self.fill_identity(self.library, item, entry)
            if entry['path'] == selected_path:
                from PySide6.QtCore import QItemSelectionModel
                self.library.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            item.setSelected(entry['path'] in selected_paths)
        self.library.blockSignals(False)
        self.library.reflow()
        self.filter_library()
        self.library.doItemsLayout()
        self.library.verticalScrollBar().setValue(scroll)

    def selected_entry(self):
        if self.library_tools.batch.isChecked():
            entries = self.library_tools.selected()
            return entries[0] if len(entries) == 1 else None
        item = self.library.currentItem()
        if item is None:
            self.report('请先选择一份录像。')
            return None
        return next((entry for entry in self.hub.entries if entry['path'] == item.data(Qt.UserRole)), None)

    def import_media(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入本地录像', '', '视频 (*.mp4 *.mkv *.mov *.ts *.flv *.webm)')
        if path:
            self.load_entry({'path': str(Path(path).resolve()), 'title': Path(path).stem, 'created_at': time.time()})

    def open_selected(self):
        entry = self.selected_entry()
        if entry:
            self.load_entry(entry)

    def load_entry(self, entry):
        if any(job.kind == 'library_delete' and job.state in ACTIVE for job in self.hub.jobs):
            self.report('请等待录像删除完成后再打开文件。')
            return
        sidecar = Path(entry['path']).with_suffix('.danmaku.json')
        if not entry.get('danmaku_path') and sidecar.is_file():
            entry['danmaku_path'] = str(sidecar)
        if entry.get('segments'):
            self.review(entry)
            return
        def submit():
            if not Path(entry['path']).is_file():
                raise FileNotFoundError('原录像已移动或删除，请重新导入')
            ffmpeg = self.service.options()['ffmpeg']

            def inspect(job):
                info = {**entry, **probe(job, ffmpeg, entry['path'], self.hub.root / 'work')}
                if entry.get('danmaku_path'):
                    reader = ChatReader()
                    reader.read(entry['danmaku_path'])
                    info['chat_burned'] = reader.video_burned or entry.get('chat_burned', False)
                if not entry.get('cover_path'):
                    info['cover_path'] = thumbnail(job, ffmpeg, entry['path'], self.hub.root / 'thumbnails')
                return info

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
        self.review_state.setText(media_type(entry) + (' · 画面已含弹幕' if entry.get('chat_burned') else ''))
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
        self.burn_chat.blockSignals(True)
        self.burn_chat.setEnabled(bool(entry.get('danmaku_path')) and not entry.get('chat_burned', False))
        self.burn_chat.setChecked(self.burn_chat.isEnabled() and entry.get('export_burn_chat', False))
        self.burn_chat.blockSignals(False)
        self.mode.setCurrentIndex(1 if entry.get('danmaku_path') or self.export_scope.currentData() == 'merge' else 0)
        self.encoding.set_value(entry.get('encoding'))
        restore_ranges(entry)
        self.refresh_ranges()
        self.export_scope_changed()
        self.mode_changed()
        self.preview.chat.set_events([])
        self.chat_model.events = []
        self.chat_model.filter()
        for widget, value in [(self.chat_enabled, entry.get('chat_enabled', not entry.get('chat_burned', False))),
                              (self.chat_offset, entry.get('danmaku_offset', 0)),
                              (self.chat_size, entry.get('chat_size', 36)),
                              (self.chat_opacity, entry.get('chat_opacity', 80))]:
            widget.blockSignals(True)
            widget.setChecked(value) if isinstance(widget, QCheckBox) else widget.setValue(value)
            widget.blockSignals(False)
        self.save_chat_options()
        if self.heat_future is not None:
            self.heat_future.cancel()
        self.heat_future = None
        self.heat_reader = HighlightReader()
        self.heat_signature = None
        self.heat_next_check = 0
        self.heat_results.clear()
        self.timeline.heat_points = []
        self.timeline.heat_threshold = 0
        self.heat_keywords.setText(entry.get('heat_keywords', ''))
        self.heat_threshold.blockSignals(True)
        self.heat_threshold.setValue(entry.get('heat_threshold', 80))
        self.heat_threshold.blockSignals(False)
        self.heat_threshold_label.setText(f'峰值阈值 {self.heat_threshold.value()}%')
        self.heat_status.setText('正在读取弹幕热度…')
        self.refresh_highlights()
        self.loaded_duration = duration
        self.position_label.setText(clock(0) + ' / ' + clock(duration))
        self.refresh_markers()
        self.save_selection()
        self.tabs.setCurrentWidget(self.library_page)
        self.library_review.setCurrentWidget(self.review_panel)
        self.filters.hide()
        self.edit_button.setChecked(True)
        self.toggle_editor(True)
        self.update_media_layout()
        self.preview.show()
        self.report('已打开回看；默认静音。时间轴使用媒体时间，断流间隔不计入时长。')
        self.guard(lambda: self.preview.load(entry['path']))
        self.change_playback_rate()
        self.mute_button.setText('开启声音')

    def save_selection(self, *_):
        if not self.current:
            return
        start, end = self.inpoint.value(), self.outpoint.value()
        ranges = self.current['ranges']
        selected = self.current['selected_range']
        if selected < 0 and (start or end):
            ranges.append({'start': start, 'end': end, 'enabled': True})
            self.current['selected_range'] = selected = len(ranges) - 1
            self.refresh_ranges(update_selection=False)
        if 0 <= selected < len(ranges):
            ranges[selected].update(start=start, end=end)
            self.range_list.blockSignals(True)
            self.range_list.item(selected).setText(f'{selected + 1}. {clock(start)} → {clock(end)}')
            self.range_list.blockSignals(False)
        self.current['selection'] = [start, end]
        self.timeline.ranges = ranges
        self.timeline.selected_range = selected
        self.timeline.set_selection(start, end)
        valid = selected >= 0 and end > start
        active = enabled_ranges(self.current)
        all_valid = bool(active) and all(0 <= item['start'] < item['end'] <= self.current['duration'] for item in active)
        total = sum(max(0, item['end'] - item['start']) for item in active)
        self.selection_label.setText(f'当前 {clock(start)} → {clock(end)}\n启用 {len(active)}/{len(ranges)} 段 · 合计 {total:.3f} 秒')
        self.export_button.setEnabled(valid if self.export_scope.currentData() == 'current' else all_valid)
        self.selection_button.setEnabled(valid)
        self.hub.save()

    def refresh_ranges(self, *, update_selection=True):
        if not self.current:
            return
        ranges = self.current['ranges']
        selected = self.current['selected_range']
        self.range_list.blockSignals(True)
        self.range_list.clear()
        for index, item in enumerate(ranges):
            row = QListWidgetItem(f"{index + 1}. {clock(item['start'])} → {clock(item['end'])}")
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if item.get('enabled', True) else Qt.Unchecked)
            self.range_list.addItem(row)
        self.range_list.setCurrentRow(selected)
        self.range_list.setFixedHeight(max(68, min(150, len(ranges) * 40 + 4)))
        self.range_list.blockSignals(False)
        self.ranges_all.blockSignals(True)
        self.ranges_all.setChecked(bool(ranges) and all(item.get('enabled', True) for item in ranges))
        self.ranges_all.blockSignals(False)
        if update_selection:
            self.select_range(selected)

    def select_range(self, index):
        if not self.current:
            return
        ranges = self.current['ranges']
        index = index if 0 <= index < len(ranges) else -1
        self.current['selected_range'] = index
        self.range_list.blockSignals(True)
        self.range_list.setCurrentRow(index)
        self.range_list.blockSignals(False)
        item = ranges[index] if index >= 0 else {'start': 0, 'end': 0}
        for widget, value in ((self.inpoint, item['start']), (self.outpoint, item['end'])):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self.save_selection()

    def add_range(self, start=None, end=None):
        if not self.current or self.current['duration'] <= 0:
            return
        if start is None or isinstance(start, bool):
            start = min(self.preview.position(), max(0, self.current['duration'] - 10))
            end = min(self.current['duration'], start + 10)
        start, end = max(0, float(start)), min(self.current['duration'], float(end))
        if end <= start:
            return
        ranges = self.current['ranges']
        ranges.append({'start': start, 'end': end, 'enabled': True})
        self.current['selected_range'] = len(ranges) - 1
        self.refresh_ranges()

    def remove_range(self):
        if self.current and self.current['selected_range'] >= 0:
            self.current['ranges'].pop(self.current['selected_range'])
            self.current['selected_range'] = min(self.current['selected_range'], len(self.current['ranges']) - 1)
            self.refresh_ranges()

    def move_range(self, direction):
        if not self.current:
            return
        index = self.current['selected_range']
        destination = index + direction
        ranges = self.current['ranges']
        if index >= 0 and 0 <= destination < len(ranges):
            ranges[index], ranges[destination] = ranges[destination], ranges[index]
            self.current['selected_range'] = destination
            self.refresh_ranges()

    def range_toggled(self, item):
        if self.current:
            self.current['ranges'][self.range_list.row(item)]['enabled'] = item.checkState() == Qt.Checked
            self.refresh_ranges()

    def toggle_all_ranges(self, checked):
        if self.current:
            for item in self.current['ranges']:
                item['enabled'] = checked
            self.refresh_ranges()

    def add_all_highlights(self):
        for index in range(self.heat_results.count()):
            peak = self.heat_results.item(index).data(Qt.UserRole)
            if self.current and not any(abs(item['start'] - peak['start']) < .001 and abs(item['end'] - peak['end']) < .001
                                        for item in self.current['ranges']):
                self.add_range(peak['start'], peak['end'])

    def export_scope_changed(self, *_):
        precise = self.export_scope.currentData() == 'merge' or self.burn_chat.isChecked()
        self.mode.model().item(0).setEnabled(not precise)
        if precise:
            self.mode.setCurrentIndex(1)
        if self.current and 'ranges' in self.current:
            self.save_selection()

    def save_burn_options(self, *_):
        if self.current:
            self.current['export_burn_chat'] = self.burn_chat.isChecked()
            self.hub.save()
        self.export_scope_changed()
        self.mode_changed()

    def save_chat_options(self, *_):
        renderer = self.preview.chat
        renderer.enabled = self.chat_enabled.isChecked()
        renderer.offset = self.chat_offset.value()
        renderer.font_size = self.chat_size.value()
        renderer.opacity = self.chat_opacity.value()
        renderer.signature = None
        renderer.frame = None
        if self.current:
            self.current.update(chat_enabled=renderer.enabled, danmaku_offset=renderer.offset,
                                chat_size=renderer.font_size, chat_opacity=renderer.opacity)
            self.hub.save()
            self.heat_next_check = 0
        if hasattr(self, 'chat_model'):
            self.filter_chat()

    def filter_chat(self, *_):
        self.chat_model.filter(self.chat_search.text(), self.chat_kind.currentData(), self.chat_offset.value())
        self.chat_count.setText(f'共 {len(self.chat_model.events)} 条 · 显示 {self.chat_model.rowCount()} 条'
            if self.current and self.current.get('danmaku_path') else '没有弹幕记录；旧录像或原版单文件无法补回。')

    def seek_chat(self, index):
        event = index.data(Qt.UserRole)
        if event:
            self.seek_position(event['time'] + self.chat_offset.value())

    def export_chat(self, extension):
        if not self.current or not self.current.get('danmaku_path'):
            self.report('这份录像没有弹幕记录。')
            return
        start, end = (self.inpoint.value(), self.outpoint.value()) if self.chat_scope.currentData() == 'selection' else (0, self.current['duration'])
        if end <= start:
            self.report('请先选择有效的时间范围。')
            return
        target, _ = QFileDialog.getSaveFileName(self, '导出弹幕（使用新文件名）',
            str(Path(self.current['path']).with_suffix('.danmaku.json' if extension == 'json' else '.ass')), f'弹幕 (*.{extension})')
        if not target:
            return
        if Path(target).suffix.lower() != '.' + extension:
            target += '.' + extension
        def save():
            if Path(target).exists():
                raise FileExistsError('目标已存在，请使用新文件名')
            events = trim_events(self.chat_model.events, start, end, shift=self.chat_offset.value())
            if extension == 'json':
                write_json(target, events, video_burned=self.current.get('chat_burned', False))
            else:
                write_ass(target, events, font_size=self.chat_size.value(), opacity=self.chat_opacity.value())
            self.report('弹幕已导出：' + target)
        self.guard(save)

    def change_playback_rate(self, *_):
        if self.preview.core is not None:
            self.preview.core.speed = self.playback_rate.currentData()

    def mode_changed(self, *_):
        self.encoding.setEnabled(self.mode.currentData() == 'precise')
        if hasattr(self, 'burn_chat') and self.burn_chat.isChecked():
            self.mode_help.setText('将文字弹幕、表情与SC合成到MP4，另保留弹幕文件。沿用弹幕页字号、透明度和时间偏移；重编码可能改变画质，变帧率录像按探测帧率输出。')
            return
        self.mode_help.setText('保留编码，输出 MKV。起止可能按关键帧扩展，成品时长可能超过选区；侧车弹幕按请求选区裁剪，可能与视频偏移，需同步时请选择精确切片。'
            if self.mode.currentData() == 'copy' else '输出 MP4，使用下方视频编码与AAC音频；需要更多时间，画质可能变化。')

    def save_encoding_options(self):
        if self.current:
            self.current['encoding'] = self.encoding.value()
            self.hub.save()

    def export(self):
        if not self.current:
            return
        mode = self.mode.currentData()
        scope = self.export_scope.currentData()
        burn = self.burn_chat.isChecked()
        encoding = self.encoding.value()
        suffix = '.mkv' if mode == 'copy' else '.mp4'
        name = Path(self.filename.text().strip() or 'clip').stem + suffix
        folder = Path(self.current['path']).parent
        if scope == 'separate':
            destination = QFileDialog.getExistingDirectory(self, '每个启用选区导出一个文件', str(folder))
            if not destination:
                return
            targets = [str(Path(destination) / f'{Path(name).stem}-{index + 1:03}{suffix}') for index, _ in enumerate(enabled_ranges(self.current))]
            def batch():
                self.hub.submit('批量导出：' + self.current['title'], 'export', export_ranges,
                    self.service.options()['ffmpeg'], dict(self.current), enabled_ranges(self.current), mode, targets, self.hub.root / 'work', False, burn, encoding)
                self.toggle_tasks(True)
            self.guard(batch)
            return
        target, _ = QFileDialog.getSaveFileName(self, '导出选区（使用新文件名）', str(folder / name), f'视频 (*{suffix})')
        if not target:
            return
        if Path(target).suffix.lower() != suffix:
            target += suffix
        def submit():
            if scope == 'merge':
                job = self.hub.submit('拼接导出：' + self.current['title'], 'export', export_ranges,
                    self.service.options()['ffmpeg'], dict(self.current), enabled_ranges(self.current), mode,
                    [target], self.hub.root / 'work', True, burn, encoding)
            else:
                job = self.hub.submit('导出：' + self.current['title'], 'export', export_clip,
                    self.service.options()['ffmpeg'], dict(self.current), 0 if scope == 'full' else self.inpoint.value(),
                    self.current['duration'] if scope == 'full' else self.outpoint.value(), mode, target, self.hub.root / 'work', burn, encoding)
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
        self.refresh_highlights()
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
            if self.chat_follow.isChecked():
                index = self.chat_model.index_at(position)
                if index.isValid() and index != self.chat_list.currentIndex():
                    self.chat_list.setCurrentIndex(index)
                    self.chat_list.scrollTo(index, QAbstractItemView.PositionAtCenter)
            if self.preview.chat_error:
                self.chat_count.setText('画面弹幕：' + self.preview.chat_error)
            if self.preview.core:
                self.play_button.setText('播放' if self.preview.core.pause else '暂停')
            self.position_label.setText(clock(position) + ' / ' + clock(self.current['duration']))
        except Exception as error:
            self.report('播放器：' + str(error))

    def refresh_tasks(self):
        self.guard(self.finish_project_source)
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
            results = job.result.get('outputs', [job.result]) if isinstance(job.result, dict) else []
            encoders = sorted({row['encoder'] for row in results if row.get('encoder')})
            item.setToolTip(3, job.message+('\n编码器：'+', '.join(encoders) if encoders else ''))
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
        self.library_tools.stop()
        self.cover_editor.shutdown()
        self.project_editor.shutdown_preview()
        self.preview.stop()
        super().closeEvent(event)
        self.closed.emit()

    def showEvent(self, event):
        super().showEvent(event)
        self.library_tools.timer.start()
        self.library_tools.next_check = 0
        if hasattr(self, 'cover_editor') and self.library_review.currentWidget() is self.cover_editor:
            self.cover_editor.timer.start()
            return
        if hasattr(self, 'project_editor') and self.library_review.currentWidget() is self.project_editor:
            QTimer.singleShot(0, self.project_editor.request_preview)
        if self.current and self.preview.core is None:
            self.guard(lambda: self.preview.load(self.current['path']))
            self.change_playback_rate()
            self.mute_button.setText('开启声音')
