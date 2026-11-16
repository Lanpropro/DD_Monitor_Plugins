"""Grouped video-card, navigation and playback settings."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from ddm.ui import ActionButton, HintLabel, SelectControl, SettingsSurface, TitleLabel, ToggleSwitch
from .page_shell import InlinePanel
from .preferences import DEFAULTS


class WatchLaterSettings(InlinePanel):
    applied = Signal(object)

    def __init__(self, values, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 12, 28, 18)
        outer.setSpacing(16)
        outer.addWidget(TitleLabel('视频卡片与播放', self))
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 6, 0)
        body.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        appearance = self._section(body, '显示与预览', '选择页面的展示方式，以及浏览与预览行为。')
        self.view = self._select([('网格', 'grid'), ('列表', 'list')], values['default_view'])
        self._row(appearance, '默认视图', '首次打开时使用的展示方式。', self.view)
        self.video_preview = self._toggle(values['video_preview'], '启用视频预览')
        self._row(appearance, '启用视频预览（如果支持）', '悬停卡片显示 B 站预览帧；同时控制进度条预览。关闭后不请求预览数据。', self.video_preview)
        self.remember_state = self._toggle(values['remember_state'], '留存状态')
        self._row(appearance, '留存状态', '再次打开选片页面时，恢复上次的浏览位置、视图、搜索和筛选。', self.remember_state)

        grid = self._section(body, '视频卡片网格', '按窗口宽度自动排列，也可以指定每行的视频数量。')
        self.columns = self._select([('自动', 0)] + [(f'{n} 列', n) for n in range(2, 7)], values['grid_columns'])
        self._row(grid, '每行视频数', '固定列数时，卡片随窗口宽度缩放。', self.columns)
        self.card_size = self._select([('紧凑', 'compact'), ('标准', 'standard'), ('宽大', 'large')], values['card_size'])
        self._row(grid, '卡片大小', '自动列数时生效；固定列数时由可用宽度决定。', self.card_size)

        playback = self._section(body, '播放行为', '自动播放决定进入格子后的状态；自动连播决定结束后的行为。')
        self.auto_play = self._toggle(values['auto_play'], '自动播放')
        self._row(playback, '自动播放', '视频进入格子后立即播放；关闭后保持暂停，手动点击播放。', self.auto_play)
        self.autoplay = self._toggle(values['autoplay'], '自动连播')
        self._row(playback, '自动连播', '自然播放结束后先下一 P，再列表下一条；列表末尾停止。', self.autoplay)
        self.prefetch_next = self._toggle(values['prefetch_next'], '提前加载下一条')
        self._row(playback, '提前加载下一条', '仅在自动连播开启时可用。接近结束时预取下一项；每路最多约 10 MiB 音视频缓冲，会额外占用内存和流量，用于减少切换等待。', self.prefetch_next)
        self.columns.currentIndexChanged.connect(self._sync_size)
        self.autoplay.toggled.connect(self._sync_prefetch)
        self._sync_size()
        self._sync_prefetch()
        body.addStretch()
        buttons = QHBoxLayout()
        self.reset_button = ActionButton('恢复默认', self)
        self.reset_button.setToolTip('恢复默认选项，点击应用后保存')
        self.reset_button.clicked.connect(self._reset)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        cancel = ActionButton('取消', self)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        apply = ActionButton('应用', self)
        apply.setObjectName('PrimaryButton')
        apply.clicked.connect(self._apply)
        buttons.addWidget(apply)
        outer.addLayout(buttons)

    def _section(self, body, title, hint):
        heading = QLabel(title, self)
        heading.setObjectName('SectionLabel')
        body.addWidget(heading)
        body.addWidget(HintLabel(hint, self))
        surface = SettingsSurface(self)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(0)
        body.addWidget(surface)
        return layout

    def _row(self, layout, title, hint, control):
        if layout.count():
            separator = QFrame(self)
            separator.setObjectName('WatchLaterSettingsDivider')
            separator.setFixedHeight(1)
            layout.addWidget(separator)
        row = QHBoxLayout()
        row.setContentsMargins(0, 16, 0, 16)
        row.setSpacing(16)
        labels = QVBoxLayout()
        labels.setSpacing(5)
        label = QLabel(title, self)
        label.setObjectName('WatchLaterSettingTitle')
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setBuddy(control)
        control.setAccessibleName(title)
        labels.addWidget(label)
        labels.addWidget(HintLabel(hint, self))
        row.addLayout(labels, 1)
        row.addWidget(control, 0, Qt.AlignVCenter)
        layout.addLayout(row)

    def _select(self, options, value):
        control = SelectControl(self)
        for label, data in options:
            control.addItem(label, data)
        control.setCurrentIndex(control.findData(value))
        return control

    def _reset(self):
        for name, key in (('view', 'default_view'), ('columns', 'grid_columns'), ('card_size', 'card_size')):
            control = getattr(self, name)
            control.setCurrentIndex(control.findData(DEFAULTS[key]))
        for name in ('autoplay', 'video_preview', 'remember_state', 'auto_play', 'prefetch_next'):
            getattr(self, name).setChecked(DEFAULTS[name])

    def _toggle(self, checked, title):
        control = ToggleSwitch(title, self)
        control.setChecked(checked)
        return control

    def _sync_size(self):
        self.card_size.setEnabled(self.columns.currentData() == 0)

    def _sync_prefetch(self):
        enabled = self.autoplay.isChecked()
        self.prefetch_next.setEnabled(enabled)
        if not enabled:
            self.prefetch_next.setChecked(False)

    def _apply(self):
        self.applied.emit(dict(default_view=self.view.currentData(), grid_columns=self.columns.currentData(),
            card_size=self.card_size.currentData(), autoplay=self.autoplay.isChecked(),
            remember_state=self.remember_state.isChecked(), auto_play=self.auto_play.isChecked(),
            prefetch_next=self.prefetch_next.isChecked(), video_preview=self.video_preview.isChecked()))
        self.accept()
