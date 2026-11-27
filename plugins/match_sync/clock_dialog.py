"""Timer sampling, human review and explicit application of playback offsets."""
import threading
import time

from PySide6.QtCore import QObject, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSizePolicy, QStyledItemDelegate, QStyle, QVBoxLayout, QWidget)

from ddm import theme
from .canvas import Canvas
from .clock_sync import (ClockReading, calculate_event_offsets, calculate_offsets, format_clock, parse_clock,
                         suggest_transition, valid_region)
from .clock_capture import EVENT_AFTER, EVENT_BEFORE, capture_band, capture_event_band, recognize_bands
from .panels import ConsoleIconButton, use_link_cursor


CLICK_FRAME_ROLE = Qt.UserRole


def settings_button(text, tooltip, *, primary=False, button=None):
    button = button or QPushButton()
    button.setText(text)
    button.setObjectName('PrimaryButton' if primary else 'IconButton')
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setFocusPolicy(Qt.StrongFocus)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setStyleSheet(f'QPushButton#{button.objectName()}:disabled {{ background: {theme.ELEVATED}; '
                         f'color: {theme.TEXT3}; border: 1px solid {theme.BORDER}; '
                         f'border-radius: {theme.CONTROL_HEIGHT // 2}px; }}')
    use_link_cursor(button)
    return button


class ClockTimeEdit(QWidget):
    """Minute/second input with a fixed colon between editable fields."""
    textChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ClockFirstFrame')
        self.minutes = QLineEdit()
        self.seconds = QLineEdit()
        for edit in (self.minutes, self.seconds):
            edit.setAlignment(Qt.AlignCenter)
            edit.setMaxLength(2)
            edit.setPlaceholderText('00')
            edit.setFixedSize(40, 32)
            edit.setObjectName('ClockTimePart')
            edit.textChanged.connect(self._changed)
        colon = QLabel(':')
        colon.setAlignment(Qt.AlignCenter)
        colon.setObjectName('ClockTimeColon')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.minutes)
        layout.addWidget(colon)
        layout.addWidget(self.seconds)

    def _changed(self, _text=''):
        self.textChanged.emit(self.text())

    def text(self):
        minutes, seconds = self.minutes.text().strip(), self.seconds.text().strip()
        if not minutes and not seconds:
            return ''
        return f'{minutes or "0"}:{seconds.zfill(2) if seconds else "00"}'

    def setText(self, value):
        value = str(value or '').strip()
        if ':' in value or '：' in value:
            minutes, seconds = value.replace('：', ':').split(':', 1)
        else:
            digits = ''.join(char for char in value if char.isdigit())
            minutes, seconds = (digits[:-2], digits[-2:]) if len(digits) >= 3 else ('', digits)
        self.minutes.setText(minutes[-3:])
        self.seconds.setText(seconds[:2])

    def clear(self):
        self.minutes.clear()
        self.seconds.clear()


class ClockResult(QObject):
    finished = Signal(object)


class ClockFrameDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        selected = bool(option.state & QStyle.State_Selected)
        marker = index.data(CLICK_FRAME_ROLE)
        marked = marker or index.row() == 0 and not self.parent().property('eventFrames')
        if selected or marked:
            painter.save()
            painter.setPen(QPen(theme.qcolor(theme.PINK if selected else theme.ACCENT), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2), 5, 5)
            if selected and marked:
                painter.setPen(QPen(theme.qcolor(theme.ACCENT), 2))
                painter.drawRoundedRect(option.rect.adjusted(5, 5, -5, -5), 4, 4)
            if marker:
                badge = QRectF(option.rect.left() + 8, option.rect.top() + 8,
                               option.fontMetrics.horizontalAdvance(marker) + 12, option.fontMetrics.height() + 4)
                painter.setPen(Qt.NoPen)
                painter.setBrush(theme.qcolor(theme.BG))
                painter.drawRoundedRect(badge, 4, 4)
                painter.setPen(theme.qcolor(theme.ACCENT))
                painter.drawText(badge, Qt.AlignCenter, marker)
            painter.restore()


def session_token(viewer, keys):
    # Cached media timestamps do not change when SafetyBuffer gently varies playback speed.
    if not viewer.running or viewer.replay.active:
        raise ValueError('请恢复直播播放后采样；暂停或回看时不能对时')
    token = [viewer.alignment.reference]
    for key in keys:
        row = viewer.rows.get(key)
        if row is None or row.decoder is None or row.paused:
            raise ValueError('所选直播间尚未开始播放或已暂停')
        token.append((key, row.decoder, row.decoder.history))
    return tuple(token)


def apply_offsets(viewer, desired, keys, *, event=False):
    """Reuse the viewer's manual offset state rather than create another clock."""
    current = dict(viewer.shifts())
    current.update({key: desired[key] for key in keys})
    desired = current
    if any(not row.delay.minimum() <= desired[viewer.alignment.reference] - desired[key] <= row.delay.maximum()
           for key, row in viewer.rows.items() if key in desired):
        raise ValueError('建议偏移超出当前控件范围，请检查回合和时间')
    viewer._save_delay_offsets(desired)
    viewer.automatic.setChecked(False)
    viewer.analysis_serial += 1
    viewer.matching = False
    viewer.locked_clock = None
    viewer.sync_waiting = False
    viewer.offset_repositioning = True
    viewer.rendered_shifts.clear()
    viewer.shift_reference = None
    viewer.generation += 1
    for key in keys:
        row = viewer.rows[key]
        row.review_alignment = False
        row.awaiting_confirmation = False
        row.user_confirmed = True
        row.match_text = ('赛事事件' if event else '计时器') + '偏移已确认；手动锁定'
        row.refresh_status()
    viewer.changed()
    viewer.render()


class ClockLane(QWidget):
    def __init__(self, dialog, key, row):
        super().__init__()
        self.dialog, self.key = dialog, key
        self.frames = []
        self.click_frame_index = None
        self.preview = None
        self.region = None
        self.load_region()
        self.display_name = row.label() + ('（主画面）' if key == dialog.reference else '')
        avatar = QLabel()
        avatar.setFixedSize(48, 48)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        try:
            pixmap = row.avatar.grab().scaled(44, 44, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            avatar.setPixmap(pixmap)
        except (AttributeError, RuntimeError):
            avatar.setText((row.label() or '?')[0])
        avatar.setStyleSheet('border-radius: 24px;')
        self.avatar = avatar
        self.pick = ConsoleIconButton('region', '框选游戏计时器区域', size=32)
        self.pick.setEnabled(False)
        self.pick.clicked.connect(self.choose_region)
        self.first = ClockTimeEdit()
        self.first.setAccessibleName(row.label() + '第一帧计时器读数')
        self.status = QLabel('请框选游戏计时器区域' if not self.region else '已记住计时器区域，可重新框选')
        self.setObjectName('MatchSyncClockLane')
        self.status.setObjectName('ClockLaneStatus')
        self.status.setWordWrap(True)
        self.strip = QListWidget()
        self.strip.setViewMode(QListView.IconMode)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(128, 70))
        self.strip.setGridSize(QSize(140, 88))
        self.strip.setItemDelegate(ClockFrameDelegate(self.strip))
        self.strip.setObjectName('ClockFrameStrip')
        self.strip.setFixedHeight(102)
        self.strip.setAccessibleName(row.label() + '翻秒帧：选择数字首次变化后的画面')
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        identity = QWidget()
        identity.setFixedWidth(60)
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(4)
        identity_layout.addWidget(self.avatar, 0, Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(identity, 0, 0, Qt.AlignTop)
        layout.addWidget(self.strip, 0, 1)
        layout.addWidget(self.pick, 0, 2, Qt.AlignTop)
        first_group = QWidget()
        first_layout = QVBoxLayout(first_group)
        first_layout.setContentsMargins(0, 0, 0, 0)
        first_layout.setSpacing(2)
        first_label = QLabel('首帧时间')
        first_label.setObjectName('ClockTimeLabel')
        first_layout.addWidget(first_label)
        first_layout.addWidget(self.first)
        self.first_group = first_group
        layout.addWidget(first_group, 0, 3, Qt.AlignTop)
        layout.addWidget(self.status, 1, 0, 1, 4)
        layout.setColumnStretch(1, 1)
        self.setMinimumHeight(142)
        self.setMaximumHeight(170)
        for signal in (self.first.textChanged,):
            signal.connect(dialog.invalidate)
        self.strip.currentRowChanged.connect(self.select_transition)

    def select_transition(self, index):
        self.dialog.invalidate()
        if index >= 0 and self.frames:
            if self.dialog.is_event_mode():
                if index == 0:
                    self.status.setText('需要一帧变化前的画面，请选稍后的事件或重新取帧')
                else:
                    anchor = self.dialog.anchors[self.key]
                    before, after = self.frames[index - 1][0] - anchor, self.frames[index][0] - anchor
                    self.status.setText(f'{self.display_name} · 变化边界 {before:+.2f}s 至 {after:+.2f}s · 请核对相邻两帧')
            else:
                self.status.setText(f'已选第 {index + 1} 帧为首次翻秒后的画面；请核对数字刚刚变化')

    def show_preview(self):
        self.click_frame_index = None
        self.strip.clear()
        if self.preview is None:
            return
        image = self.preview[1]
        if self.region is not None:
            x, y, width, height = self.region
            image = image.copy(round(x * image.width()), round(y * image.height()),
                               max(1, round(width * image.width())), max(1, round(height * image.height())))
            # QIcon limits thumbnail size but does not enlarge a small crop on its own.
            scale = self.strip.devicePixelRatioF()
            image = image.scaled(self.strip.iconSize() * scale, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            image.setDevicePixelRatio(scale)
        label = '赛事选区' if self.dialog.is_event_mode() else '计时器选区'
        item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), label if self.region else '画面预览')
        item.setSizeHint(QSize(160, 90))
        self.strip.addItem(item)

    def set_preview(self, item):
        stamp, image = item
        if image.width() > 640:
            image = image.scaled(640, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview = (stamp, image)
        self.pick.setEnabled(not self.dialog.busy)
        self.frames = []
        self.first.clear()
        self.show_preview()
        if self.dialog.is_event_mode():
            self.status.setText('可框选圈数、名次或圈速变化所在区域')
        else:
            self.status.setText('已获取画面，请框选游戏计时器区域' if self.region is None else '已显示计时器选区，可重新框选或采样')

    def region_setting(self):
        return 'event_regions' if self.dialog.is_event_mode() else 'clock_regions'

    def load_region(self):
        saved = self.dialog.viewer.context.setting(self.region_setting(), {}).get(self.key, ())
        self.region = tuple(saved) if valid_region(saved) else None

    def choose_region(self):
        if self.preview is None:
            self.dialog.status.setText('当前没有可框选画面，请点击获取画面')
            return
        picker = QDialog(self.dialog)
        picker.setWindowTitle(('框选赛事区域 · ' if self.dialog.is_event_mode() else '框选游戏计时器 · ') + self.display_name)
        picker.resize(1000, 650)
        canvas = Canvas(selecting=True)
        canvas.set_frame(self.preview)
        canvas.crop = QRectF(*(self.region or (0, 0, 1, 1)))
        canvas.selection_ready = self.region is not None
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(picker.accept)
        buttons.rejected.connect(picker.reject)
        layout = QVBoxLayout(picker)
        layout.addWidget(QLabel('Ctrl＋滚轮：缩放　滚轮：上下平移　Shift＋滚轮：左右平移'))
        layout.addWidget(canvas, 1)
        layout.addWidget(buttons)
        if picker.exec() == QDialog.DialogCode.Accepted:
            crop = canvas.crop
            self.region = (crop.x(), crop.y(), crop.width(), crop.height())
            if valid_region(self.region):
                saved = dict(self.dialog.viewer.context.setting(self.region_setting(), {}))
                saved[self.key] = self.region
                self.dialog.viewer.context.set_setting(self.region_setting(), saved)
                self.frames = []
                self.first.clear()
                self.show_preview()
                self.dialog.sample_token = None
                self.status.setText('区域已保存，请重新采样')
                self.dialog.invalidate()

    def set_band(self, frames, readings, direction):
        self.frames = frames
        self.click_frame_index = None
        self.strip.clear()
        if direction == 0:
            anchor = self.dialog.anchors[self.key]
            self.click_frame_index = min(range(len(frames)), key=lambda index: abs(frames[index][0] - anchor))
            for index, (stamp, image) in enumerate(frames):
                delta = stamp - anchor
                exact = abs(delta) <= .001
                text = '0.00s' if exact else f'{delta:+.2f}s'
                item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), text)
                if index == self.click_frame_index:
                    marker = '点击帧' if exact else '距点击最近'
                    item.setData(CLICK_FRAME_ROLE, marker)
                    item.setData(Qt.AccessibleTextRole, f'{marker} · {text}')
                    item.setToolTip(f'{marker} · {text}')
                self.strip.addItem(item)
            QTimer.singleShot(0, self, self.center_on_click)
            first, last = frames[0][0] - anchor, frames[-1][0] - anchor
            self.status.setText(f'{self.display_name} · 已取帧 {first:+.2f}s 至 {last:+.2f}s · 选择变化刚出现的画面')
            return
        for index, (_stamp, image) in enumerate(frames):
            value = readings[index] if index < len(readings) else None
            label = ('第一帧 ' if index == 0 else f'{index + 1} · ') + (format_clock(value) if value is not None else '未识别')
            item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), label)
            item.setSizeHint(QSize(160, 90))
            if index == 0:
                item.setBackground(QColor(theme.ACCENT_SOFT))
            self.strip.addItem(item)
        self.first.setText(format_clock(readings[0]) if readings and readings[0] is not None else '')
        found = suggest_transition([stamp for stamp, _image in frames], readings, direction)
        self.strip.setCurrentRow(found if found is not None else -1)
        self.status.setText('已建议翻秒帧，请核对计时器数字后计算' if found is not None else '请填写首帧时间，并点击第一次翻秒后的画面')

    def center_on_click(self):
        if self.click_frame_index is not None:
            # Flush pending IconMode layout; insertion can otherwise scroll back to its end.
            self.strip.doItemsLayout()
            self.strip.scrollToItem(self.strip.item(self.click_frame_index), QAbstractItemView.PositionAtCenter)


class ClockDialog(QDialog):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.reference = viewer.alignment.reference
        self.cancelled = threading.Event()
        self.busy = False
        self.sample_token = None
        self.proposal = None
        self.setObjectName('MatchSyncClockDialog')
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self._drag_offset = None
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty('settingsGlassWindow', False)
        self.setProperty('settings_glass_enabled', False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(f'''
            #MatchSyncClockDialog {{ background: transparent; border: none; }}
            #ClockDialogToolbar, #ClockDialogFooter {{
                background: transparent; border: none;
            }}
            #ClockDialogToolbar QComboBox, #ClockFirstFrame QLineEdit {{
                background: {theme.CONTENT}; color: {theme.TEXT1};
                border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_MD}px; padding: 5px 8px;
            }}
            #ClockTimeColon, #ClockTimeLabel {{ color: {theme.TEXT2}; background: transparent; }}
            #MatchSyncClockLane {{
                background: {theme.CONTENT}; border: 1px solid {theme.BORDER};
                border-radius: {theme.RADIUS_MD}px;
            }}
            #ClockFrameStrip {{
                background: {theme.ELEVATED}; border: 1px solid {theme.BORDER};
                border-radius: {theme.RADIUS_MD}px; color: {theme.TEXT2};
            }}
            #ClockLaneStatus, #ClockDialogStatus {{ color: {theme.TEXT2}; background: transparent; }}
            #ClockDialogBody {{ background: transparent; }}
        ''')
        self.setWindowTitle('计时器辅助对齐')
        self.resize(1060, 760)
        self.setMinimumSize(820, 580)
        self.mode = QComboBox()
        self.mode.setObjectName('ClockMode')
        self.mode.setFixedWidth(132)
        if getattr(viewer, 'race_event_alignment', False):
            self.mode.addItem('赛事事件', 0)
        self.mode.addItem('倒计时', -1)
        self.mode.addItem('正计时', 1)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.refresh = settings_button('获取画面', '从现有缓存获取画面')
        self.refresh.clicked.connect(self.refresh_previews)
        self.sample = settings_button('采样并识别', '采样计时器并本地识别', primary=True)
        self.sample.clicked.connect(lambda: self.start_job(preview=False))
        self.jump = settings_button('回到点击帧', '定位到各路标记的点击帧')
        self.jump.clicked.connect(self.center_on_click)
        header = QFrame()
        header.setObjectName('ClockDialogToolbar')
        heading = QHBoxLayout(header)
        heading.setContentsMargins(12, 10, 12, 10)
        heading.setSpacing(10)
        heading.addWidget(self.mode)
        heading.addStretch()
        heading.addWidget(self.jump)
        heading.addWidget(self.refresh)
        heading.addWidget(self.sample)
        body = QWidget()
        body.setObjectName('ClockDialogBody')
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(10)
        self.lanes = {key: ClockLane(self, key, row) for key, row in viewer.rows.items()
                      if row.decoder is not None}
        for lane in self.lanes.values():
            body_layout.addWidget(lane)
        body_layout.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setObjectName('ClockDialogScroll')
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(body)
        self.status = QLabel('各路请选择同一局、同一回合的游戏计时器；主播电脑时钟不能用来对齐游戏。')
        self.status.setObjectName('ClockDialogStatus')
        self.status.setWordWrap(True)
        self.calculate = settings_button('计算偏移', '根据已核对的计时器计算偏移')
        self.calculate.clicked.connect(self.calculate_proposal)
        self.apply = settings_button('确认应用', '确认偏移并手动锁定', primary=True)
        self.apply.setEnabled(False)
        self.apply.clicked.connect(self.apply_proposal)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        settings_button('关闭', '关闭计时器对齐', button=close.button(QDialogButtonBox.Close))
        close.rejected.connect(self.reject)
        footer = QFrame()
        footer.setObjectName('ClockDialogFooter')
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(12, 8, 12, 8)
        actions.setSpacing(10)
        actions.addWidget(self.calculate)
        actions.addStretch()
        actions.addWidget(self.apply)
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(header)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.status)
        layout.addWidget(footer)
        self.watch = QTimer(self)
        self.watch.setInterval(250)
        self.watch.timeout.connect(self.check_session)
        self.watch.start()
        self.mode_changed()
        self.populate_current_previews()
        QTimer.singleShot(0, self, lambda: self.start_job(preview=True, sample_event=self.is_event_mode()))
        self.finished.connect(lambda: self.cancelled.set())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        painter.fillPath(path, theme.qcolor(theme.BG))
        painter.setPen(QPen(theme.qcolor(theme.BORDER_STRONG), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() <= 58:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def current_preview(self, key):
        """Reuse a frame already displayed by the main or comparison canvas."""
        if key == self.reference:
            frame = getattr(self.viewer.canvas, 'frame', None)
        else:
            card = self.viewer.comparison_panel.cards.get(key)
            frame = card[2].frame if card is not None else None
            if self.is_event_mode() and (card is None or not card[2].isVisible()):
                floating = next((item for item in self.viewer.floating_crops.get(key, {}).values()
                                 if item.isVisible() and item.frame_key is not None), None)
                return (floating.frame_key, floating.image) if floating is not None else None
        if frame is None or not hasattr(frame, '__len__') or len(frame) != 2:
            return None
        return frame

    def populate_current_previews(self):
        for key, lane in self.lanes.items():
            frame = self.current_preview(key)
            if frame is not None:
                lane.set_preview(frame)

    def selected_keys(self):
        return tuple(self.lanes)

    def is_event_mode(self):
        return self.mode.currentData() == 0

    def pin_event(self):
        if not self.is_event_mode():
            return
        clock = self.viewer.live_clock()
        # Use the displayed main frame as click time, including during a short buffer wait.
        shifts = self.viewer.shifts()
        main = self.current_preview(self.reference)
        if main is not None:
            clock = main[0] - shifts[self.reference]
        self.event_anchors = {key: clock + shifts[key] for key in self.lanes if key in shifts}
        for key in self.event_anchors:
            if key == self.reference:
                continue
            frame = self.current_preview(key)
            if frame is not None:
                self.event_anchors[key] = frame[0]

    def refresh_previews(self):
        self.pin_event()
        self.start_job(preview=True)

    def center_on_click(self):
        for lane in self.lanes.values():
            lane.center_on_click()

    def mode_changed(self, *_args):
        event_mode = self.is_event_mode()
        for lane in self.lanes.values():
            lane.load_region()
            lane.first_group.setVisible(not event_mode)
            lane.strip.setProperty('eventFrames', event_mode)
            lane.pick.setToolTip('框选圈数、排名或圈速区域' if event_mode else '框选游戏计时器区域')
            lane.strip.setAccessibleName(lane.display_name + ('：选择事件变化后第一帧' if event_mode else '：选择首次翻秒帧'))
        self.sample.setText('赛事取帧' if event_mode else '采样并识别')
        self.jump.setVisible(event_mode)
        self.sample.setToolTip(f'取点击前 {EVENT_BEFORE:g} 秒、后 {EVENT_AFTER:g} 秒的画面' if event_mode else '采样计时器并本地识别')
        self.clear_bands()
        if event_mode:
            self.pin_event()
            self.status.setText('已标记点击位置。请选两路共同的圈数、排名或圈速变化，可先各自框选对应区域。')
        else:
            self.status.setText('请框选各路相同的游戏计时器，然后采样和核对首次翻秒。')

    def invalidate(self, *_args):
        self.proposal = None
        self.apply.setEnabled(False)

    def clear_bands(self):
        for lane in self.lanes.values():
            lane.frames = []
            lane.first.clear()
            lane.show_preview()
        self.sample_token = None
        self.jump.setEnabled(False)
        self.invalidate()

    def check_session(self):
        if not self.viewer.running:
            self.cancelled.set()
            self.reject()
            return
        if self.sample_token is not None:
            try:
                self.validate_sample()
            except ValueError as error:
                self.invalidate()
                self.sample_token = None
                self.status.setText(str(error))

    def validate_sample(self):
        if self.sample_token is None:
            raise ValueError('请先采样计时器画面')
        if session_token(self.viewer, self.sample_keys) != self.sample_token:
            raise ValueError('直播间、主画面或来源已变化，请重新采样')
        if self.selected_keys() != self.sample_keys:
            raise ValueError('参与对齐的直播间已变化，请重新采样')
        shifts = self.viewer.shifts()
        if not self.is_event_mode() and any(abs(shifts[key] - self.sample_shifts[key]) > .05 for key in self.sample_keys):
            raise ValueError('播放偏移已变化，请重新采样')

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.refresh, self.sample, self.calculate, self.mode):
            widget.setEnabled(not busy)
        for lane in self.lanes.values():
            lane.pick.setEnabled(not busy and lane.preview is not None)
        self.jump.setEnabled(not busy and any(lane.click_frame_index is not None for lane in self.lanes.values()))

    def start_job(self, *, preview, sample_event=False):
        if self.busy or self.cancelled.is_set():
            return
        self.invalidate()
        keys = self.selected_keys()
        event_mode = self.is_event_mode()
        try:
            token = session_token(self.viewer, keys)
            if self.reference not in keys or len(keys) < 2:
                raise ValueError('至少需要主画面和一路副画面')
            if not preview and not event_mode and any(self.lanes[key].region is None for key in keys):
                raise ValueError('请先分别框选各路游戏计时器')
            clock, shifts = self.viewer.live_clock(), dict(self.viewer.shifts())
            requests = [(key, self.viewer.rows[key].decoder.history,
                         self.event_anchors.get(key, clock + shifts[key]) if event_mode else clock + shifts[key],
                         self.lanes[key].region or (0, 0, 1, 1)) for key in keys]
        except ValueError as error:
            self.status.setText(str(error))
            return
        current = {key: self.current_preview(key) for key in keys} if preview else {}
        missing = tuple(key for key in keys if current.get(key) is None)
        self.set_busy(True)
        self.status.setText('正在从缓存取帧…' if preview else '正在等待并截取事件前后的画面…'
                            if event_mode else '正在采样与本地识别；直播继续播放…')
        bridge = ClockResult()
        self.bridge = bridge
        cancelled = self.cancelled
        deadline = time.monotonic() + EVENT_AFTER
        def work():
            try:
                bands = {key: [item] for key, item in current.items() if item is not None}
                for key, history, anchor, region in requests:
                    if key not in missing:
                        continue
                    if event_mode and not preview:
                        bands[key] = capture_event_band(history, anchor, region, cancelled, deadline=deadline)
                    else:
                        try:
                            bands[key] = capture_band(history, anchor, region, cancelled, preview=preview)
                        except ValueError:
                            if not (event_mode and sample_event):
                                raise
                if cancelled.is_set():
                    return
                readings, note = ({}, '') if preview or event_mode else recognize_bands(bands, cancelled)
                result = (bands, readings, note, '')
            except Exception as error:
                message = str(error) if isinstance(error, ValueError) else f'取帧或识别失败：{type(error).__name__}；请重新采样'
                result = ({}, {}, '', message)
            if not cancelled.is_set():
                bridge.finished.emit(result)
        def ready(result):
            if cancelled.is_set():
                return
            self.set_busy(False)
            bands, readings, note, error = result
            try:
                if session_token(self.viewer, keys) != token:
                    raise ValueError('来源已变化，请重新获取')
                if error:
                    raise ValueError(error)
                if preview:
                    self.sample_token = None
                    for key, frames in bands.items():
                        self.lanes[key].set_preview(frames[-1])
                    self.status.setText('已标记位置，可框选区域并点击「赛事取帧」' if event_mode
                                        else '画面已准备，请框选各路相同的游戏计时器')
                    if event_mode and sample_event:
                        self.start_job(preview=False)
                else:
                    self.sample_token, self.sample_keys = token, keys
                    self.sample_shifts = shifts
                    self.anchors = {key: anchor for key, _history, anchor, _region in requests}
                    for key, frames in bands.items():
                        self.lanes[key].set_band(frames, readings.get(key, [None] * len(frames)), self.mode.currentData())
                    self.jump.setEnabled(event_mode)
                    self.status.setText('请选择两路同一变化刚出现的帧，再计算偏移。可用方向键逐帧核对。'
                                        if event_mode else note or '请核对第一帧时间与首次翻秒帧，再计算偏移；建议不等于自动确认。')
            except ValueError as error:
                self.status.setText(str(error))
        bridge.finished.connect(ready)
        threading.Thread(target=work, name='match-clock-sample', daemon=True).start()

    def calculate_proposal(self):
        self.invalidate()
        try:
            self.validate_sample()
            readings = {}
            for key in self.sample_keys:
                lane = self.lanes[key]
                if self.is_event_mode():
                    index = lane.strip.currentRow()
                    if index > 0:
                        readings[key] = (lane.frames[index - 1][0], lane.frames[index][0])
                    continue
                value = parse_clock(lane.first.text())
                if value is None:
                    raise ValueError('请填写各路第一帧时间，例如 0130 或 01:30')
                readings[key] = ClockReading(value, tuple(stamp for stamp, _image in lane.frames),
                                             lane.strip.currentRow(), self.anchors[key])
            if self.is_event_mode():
                desired, errors = calculate_event_offsets(readings, self.viewer.shifts(), self.reference)
            else:
                desired, errors = calculate_offsets(readings, self.sample_shifts, self.reference, self.mode.currentData())
            self.reviewed_readings = readings
            self.proposal = desired
            labels = []
            for key in readings:
                if key != self.reference:
                    delay = desired[self.reference] - desired[key]
                    labels.append(f'{self.lanes[key].display_name}：偏移 {delay:+.2f}s，采样边界误差约 ±{errors[key]:.2f}s')
                    self.lanes[key].status.setText(
                        f'{self.lanes[key].display_name} · 建议偏移 {delay:+.2f}s · 误差约 ±{errors[key]:.2f}s')
            self.status.setText('；'.join(labels) + '。确认应用后自动打开对照微调，仍可逐路继续调整。')
            self.apply.setEnabled(True)
        except ValueError as error:
            self.status.setText(str(error))

    def apply_proposal(self):
        try:
            self.validate_sample()
            if self.proposal is None:
                return
            apply_offsets(self.viewer, self.proposal, self.reviewed_readings, event=self.is_event_mode())
            if self.is_event_mode():
                self.viewer.clock_recovery.cancel()
            else:
                self.viewer.clock_recovery.arm(self.reviewed_readings,
                    {key: self.lanes[key].region for key in self.sample_keys}, self.mode.currentData())
        except ValueError as error:
            self.status.setText(str(error))
            return
        secondary = next((key for key in self.reviewed_readings if key != self.reference), None)
        self.viewer.compare.setChecked(True)
        if secondary is not None:
            self.viewer.show_comparison(secondary)
        self.accept()


def open_clock_dialog(viewer):
    if not viewer.running or sum(row.decoder is not None for row in viewer.rows.values()) < 2:
        viewer.notice.setText('请先播放主画面和至少一路副画面，再使用计时器对齐')
        return
    dialog = getattr(viewer, 'clock_dialog', None)
    if dialog is not None and dialog.isVisible():
        dialog.raise_()
        return
    dialog = ClockDialog(viewer)
    viewer.clock_dialog = dialog
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.finished.connect(lambda: setattr(viewer, 'clock_dialog', None))
    dialog.show()
