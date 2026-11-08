"""Qt controls shared by the replay and project export panels."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QSpinBox, QWidget
from .encoding import normalize


class EncodingOptions(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.codec, self.priority = QComboBox(), QComboBox()
        for label, value in [('H.264', 'h264'), ('H.265', 'h265')]:
            self.codec.addItem(label, value)
        for label, value in [('速度优先', 'speed'), ('画质优先', 'quality')]:
            self.priority.addItem(label, value)
        self.bitrate = QSpinBox()
        self.bitrate.setRange(0, 100000)
        self.bitrate.setSpecialValueText('自动画质（CRF 20）')
        self.bitrate.setSuffix(' Kbps')
        self.bitrate.setKeyboardTracking(False)
        for label, field in [('视频编码', self.codec), ('编码优先', self.priority), ('视频码率', self.bitrate)]:
            field.setMinimumWidth(0)
            form.addRow(label, field)
        self.codec.currentIndexChanged.connect(self.changed)
        self.priority.currentIndexChanged.connect(self.changed)
        self.bitrate.valueChanged.connect(self.changed)

    def value(self):
        return normalize(dict(codec=self.codec.currentData(), priority=self.priority.currentData(), bitrate=self.bitrate.value()))

    def set_value(self, value):
        value = normalize(value)
        for field, setting in [(self.codec, value['codec']), (self.priority, value['priority']), (self.bitrate, value['bitrate'])]:
            field.blockSignals(True)
            if field is self.bitrate:
                field.setValue(setting)
            else:
                field.setCurrentIndex(field.findData(setting))
            field.blockSignals(False)
