"""Timer sampling, human review and explicit application of playback offsets."""
import threading

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QStyledItemDelegate, QStyle, QVBoxLayout, QWidget)

from ddm import theme
from .canvas import Canvas
from .clock_sync import (ClockReading, calculate_offsets, format_clock, parse_clock,
                         suggest_transition, valid_region)
from .clock_capture import capture_band, recognize_bands


class ClockResult(QObject):
    finished = Signal(object)


class ClockFrameDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        selected = bool(option.state & QStyle.State_Selected)
        if selected or index.row() == 0:
            painter.save()
            painter.setPen(QPen(theme.qcolor(theme.PINK if selected else theme.ACCENT), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2), 5, 5)
            painter.restore()


def session_token(viewer, keys):
    if not viewer.running or viewer.replay.active or viewer.playback_rate != 1:
        raise ValueError('请恢复正常直播播放后采样；暂停、回看或变速时不能对时')
    token = [viewer.alignment.reference]
    for key in keys:
        row = viewer.rows.get(key)
        if row is None or row.decoder is None or row.paused:
            raise ValueError('所选直播间尚未开始播放或已暂停')
        token.append((key, row.decoder, row.decoder.history))
    return tuple(token)


def apply_offsets(viewer, desired, keys):
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
        row.match_text = '计时器偏移已确认；手动锁定'
        row.refresh_status()
    viewer.changed()
    viewer.render()


class ClockLane(QWidget):
    def __init__(self, dialog, key, row):
        super().__init__()
        self.dialog, self.key = dialog, key
        self.frames = []
        self.preview = None
        saved = dialog.viewer.context.setting('clock_regions', {}).get(key, ())
        self.region = tuple(saved) if valid_region(saved) else None
        self.included = QCheckBox(row.label() + ('（主画面）' if key == dialog.reference else ''))
        self.included.setChecked(True)
        self.included.setEnabled(key != dialog.reference)
        self.pick = QPushButton('框选计时器')
        self.pick.clicked.connect(self.choose_region)
        self.first = QLineEdit()
        self.first.setPlaceholderText('第一帧：分:秒')
        self.first.setFixedWidth(130)
        self.first.setAccessibleName(row.label() + '第一帧计时器读数')
        heading = QHBoxLayout()
        heading.addWidget(self.included, 1)
        heading.addWidget(self.pick)
        heading.addWidget(self.first)
        self.status = QLabel('请分别框选同一个游戏计时器' if not self.region else '已记住计时器区域')
        self.status.setWordWrap(True)
        self.strip = QListWidget()
        self.strip.setViewMode(QListView.IconMode)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(150, 70))
        self.strip.setGridSize(QSize(166, 96))
        self.strip.setItemDelegate(ClockFrameDelegate(self.strip))
        self.strip.setFixedHeight(115)
        self.strip.setAccessibleName(row.label() + '翻秒帧：选择数字首次变化后的画面')
        layout = QVBoxLayout(self)
        layout.addLayout(heading)
        layout.addWidget(self.strip)
        layout.addWidget(self.status)
        for signal in (self.first.textChanged, self.included.toggled):
            signal.connect(dialog.invalidate)
        self.strip.currentRowChanged.connect(self.select_transition)

    def select_transition(self, index):
        self.dialog.invalidate()
        if index >= 0:
            self.status.setText(f'已选第 {index + 1} 帧为首次翻秒后的画面；请核对数字刚刚变化')

    def choose_region(self):
        if self.preview is None:
            self.dialog.status.setText('当前没有可框选画面，请点击获取画面')
            return
        picker = QDialog(self.dialog)
        picker.setWindowTitle('框选游戏计时器 · ' + self.included.text())
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
                saved = dict(self.dialog.viewer.context.setting('clock_regions', {}))
                saved[self.key] = self.region
                self.dialog.viewer.context.set_setting('clock_regions', saved)
                self.frames = []
                self.strip.clear()
                self.status.setText('区域已保存，请重新采样')
                self.dialog.invalidate()

    def set_band(self, frames, readings, direction):
        self.frames = frames
        self.strip.clear()
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
        self.status.setText('已建议翻秒帧，请核对后计算' if found is not None else '请填写第一帧时间，并点击第一次翻秒后的画面')


class ClockDialog(QDialog):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.reference = viewer.alignment.reference
        self.cancelled = threading.Event()
        self.busy = False
        self.sample_token = None
        self.proposal = None
        self.setWindowTitle('计时器辅助对齐')
        self.resize(1060, 760)
        self.setMinimumSize(820, 580)
        self.mode = QComboBox()
        self.mode.addItem('倒计时', -1)
        self.mode.addItem('正计时', 1)
        self.mode.currentIndexChanged.connect(self.clear_bands)
        self.refresh = QPushButton('获取画面')
        self.refresh.clicked.connect(lambda: self.start_job(preview=True))
        self.sample = QPushButton('采样并识别')
        self.sample.clicked.connect(lambda: self.start_job(preview=False))
        heading = QHBoxLayout()
        heading.addWidget(self.mode)
        heading.addStretch()
        heading.addWidget(self.refresh)
        heading.addWidget(self.sample)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        self.lanes = {key: ClockLane(self, key, row) for key, row in viewer.rows.items()
                      if row.decoder is not None}
        for lane in self.lanes.values():
            body_layout.addWidget(lane)
        body_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.status = QLabel('各路请选择同一局、同一回合的游戏计时器；主播电脑时钟不能用来对齐游戏。')
        self.status.setWordWrap(True)
        self.calculate = QPushButton('计算偏移')
        self.calculate.clicked.connect(self.calculate_proposal)
        self.apply = QPushButton('确认应用')
        self.apply.setEnabled(False)
        self.apply.clicked.connect(self.apply_proposal)
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        actions = QHBoxLayout()
        actions.addWidget(self.calculate)
        actions.addStretch()
        actions.addWidget(self.apply)
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addLayout(heading)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.status)
        layout.addLayout(actions)
        self.watch = QTimer(self)
        self.watch.setInterval(250)
        self.watch.timeout.connect(self.check_session)
        self.watch.start()
        QTimer.singleShot(0, lambda: self.start_job(preview=True))
        self.finished.connect(lambda: self.cancelled.set())

    def selected_keys(self):
        return tuple(key for key, lane in self.lanes.items() if lane.included.isChecked())

    def invalidate(self, *_args):
        self.proposal = None
        self.apply.setEnabled(False)

    def clear_bands(self):
        for lane in self.lanes.values():
            lane.frames = []
            lane.strip.clear()
        self.sample_token = None
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
        if any(abs(shifts[key] - self.sample_shifts[key]) > .05 for key in self.sample_keys):
            raise ValueError('播放偏移已变化，请重新采样')

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.refresh, self.sample, self.calculate, self.mode):
            widget.setEnabled(not busy)
        for lane in self.lanes.values():
            lane.pick.setEnabled(not busy)
            lane.included.setEnabled(not busy and lane.key != self.reference)

    def start_job(self, *, preview):
        if self.busy or self.cancelled.is_set():
            return
        self.invalidate()
        keys = self.selected_keys()
        try:
            token = session_token(self.viewer, keys)
            if self.reference not in keys or len(keys) < 2:
                raise ValueError('至少需要主画面和一路副画面')
            if not preview and any(self.lanes[key].region is None for key in keys):
                raise ValueError('请先分别框选各路游戏计时器')
            clock, shifts = self.viewer.live_clock(), dict(self.viewer.shifts())
            requests = [(key, self.viewer.rows[key].decoder.history, clock + shifts[key],
                         self.lanes[key].region or (0, 0, 1, 1)) for key in keys]
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.set_busy(True)
        self.status.setText('正在从缓存取帧…' if preview else '正在采样与本地识别；直播继续播放…')
        bridge = ClockResult()
        self.bridge = bridge
        cancelled = self.cancelled
        def work():
            try:
                bands = {key: capture_band(history, anchor, region, cancelled, preview=preview)
                         for key, history, anchor, region in requests}
                if cancelled.is_set():
                    return
                readings, note = ({}, '') if preview else recognize_bands(bands, cancelled)
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
                    for key, frames in bands.items():
                        self.lanes[key].preview = frames[-1]
                    self.status.setText('画面已获取，请框选各路相同的游戏计时器')
                else:
                    self.sample_token, self.sample_keys = token, keys
                    self.sample_shifts = shifts
                    self.anchors = {key: anchor for key, _history, anchor, _region in requests}
                    for key, frames in bands.items():
                        self.lanes[key].set_band(frames, readings.get(key, [None] * len(frames)), self.mode.currentData())
                    self.status.setText(note or '请核对第一帧时间与首次翻秒帧，再计算偏移；建议不等于自动确认。')
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
                value = parse_clock(lane.first.text())
                if value is None:
                    raise ValueError('请按分:秒填写各路第一帧时间')
                readings[key] = ClockReading(value, tuple(stamp for stamp, _image in lane.frames),
                                             lane.strip.currentRow(), self.anchors[key])
            desired, errors = calculate_offsets(readings, self.sample_shifts, self.reference, self.mode.currentData())
            self.reviewed_readings = readings
            self.proposal = desired
            labels = []
            for key in self.sample_keys:
                if key != self.reference:
                    delay = desired[self.reference] - desired[key]
                    labels.append(f'{self.lanes[key].included.text()}：偏移 {delay:+.2f}s，采样边界误差约 ±{errors[key]:.2f}s')
            self.status.setText('；'.join(labels) + '。确认后手动锁定；断流后自动复核计时器，无法复核时等待确认。')
            self.apply.setEnabled(True)
        except ValueError as error:
            self.status.setText(str(error))

    def apply_proposal(self):
        try:
            self.validate_sample()
            if self.proposal is None:
                return
            apply_offsets(self.viewer, self.proposal, self.sample_keys)
            self.viewer.clock_recovery.arm(self.reviewed_readings,
                {key: self.lanes[key].region for key in self.sample_keys}, self.mode.currentData())
        except ValueError as error:
            self.status.setText(str(error))
            return
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
