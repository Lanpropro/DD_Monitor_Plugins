"""Plugin-owned viewing settings; room controls own rolling comment colors."""
from PySide6.QtCore import QPoint, QSignalBlocker, Qt
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QLabel, QMenu,
                              QScrollArea, QSlider, QWidget, QWidgetAction)

from ddm import theme
from ddm.config import DEFAULT_SETTINGS

try:
    from ddm.ui.selection import ToggleSwitch
except ModuleNotFoundError as error:
    if error.name not in ('ddm.ui', 'ddm.ui.selection'):
        raise
    ToggleSwitch = QCheckBox

try:
    from ddm.playback_material import style_menu
    from ddm.settings_material import normalize
except ModuleNotFoundError as error:
    if error.name != 'ddm.playback_material':
        raise
    style_menu = None


CONTROLS = (
    ('video_danmaku_area', '显示区域', 10, 100, '%'),
    ('video_danmaku_opacity', '不透明度', 10, 100, '%'),
    ('video_danmaku_size', '字体大小', 12, 64, ' px'),
    ('video_danmaku_speed', '弹幕速度', 50, 200, '%'),
    ('video_danmaku_outline_width', '描边宽度', 0, 6, ' px'),
)


class MatchDanmakuSettings(QMenu):
    def __init__(self, viewer, settings):
        super().__init__(viewer.picture)
        self.viewer = viewer
        self.setObjectName('MatchDanmakuSettings')
        self.setStyleSheet(f'''QSlider::sub-page:horizontal {{background: {theme.PINK};}}
            QSlider::handle:horizontal {{background: {theme.TEXT1};}}''')
        saved = viewer.context.setting('overlay_settings', {})
        values = {key: saved.get(key, settings.get(key, DEFAULT_SETTINGS[key]))
                  for key in ('danmaku_font', 'video_danmaku_scale', 'super_chat_enabled',
                              *(item[0] for item in CONTROLS))}
        # The Tile's existing independent-setting contract also protects these
        # values when the host reapplies global settings or switches the main room.
        values.update(video_danmaku_color_mode='original', video_danmaku_color='#ffffff',
                      video_danmaku_scale_curve=None,
                      video_danmaku_scale_strength=DEFAULT_SETTINGS['video_danmaku_scale_strength'])
        viewer.picture.video_danmaku_overrides = values
        holder = QWidget(self)
        grid = QGridLayout(holder)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(14)
        self.controls, self.labels = {}, {}
        heading = QLabel('二路弹幕 · 独立设置')
        grid.addWidget(heading, 0, 0, 1, 3)
        for row, (key, label, low, high, suffix) in enumerate(CONTROLS, 1):
            grid.addWidget(QLabel(label), row, 0)
            slider = QSlider(Qt.Horizontal, holder)
            slider.setRange(low, high)
            slider.setMinimumWidth(200)
            slider.setAccessibleName(label)
            value_label = QLabel()
            value_label.setFixedWidth(64)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.controls[key], self.labels[key] = slider, value_label
            slider.valueChanged.connect(lambda value, k=key: self.change({k: value}))
            grid.addWidget(slider, row, 1)
            grid.addWidget(value_label, row, 2)
        self.scale = QCheckBox('弹幕随屏幕缩放', holder)
        self.scale.toggled.connect(lambda checked: self.change({'video_danmaku_scale': checked}))
        grid.addWidget(self.scale, 6, 0, 1, 3)
        label = QLabel('显示醒目留言（SC）')
        self.super_chat = ToggleSwitch(parent=holder)
        self.super_chat.setAccessibleName(label.text())
        label.setBuddy(self.super_chat)
        self.super_chat.toggled.connect(self.change_super_chat)
        grid.addWidget(label, 7, 0, 1, 2)
        grid.addWidget(self.super_chat, 7, 2, Qt.AlignRight)
        self.color_note = QLabel('弹幕颜色由下方控制台各路颜色控制')
        grid.addWidget(self.color_note, 8, 0, 1, 3)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(holder)
        action = QWidgetAction(self)
        action.setDefaultWidget(self.scroll)
        self.addAction(action)
        self.holder = holder

    def change(self, values):
        self.viewer.picture.video_danmaku_overrides.update(values)
        self.viewer.picture.apply_video_danmaku_settings()
        self.save()

    def change_super_chat(self, enabled):
        self.change({'super_chat_enabled': enabled})
        if not enabled:
            self.viewer.video_danmaku.set_super_chats([])
            self.viewer.super_chat_timer.stop()
            pending = self.viewer.overlay_pending
            for room in [room for room, (_, event) in pending.items() if event.get('kind') == 'super_chat']:
                pending.pop(room)

    def save(self):
        self.viewer.context.set_setting('overlay_settings', dict(self.viewer.picture.video_danmaku_overrides))

    def apply_settings(self, settings, *, independent=None):
        for key, _label, _low, _high, suffix in CONTROLS:
            value = settings.get(key)
            if value is None:
                value = DEFAULT_SETTINGS[key]
            slider = self.controls[key]
            slider.blockSignals(True)
            slider.setValue(int(value))
            slider.blockSignals(False)
            self.labels[key].setText(f'{slider.value()}{suffix}')
        self.scale.blockSignals(True)
        self.scale.setChecked(bool(settings.get('video_danmaku_scale', True)))
        self.scale.blockSignals(False)
        with QSignalBlocker(self.super_chat):
            self.super_chat.setChecked(bool(settings.get('super_chat_enabled', True)))

    def open_at(self, button, _settings=None):
        self.apply_settings(self.viewer.video_danmaku.settings)
        if style_menu is not None:
            appearance = normalize(self.viewer.context.window.settings)
            opacity = appearance['settings_glass_opacity'] if appearance['settings_glass_enabled'] else None
            style_menu(self, opacity)
        self.ensurePolished()
        screen = button.screen().availableGeometry()
        top = button.mapToGlobal(QPoint(button.width(), 0))
        bottom = button.mapToGlobal(button.rect().bottomLeft())
        above, below = top.y() - screen.top() - 6, screen.bottom() - bottom.y() - 6
        self.scroll.setFixedSize(min(self.holder.sizeHint().width() + 20, screen.width() - 20),
                                 min(self.holder.sizeHint().height(), max(1, max(above, below) - 16)))
        self.adjustSize()
        def position():
            x = max(screen.left(), min(top.x() - self.width(), screen.right() - self.width() + 1))
            y = top.y() - self.height() - 6 if above >= below else bottom.y() + 6
            return QPoint(x, max(screen.top(), min(y, screen.bottom() - self.height() + 1)))
        self.popup(position())
        self.move(position())


def install(viewer, settings):
    old = viewer.picture.danmaku_settings_menu
    menu = MatchDanmakuSettings(viewer, settings)
    viewer.picture.danmaku_settings_menu = menu
    old.deleteLater()
    viewer.picture.apply_video_danmaku_settings(settings)
    menu.save()
