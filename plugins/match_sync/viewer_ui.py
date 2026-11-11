"""Viewer ui. State and Qt callback ownership stay on Viewer."""
from __future__ import annotations

from .canvas import Canvas
from .comparison import ComparisonPanel
from .panels import ConsoleIconButton, ConsoleModeToggle, ConsoleNotice, SettingsPanel, native_console_button, style_checkbox
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget
from PySide6.QtCore import QTimer, Qt
from .replay import ReplayBar
from .sending import SendConsole
from .chat_panel import SendingDanmakuPanel
from . import danmaku_settings
from ddm.widgets import Tile, TileButton
from ddm import theme


def build_ui(viewer):
    context = viewer.context
    viewer.crop_quality = QComboBox(viewer)
    for label, mode in (('流畅优先：直接裁切', 'source'), ('轻量增强：放大与轻锐化', 'light')):
        viewer.crop_quality.addItem(label, mode)
    mode = context.setting('crop_quality', 'source')
    mode = {'highest': 'source', 'superres': 'light'}.get(mode, mode)
    viewer.crop_quality.setCurrentIndex(max(0, viewer.crop_quality.findData(mode)))
    viewer.crop_quality.setAccessibleName('画中画画质')
    viewer.crop_quality.hide()
    viewer.crop_quality.currentIndexChanged.connect(viewer._crop_quality_changed)
    viewer.replay_preview_ratio = max(.1, min(.6, float(context.setting('replay_preview_ratio',
        context.setting('replay_preview_width', 280) / 1280))))
    viewer.record_format = QComboBox(viewer)
    viewer.record_format.addItem('MKV', 'mkv')
    viewer.record_format.addItem('MP4', 'mp4')
    viewer.record_format.setCurrentIndex(max(0, viewer.record_format.findData(context.setting('record_format', 'mkv'))))
    viewer.record_format.setAccessibleName('录制格式')
    viewer.record_format.hide()
    viewer.record_format.currentIndexChanged.connect(viewer.save)
    viewer.crop_repaint_regions = {}
    viewer.crop_repaint_timer = QTimer(viewer)
    viewer.crop_repaint_timer.setSingleShot(True)
    viewer.crop_repaint_timer.setInterval(16)
    viewer.crop_repaint_timer.timeout.connect(viewer.flush_crop_background)
    viewer.hidden_host_widgets = {}
    viewer.sidebar_item_styles = {}
    viewer.setAcceptDrops(True)
    viewer.setWindowTitle("比赛二路同步")
    viewer.setObjectName("MatchSync")
    viewer.setStyleSheet("#MatchSync { background: transparent; }")
    viewer.resize(1320, 880)
    viewer.main = QComboBox(viewer)
    viewer.main.setFixedWidth(180)
    viewer.main.currentIndexChanged.connect(viewer._main_changed)
    viewer.automatic = ConsoleModeToggle("自动对齐比赛画面", 'automatic')
    viewer.automatic.setToolTip("关闭时保存当前相对偏移；重新勾选或主动修改偏移可解除保存状态。切换不收起对照。")
    viewer.automatic.setChecked(context.setting("automatic", True))
    viewer.alignment.automatic = viewer.automatic.isChecked()
    viewer.automatic.toggled.connect(viewer._automatic_changed)
    viewer.compare = ConsoleModeToggle("对照微调", 'compare')
    viewer.automatic.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    viewer.compare.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    viewer.compare.toggled.connect(viewer.set_comparison_visible)
    viewer.late_notice = ConsoleNotice()
    viewer.late_notice.setObjectName("MatchSyncLateNotice")
    viewer.late_notice.setTextFormat(Qt.PlainText)
    viewer.late_notice.setWordWrap(False)
    viewer.late_notice.setAlignment(Qt.AlignCenter)
    viewer.late_notice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    viewer.late_notice.setMinimumWidth(0)
    viewer.late_notice.hide()
    viewer.buffer_reserve = QComboBox()
    for label, seconds in (('缓存 2 秒 · 低延迟', 2), ('缓存 5 秒 · 均衡', 5), ('缓存 8 秒 · 抗波动', 8)):
        viewer.buffer_reserve.addItem(label, seconds)
    viewer.buffer_reserve.setCurrentIndex(viewer.buffer_reserve.findData(viewer.reserve_seconds))
    viewer.buffer_reserve.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    viewer.buffer_reserve.setToolTip('自动对齐后的目标可播放余量。增加时通过轻微降速逐步积累，可能需要数分钟；不会暂停或跳转主画面。')
    viewer.buffer_reserve.setAccessibleName('抗波动缓存')
    viewer.buffer_reserve.currentIndexChanged.connect(viewer._buffer_reserve_changed)
    viewer.input = QLineEdit()
    viewer.input.setPlaceholderText("房间号、平台前缀或官方直播链接")
    viewer.input.returnPressed.connect(viewer._add_input)
    add = QPushButton("添加直播间")
    add.clicked.connect(viewer._add_input)
    use_current = QPushButton("加入当前观看的房间")
    use_current.clicked.connect(viewer.import_current)
    viewer.add_controls = (viewer.input, add, use_current)
    viewer.settings_panel = SettingsPanel(viewer)
    viewer.match_settings = None
    viewer.settings_height = 280
    viewer.console_size_timer = QTimer(viewer)
    viewer.console_size_timer.setSingleShot(True)
    viewer.console_size_timer.timeout.connect(viewer.fit_console_height)
    viewer.minimize_settings = ConsoleIconButton('minimize', '最小化播放控制台')
    viewer.minimize_settings.setToolTip("收起设置栏，保留画面、声音和弹幕播放。")
    viewer.minimize_settings.clicked.connect(lambda: viewer.set_settings_visible(False))
    viewer.restore_settings = QPushButton("展开设置")
    viewer.restore_settings.setFixedWidth(90)
    viewer.restore_settings.clicked.connect(lambda: viewer.set_settings_visible(True))
    viewer.restore_settings.hide()
    viewer.console_toolbar = QWidget()
    viewer.console_toolbar.setObjectName('MatchSyncToolbar')
    viewer.console_toolbar.setStyleSheet(f'QWidget#MatchSyncToolbar {{border-bottom: 1px solid {theme.BORDER_STRONG};}}')
    heading = QGridLayout(viewer.console_toolbar)
    heading.setContentsMargins(16, 10, 16, 10)
    heading.setHorizontalSpacing(16)
    for column, stretch in enumerate((1, 2, 1)):
        heading.setColumnStretch(column, stretch)
    leading = QWidget()
    leading_layout = QHBoxLayout(leading)
    leading_layout.setContentsMargins(0, 0, 0, 0)
    leading_layout.setSpacing(16)
    viewer.main.hide()
    leading_layout.addWidget(viewer.automatic)
    leading_layout.addWidget(viewer.compare)
    heading.addWidget(leading, 0, 0, Qt.AlignLeft)
    viewer.console_notices = QWidget()
    viewer.console_notices.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    notices = QHBoxLayout(viewer.console_notices)
    notices.setContentsMargins(0, 0, 0, 0)
    notices.setSpacing(8)
    viewer.notice = ConsoleNotice()
    viewer.buffer_notice = ConsoleNotice()
    viewer.buffer_notice.hide()
    for label in (viewer.notice, viewer.late_notice, viewer.buffer_notice):
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        notices.addWidget(label, 1)
    heading.addWidget(viewer.console_notices, 0, 1)
    trailing = QWidget()
    trailing_layout = QHBoxLayout(trailing)
    trailing_layout.setContentsMargins(0, 0, 0, 0)
    trailing_layout.setSpacing(16)
    for widget in viewer.add_controls:
        trailing_layout.addWidget(widget)
    viewer.match_settings_button = native_console_button(TileButton('⚙'), '比赛二路设置')
    viewer.match_settings_button.clicked.connect(viewer.open_match_settings)
    trailing_layout.addWidget(viewer.match_settings_button)
    trailing_layout.addWidget(viewer.minimize_settings)
    heading.addWidget(trailing, 0, 2, Qt.AlignRight)
    viewer.picture = Tile({})
    # Keep pixel hover inside the tile instead of dispatching it through
    # every surrounding layout and application event filter again.
    viewer.picture.setAttribute(Qt.WA_NoMousePropagation, True)
    # The Qt canvas has no libmpv child. Keep mask updates, but stop the
    # host surface's periodic search for a native playback window.
    viewer._native_clip_timer = getattr(viewer.picture.video, '_clip_timer', None)
    viewer.native_clip_retry_blocks = 0
    if viewer._native_clip_timer is not None:
        viewer._native_clip_timer.stop()
        viewer._native_clip_timer.installEventFilter(viewer)
    viewer.replay_bar = ReplayBar(viewer)
    viewer.restore_settings.setParent(viewer.picture.bottom)
    viewer.restore_settings.setObjectName("TileCtrl")
    viewer.restore_settings.setFixedHeight(26)
    viewer.picture.bottom.layout().insertWidget(viewer.picture.bottom.layout().indexOf(viewer.picture.danmaku_button),
                                             viewer.restore_settings, 0, Qt.AlignVCenter)
    viewer.restore_settings.hide()
    viewer.picture.bottom.installEventFilter(viewer)
    viewer.canvas = Canvas(viewer.picture.video)
    # Canvas owns the empty-state prompt, including when its skin is transparent.
    viewer.picture.cover.installEventFilter(viewer)
    viewer.picture.cover.hide()
    video_layout = QVBoxLayout(viewer.picture.video)
    video_layout.setContentsMargins(0, 0, 0, 0)
    video_layout.addWidget(viewer.canvas)
    viewer.picture.roomDropped.connect(viewer._add_dragged)
    viewer.picture.qualityChanged.connect(viewer._quality_changed)
    viewer.picture.streamInfoRequested.connect(viewer._open_stream_info)
    viewer.picture.volumeChanged.connect(lambda _room, value: viewer._picture_setting("volume", value))
    viewer.picture.muteToggled.connect(lambda _room, muted: viewer._picture_setting("audible", not muted))
    viewer.picture.audioChannelChanged.connect(lambda _room, value: viewer._picture_setting("channel", value))
    viewer.picture.reloadRequested.connect(lambda _room: viewer._reload_picture())
    viewer.picture.closeRequested.connect(lambda room: viewer.remove_room(str(room.get("room_id") or "")))
    viewer.picture.pauseToggled.connect(lambda _room: viewer._pause_picture())
    viewer.picture.fullscreenRequested.connect(lambda _tile: viewer._fullscreen_picture())
    viewer.picture.recordingRequested.connect(viewer._record_picture)
    viewer.picture.videoDanmakuAdvancedRequested.connect(viewer._open_danmaku_settings)
    viewer.fullscreen_dialog = None
    viewer.panel = SendingDanmakuPanel(viewer)
    viewer.panel.roomDropped.connect(viewer._add_dragged)
    viewer.panel.body.setAcceptDrops(False)
    viewer.panel.body.viewport().setAcceptDrops(False)
    viewer.panel.setMinimumWidth(260)
    viewer.panel.set_placeholder('')
    settings = getattr(context.window, "settings", {})
    viewer.video_danmaku = getattr(viewer.picture, "video_danmaku", None)
    if viewer.video_danmaku is not None:
        # Canvas is painted by Qt; a native VLC overlay can obscure its backing store.
        if hasattr(viewer.video_danmaku, "set_native_video"):
            viewer.video_danmaku.set_native_video(False)
        danmaku_settings.install(viewer, settings)
        viewer.picture.videoDanmakuChanged.connect(lambda: viewer.overlay_pending.clear())
    viewer.panel.apply_style(settings.get("danmaku_font", ""),
                           int(settings.get("danmaku_font_size") or 13))
    viewer.panel.set_max_blocks(int(settings.get("danmaku_max_blocks") or 3000))
    split = QSplitter(Qt.Horizontal)
    split.setOpaqueResize(False)
    split.setChildrenCollapsible(False)
    split.setHandleWidth(4)
    split.setStyleSheet("QSplitter::handle { background: transparent; } QSplitter::handle:hover { background: palette(mid); }")
    viewer.picture_split = split
    body = QWidget()
    body.setObjectName("MatchSyncRows")
    viewer.rows_layout = QVBoxLayout(body)
    viewer.rows_layout.setContentsMargins(0, 0, 0, 0)
    viewer.rows_layout.setSpacing(2)
    viewer.rows_layout.setSizeConstraint(QLayout.SetMinimumSize)
    viewer.rows_layout.setAlignment(Qt.AlignTop)
    body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    viewer.controls = QScrollArea()
    viewer.controls.setFrameShape(QFrame.NoFrame)
    viewer.controls.viewport().setAutoFillBackground(False)
    viewer.controls.setWidgetResizable(True)
    viewer.controls.setWidget(body)
    body.installEventFilter(viewer)
    viewer.controls.viewport().installEventFilter(viewer)
    body.setAcceptDrops(True)
    viewer.controls.viewport().setAcceptDrops(True)
    viewer.controls.setMinimumHeight(60)
    viewer.body_split = QSplitter(Qt.Vertical)
    viewer.body_split.setOpaqueResize(False)
    viewer.body_split.setHandleWidth(4)
    viewer.body_split.setStyleSheet("QSplitter::handle { background: transparent; } QSplitter::handle:hover { background: palette(mid); }")
    viewer.body_split.addWidget(viewer.picture)
    viewer.comparison_panel = ComparisonPanel(viewer)
    viewer.body_split.addWidget(viewer.comparison_panel)
    viewer.body_split.addWidget(viewer.settings_panel)
    viewer.body_split.setSizes([660, 0, 280])
    viewer.body_split.splitterMoved.connect(viewer.remember_console_height)
    viewer.audio_status = QLabel()
    settings_layout = QVBoxLayout(viewer.settings_panel)
    settings_layout.setContentsMargins(1, 1, 1, 1)
    settings_layout.setSpacing(0)
    settings_layout.addWidget(viewer.console_toolbar)
    settings_layout.addWidget(viewer.controls, 1)
    viewer.console_footer = QWidget()
    viewer.console_footer.setObjectName('MatchSyncFooter')
    viewer.console_footer.setStyleSheet(f'''QWidget#MatchSyncFooter {{background: {theme.BG};
            border-bottom-left-radius: 9px; border-bottom-right-radius: 9px;}}
            #MatchSyncFooter QLabel {{color: {theme.TEXT2}; font-size: 12px;}}''')
    footer = QHBoxLayout(viewer.console_footer)
    footer.setContentsMargins(16, 10, 16, 10)
    footer.setSpacing(16)
    viewer.buffer_notice_timer = QTimer(viewer)
    viewer.buffer_notice_timer.setSingleShot(True)
    viewer.buffer_notice_timer.setInterval(15000)
    viewer.buffer_notice_timer.timeout.connect(viewer.dismiss_buffer_notice)
    viewer.audio_status.setWordWrap(True)
    viewer.audio_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    footer.addWidget(viewer.audio_status, 1)
    settings_layout.addWidget(viewer.console_footer)
    viewer.removal_notice = QFrame(viewer.settings_panel)
    viewer.removal_notice.setObjectName('MatchSyncRemovalNotice')
    viewer.removal_notice.setStyleSheet(f'''#MatchSyncRemovalNotice {{background: {theme.ELEVATED};
            border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px;}}''')
    removal_layout = QHBoxLayout(viewer.removal_notice)
    removal_layout.setContentsMargins(12, 6, 12, 6)
    viewer.removal_label = QLabel()
    removal_layout.addWidget(viewer.removal_label)
    undo = QPushButton('撤销')
    undo.setAutoDefault(False)
    undo.clicked.connect(viewer.undo_remove_room)
    removal_layout.addWidget(undo)
    viewer.removal_notice.hide()
    viewer.removed_room = None
    viewer.removal_timer = QTimer(viewer)
    viewer.removal_timer.setSingleShot(True)
    viewer.removal_timer.setInterval(5000)
    viewer.removal_timer.timeout.connect(viewer.dismiss_removal_notice)
    viewer.left_pane = QWidget()
    left_layout = QVBoxLayout(viewer.left_pane)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(4)
    left_layout.addWidget(viewer.body_split, 1)
    split.addWidget(viewer.left_pane)
    viewer.send_console = SendConsole(viewer)
    right_pane = QWidget()
    viewer.chat_pane = right_pane
    viewer.chat_collapsed = False
    right_layout = QVBoxLayout(right_pane)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(0)
    right_layout.addWidget(viewer.panel, 1)
    viewer.chat_space = QWidget(right_pane)
    right_layout.addWidget(viewer.chat_space, 1)
    viewer.chat_space.hide()
    right_layout.addWidget(viewer.send_console)
    split.addWidget(right_pane)
    viewer.minimize_chat = ConsoleIconButton('minimize', '最小化弹幕栏', viewer.panel.header)
    viewer.minimize_chat.clicked.connect(lambda: viewer.set_chat_visible(False))
    viewer.panel.header.layout().addWidget(viewer.minimize_chat)
    viewer.restore_chat = QPushButton('展开弹幕', viewer.picture.bottom)
    viewer.restore_chat.setObjectName('TileCtrl')
    viewer.restore_chat.setFixedSize(90, 26)
    viewer.restore_chat.setToolTip('恢复弹幕列表')
    viewer.restore_chat.clicked.connect(lambda: viewer.set_chat_visible(True))
    viewer.picture.bottom.layout().insertWidget(
        viewer.picture.bottom.layout().indexOf(viewer.picture.danmaku_button), viewer.restore_chat)
    viewer.restore_chat.hide()
    viewer.settings_panel.installEventFilter(viewer)
    split.setSizes([930, 330])
    layout = QVBoxLayout(viewer)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(split)


def refresh_appearance(viewer, *_args):
    if not hasattr(viewer, "appearance_styles"):
        viewer.appearance_styles = {}
    def styled(widget, value):
        if viewer.appearance_styles.get(widget) != value:
            viewer.appearance_styles[widget] = value
            widget.setStyleSheet(value)
    controller = getattr(viewer.context.window, "skin_controller", None)
    viewer.skin_visible = bool(controller is not None and controller.layer.path and not controller.layer.error)
    # A fully transparent panel cannot keep text legible over a detailed skin.
    # Keep a readable theme veil even at zero mask; the same slider strengthens it.
    viewer.panel_alpha = .72 + .28 * max(0, min(100, controller.layer.mask)) / 100 if viewer.skin_visible else 1.0
    for checkbox in viewer.settings_panel.findChildren(QCheckBox):
        style_checkbox(checkbox)
    color = theme.qcolor(theme.CONTENT)
    background = (f"rgba({color.red()}, {color.green()}, {color.blue()}, {round(255 * viewer.panel_alpha)})"
                  if viewer.skin_visible else "palette(base)")
    styled(viewer.comparison_panel, f"""
            #MatchSyncCompare {{ background: {background}; border: none; border-radius: 8px; }}
            #MatchSyncCompare QLabel, #MatchSyncCompare QCheckBox {{ color: palette(window-text); }}
            #MatchSyncCompare QScrollArea, #MatchSyncCompare QScrollArea > QWidget {{ background: transparent; border: none; }}
        """)
    styled(viewer.panel, f"#MatchSync #DanmakuPanel {{ background: {background}; border: none; }}"
           '#MatchSync #DanmakuPanel[joined="true"] {border-bottom-left-radius: 0; border-bottom-right-radius: 0;}')
    panel_bar = getattr(viewer.panel, 'bar', None)
    for widget, name in ((panel_bar, "DanmakuBar"),
                         (viewer.panel.findChild(QWidget, "DanmakuHeader"), "DanmakuHeader")):
        if widget is not None:
            styled(widget, f"#MatchSync #{name} {{ background: transparent; border: none; }}")
    for label in panel_bar.findChildren(QLabel) if panel_bar is not None else ():
        styled(label, "color: palette(window-text); background: transparent;")
    viewer.panel.body.viewport().setAutoFillBackground(False)
    if getattr(viewer, '_chat_light_theme', None) != theme.is_light():
        viewer._chat_light_theme = theme.is_light()
        if viewer.panel._blocks:
            viewer.panel._render_all()
    styled(viewer.picture, f"#MatchSync #Tile {{ background: {background}; }} "
                               "#MatchSync #TileVideo, #MatchSync #TileBottom { background: transparent; }"
                               if viewer.skin_visible else "")
    viewer.canvas.transparent_background = viewer.skin_visible
    viewer.canvas._sync_opaque_paint()
    viewer.canvas.update()
    for _card, _name, canvas, _state in viewer.comparison_panel.cards.values():
        canvas.transparent_background = viewer.skin_visible
        canvas._sync_opaque_paint()
        canvas.update()
    viewer.settings_panel.update()
    viewer.send_console.refresh_design()


def _open_danmaku_settings(viewer):
    viewer.picture.danmaku_settings_menu.open_at(viewer.picture.danmaku_settings_button)
