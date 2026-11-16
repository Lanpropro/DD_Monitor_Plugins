"""Appearance and playback defaults for the watch-later plugin."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QVBoxLayout
from ddm.ui import ActionButton, HintLabel, SelectControl, SettingsSurface, TitleLabel, ToggleSwitch


class WatchLaterSettings(QDialog):
    applied = Signal(object)

    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle('稍后再看设置')
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumWidth(440)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)
        outer.addWidget(TitleLabel('稍后再看设置', self))
        outer.addWidget(HintLabel('仅影响此插件，不改变画面墙或本体设置', self))
        surface = SettingsSurface(self)
        form = QFormLayout(surface)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(14)
        self.view = self._select([('网格', 'grid'), ('列表', 'list')], values['default_view'])
        form.addRow('默认视图', self.view)
        self.columns = self._select([('自动', 0)] + [(f'{n} 列', n) for n in range(2, 7)], values['grid_columns'])
        form.addRow('每行视频数', self.columns)
        self.card_size = self._select([('紧凑', 'compact'), ('标准', 'standard'), ('宽大', 'large')], values['card_size'])
        form.addRow('卡片大小', self.card_size)
        form.addRow(HintLabel('自动列数时按卡片大小排列；固定列数时随窗口宽度缩放。', self))
        self.autoplay = ToggleSwitch('自动连播', self)
        self.autoplay.setChecked(values['autoplay'])
        form.addRow('自动连播', self.autoplay)
        form.addRow(HintLabel('开启后先播放下一 P，再播放列表下一条；列表末尾停止。默认关闭。', self))
        self.columns.currentIndexChanged.connect(self._sync_size)
        self._sync_size()
        outer.addWidget(surface)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = ActionButton('取消', self)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        apply = ActionButton('应用', self)
        apply.setObjectName('PrimaryButton')
        apply.clicked.connect(self._apply)
        buttons.addWidget(apply)
        outer.addLayout(buttons)

    def _select(self, options, value):
        control = SelectControl(self)
        for label, data in options:
            control.addItem(label, data)
        control.setCurrentIndex(control.findData(value))
        return control

    def _sync_size(self):
        self.card_size.setEnabled(self.columns.currentData() == 0)

    def _apply(self):
        self.applied.emit(dict(default_view=self.view.currentData(), grid_columns=self.columns.currentData(),
                               card_size=self.card_size.currentData(), autoplay=self.autoplay.isChecked()))
        self.accept()
