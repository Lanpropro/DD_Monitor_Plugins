"""Project text inspector, using the cover style vocabulary and host Qt controls."""
from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFontComboBox,
    QFormLayout, QLineEdit, QPushButton, QSizePolicy, QWidget)
from ddm import theme
from .graphic_style import TEXT_DEFAULTS, TEXT_PRESETS


class TextStyleOptions(QWidget):
    presetRequested = Signal()

    def __init__(self):
        super().__init__()
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.font = QFontComboBox()
        form.addRow('字体', self.font)
        self.fields = {}
        for key, title in [('bold', '加粗'), ('italic', '斜体'), ('shadow', '投影')]:
            field = QCheckBox(title)
            self.fields[key] = field
            form.addRow(field)
        self.fields['outline'] = QDoubleSpinBox()
        self.fields['outline'].setRange(0, 30)
        self.fields['outline'].setKeyboardTracking(False)
        form.addRow('描边(1080p)', self.fields['outline'])
        for key, title in [('stroke', '描边色'), ('background', '底色')]:
            field = QLineEdit()
            field.setPlaceholderText('#RRGGBB，留空关闭')
            self.fields[key] = field
            form.addRow(title, field)
        self.align = QComboBox()
        for title, value in [('左对齐', 'left'), ('居中', 'center'), ('右对齐', 'right')]:
            self.align.addItem(title, value)
        form.addRow('文字对齐', self.align)
        self.presets = QComboBox()
        for title, values in TEXT_PRESETS:
            self.presets.addItem(title, values)
        form.addRow('文字样式', self.presets)
        self.preset_button = QPushButton('应用所选样式')
        self.preset_button.setObjectName('IconButton')
        self.preset_button.clicked.connect(self.presetRequested)
        form.addRow(self.preset_button)
        for field in (self.font, self.align, self.presets, *self.fields.values()):
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def set_values(self, style):
        values = {**TEXT_DEFAULTS, **style}
        self.font.setCurrentFont(QFont(values['font'] or theme.danmaku_font_family()))
        self.align.setCurrentIndex(self.align.findData(values['align']))
        for key, field in self.fields.items():
            if isinstance(field, QCheckBox):
                field.setChecked(values[key])
            elif isinstance(field, QDoubleSpinBox):
                field.setValue(values[key])
            else:
                field.setText(values[key])

    def values(self):
        result = dict(font=self.font.currentFont().family(), align=self.align.currentData())
        for key, field in self.fields.items():
            result[key] = field.isChecked() if isinstance(field, QCheckBox) else field.value() if isinstance(field, QDoubleSpinBox) else field.text()
        return result
