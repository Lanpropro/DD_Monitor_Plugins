"""One selectable match picture, independently mixed audio, and labelled chat."""
from __future__ import annotations

import logging
import re
import shutil
import sys
import threading
import time
from collections import deque
from contextlib import nullcontext
from dataclasses import replace
from math import ceil
from pathlib import Path
from urllib.parse import urlsplit
from shiboken6 import isValid

from PySide6.QtCore import QEasingCurve, QEvent, QMimeData, QObject, QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QDrag, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter, QStyle, QTextEdit, QToolTip, QVBoxLayout, QWidget)

from ddm.widgets import AUTO_QUALITY, Avatar, DanmakuToggleButton, RefreshButton, ROOM_MIME, Tile, TileButton, VolumeButton
from ddm.fullscreen_cursor import FullscreenCursor
from ddm.dialogs import SHORTCUT_ACTIONS
from ddm import config as config_module, motion, theme
from .engine import Alignment, Match, RATE, SafetyBuffer, LateBuffer, match_scenes, refine_match
from .media import Chat, Decoder, PlatformChat
from .fullscreen import mark_fullscreen
from .recording import MultiTrackRecorder
from .sending import SendConsole, SendingDanmakuPanel
from .panels import ConsoleChannels, ConsoleChoice, ConsoleIconButton, ConsoleModeToggle, MatchSettingsDialog, SettingsPanel, console_hover_color, console_role_icon, native_console_button, style_checkbox, use_link_cursor
from .replay import ReplayBar, ReplayTimeline
from .floating import FloatingCrop
from .regions import match_regions
from .diagnostics import PlaybackProbe
# Compatibility exports: the implementation lives in the owning modules.
from .audio import AudioFeed, AudioPump
from .canvas import Canvas
from .room_widgets import CompactStatus, RoomRow, COLORS, ROW_MIME
from .comparison import ComparisonPanel
from .room_input import parse_room
from .playback_timing import picture_poll_interval, cache_gap
from .alignment_control import Results
from . import source_control, chat_control, alignment_control, playback_timing, picture_control, recording_control, replay_control, presentation, floating_control


class Viewer(QDialog):
    def __init__(self, context, sources, initial_room=None):
        super().__init__(context.window)
        self.context = context
        self.sources = sources
        self.rows = {}
        self.dragged_row = None
        self.row_animations = {}
        self.row_preview = []
        self.saved_rooms = {str(item["room_id"]): item for item in context.setting("rooms", [])}
        self.running = False
        self.status_ui_due = 0.0
        self.replay = ReplayTimeline()
        self._replay_live_anchor = 0.0
        self._replay_live_updated = 0.0
        self.overlay_pending = {}
        self.overlay_last = ""
        self.alignment = Alignment()
        self.alignment.buffer_seconds = 6.0
        self.safety_buffer = SafetyBuffer()
        self.reserve_seconds = context.setting('buffer_reserve', 5)
        if self.reserve_seconds not in (2, 5, 8):
            self.reserve_seconds = 5
        self.late_buffer = LateBuffer(self.reserve_seconds)
        self.playback_rate = 1.0
        self.stable_reference_shift = None
        self.skin_visible = False
        self.panel_alpha = 1.0
        self.independent_playback = {}
        self.multi_recorder = None
        self.record_started = None
        self.record_elapsed = ''
        self.independent_shifts = {}
        self.rematch_rooms = set()
        self.rematch_admit = set()
        self.last_health_update = None
        self.analysis_cursor = 0
        self.admitted_rooms = set()
        self.rendered_shifts = {}
        self.relative_rates = {}
        self.shift_reference = None
        self.shift_updated = None
        self.generation = 0
        self.matching = False
        self.analysis_serial = 0
        self.locked_clock = None
        self.sync_waiting = False
        self.offset_repositioning = False
        self.offset_target = None
        self.suppressed = {}
        self.prefer_highest = False
        self.embedded = False
        self.floating_crops = {}
        self.stream_info_dialog = None
        self.crop_quality = QComboBox(self)
        for label, mode in (('流畅优先：直接裁切', 'source'), ('轻量增强：放大与轻锐化', 'light')):
            self.crop_quality.addItem(label, mode)
        mode = context.setting('crop_quality', 'source')
        mode = {'highest': 'source', 'superres': 'light'}.get(mode, mode)
        self.crop_quality.setCurrentIndex(max(0, self.crop_quality.findData(mode)))
        self.crop_quality.setAccessibleName('画中画画质')
        self.crop_quality.hide()
        self.crop_quality.currentIndexChanged.connect(self._crop_quality_changed)
        self.replay_preview_ratio = max(.1, min(.6, float(context.setting('replay_preview_ratio',
            context.setting('replay_preview_width', 280) / 1280))))
        self.record_format = QComboBox(self)
        self.record_format.addItem('MKV', 'mkv')
        self.record_format.addItem('MP4', 'mp4')
        self.record_format.setCurrentIndex(max(0, self.record_format.findData(context.setting('record_format', 'mkv'))))
        self.record_format.setAccessibleName('录制格式')
        self.record_format.hide()
        self.record_format.currentIndexChanged.connect(self.save)
        self.crop_repaint_regions = {}
        self.crop_repaint_timer = QTimer(self)
        self.crop_repaint_timer.setSingleShot(True)
        self.crop_repaint_timer.setInterval(16)
        self.crop_repaint_timer.timeout.connect(self.flush_crop_background)
        self.hidden_host_widgets = {}
        self.sidebar_item_styles = {}
        self.setAcceptDrops(True)
        self.setWindowTitle("比赛二路同步")
        self.setObjectName("MatchSync")
        self.setStyleSheet("#MatchSync { background: transparent; }")
        self.resize(1320, 880)
        self.main = QComboBox(self)
        self.main.setFixedWidth(180)
        self.main.currentIndexChanged.connect(self._main_changed)
        self.automatic = ConsoleModeToggle("自动对齐比赛画面", 'automatic')
        self.automatic.setToolTip("关闭时保存当前相对偏移；重新勾选或主动修改偏移可解除保存状态。切换不收起对照。")
        self.automatic.setChecked(context.setting("automatic", True))
        self.alignment.automatic = self.automatic.isChecked()
        self.automatic.toggled.connect(self._automatic_changed)
        self.compare = ConsoleModeToggle("对照微调", 'compare')
        self.automatic.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.compare.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.compare.toggled.connect(self.set_comparison_visible)
        self.late_notice = QLabel()
        self.late_notice.setObjectName("MatchSyncLateNotice")
        self.late_notice.setTextFormat(Qt.PlainText)
        self.late_notice.setWordWrap(True)
        self.late_notice.setAlignment(Qt.AlignCenter)
        self.late_notice.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.late_notice.setMinimumWidth(0)
        self.late_notice.hide()
        self.buffer_reserve = QComboBox()
        for label, seconds in (('缓存 2 秒 · 低延迟', 2), ('缓存 5 秒 · 均衡', 5), ('缓存 8 秒 · 抗波动', 8)):
            self.buffer_reserve.addItem(label, seconds)
        self.buffer_reserve.setCurrentIndex(self.buffer_reserve.findData(self.reserve_seconds))
        self.buffer_reserve.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.buffer_reserve.setToolTip('自动对齐后的目标可播放余量。增加时通过轻微降速逐步积累，可能需要数分钟；不会暂停或跳转主画面。')
        self.buffer_reserve.setAccessibleName('抗波动缓存')
        self.buffer_reserve.currentIndexChanged.connect(self._buffer_reserve_changed)
        self.input = QLineEdit()
        self.input.setPlaceholderText("房间号、平台前缀或官方直播链接")
        self.input.returnPressed.connect(self._add_input)
        add = QPushButton("添加直播间")
        add.clicked.connect(self._add_input)
        use_current = QPushButton("加入当前观看的房间")
        use_current.clicked.connect(self.import_current)
        self.add_controls = (self.input, add, use_current)
        self.settings_panel = SettingsPanel(self)
        self.match_settings = None
        self.settings_height = 280
        self.console_size_timer = QTimer(self)
        self.console_size_timer.setSingleShot(True)
        self.console_size_timer.timeout.connect(self.fit_console_height)
        self.minimize_settings = ConsoleIconButton('minimize', '最小化播放控制台')
        self.minimize_settings.setToolTip("收起设置栏，保留画面、声音和弹幕播放。")
        self.minimize_settings.clicked.connect(lambda: self.set_settings_visible(False))
        self.restore_settings = QPushButton("展开设置")
        self.restore_settings.setFixedWidth(90)
        self.restore_settings.clicked.connect(lambda: self.set_settings_visible(True))
        self.restore_settings.hide()
        self.console_toolbar = QWidget()
        self.console_toolbar.setObjectName('MatchSyncToolbar')
        self.console_toolbar.setStyleSheet(f'QWidget#MatchSyncToolbar {{border-bottom: 1px solid {theme.BORDER_STRONG};}}')
        heading = QHBoxLayout(self.console_toolbar)
        heading.setContentsMargins(16, 10, 16, 10)
        heading.setSpacing(16)
        self.main.hide()
        heading.addWidget(self.automatic)
        heading.addWidget(self.compare)
        heading.addWidget(self.late_notice, 1)
        for widget in self.add_controls:
            heading.addWidget(widget)
        heading.addStretch(0)
        self.match_settings_button = native_console_button(TileButton('⚙'), '比赛二路设置')
        self.match_settings_button.clicked.connect(self.open_match_settings)
        heading.addWidget(self.match_settings_button)
        heading.addWidget(self.minimize_settings)
        self.picture = Tile({})
        # Keep pixel hover inside the tile instead of dispatching it through
        # every surrounding layout and application event filter again.
        self.picture.setAttribute(Qt.WA_NoMousePropagation, True)
        # The Qt canvas has no libmpv child. Keep mask updates, but stop the
        # host surface's periodic search for a native playback window.
        self._native_clip_timer = getattr(self.picture.video, '_clip_timer', None)
        self.native_clip_retry_blocks = 0
        if self._native_clip_timer is not None:
            self._native_clip_timer.stop()
            self._native_clip_timer.installEventFilter(self)
        self.replay_bar = ReplayBar(self)
        self.restore_settings.setParent(self.picture.bottom)
        self.restore_settings.setObjectName("TileCtrl")
        self.restore_settings.setFixedHeight(26)
        self.picture.bottom.layout().insertWidget(self.picture.bottom.layout().indexOf(self.picture.danmaku_button),
                                                 self.restore_settings, 0, Qt.AlignVCenter)
        self.restore_settings.hide()
        self.picture.bottom.installEventFilter(self)
        self.canvas = Canvas(self.picture.video)
        # Canvas owns the empty-state prompt, including when its skin is transparent.
        self.picture.cover.installEventFilter(self)
        self.picture.cover.hide()
        video_layout = QVBoxLayout(self.picture.video)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.addWidget(self.canvas)
        self.picture.roomDropped.connect(self._add_dragged)
        self.picture.qualityChanged.connect(self._quality_changed)
        self.picture.streamInfoRequested.connect(self._open_stream_info)
        self.picture.volumeChanged.connect(lambda _room, value: self._picture_setting("volume", value))
        self.picture.muteToggled.connect(lambda _room, muted: self._picture_setting("audible", not muted))
        self.picture.audioChannelChanged.connect(lambda _room, value: self._picture_setting("channel", value))
        self.picture.reloadRequested.connect(lambda _room: self._reload_picture())
        self.picture.closeRequested.connect(lambda room: self.remove_room(str(room.get("room_id") or "")))
        self.picture.pauseToggled.connect(lambda _room: self._pause_picture())
        self.picture.fullscreenRequested.connect(lambda _tile: self._fullscreen_picture())
        self.picture.recordingRequested.connect(self._record_picture)
        self.picture.videoDanmakuAdvancedRequested.connect(self._open_danmaku_settings)
        self.fullscreen_dialog = None
        self.panel = SendingDanmakuPanel(self)
        self.panel.roomDropped.connect(self._add_dragged)
        self.panel.body.setAcceptDrops(False)
        self.panel.body.viewport().setAcceptDrops(False)
        self.panel.setMinimumWidth(260)
        self.panel.set_placeholder('')
        settings = getattr(context.window, "settings", {})
        self.video_danmaku = getattr(self.picture, "video_danmaku", None)
        if self.video_danmaku is not None:
            # Canvas is painted by Qt; a native VLC overlay can obscure its backing store.
            if hasattr(self.video_danmaku, "set_native_video"):
                self.video_danmaku.set_native_video(False)
            self.video_danmaku.apply_settings(settings)
            self.picture.videoDanmakuChanged.connect(lambda: self.overlay_pending.clear())
        self.panel.apply_style(settings.get("danmaku_font", ""),
                               int(settings.get("danmaku_font_size") or 13))
        self.panel.set_max_blocks(int(settings.get("danmaku_max_blocks") or 3000))
        split = QSplitter(Qt.Horizontal)
        split.setOpaqueResize(False)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(4)
        split.setStyleSheet("QSplitter::handle { background: transparent; } QSplitter::handle:hover { background: palette(mid); }")
        self.picture_split = split
        self.notice = QLabel('')
        self.notice.setWordWrap(True)
        body = QWidget()
        body.setObjectName("MatchSyncRows")
        self.rows_layout = QVBoxLayout(body)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(2)
        self.rows_layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.rows_layout.setAlignment(Qt.AlignTop)
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.controls = QScrollArea()
        self.controls.setFrameShape(QFrame.NoFrame)
        self.controls.viewport().setAutoFillBackground(False)
        self.controls.setWidgetResizable(True)
        self.controls.setWidget(body)
        body.installEventFilter(self)
        self.controls.viewport().installEventFilter(self)
        body.setAcceptDrops(True)
        self.controls.viewport().setAcceptDrops(True)
        self.controls.setMinimumHeight(60)
        self.body_split = QSplitter(Qt.Vertical)
        self.body_split.setOpaqueResize(False)
        self.body_split.setHandleWidth(4)
        self.body_split.setStyleSheet("QSplitter::handle { background: transparent; } QSplitter::handle:hover { background: palette(mid); }")
        self.body_split.addWidget(self.picture)
        self.comparison_panel = ComparisonPanel(self)
        self.body_split.addWidget(self.comparison_panel)
        self.body_split.addWidget(self.settings_panel)
        self.body_split.setSizes([660, 0, 280])
        self.body_split.splitterMoved.connect(self.remember_console_height)
        self.audio_status = QLabel()
        settings_layout = QVBoxLayout(self.settings_panel)
        settings_layout.setContentsMargins(1, 1, 1, 1)
        settings_layout.setSpacing(0)
        settings_layout.addWidget(self.console_toolbar)
        settings_layout.addWidget(self.controls, 1)
        self.console_footer = QWidget()
        self.console_footer.setObjectName('MatchSyncFooter')
        self.console_footer.setStyleSheet(f'''QWidget#MatchSyncFooter {{background: {theme.BG};
            border-bottom-left-radius: 9px; border-bottom-right-radius: 9px;}}
            #MatchSyncFooter QLabel {{color: {theme.TEXT2}; font-size: 12px;}}''')
        footer = QHBoxLayout(self.console_footer)
        footer.setContentsMargins(16, 10, 16, 10)
        footer.setSpacing(16)
        footer.addWidget(self.notice, 1)
        self.buffer_notice = QLabel()
        self.buffer_notice.setTextFormat(Qt.PlainText)
        self.buffer_notice.setWordWrap(True)
        self.buffer_notice.setStyleSheet(f'color: {theme.ERROR};')
        self.buffer_notice.hide()
        footer.addWidget(self.buffer_notice, 1)
        self.buffer_notice_timer = QTimer(self)
        self.buffer_notice_timer.setSingleShot(True)
        self.buffer_notice_timer.setInterval(15000)
        self.buffer_notice_timer.timeout.connect(self.dismiss_buffer_notice)
        self.audio_status.setWordWrap(True)
        self.audio_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        footer.addWidget(self.audio_status, 1)
        settings_layout.addWidget(self.console_footer)
        self.removal_notice = QFrame(self.settings_panel)
        self.removal_notice.setObjectName('MatchSyncRemovalNotice')
        self.removal_notice.setStyleSheet(f'''#MatchSyncRemovalNotice {{background: {theme.ELEVATED};
            border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px;}}''')
        removal_layout = QHBoxLayout(self.removal_notice)
        removal_layout.setContentsMargins(12, 6, 12, 6)
        self.removal_label = QLabel()
        removal_layout.addWidget(self.removal_label)
        undo = QPushButton('撤销')
        undo.setAutoDefault(False)
        undo.clicked.connect(self.undo_remove_room)
        removal_layout.addWidget(undo)
        self.removal_notice.hide()
        self.removed_room = None
        self.removal_timer = QTimer(self)
        self.removal_timer.setSingleShot(True)
        self.removal_timer.setInterval(5000)
        self.removal_timer.timeout.connect(self.dismiss_removal_notice)
        self.left_pane = QWidget()
        left_layout = QVBoxLayout(self.left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self.body_split, 1)
        split.addWidget(self.left_pane)
        self.send_console = SendConsole(self)
        right_pane = QWidget()
        self.chat_pane = right_pane
        self.chat_collapsed = False
        right_layout = QVBoxLayout(right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.panel, 1)
        self.chat_space = QWidget(right_pane)
        right_layout.addWidget(self.chat_space, 1)
        self.chat_space.hide()
        right_layout.addWidget(self.send_console)
        split.addWidget(right_pane)
        self.minimize_chat = ConsoleIconButton('minimize', '最小化弹幕栏', self.panel.header)
        self.minimize_chat.clicked.connect(lambda: self.set_chat_visible(False))
        self.panel.header.layout().addWidget(self.minimize_chat)
        self.restore_chat = QPushButton('展开弹幕', self.picture.bottom)
        self.restore_chat.setObjectName('TileCtrl')
        self.restore_chat.setFixedSize(90, 26)
        self.restore_chat.setToolTip('恢复弹幕列表')
        self.restore_chat.clicked.connect(lambda: self.set_chat_visible(True))
        self.picture.bottom.layout().insertWidget(
            self.picture.bottom.layout().indexOf(self.picture.danmaku_button), self.restore_chat)
        self.restore_chat.hide()
        self.settings_panel.installEventFilter(self)
        split.setSizes([930, 330])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self.audio = AudioPump(self)
        self.audio.status.connect(self.audio_status.setText)
        self.host_audio_timer = QTimer(self)
        self.host_audio_timer.setInterval(100)
        self.host_audio_timer.timeout.connect(self._suppress_host_audio)
        self.render_timer = QTimer(self)
        self.render_timer.setTimerType(Qt.PreciseTimer)
        self.render_timer.setInterval(33)
        self.render_timer.timeout.connect(self.render)
        self.playback_probe = PlaybackProbe(self, Path(config_module.REPO) / 'logs' / 'match-playback.jsonl')
        self.playback_probe.preview = self.replay_bar.preview.metrics
        self.playback_probe.audio = self.audio
        self.canvas.paint_probe = self.playback_probe
        self.match_timer = QTimer(self)
        self.match_timer.setInterval(500)
        self.match_timer.timeout.connect(self.analyse)
        self.results = Results()
        self.results.matched.connect(self._matched)
        self.results.completed.connect(self._analysis_finished)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self.save)
        if initial_room:
            self.add_room(str(initial_room.get("room_id") or ""),
                          {"alias": initial_room.get("uname") or ""})
        index = self.main.findData(context.setting("main_room", ""))
        if index >= 0:
            self.main.setCurrentIndex(index)
        self._automatic_changed()
        theme.signals().changed.connect(self.refresh_appearance)
        controller = getattr(context.window, "skin_controller", None)
        if controller is not None:
            controller.changed.connect(self.refresh_appearance)
        self.refresh_appearance()

    def refresh_appearance(self, *_args):
        if not hasattr(self, "appearance_styles"):
            self.appearance_styles = {}
        def styled(widget, value):
            if self.appearance_styles.get(widget) != value:
                self.appearance_styles[widget] = value
                widget.setStyleSheet(value)
        controller = getattr(self.context.window, "skin_controller", None)
        self.skin_visible = bool(controller is not None and controller.layer.path and not controller.layer.error)
        # A fully transparent panel cannot keep text legible over a detailed skin.
        # Keep a readable theme veil even at zero mask; the same slider strengthens it.
        self.panel_alpha = .72 + .28 * max(0, min(100, controller.layer.mask)) / 100 if self.skin_visible else 1.0
        for checkbox in self.settings_panel.findChildren(QCheckBox):
            style_checkbox(checkbox)
        color = theme.qcolor(theme.CONTENT)
        background = (f"rgba({color.red()}, {color.green()}, {color.blue()}, {round(255 * self.panel_alpha)})"
                      if self.skin_visible else "palette(base)")
        styled(self.comparison_panel, f"""
            #MatchSyncCompare {{ background: {background}; border: none; border-radius: 8px; }}
            #MatchSyncCompare QLabel, #MatchSyncCompare QCheckBox {{ color: palette(window-text); }}
            #MatchSyncCompare QScrollArea, #MatchSyncCompare QScrollArea > QWidget {{ background: transparent; border: none; }}
        """)
        styled(self.panel, f"#MatchSync #DanmakuPanel {{ background: {background}; border: none; }}"
               '#MatchSync #DanmakuPanel[joined="true"] {border-bottom-left-radius: 0; border-bottom-right-radius: 0;}')
        panel_bar = getattr(self.panel, 'bar', None)
        for widget, name in ((panel_bar, "DanmakuBar"),
                             (self.panel.findChild(QWidget, "DanmakuHeader"), "DanmakuHeader")):
            if widget is not None:
                styled(widget, f"#MatchSync #{name} {{ background: transparent; border: none; }}")
        for label in panel_bar.findChildren(QLabel) if panel_bar is not None else ():
            styled(label, "color: palette(window-text); background: transparent;")
        self.panel.body.viewport().setAutoFillBackground(False)
        if getattr(self, '_chat_light_theme', None) != theme.is_light():
            self._chat_light_theme = theme.is_light()
            if self.panel._blocks:
                self.panel._render_all()
        styled(self.picture, f"#MatchSync #Tile {{ background: {background}; }} "
                                   "#MatchSync #TileVideo, #MatchSync #TileBottom { background: transparent; }"
                                   if self.skin_visible else "")
        self.canvas.transparent_background = self.skin_visible
        self.canvas._sync_opaque_paint()
        self.canvas.update()
        for _card, _name, canvas, _state in self.comparison_panel.cards.values():
            canvas.transparent_background = self.skin_visible
            canvas._sync_opaque_paint()
            canvas.update()
        self.settings_panel.update()
        self.send_console.refresh_design()

    def embed(self, content):
        self.embedded = True
        self.setParent(content, Qt.Widget)
        self.setGeometry(content.rect())
        content.installEventFilter(self)
        for widget in self.add_controls:
            widget.hide()
        self.notice.setText('')

    def _shortcut_action(self, watched, event):
        if not self.isVisible() or not isinstance(watched, QWidget):
            return None
        if watched.window() not in (self.window(), self.fullscreen_dialog):
            return None
        if QApplication.activeModalWidget() not in (None, self, self.fullscreen_dialog):
            return None
        if isinstance(watched, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            return None
        if isinstance(watched, QComboBox) and watched.isEditable():
            return None
        pressed = QKeySequence(event.keyCombination()).toString()
        shortcuts = getattr(self.context.window, "shortcuts", {})
        return next((action for action, _label, default in SHORTCUT_ACTIONS
                     if pressed and pressed == shortcuts.get(action, default)), None)

    def _handle_shortcut(self, action):
        if action == "restore":
            if self.fullscreen_dialog is not None:
                self.fullscreen_dialog.close()
            return
        if action == "focus" and self.fullscreen_dialog is not None:
            self.fullscreen_dialog.close()
            return
        point = QCursor.pos()
        target = None
        if self.picture.isVisible() and self.picture.rect().contains(self.picture.mapFromGlobal(point)):
            target = self.rows.get(self.main.currentData())
        else:
            for key, (card, _name, _canvas, _state) in self.comparison_panel.cards.items():
                if card.isVisible() and card.rect().contains(card.mapFromGlobal(point)):
                    target = self.rows.get(key)
                    break
        if action == "solo":
            for row in self.rows.values():
                row.audible.setChecked(row is target)
        elif target is not None:
            if action == "focus":
                self._fullscreen_picture()
            elif action == "mute":
                target.audible.toggle()
            elif action == "video_danmaku":
                self.picture.danmaku_button.toggle()
            elif action == "audio_cycle":
                if not target.audible.isChecked():
                    target.channel.setCurrentIndex(0)
                    target.audible.setChecked(True)
                elif target.channel.currentIndex() == target.channel.count() - 1:
                    target.audible.setChecked(False)
                else:
                    target.channel.setCurrentIndex(target.channel.currentIndex() + 1)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Timer and watched is getattr(self, '_native_clip_timer', None):
            self.native_clip_retry_blocks += 1
            watched.stop()
            return True
        if kind in (QEvent.MouseMove, QEvent.HoverMove, QEvent.Enter, QEvent.Leave):
            probe = getattr(self, 'playback_probe', None)
            if probe is not None and probe.thread is not None:
                probe.mouse(watched)
        if kind in (QEvent.ShortcutOverride, QEvent.KeyPress):
            action = self._shortcut_action(watched, event)
            if action is not None:
                event.accept()
                if kind == QEvent.KeyPress and not event.isAutoRepeat():
                    self._handle_shortcut(action)
                return True
        elif kind == QEvent.Show:
            if watched is self.picture.cover or watched in self.hidden_host_widgets:
                watched.hide()
        elif (kind in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop)
                and getattr(self, "controls", None) is not None
                and isinstance(watched, QWidget)
                and (watched is self.controls.viewport() or watched is self.controls.widget()
                     or self.controls.widget().isAncestorOf(watched))
                and self.dragged_row is not None and event.mimeData().hasFormat(ROW_MIME)
                and bytes(event.mimeData().data(ROW_MIME)).decode('utf-8') == self.dragged_row):
            point = watched.mapTo(self.controls.widget(), event.position().toPoint())
            if kind == QEvent.Drop:
                self.finish_row_drag(point)
            else:
                self.preview_row_drag(point)
            event.acceptProposedAction()
            return True
        elif kind == QEvent.Resize:
            if hasattr(self, 'controls') and watched is self.controls.viewport():
                self.align_room_controls()
            if watched is self.settings_panel and hasattr(self, 'send_console'):
                self.send_console.sync_height()
                self.position_removal_notice()
                self.queue_console_height()
            if watched is self.picture.bottom:
                QTimer.singleShot(0, self._layout_restore_control)
            if self.embedded and watched is self.parentWidget():
                self.setGeometry(watched.rect())
        elif kind == QEvent.FocusIn:
            for key, row in self.rows.items():
                if watched is row.delay:
                    self.show_comparison(key)
                    break
        return super().eventFilter(watched, event)

    def _switch_stage(self, stage):
        probe = getattr(self, 'switch_probe', None)
        return probe.span(stage) if probe is not None else nullcontext()

    def showEvent(self, event):
        QApplication.instance().installEventFilter(self)
        if self.embedded:
            suspend = getattr(self.context.window, "set_match_sync_playback", None)
            if callable(suspend):
                with self._switch_stage('suspend_host_playback'):
                    suspend(True)
            self.host_audio_timer.start()
            self._suppress_host_audio()
        if self.embedded and not self.hidden_host_widgets:
            host = self.context.window
            for widget in (getattr(host, "wall", None), getattr(host, "empty_hint", None)):
                if widget is not None:
                    self.hidden_host_widgets[widget] = not widget.isHidden()
                    widget.installEventFilter(self)
                    widget.hide()
        with self._switch_stage('show_controls'):
            self._sync_sidebar_marks()
            self.send_console.sync_height()
        self.console_content_height = None
        self.align_room_controls()
        super().showEvent(event)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def _sync_sidebar_marks(self):
        sidebar = getattr(self.context.window, "sidebar", None)
        if not self.embedded or sidebar is None or self.isHidden():
            return
        now = time.monotonic()
        state = (self.running, tuple((key, id(row.decoder)) for key, row in self.rows.items()),
                 getattr(sidebar, "side", "left"), getattr(sidebar, "collapsed", False),
                 len(getattr(sidebar, "_items", [])), theme.is_light())
        if state == getattr(self, "sidebar_marks_state", None) and now < self.sidebar_marks_due:
            return
        self.sidebar_marks_state, self.sidebar_marks_due = state, now + .25
        for row in self.rows.values():
            row.refresh_avatar()
            self.send_console.refresh_avatar(row.room_id)
        for item in getattr(sidebar, "_items", []):
            row = self.rows.get(str(item.room.get("room_id") or ""))
            active = self.running and row is not None and row.decoder is not None
            self._style_sidebar_item(item, active, bool(item.property('onWall')))
        self._sync_strip_marks(sidebar, True)

    def _style_sidebar_item(self, item, active, hold):
        # Newer hosts convert QSS for continuous corners. Compare their source
        # recipe, otherwise each poll reapplies QSS that the host converts again.
        source_style = getattr(sys.modules.get('ddm.continuous_corners'), 'source_stylesheet', str)
        current = source_style(item.styleSheet())
        changed = bool(item.property('matchSync')) != active
        if changed:
            item.setProperty('matchSync', active)
        if active or hold:
            original = self.sidebar_item_styles.setdefault(item, current)
            style = original + """
                #NavItem[onWall="true"] { border: none; }
                #NavItem[matchSync="true"], #NavItem[matchSync="true"][onWall="true"] {
                    border: 1px solid #fb7299;
                }
            """
        elif item in self.sidebar_item_styles:
            style = self.sidebar_item_styles.pop(item)
        else:
            return
        if current != style:
            item.setStyleSheet(style)
        elif changed:
            item.style().unpolish(item)
            item.style().polish(item)
            item.update()

    def _sync_strip_marks(self, sidebar, enabled):
        strip = getattr(sidebar, "_head_strip", None)
        for room_id, avatar in getattr(strip, "_avatars", {}).items():
            for mark in avatar.findChildren(QLabel, options=Qt.FindDirectChildrenOnly):
                if f"background: {theme.ACCENT};" in mark.styleSheet():
                    if enabled:
                        mark.setProperty("matchSyncHidden", True)
                        mark.hide()
                    elif mark.property("matchSyncHidden"):
                        mark.setProperty("matchSyncHidden", False)
                        mark.show()
            frame = avatar.findChild(QFrame, "MatchSyncStripMark")
            row = self.rows.get(room_id)
            active = enabled and self.running and row is not None and row.decoder is not None
            if active and frame is None:
                frame = QFrame(avatar)
                frame.setObjectName("MatchSyncStripMark")
                frame.setAttribute(Qt.WA_TransparentForMouseEvents)
                frame.setGeometry(avatar.rect())
                frame.setStyleSheet(f"background: transparent; border: 1px solid {theme.PINK};"
                                   f" border-radius: {avatar.width() // 2}px;")
            if frame is not None:
                frame.setVisible(bool(active))
                frame.raise_()

    def set_settings_visible(self, visible):
        if visible and self.fullscreen_dialog is not None:
            self.fullscreen_dialog.close()
        if not visible:
            self.settings_height = self.settings_panel.height()
            self.compare.setChecked(False)
        self.settings_panel.setVisible(visible)
        self.send_console.sync_height()
        self.restore_settings.setVisible(not visible)
        self._layout_restore_control()
        if visible:
            self.left_pane.layout().activate()
            self.body_split.setSizes([
                max(1, self.body_split.height() - self.settings_height -
                    (self.comparison_panel.height() if self.compare.isChecked() else 0)),
                self.comparison_panel.height() if self.compare.isChecked() else 0, self.settings_height])

    def _layout_restore_control(self):
        tile = self.picture
        tile._layout_bottom_controls()
        if self.restore_settings.isHidden():
            return
        self.restore_settings.setFixedWidth(58 if tile.width() < 420 else 90)
        self.restore_settings.setText("展开" if tile.width() < 420 else "展开设置")
        layout = tile.bottom.layout()
        fixed = [layout.itemAt(i).widget() for i in range(layout.count())]
        fixed = [widget for widget in fixed if widget is not None and not widget.isHidden()
                 and widget not in (tile.volume_slider, tile.status_label)]
        margins = layout.contentsMargins()
        remaining = tile.width() - margins.left() - margins.right() - layout.spacing() * (len(fixed) + 1) - 8
        remaining -= sum(widget.sizeHint().width() if widget is tile.recording_time else widget.width() for widget in fixed)
        tile.volume_slider.setFixedWidth(max(18, min(86, remaining)))
        layout.activate()

    def set_comparison_visible(self, visible):
        sizes = self.body_split.sizes()
        self.comparison_panel.setVisible(visible)
        self.generation += 1
        height = 240 if visible else 0
        if len(sizes) == 3:
            self.body_split.setSizes([max(1, sizes[0] + sizes[1] - height), height, sizes[2]])
        if visible:
            self.comparison_panel.render(self.live_clock(), self.shifts())

    def reorder_room(self, room_id, target_id, after=False):
        if room_id == target_id or room_id not in self.rows or target_id not in self.rows:
            return
        keys = list(self.rows)
        keys.remove(room_id)
        keys.insert(keys.index(target_id) + int(after), room_id)
        self.rows = {key: self.rows[key] for key in keys}
        for index, row in enumerate(self.rows.values()):
            self.rows_layout.insertWidget(index, row)
        self.update_main_choices()
        self.changed()

    def align_room_controls(self):
        rows = list(self.rows.values())
        if self.dragged_row is not None:
            return
        if not rows:
            self.queue_console_height()
            return
        name_width = min(140, max(row.name_label.fontMetrics().horizontalAdvance(row.name_label.text()) for row in rows))
        for row in rows:
            if row.name_label.width() != name_width or row.name_label.minimumWidth() != name_width:
                row.name_label.setFixedWidth(name_width)
        widths = [max(min(widget.maximumWidth(), max(widget.minimumWidth(), widget.sizeHint().width()))
                      for widget in widgets) for widgets in zip(*(row.control_widgets for row in rows))]
        compact = self.controls.viewport().width() < sum(widths) + 12 * 5 + 16
        for index, row in enumerate(rows):
            row.layout_controls(compact, index == 0)
            for column in set(row.control_columns):
                needed = max(w for c, w in zip(row.control_columns, widths) if c == column)
                if row.controls_grid.columnMinimumWidth(column) != needed:
                    row.controls_grid.setColumnMinimumWidth(column, needed)
        self.queue_console_height()

    def queue_console_height(self):
        if self.body_split.count() != 3:
            return
        height = self.settings_height
        if height != getattr(self, 'console_content_height', None) or self.settings_panel.height() != height:
            self.console_content_height = height
            self.settings_height = height
            self.console_size_timer.start(0)

    def remember_console_height(self, _position, _index):
        if not self.settings_panel.isHidden() and self.dragged_row is None:
            self.settings_height = self.console_content_height = self.settings_panel.height()

    def fit_console_height(self):
        if self.settings_panel.isHidden() or self.dragged_row is not None:
            return
        sizes = self.body_split.sizes()
        # Fullscreen reparents the picture out of this splitter temporarily.
        if len(sizes) != 3:
            return
        height = self.console_content_height
        self.body_split.setSizes([max(1, sizes[0] + sizes[2] - height), sizes[1], height])

    def begin_row_drag(self, room_id):
        self.console_size_timer.stop()
        self.dragged_row = room_id
        self.row_preview = list(self.rows)
        self.row_slots = {key: row.geometry() for key, row in self.rows.items()}
        self.rows_layout.setEnabled(False)
        self.rows[room_id].hide()

    def preview_row_drag(self, point):
        if self.dragged_row is None:
            return
        for animation in self.row_animations.values():
            elapsed = round((time.perf_counter() - animation.property('started_at')) * 1000)
            animation.setCurrentTime(min(animation.duration(), max(animation.currentTime(), elapsed)))
        order = [key for key in self.rows if key != self.dragged_row]
        index = sum(point.y() >= self.row_slots[key].center().y() for key in order)
        order.insert(index, self.dragged_row)
        if order != self.row_preview:
            self.row_preview = order
            y = 0
            for key in order:
                row = self.rows[key]
                if key != self.dragged_row:
                    previous = self.row_animations.pop(key, None)
                    if previous is not None:
                        previous.stop()
                        previous.deleteLater()
                    target = QPoint(self.row_slots[key].x(), y)
                    if motion.enabled():
                        animation = QPropertyAnimation(row, b"pos", self)
                        animation.setDuration(150)
                        animation.setEasingCurve(QEasingCurve.OutCubic)
                        animation.setStartValue(row.pos())
                        animation.setEndValue(target)
                        animation.setProperty('started_at', time.perf_counter())
                        self.row_animations[key] = animation
                        animation.start()
                    else:
                        row.move(target)
                y += self.row_slots[key].height() + self.rows_layout.spacing()
        viewport = self.controls.viewport()
        local = self.controls.widget().mapTo(viewport, point)
        bar = self.controls.verticalScrollBar()
        if local.y() < 24:
            bar.setValue(bar.value() - 12)
        elif local.y() > viewport.height() - 24:
            bar.setValue(bar.value() + 12)

    def finish_row_drag(self, point):
        if self.dragged_row is None:
            return
        self.preview_row_drag(point)
        self.rows = {key: self.rows[key] for key in self.row_preview}
        self.end_row_drag()
        self.update_main_choices()
        self.changed()

    def end_row_drag(self):
        for animation in self.row_animations.values():
            animation.stop()
            animation.deleteLater()
        self.row_animations.clear()
        if self.dragged_row in self.rows:
            self.rows[self.dragged_row].show()
        self.dragged_row = None
        for index, row in enumerate(self.rows.values()):
            self.rows_layout.insertWidget(index, row)
        self.rows_layout.setEnabled(True)
        self.rows_layout.activate()

    def _queue_rematch(self, row, local=False):
        return alignment_control._queue_rematch(self, row, local)

    def retry_alignment(self, room_id):
        return alignment_control.retry_alignment(self, room_id)

    def submit_alignment_hint(self, room_id, direction):
        return alignment_control.submit_alignment_hint(self, room_id, direction)

    def _delay_changed(self, *_args):
        return alignment_control._delay_changed(self, *_args)

    def show_comparison(self, room_id):
        self.compare.setChecked(True)
        self.comparison_panel.render(self.live_clock(), self.shifts())
        index = self.comparison_panel.target.findData(room_id)
        if index >= 0:
            self.comparison_panel.target.setCurrentIndex(index)

    def set_chat_visible(self, visible):
        if visible == (not self.chat_collapsed):
            return
        self.chat_collapsed = not visible
        self.panel.setVisible(visible)
        self.chat_space.setVisible(not visible)
        self.restore_chat.setVisible(not visible)

    def _restore_host_widgets(self):
        self.host_audio_timer.stop()
        self._release_host_audio()
        sidebar = getattr(self.context.window, "sidebar", None)
        if sidebar is not None:
            for item in tuple(self.sidebar_item_styles):
                if isValid(item):
                    self._style_sidebar_item(item, False, False)
                else:
                    self.sidebar_item_styles.pop(item)
            self._sync_strip_marks(sidebar, False)
        previous, self.hidden_host_widgets = self.hidden_host_widgets, {}
        for widget, visible in previous.items():
            widget.removeEventFilter(self)
            widget.setVisible(visible)
        resume = getattr(self.context.window, "set_match_sync_playback", None)
        if self.embedded and callable(resume):
            with self._switch_stage('resume_host_playback'):
                resume(False)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(ROOM_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(ROOM_MIME):
            return
        room_id = bytes(event.mimeData().data(ROOM_MIME)).decode("utf-8", "ignore")
        if self._add_dragged(room_id):
            event.acceptProposedAction()

    def _add_dragged(self, room_id):
        room = next((item for item in getattr(self.context.window, "rooms", [])
                     if str(item.get("room_id") or "") == room_id), {})
        if self.add_room(room_id, {"alias": room.get("uname") or ""}):
            if self.embedded and not self.running:
                self.toggle_running()
            return True
        return False

    def changed(self, *_args):
        self.save_timer.start()

    def save(self):
        self.context.set_setting("rooms", [row.preferences() for row in self.rows.values()])
        self.context.set_setting("main_room", self.main.currentData() or "")
        self.context.set_setting("automatic", self.automatic.isChecked())
        self.context.set_setting('buffer_reserve', self.reserve_seconds)
        self.context.set_setting('crop_quality', self.crop_quality.currentData())
        self.context.set_setting('record_format', self.record_format.currentData())
        self.context.set_setting('replay_preview_ratio', self.replay_preview_ratio)

    def _crop_source_policy(self, row):
        return floating_control._crop_source_policy(self, row)

    def _crop_quality_changed(self):
        return floating_control._crop_quality_changed(self)

    def _apply_crop_quality(self):
        return floating_control._apply_crop_quality(self)

    def _close_floating_crop(self, key):
        return floating_control._close_floating_crop(self, key)

    def _buffer_reserve_changed(self):
        self.reserve_seconds = self.buffer_reserve.currentData()
        self.late_buffer = LateBuffer(self.reserve_seconds)
        self.late_notice.hide()
        self.changed()

    def _open_danmaku_settings(self):
        open_settings = getattr(self.context.window, 'open_settings', None)
        if callable(open_settings):
            open_settings('danmaku')
            settings = getattr(self.context.window, 'settings', {})
            self.video_danmaku.apply_settings(settings)
            self.picture.danmaku_settings_menu.apply_settings(settings)
            self.panel.apply_style(settings.get('danmaku_font', ''),
                                   int(settings.get('danmaku_font_size') or 13))
            self.panel.set_max_blocks(int(settings.get('danmaku_max_blocks') or 3000))

    def position_removal_notice(self):
        self.removal_notice.move(max(0, (self.settings_panel.width() - self.removal_notice.width()) // 2),
            max(0, self.settings_panel.height() - self.console_footer.height() - self.removal_notice.height() - 8))

    def dismiss_removal_notice(self):
        return source_control.dismiss_removal_notice(self)

    def remove_room_with_undo(self, room_id):
        return source_control.remove_room_with_undo(self, room_id)

    def undo_remove_room(self):
        return source_control.undo_remove_room(self)

    def _add_input(self):
        return source_control._add_input(self)

    def add_room(self, text, preferences=None):
        return source_control.add_room(self, text, preferences)

    def import_current(self):
        return source_control.import_current(self)

    def update_main_choices(self):
        return source_control.update_main_choices(self)

    def _main_changed(self, *_args):
        return picture_control._main_changed(self, *_args)

    def select_main(self, room_id):
        return picture_control.select_main(self, room_id)

    def _show_buffering(self, active):
        return picture_control._show_buffering(self, active)

    def _stream_info_snapshot(self):
        return picture_control._stream_info_snapshot(self)

    def _open_stream_info(self):
        return picture_control._open_stream_info(self)

    def _stream_info_closed(self, _result):
        return picture_control._stream_info_closed(self, _result)

    def sync_picture(self, *_args, refresh_metadata=True):
        return picture_control.sync_picture(self, *_args, refresh_metadata=refresh_metadata)

    def replay_histories(self):
        return replay_control.replay_histories(self)

    def open_match_settings(self):
        return picture_control.open_match_settings(self)

    def live_clock(self):
        return replay_control.live_clock(self)

    def _rebuild_replay_chat(self, stamp, live=False):
        return chat_control._rebuild_replay_chat(self, stamp, live)

    def seek_replay(self, stamp):
        return replay_control.seek_replay(self, stamp)

    def return_live(self):
        return replay_control.return_live(self)

    def _picture_setting(self, key, value):
        return picture_control._picture_setting(self, key, value)

    def _quality_changed(self, _room, quality):
        return picture_control._quality_changed(self, _room, quality)

    def _reload_picture(self):
        return picture_control._reload_picture(self)

    def _pause_picture(self):
        return picture_control._pause_picture(self)

    def _fullscreen_picture(self):
        return picture_control._fullscreen_picture(self)

    def _stop_recording(self):
        return recording_control._stop_recording(self)

    def _record_picture(self):
        return recording_control._record_picture(self)

    def _recording_snapshot(self):
        return recording_control._recording_snapshot(self)

    @Slot(str)
    def _recording_started(self, path):
        return recording_control._recording_started(self, path)

    @Slot(str, str)
    def _recording_finished(self, path, error):
        return recording_control._recording_finished(self, path, error)

    def _save_delay_offsets(self, shifts):
        return alignment_control._save_delay_offsets(self, shifts)

    def _automatic_changed(self, *_args):
        return alignment_control._automatic_changed(self, *_args)

    def remove_room(self, room_id):
        return source_control.remove_room(self, room_id)

    def toggle_independent(self, room_id):
        return alignment_control.toggle_independent(self, room_id)

    def target_shifts(self):
        return alignment_control.target_shifts(self)

    def _block_alignment(self, room_id, reason):
        return alignment_control._block_alignment(self, room_id, reason)

    def shifts(self):
        return alignment_control.shifts(self)

    def _smooth_shifts(self, wanted):
        return alignment_control._smooth_shifts(self, wanted)

    def toggle_running(self):
        if self.running:
            self.stop()
            return
        if not self.rows:
            self.notice.setText("请先添加直播间")
            return
        self.running = True
        self.independent_playback.clear()
        self.independent_shifts.clear()
        self.rematch_rooms = set(self.rows) - {self.alignment.reference} if self.alignment.automatic else set()
        self.rematch_admit.clear()
        self.safety_buffer = SafetyBuffer()
        self.late_buffer = LateBuffer(self.reserve_seconds)
        self.late_notice.hide()
        if not self.alignment.manual_locked:
            self.stable_reference_shift = None
        self.last_health_update = None
        self.playback_rate = 1.0
        self.admitted_rooms.clear()
        self.rendered_shifts.clear()
        self.shift_reference = None
        self.shift_updated = None
        self.relative_rates.clear()
        self.generation += 1
        if not self.alignment.manual_locked:
            self.alignment.lags = {self.alignment.reference: 0}
        self.locked_clock = None
        self.sync_waiting = False
        self.alignment.candidates.clear()
        self._refresh_chat_status()
        for row in self.rows.values():
            row.user_confirmed = False
            self._start_row(row)
        self.sync_picture()
        self._sync_sidebar_marks()
        self.audio.start()
        self.playback_probe.start()
        self.render_timer.start()
        self.match_timer.start()

    def _start_row(self, row):
        return source_control._start_row(self, row)

    def _start_chat(self, row):
        return chat_control._start_chat(self, row)

    def _refresh_chat_status(self):
        return chat_control._refresh_chat_status(self)

    def _valid(self, row, worker, chat=False):
        return source_control._valid(self, row, worker, chat)

    def _information(self, row, worker, info):
        return source_control._information(self, row, worker, info)

    def _state(self, row, worker, text, chat):
        return source_control._state(self, row, worker, text, chat)

    def _reset(self, row, worker):
        return source_control._reset(self, row, worker)

    def _message(self, row, worker, event):
        return chat_control._message(self, row, worker, event)

    def _recover_clock(self, clock, shifts):
        return playback_timing._recover_clock(self, clock, shifts)

    def _update_late_buffer(self, now, clock, shifts, ahead, capacity):
        return playback_timing._update_late_buffer(self, now, clock, shifts, ahead, capacity)

    def dismiss_buffer_notice(self):
        return playback_timing.dismiss_buffer_notice(self)

    def _observe_buffer_exhaustion(self, row, exhausted, eligible, now):
        return playback_timing._observe_buffer_exhaustion(self, row, exhausted, eligible, now)

    def _update_sync_health(self, clock, shifts):
        return alignment_control._update_sync_health(self, clock, shifts)

    def confirm_alignment(self, room_id):
        return alignment_control.confirm_alignment(self, room_id)

    def admit_pending(self, room_id, automatic=False):
        return alignment_control.admit_pending(self, room_id, automatic)

    def _render_overlay(self, clock, shifts):
        return chat_control._render_overlay(self, clock, shifts)

    def render(self):
        return presentation.render(self)

    def _render(self, probe):
        return presentation._render(self, probe)

    def _configure_picture_budget(self):
        return presentation._configure_picture_budget(self)

    def analyse(self):
        return alignment_control.analyse(self)

    def _analysis_finished(self, serial, epoch, results):
        return alignment_control._analysis_finished(self, serial, epoch, results)

    def _matched(self, epoch, results):
        return alignment_control._matched(self, epoch, results)

    def floating_surface(self):
        return floating_control.floating_surface(self)

    def reparent_floating_crops(self, parent):
        return floating_control.reparent_floating_crops(self, parent)

    def restore_crop_background(self, floating, exposed):
        return floating_control.restore_crop_background(self, floating, exposed)

    def flush_crop_background(self):
        return floating_control.flush_crop_background(self)

    def show_floating_crop(self, row, crop, frame):
        return floating_control.show_floating_crop(self, row, crop, frame)

    def render_floating_crops(self, clock, shifts):
        return floating_control.render_floating_crops(self, clock, shifts)

    def choose_crop(self, row, floating=False):
        return floating_control.choose_crop(self, row, floating)

    def _suppress_host_audio(self):
        players = getattr(self.context.window, "players", {})
        current = {}
        active = self.host_audio_timer.isActive() if self.embedded else self.audio.sink is not None
        if active:
            for tile, player in list(players.items()):
                if (self.embedded or str((tile.room or {}).get("room_id") or "") in self.rows) and not player._released:
                    player.set_muted(True)
                    current[player] = tile
        for player, tile in self.suppressed.items():
            if player not in current and not player._released:
                self._restore_host_audio(player, tile)
        self.suppressed = current

    def _restore_host_audio(self, player, old_tile):
        players = getattr(self.context.window, "players", {})
        owner = next((tile for tile, value in players.items() if value is player), old_tile)
        player.set_muted(bool(owner.muted))

    def _release_host_audio(self):
        for player, tile in self.suppressed.items():
            if not player._released:
                self._restore_host_audio(player, tile)
        self.suppressed.clear()

    @staticmethod
    def _stop_row(row):
        return source_control._stop_row(row)

    def stop(self):
        self.dismiss_buffer_notice()
        self.replay_bar.preview.stop()
        self.running = False
        if self.stream_info_dialog is not None:
            self.stream_info_dialog.close()
        with self._switch_stage('stop_playback_probe'):
            self.playback_probe.stop()
        self.independent_playback.clear()
        for floating in list(self.floating_crops.values()):
            floating.close()
        self.analysis_serial += 1
        self.late_buffer = LateBuffer(self.reserve_seconds)
        self.late_notice.hide()
        self.matching = False
        if self.fullscreen_dialog is not None:
            self.fullscreen_dialog.close()
        self._stop_recording()
        self.replay = ReplayTimeline()
        if self.multi_recorder is not None:
            self.multi_recorder.stop(wait=True)
        self.replay_bar.refresh(force=True)
        self.sync_waiting = False
        self.locked_clock = None
        self.offset_repositioning = False
        self.generation += 1
        self.render_timer.stop()
        self.match_timer.stop()
        self.compare.setChecked(False)
        with self._switch_stage('stop_audio'):
            self.audio.stop()
        for _card, _name, canvas, state in self.comparison_panel.cards.values():
            canvas.set_frame(None)
            canvas.waiting = True
            state.setText("已停止")
        with self._switch_stage('stop_sources'):
            for row in self.rows.values():
                self._stop_row(row)
                seed = self.sources.get(row.room_id) or {}
                if str(seed.get("url") or "").startswith(("http://", "https://")):
                    self.sources.pop(row.room_id, None)
        if not self.host_audio_timer.isActive():
            self._release_host_audio()
        self.panel.set_status("已停止")
        self.panel.count.set_connections(0)
        self.overlay_pending.clear()
        self.overlay_last = ""
        if self.video_danmaku is not None:
            self.video_danmaku.clear()
        self.canvas.set_frame(None)
        self._show_buffering(False)
        self._sync_sidebar_marks()

    def closeEvent(self, event):
        if self.embedded:
            self.reject()
            event.accept()
            return
        self.stop()
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.save()
        super().closeEvent(event)
        with self._switch_stage('restore_host_widgets'):
            self._restore_host_widgets()

    def reject(self):
        if self.embedded:
            self.stop()
            if self.save_timer.isActive():
                self.save_timer.stop()
                self.save()
            self.hide()
            with self._switch_stage('restore_host_widgets'):
                self._restore_host_widgets()
            self.finished.emit(0)
        else:
            super().reject()
