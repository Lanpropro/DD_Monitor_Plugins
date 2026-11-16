"""Plugin-owned recording choices inside the host recording settings page."""
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QVBoxLayout, QWidget
from shiboken6 import isValid


class RecordingOptions(QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setObjectName('replayRecordingOptions')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.setObjectName('replayRecordingMode')
        self.mode.addItem('原版录制：完整录像与即时重放', 'original')
        self.mode.addItem('切片录制：边录边看与制作切片', 'clip')
        form.addRow('录制方式', self.mode)
        layout.addLayout(form)
        hint = QLabel('两种方式互斥，沿用上方保存目录与磁盘警戒线。切片录制保存直播原始流，不使用原版输出格式、重编码或即时回放缓存参数。停止当前录制和缓存后可切换。')
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

    def sync(self):
        if not isValid(self) or self.owner is None:
            return
        previous = self.mode.blockSignals(True)
        self.mode.setCurrentIndex(self.mode.findData(self.owner.capture_mode))
        self.mode.blockSignals(previous)
        self.description.setText('切片录制：持续保存 HLS 归档和弹幕，可在录像页边录边看；M3U8 是索引，视频按源编码保存为 TS 或 fMP4 分段，切片可导出 MP4 / MKV。'
            if self.owner.capture_mode == 'clip' else '原版录制：使用上方的完整录像与即时重放设置。若要边录边看并制作切片，请切换到切片录制。')

    def apply_mode(self):
        try:
            self.owner.set_capture_mode(self.mode.currentData())
        except (RuntimeError, ValueError, OSError) as error:
            self.status.setText(str(error))
            self.status.show()
            self.sync()
        else:
            self.status.setText('录制方式已保存，下次开始录制时生效。')
            self.status.show()

    def detach(self):
        if isValid(self):
            self.setEnabled(False)
            self.status.setText('插件已卸载，重新装载后可调整。')
            self.status.show()
        self.owner = None
