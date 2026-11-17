"""Plugin recording routing and lower-page HLS options; native settings stay above."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QCheckBox, QVBoxLayout, QWidget)
from shiboken6 import isValid


MODES = [('仅本体：完整录像与即时回放', 'original'),
         ('仅切片：边录边看与制作切片', 'clip'),
         ('同时录制：本体录像与切片归档', 'both')]


def mode_combo():
    combo = QComboBox()
    for label, value in MODES:
        combo.addItem(label, value)
        combo.setItemData(combo.count()-1, label, Qt.ToolTipRole)
    combo.setAccessibleName('格子录制按钮的录制方式')
    return combo


def scan_recording_directories(job, directories):
    from .media import scan_directory, check_cancel
    rows = {}
    for directory in directories:
        check_cancel(job)
        for row in scan_directory(job, directory):
            rows[row['path']] = row
    return list(rows.values())


class RecordingOptions(QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setObjectName('replayRecordingOptions')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode_row = QWidget(parent)
        mode_layout = QVBoxLayout(self.mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.mode = mode_combo()
        self.mode.setObjectName('replayRecordingMode')
        form.addRow('格子录制方式', self.mode)
        mode_layout.addLayout(form)
        hint = QLabel('上方本体参数与下方切片参数分别生效；两种录制可以单独使用或同时保存。录制和缓存运行时不能切换方式。')
        hint.setWordWrap(True)
        hint.setObjectName('SettingsHint')
        mode_layout.addWidget(hint)
        add_mode = getattr(parent, 'add_recording_mode', None)
        if add_mode is not None:
            add_mode(self.mode_row)
        else:
            layout.addWidget(self.mode_row)
        options = QFormLayout()
        directory_row = QHBoxLayout()
        self.directory = QLineEdit()
        self.directory.setPlaceholderText('留空沿用上方保存目录')
        directory_row.addWidget(self.directory, 1)
        browse = QPushButton('浏览…')
        browse.setObjectName('IconButton')
        browse.clicked.connect(self.browse)
        directory_row.addWidget(browse)
        options.addRow('切片保存目录', directory_row)
        self.segment = QSpinBox()
        self.segment.setRange(1, 10)
        self.segment.setSuffix(' 秒')
        options.addRow('HLS 目标分段时长', self.segment)
        self.danmaku = QCheckBox('保存弹幕，用于回放与亮点推荐')
        options.addRow('弹幕采集', self.danmaku)
        layout.addLayout(options)
        hint = QLabel('切片保存直播原始流，不使用上方的本体格式、编码或即时回放参数。M3U8 是索引，实际视频为 TS / fMP4 分段；分段按关键帧封口，实际时长可能超过设定值。切片可导出 MP4 / MKV。')
        hint.setWordWrap(True)
        hint.setObjectName('SettingsHint')
        layout.addWidget(hint)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setObjectName('SettingsHint')
        layout.addWidget(self.description)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName('SettingsHint')
        layout.addWidget(self.status)
        self.status.hide()
        self.sync()
        self.mode.currentIndexChanged.connect(self.apply_mode)
        self.directory.editingFinished.connect(self.apply_options)
        self.segment.valueChanged.connect(self.apply_options)
        self.danmaku.toggled.connect(self.apply_options)

    def sync(self):
        if not isValid(self) or self.owner is None:
            return
        options = self.owner.clip_options()
        for widget, value, setter in [(self.mode, self.mode.findData(self.owner.capture_mode), 'setCurrentIndex'),
                (self.directory, options.get('directory', ''), 'setText'),
                (self.segment, options.get('segment_seconds', 2), 'setValue'),
                (self.danmaku, options.get('danmaku', True), 'setChecked')]:
            previous = widget.blockSignals(True)
            getattr(widget, setter)(value)
            widget.blockSignals(previous)
        self.description.setText({'original': '当前仅本体录制：下方切片参数保留，选择仅切片或同时录制后生效。',
            'clip': '当前仅切片录制：格子按钮只保存切片归档，本体录像与即时回放不运行。',
            'both': '当前同时录制：格子按钮同时开始/停止本体录像与切片归档；本体即时回放保持可用。'}[self.owner.capture_mode])

    def apply_mode(self):
        self.apply_change(lambda: self.owner.set_capture_mode(self.mode.currentData()))

    def apply_options(self):
        text = self.directory.text().strip()
        options = dict(directory=str(Path(text).expanduser().resolve()) if text else '',
            segment_seconds=self.segment.value(), danmaku=self.danmaku.isChecked())
        self.apply_change(lambda: self.owner.set_clip_options(options))

    def apply_change(self, callback):
        try:
            callback()
        except (RuntimeError, ValueError, OSError) as error:
            self.status.setText(str(error))
            self.sync()
        else:
            self.status.setText('已保存，下一次开始录制时使用这些设置。')
        self.status.show()

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, '选择切片保存目录', self.directory.text())
        if folder:
            self.directory.setText(folder)
            self.apply_options()

    def detach(self):
        if isValid(self):
            self.setEnabled(False)
            self.mode_row.setEnabled(False)
            self.status.setText('插件已卸载，重新装载后可调整。')
            self.status.show()
        self.owner = None
