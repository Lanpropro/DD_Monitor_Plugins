"""Room controls and compact status presentation; callbacks stay on the session."""
from __future__ import annotations

import re
import time
from ddm.widgets import Avatar, DanmakuToggleButton, ROOM_MIME, RefreshButton, TileButton, VolumeButton
from .panels import ConsoleChannels, ConsoleChoice, ConsoleIconButton, console_hover_color, console_role_icon, native_console_button, use_link_cursor
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QSizePolicy, QSlider, QStyle, QToolTip, QVBoxLayout, QWidget
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPen, QPixmap, QRegion
from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QRectF, Qt
from ddm import config as config_module, motion, theme
from collections import deque


COLORS = ("#38bdf8", "#fb7185", "#a78bfa", "#4ade80", "#fbbf24", "#fb923c")


ROW_MIME = "application/x-ddm-match-sync-row"


class CompactStatus(QLabel):
    """One-line native icons with a separate tooltip for each status segment."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hints = []
        self.hit_regions = []
        self.detail = ''
        self.setTextFormat(Qt.PlainText)
        self.setMinimumHeight(22)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_status(self, parts, detail, hints=None):
        hints = hints if hints is not None else [text for _, text in parts]
        if (self.parts, self.detail, self.hints) == (parts, detail, hints):
            return
        self.parts = parts
        self.hints = hints
        self.hit_regions = []
        self.detail = detail
        self.setText(' · '.join(text for _, text in parts if text))
        self.setAccessibleName(' · '.join(text or ('已连接' if icon == 'connected' else '已静音')
                                         for icon, text in parts))
        self.setAccessibleDescription(detail)
        self.update()

    def tooltip_at(self, point):
        return next((hint for rect, hint in self.hit_regions if rect.contains(point)), '')

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            hint = self.tooltip_at(event.pos())
            if hint:
                QToolTip.showText(event.globalPos(), hint, self)
            else:
                QToolTip.hideText()
            event.accept()
            return True
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font())
        color = self.palette().windowText().color()
        metrics = painter.fontMetrics()
        size, x = 16, 0
        y = (self.height() - size) // 2
        self.hit_regions = []
        for (icon, text), hint in zip(self.parts, self.hints):
            if x + size > self.width():
                break
            start = x
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(Qt.NoBrush)
            if icon == 'connected':
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor('#4ade80'))
                painter.drawEllipse(QRectF(x + 4, y + 4, 8, 8))
            elif icon == 'search':
                painter.drawEllipse(QRectF(x + 2, y + 2, 8, 8))
                painter.drawLine(x + 9, y + 9, x + 14, y + 14)
            elif icon == 'buffer':
                painter.drawRoundedRect(QRectF(x + 1, y + 2, 14, 12), 1, 1)
                for offset in (5, 8, 11):
                    painter.drawLine(x + 4, y + offset, x + 12, y + offset)
            else:
                ratio = self.devicePixelRatioF()
                pixmap = self.style().standardIcon(icon).pixmap(round(size * ratio), round(size * ratio))
                pixmap.setDevicePixelRatio(ratio)
                tint = QPainter(pixmap)
                tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
                tint.fillRect(pixmap.rect(), color)
                tint.end()
                painter.drawPixmap(QRect(x, y, size, size), pixmap)
            x += size + 4
            if text:
                painter.setPen(color)
                shown = metrics.elidedText(text, Qt.ElideRight, max(0, self.width() - x))
                painter.drawText(QRect(x, 0, max(0, self.width() - x), self.height()), Qt.AlignVCenter, shown)
                x += metrics.horizontalAdvance(shown) + 12
                self.hit_regions.append((QRect(start, 0, x - start - 12, self.height()), hint))
                if shown != text:
                    break
            else:
                self.hit_regions.append((QRect(start, 0, size, self.height()), hint))
                x += 8


class RoomRow(QFrame):
    def __init__(self, viewer, room_id, preferences, color):
        super().__init__(viewer)
        self.viewer = viewer
        self.room_id = room_id
        self.setObjectName("MatchSyncRoomRow")
        self.anchor = None
        self.setAcceptDrops(True)
        self.platform = viewer.context.manager.platform_for(room_id)
        self.decoder = None
        self.chat = None
        self.paused = False
        self.sync_state = "准备缓存"
        self.mix_gain = 0.0
        self.recovery_since = None
        self.was_isolated = False
        self.actual_position = None
        self.picture_wait_since = None
        self.match_confirmed = False
        self.match_failures = 0
        self.alignment_started = time.monotonic()
        self.recheck_alignment = False
        self.rematch_locally = False
        self.user_confirmed = False
        self.user_confirmed_lag = None
        self.review_alignment = False
        self.awaiting_confirmation = False
        self.next_analysis = 0.0
        self.search_failures = 0
        self.large_candidate = None
        self.correction_remaining = 0.0
        self.reset_pending = False
        self.needs_epoch_samples = False
        self.alignment_hint = None
        self.hint_pending = False
        self.hint_serial = None
        self.hint_searching = False
        room = next((item for item in getattr(viewer.context.window, "rooms", [])
                     if str(item.get("room_id") or "") == room_id), {})
        self.title = room.get("title") or (viewer.sources.get(room_id) or {}).get("title") or ""
        self.quality = int(preferences.get("quality", 10000 if self.platform is not None else 250))
        self.quality_auto = bool(preferences.get('quality_auto', True))
        self.actual_quality = 0
        self.quality_options = []
        self.record_tracks = tuple(number for number in range(1, 7) if number in preferences.get('record_tracks', []))
        self.pending = deque(maxlen=2000)
        self.color = preferences.get("color", color)
        if self.color not in COLORS:
            self.color = color
        self.crop = tuple(preferences.get("crop", (0, 0, 1, 1)))
        self.match_text = "等待画面"
        self.video_text = "未开始"
        self.chat_text = "弹幕未连接"
        self.buffer_text = ""
        self.buffer_exhaustions = deque(maxlen=3)
        self.buffer_exhausted = False
        self.buffer_notice_at = None
        self.buffer_recovered_since = None
        self.alias = QLineEdit(preferences.get("alias", ""))
        self.alias.setParent(self)
        self.alias.hide()
        self.alias.setPlaceholderText("主播名称")
        self.alias.setFixedWidth(100)
        self.avatar = Avatar(self.label(), len(viewer.rows), size=32, parent=self)
        self.avatar_key = None
        self.setCursor(Qt.OpenHandCursor)
        self.refresh_avatar()
        self.main_button = QPushButton("主画面")
        self.main_button.setCheckable(True)
        self.main_button.setFixedWidth(96)
        self.main_button.setFixedHeight(28)
        self.main_button.setAutoDefault(False)
        use_link_cursor(self.main_button)
        self.main_button.setIcon(console_role_icon())
        self.main_button.setStyleSheet(f'''QPushButton {{background: transparent; border: 1px solid {theme.BORDER_STRONG};
            border-radius: 5px; outline: none; padding: 3px 6px; font-size: 12px;}}
            QPushButton:checked {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}
            QPushButton:hover, QPushButton:checked:hover {{background: {console_hover_color()}; color: {theme.ACCENT};}}''')
        self.main_button.setToolTip("选择这位主播作为主画面，其他路声音与弹幕继续播放。")
        self.main_button.clicked.connect(lambda: viewer.select_main(room_id))
        self.audible = native_console_button(VolumeButton(), '声音开关')
        self.audible.setCheckable(True)
        self.audible.setChecked(preferences.get("audible", True))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(preferences.get("volume", getattr(
            viewer.context.window, "settings", {}).get("default_volume", config_module.DEFAULT_VOLUME))))
        self.volume.setFixedWidth(72)
        use_link_cursor(self.volume)
        self.volume_number = QLabel(str(self.volume.value()))
        self.volume_number.setFixedWidth(26)
        self.volume_number.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.volume_number.setStyleSheet('font-size: 12px;')
        self.volume.valueChanged.connect(lambda value: self.volume_number.setText(str(value)))
        self.channel = ConsoleChannels()
        self.channel.setAccessibleName('输出声道')
        self.channel.setCurrentIndex(max(0, self.channel.findData(preferences.get("audio_channel", 0))))
        self.channel.setToolTip("点击 L / R 切换输出声道；两侧点亮为双声道，两侧关闭为静音。")
        self.channel.set_audible(self.audible.isChecked())
        self.channel.audibleChanged.connect(self.audible.setChecked)
        self.audible.toggled.connect(self.channel.set_audible)
        self.channel.currentIndexChanged.connect(viewer.changed)
        for signal in (self.channel.currentIndexChanged, self.volume.valueChanged, self.audible.toggled):
            signal.connect(self.refresh_audio_icon)
            signal.connect(viewer.sync_picture)
        self.audible.volumeChanged.connect(self.volume.setValue)
        self.refresh_audio_icon()
        self.delay = self._offset(0 if viewer.automatic.isChecked() else preferences.get("delay", 0))
        self.previous_delay = self.delay.value()
        self.delay.setFixedWidth(74)
        self.delay.installEventFilter(viewer)
        self.show_chat = native_console_button(DanmakuToggleButton('弹'), '弹幕开关')
        self.show_chat.setCheckable(True)
        self.show_chat.setChecked(preferences.get("show_chat", True))
        self.color_choice = ConsoleChoice('color')
        self.color_choice.setAccessibleName('弹幕来源颜色')
        for name, value in zip(("蓝色", "粉色", "紫色", "绿色", "黄色", "橙色"), COLORS):
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(value))
            self.color_choice.addItem(QIcon(swatch), name, value)
        self.color_choice.setCurrentIndex(self.color_choice.findData(self.color))
        self.color_choice.currentIndexChanged.connect(self._color)
        self.color_choice.setToolTip("弹幕来源颜色")
        self.decrease = QPushButton("−")
        self.increase = QPushButton("+")
        self.decrease.setToolTip("− 负数：相对提前本路画面、声音和弹幕。单位秒；必要时同时延后其他路。")
        self.increase.setToolTip("+ 正数：延后本路画面、声音和弹幕。单位秒，例如 +3 表示延后 3 秒。")
        for button in (self.decrease, self.increase):
            button.setFixedWidth(24)
            use_link_cursor(button)
        self.decrease.clicked.connect(lambda: viewer.show_comparison(self.room_id))
        self.increase.clicked.connect(lambda: viewer.show_comparison(self.room_id))
        self.decrease.clicked.connect(self.delay.stepDown)
        self.increase.clicked.connect(self.delay.stepUp)
        region = ConsoleIconButton('region', '选择对照区域', size=32)
        region.setToolTip("仅用于放大对照：框选计时器后手动微调偏移；自动匹配始终使用完整画面。")
        region.clicked.connect(lambda: viewer.choose_crop(self))
        floating = self.floating_button = ConsoleIconButton('floating', '裁取悬浮画面', size=32)
        floating.setToolTip('框选摄像头等区域，在软件内自由移动、缩放；双击悬浮画面可重新框选。')
        floating.clicked.connect(lambda: viewer.choose_crop(self, floating=True))
        self.independent = ConsoleIconButton('independent', '独立播放：不等待对齐，使用本路缓存进度', size=32)
        self.independent.setCheckable(True)
        self.independent.clicked.connect(lambda: viewer.toggle_independent(room_id))
        self.realign = native_console_button(RefreshButton(), '重新对齐')
        policy = self.realign.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.realign.setSizePolicy(policy)
        self.realign.setToolTip("重新匹配本路比赛画面，不重启直播流，不改变其他路已确认偏移。")
        self.realign.clicked.connect(lambda: viewer.retry_alignment(room_id))
        self.independent.setSizePolicy(policy)
        self.remove_button = native_console_button(TileButton('×'), '移除此路')
        self.remove_button.clicked.connect(lambda: viewer.remove_room_with_undo(room_id))
        self.assist = ConsoleIconButton('assist', '辅助自动对齐', size=32)
        self.assist.setCheckable(True)
        self.clock_button = ConsoleIconButton('clock', '计时器对齐', size=32)
        self.clock_button.setAccessibleDescription('框选各路游戏计时器并核对翻秒；请先播放至少两路')
        self.clock_button.clicked.connect(lambda: viewer.comparison_panel.open_clock())
        self.hint_fast = native_console_button(QPushButton('偏快'), '本路比主画面偏快')
        self.hint_slow = native_console_button(QPushButton('偏慢'), '本路比主画面偏慢')
        for button, direction in ((self.hint_fast, -1), (self.hint_slow, 1)):
            button.setFixedSize(44, 28)
            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(False)
            button.setSizePolicy(policy)
            button.hide()
            button.clicked.connect(lambda _checked=False, d=direction: viewer.submit_alignment_hint(room_id, d))
        self.assist.toggled.connect(self._toggle_hint_choices)
        self.clock_controls = QWidget()
        clock_layout = QHBoxLayout(self.clock_controls)
        clock_layout.setContentsMargins(0, 0, 0, 0)
        clock_layout.setSpacing(4)
        for button in (self.hint_fast, self.hint_slow, self.clock_button):
            clock_layout.addWidget(button)
        clock_layout.addStretch()
        self.clock_controls.setFixedWidth(44 * 2 + 32 + 4 * 2)
        self.join = QPushButton("确认对齐并加入")
        self.join.setAutoDefault(False)
        self.join.setFixedHeight(28)
        self.join.setStyleSheet(self.main_button.styleSheet())
        use_link_cursor(self.join)
        self.join.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.join.setToolTip("本路内容到达更晚；增加整组观看延迟后加入，原有各路保持相对同步。")
        self.join.clicked.connect(lambda: viewer.confirm_alignment(room_id))
        self.join.hide()
        self.name_label = QLabel()
        self.name_label.setMaximumWidth(140)
        self.name_label.setStyleSheet('font-size: 14px; font-weight: 600;')
        self.buffer_warning = CompactStatus()
        self.buffer_warning.setFixedSize(18, 18)
        self.buffer_warning.set_status([(QStyle.SP_MessageBoxWarning, '')],
            '短时间内多次耗尽缓冲。为获得更好的观看体验，建议切换主画面', [''])
        self.buffer_warning.setStyleSheet(f'color: {theme.ERROR};')
        self.buffer_warning.setAccessibleName('缓冲反复耗尽')
        self.buffer_warning.hide()
        self.status = CompactStatus()
        self.status.setStyleSheet(f'font-size: 12px; color: {theme.TEXT2};')
        identity = QWidget()
        identity_layout = QHBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(10)
        identity_layout.addWidget(self.avatar, 0, Qt.AlignVCenter)
        identity_text = QVBoxLayout()
        identity_text.setSpacing(2)
        identity_heading = QHBoxLayout()
        identity_heading.setSpacing(8)
        identity_heading.addWidget(self.name_label)
        identity_heading.addWidget(self.buffer_warning)
        identity_heading.addWidget(self.main_button)
        identity_heading.addWidget(self.join)
        identity_heading.addStretch()
        identity_text.addLayout(identity_heading)
        identity_text.addWidget(self.status)
        identity_layout.addLayout(identity_text)
        identity_layout.addStretch(1)
        self.offset_box = QFrame()
        self.offset_box.setObjectName('MatchSyncOffset')
        self.offset_box.setFixedSize(128, 32)
        self.offset_box.setStyleSheet(f'''QFrame#MatchSyncOffset {{background: {theme.ELEVATED};
            border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px;}}''')
        offset_layout = QHBoxLayout(self.offset_box)
        offset_layout.setContentsMargins(2, 0, 2, 0)
        offset_layout.setSpacing(0)
        self.delay.setFixedWidth(72)
        self.delay.setAlignment(Qt.AlignCenter)
        self.delay.setStyleSheet('QDoubleSpinBox {background: transparent; border: none; padding: 0; font-size: 12px;}')
        for step in (self.decrease, self.increase):
            step.setFixedHeight(30)
            step.setStyleSheet(f'''QPushButton {{background: transparent; border: none; padding: 0; border-radius: 0;}}
                QPushButton:hover {{background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};}}''')
        for widget in (self.decrease, self.delay, self.increase):
            offset_layout.addWidget(widget)
        groups = [identity]
        self.group_separators = []
        for index, widgets in enumerate(((floating, self.realign),
                        (self.independent, self.audible, self.volume, self.volume_number, self.channel),
                        (self.show_chat, self.color_choice),
                        (self.offset_box, self.assist, self.clock_controls), (region, self.remove_button)), 1):
            group = QWidget()
            line = QHBoxLayout(group)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(4)
            line.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if index in (2, 3, 4):
                separator = QFrame()
                separator.setFixedSize(1, 24)
                separator.setStyleSheet(f'background: {theme.BORDER_STRONG};')
                line.addWidget(separator)
                line.addSpacing(8)
                self.group_separators.append(separator)
            for widget in widgets:
                line.addWidget(widget)
                if isinstance(widget, QPushButton):
                    widget.setFocusPolicy(Qt.StrongFocus)
            groups.append(group)
        self.control_widgets = tuple(groups)
        self.control_columns = tuple(range(6))
        self.group_labels = [QLabel(text, self) for text in ('直播来源', '画面', '声音', '弹幕', '偏移', '其他')]
        for label in self.group_labels:
            label.setStyleSheet(f'color: {theme.TEXT3}; font-size: {theme.FONT_CAPTION}px;')
        self.controls_grid = QGridLayout()
        self.controls_grid.setHorizontalSpacing(12)
        self.controls_grid.setVerticalSpacing(5)
        self._compact_controls = None
        self.layout_controls(False, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.addLayout(self.controls_grid)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f'QFrame#MatchSyncRoomRow {{border: none; border-bottom: 1px solid {theme.BORDER_STRONG};}}')
        self.refresh_status()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.delay.setToolTip("单位秒；+ 延后本路，− 相对提前本路（必要时延后其他路）。画面、声音和弹幕一起移动。")
        self.delay.valueChanged.connect(viewer._delay_changed)
        self.volume.valueChanged.connect(viewer.changed)
        self.volume.valueChanged.connect(self.refresh_status)
        self.audible.toggled.connect(viewer.changed)
        self.audible.toggled.connect(self.refresh_status)
        self.show_chat.toggled.connect(self._chat_toggle)
        self.show_chat.toggled.connect(self.refresh_status)
        self.alias.textChanged.connect(viewer.changed)
        self.alias.textChanged.connect(self.refresh_avatar)
        self.alias.editingFinished.connect(viewer.update_main_choices)
        self.refresh_avatar()

    def layout_controls(self, compact, heading):
        for label in self.group_labels:
            label.setVisible(heading)
        if compact == self._compact_controls:
            return
        self._compact_controls = compact
        grid = self.controls_grid
        for widget in (*self.control_widgets, *self.group_labels):
            grid.removeWidget(widget)
        for column in range(6):
            grid.setColumnMinimumWidth(column, 0)
            grid.setColumnStretch(column, 0)
        positions = (((1, 0, 2), (3, 0, 1), (5, 0, 1), (5, 1, 1), (7, 0, 2), (3, 1, 1))
                     if compact else tuple((1, i, 1) for i in range(6)))
        for widget, label, (line, column, span) in zip(self.control_widgets, self.group_labels, positions):
            grid.addWidget(label, line - 1, column, 1, span)
            grid.addWidget(widget, line, column, 1, span)
        for column in range(2 if compact else 6):
            grid.setColumnStretch(column, 1 if compact or column in (0, 4) else 0)
        self.control_columns = tuple(column for _, column, _ in positions)

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.LayoutRequest and hasattr(self, 'control_columns'):
            self.viewer.align_room_controls()
        return result

    @staticmethod
    def _offset(value):
        spin = QDoubleSpinBox()
        spin.setRange(-60, 60)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        spin.setSuffix(" s")
        spin.setValue(value)
        return spin

    def refresh_avatar(self, *_args):
        if hasattr(self, 'name_label'):
            text = self.name_label.fontMetrics().elidedText(self.label(), Qt.ElideRight, 140)
            if self.name_label.text() != text:
                self.name_label.setText(text)
            if self.name_label.toolTip() != self.label():
                self.name_label.setToolTip(self.label())
        tooltip = f"{self.label()} · 拖动整张卡片调整顺序"
        if self.avatar.toolTip() != tooltip:
            self.avatar.setToolTip(tooltip)
        if self.toolTip() != tooltip:
            self.setToolTip(tooltip)
        sidebar = getattr(self.viewer.context.window, "sidebar", None)
        item = next((item for item in getattr(sidebar, "_items", [])
                     if str(item.room.get("room_id") or "") == self.room_id), None)
        pixmap = item.thumb.face_pixmap() if item is not None else None
        if pixmap is not None and not pixmap.isNull():
            if self.avatar_key != pixmap.cacheKey():
                self.avatar_key = pixmap.cacheKey()
                self.avatar.set_pixmap_image(pixmap)
        elif self.avatar_key is None and self.avatar.text() != self.label()[0]:
            self.avatar.setText(self.label()[0])

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.anchor = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.anchor is not None and event.buttons() & Qt.LeftButton
                and (event.position().toPoint() - self.anchor).manhattanLength() >= QApplication.startDragDistance()):
            anchor, self.anchor = self.anchor, None
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(ROW_MIME, self.room_id.encode("utf-8"))
            drag.setMimeData(mime)
            pixmap = QPixmap(self.size())
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(theme.qcolor(theme.ACCENT), 1))
            painter.setBrush(theme.qcolor(theme.ELEVATED))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 8, 8)
            painter.end()
            self.render(pixmap, QPoint(), QRegion(), QWidget.DrawChildren)
            pixmap, hotspot = motion.lifted_drag(pixmap, anchor)
            drag.setPixmap(pixmap)
            drag.setHotSpot(hotspot)
            self.viewer.begin_row_drag(self.room_id)
            try:
                drag.exec(Qt.MoveAction)
            finally:
                self.viewer.end_row_drag()
                self.setCursor(Qt.OpenHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.anchor = None
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        source = event.source()
        if isinstance(source, RoomRow) and source.viewer is self.viewer and event.mimeData().hasFormat(ROW_MIME):
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(ROOM_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)
        if isinstance(event.source(), RoomRow) and event.source().viewer is self.viewer:
            self.viewer.preview_row_drag(self.mapTo(self.viewer.controls.widget(), event.position().toPoint()))

    def dropEvent(self, event):
        source = event.source()
        if isinstance(source, RoomRow) and source.viewer is self.viewer and event.mimeData().hasFormat(ROW_MIME):
            if self.viewer.dragged_row is not None:
                self.viewer.finish_row_drag(self.mapTo(self.viewer.controls.widget(), event.position().toPoint()))
            else:
                self.viewer.reorder_room(source.room_id, self.room_id, event.position().y() >= self.height() / 2)
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(ROOM_MIME):
            self.viewer.dropEvent(event)
        else:
            event.ignore()

    def label(self):
        return self.alias.text().strip() or "未命名主播"

    def _color(self, *_args):
        self.color = self.color_choice.currentData()
        self.viewer.changed()

    def _chat_toggle(self):
        self.pending.clear()
        if self.viewer.running:
            self.viewer._start_chat(self)
        self.viewer.changed()

    def clear_buffer_warning(self):
        self.buffer_exhaustions.clear()
        self.buffer_exhausted = False
        self.buffer_notice_at = None
        self.buffer_recovered_since = None
        self.buffer_warning.hide()

    def refresh_audio_icon(self, *_args):
        self.audio_controls = (self.audible.isChecked(), self.channel.currentData(), self.volume.value())
        audible, channel, volume = self.audio_controls
        self.audible.set_state(not audible, volume, channel)

    def _toggle_hint_choices(self, expanded):
        for button in (self.hint_fast, self.hint_slow):
            button.setVisible(expanded)

    def preferences(self):
        return {"room_id": self.room_id, "alias": self.alias.text(), "color": self.color,
                "audible": self.audible.isChecked(), "volume": self.volume.value(),
                "audio_channel": self.channel.currentData(),
                "record_tracks": list(self.record_tracks),
                "quality": self.quality,
                "quality_auto": self.quality_auto,
                "delay": self.delay.value(),
                "show_chat": self.show_chat.isChecked(), "crop": list(self.crop)}

    def refresh_status(self):
        for button, label in ((self.audible, '声音'), (self.show_chat, '弹幕')):
            hint = ('关闭' if button.isChecked() else '开启') + label
            if button.toolTip() != hint:
                button.setToolTip(hint)
                button.setAccessibleName(hint)
        independent = self.room_id in self.viewer.independent_playback
        self.independent.setEnabled(self.room_id != self.viewer.alignment.reference)
        self.independent.setChecked(independent)
        self.independent.kind = 'resync' if independent else 'independent'
        self.independent.setToolTip('恢复自动对齐' if independent else '独立播放：不等待对齐，使用本路缓存进度')
        self.independent.setAccessibleName(self.independent.toolTip())
        self.independent.update()
        self.realign.setEnabled(self.viewer.alignment.automatic and self.room_id != self.viewer.alignment.reference)
        can_submit_hint = (self.viewer.running and self.viewer.alignment.automatic
                           and self.room_id != self.viewer.alignment.reference and not self.hint_pending
                           and not self.viewer.replay.active and not self.paused and not independent)
        hint = '辅助自动对齐：本路偏快 / 本路偏慢'
        if self.hint_pending:
            hint = '正在按你的判断完整重新搜索，完成后可再次提交'
        elif self.alignment_hint is not None:
            _, direction, weight = self.alignment_hint
            hint += f"（{'偏快' if direction < 0 else '偏慢'}，判断权重 {weight}）"
        self.assist.setEnabled(can_submit_hint)
        self.assist.setToolTip(hint)
        self.assist.setAccessibleName(hint)
        if not can_submit_hint:
            self.assist.setChecked(False)
        for button in (self.hint_fast, self.hint_slow):
            button.setEnabled(can_submit_hint)
            button.setToolTip(button.text() + '：' + hint)
        match_text = self.match_text
        clock_recovery = self.viewer.clock_recovery
        clock_pending = self.room_id in clock_recovery.pending
        if independent:
            match_text = "独立播放，不参与自动对齐"
        elif not self.viewer.alignment.automatic and not clock_pending:
            match_text = "自动对齐已关闭；手动偏移"
        elif self.room_id == self.viewer.alignment.reference:
            match_text = "主画面基准"
        text = f"{self.video_text} · {match_text} · {self.chat_text}"
        detail = text + f" · {self.playback_status()}" + (f" · {self.buffer_text}" if self.buffer_text else "")
        parts = []
        if '失败' in self.video_text or '缺少' in self.video_text or '找不到' in self.video_text:
            parts.append((QStyle.SP_MessageBoxWarning, '连接异常'))
        elif '超时' in self.video_text or '重新' in self.video_text:
            parts.append((QStyle.SP_BrowserReload, '重连中'))
        elif '已连接' in self.video_text:
            parts.append(('connected', ''))
        elif self.decoder is not None:
            parts.append((QStyle.SP_BrowserReload, '连接中'))
        if self.paused:
            parts.append((QStyle.SP_MediaPause, '已暂停'))
        elif self.decoder is None:
            parts.append((QStyle.SP_MediaStop, '已停止' if self.sync_state == '已停止' else '未开始'))
        elif clock_pending:
            parts.append((QStyle.SP_MessageBoxInformation,
                          '待确认计时器' if clock_recovery.failed else '计时器复核中'))
        elif not self.viewer.alignment.automatic:
            parts.append((QStyle.SP_MediaPlay, '手动偏移' if self.sync_state == '已同步' else '手动偏移 · 等待缓存'))
        elif self.room_id in self.viewer.independent_shifts and self.viewer.alignment.automatic:
            parts.extend(([(QStyle.SP_MediaPlay, '独立播放')] if independent else [('search', '搜索中'), (QStyle.SP_MediaPlay, '独立播放')]))
        elif self.awaiting_confirmation:
            parts.append((QStyle.SP_MessageBoxInformation, '待确认对齐'))
        elif self.sync_state == '已同步':
            if self.room_id == self.viewer.alignment.reference:
                parts.append((QStyle.SP_MediaPlay, '主画面'))
            elif self.correction_remaining > .1001:
                parts.append((QStyle.SP_BrowserReload, '平滑校正中'))
            else:
                parts.append((QStyle.SP_DialogApplyButton, '已对齐'))
        elif self.sync_state == '等待对齐':
            parts.append(('search', '搜索中'))
        else:
            parts.append((QStyle.SP_BrowserReload, '等待缓存'))
        if self.decoder is not None and (self.paused or not self.audible.isChecked()
                or self.volume.value() == 0 or self.sync_state != '已同步' or self.mix_gain == 0):
            parts.append((QStyle.SP_MediaVolumeMuted, ''))
        missing = re.search(r'缺少约 ([\d.]+) 秒', self.buffer_text)
        ahead = re.search(r'前方可播放约 ([\d.]+) 秒', self.buffer_text)
        if missing and self.room_id not in self.viewer.independent_shifts:
            parts.append((QStyle.SP_MessageBoxWarning, f'缺少 {missing[1]}s'))
        elif ahead and (not self.viewer.alignment.automatic or self.room_id == self.viewer.alignment.reference
                        or self.match_confirmed) and self.room_id not in self.viewer.independent_shifts:
            parts.append(('buffer', f'{ahead[1]}s'))
        hints = []
        for icon, label in parts:
            if icon == 'connected' or label in ('连接异常', '重连中', '连接中'):
                hints.append(self.video_text)
            elif icon == 'search' or label in ('已对齐', '待确认对齐', '待确认计时器', '计时器复核中'):
                hints.append(match_text)
            elif icon == 'buffer' or label.startswith('缺少 '):
                hints.append(self.buffer_text.split(' · ')[0])
            elif icon == QStyle.SP_MediaVolumeMuted:
                hints.append('已暂停，本路静音' if self.paused else '本路声音已关闭' if not self.audible.isChecked()
                             else '本路音量为 0' if self.volume.value() == 0 else '本路暂时静音，等待对齐或播放缓存恢复')
            elif label == '已暂停':
                hints.append('本路播放已暂停')
            elif '手动偏移' in label:
                hints.append(match_text)
            elif label == '主画面':
                hints.append('主画面：' + self.playback_status())
            else:
                hints.append(self.playback_status() if self.decoder is not None else label)
        self.status.set_status(parts, detail, hints)

    def playback_status(self):
        if self.room_id in self.viewer.independent_shifts and self.viewer.alignment.automatic:
            return "独立播放（未对齐，已静音）"
        if self.sync_state == "已同步":
            if self.room_id in self.viewer.independent_shifts:
                return "独立播放（未对齐）"
            if self.viewer.alignment.automatic and self.correction_remaining > .1001:
                return f"平滑校正中，剩余约 {self.correction_remaining:.2f} 秒"
            return "播放正常" if self.room_id == self.viewer.alignment.reference else (
                "已对齐" if self.viewer.alignment.automatic else "按当前偏移播放")
        return self.sync_state
