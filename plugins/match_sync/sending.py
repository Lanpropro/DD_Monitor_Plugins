"""Independent platform composers below the merged chat, one per live room."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import QApplication, QBoxLayout, QFrame, QHBoxLayout, QListView, QListWidget, QListWidgetItem, QLabel, QLayout, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from ddm.danmaku_actions import CommentToolbar
from ddm import theme
from .panels import ConsoleIconButton, SettingsPanel, console_button_style
from .send_design import RecipientDelegate, SavedTextDelegate, SavedTextList, align_composer, colors, style_compose, stylesheet

# Compatibility exports; internal users import the owning module.
from .chat_panel import SendingDanmakuPanel
from .send_composers import SendRow, BatchSend


class SendConsole(SettingsPanel):
    def __init__(self, viewer):
        super().__init__(viewer, 'MatchSyncSendConsole')
        self.rows = {}
        self.statuses = {}
        self.status_timers = {}
        self.send_watches = {}
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(50)
        self.watch_timer.timeout.connect(self.check_sends)
        self.batch_targets = set()
        self.selected_room = None
        self.collapsed = False
        self.expanded_without_settings = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.toolbar = QWidget(self)
        self.toolbar.setObjectName('SendToolbar')
        heading = QHBoxLayout(self.toolbar)
        heading.setContentsMargins(16, 10, 16, 10)
        heading.setSpacing(16)
        self.title = QLabel('弹幕控制台')
        self.title.setObjectName('SendTitle')
        heading.addWidget(self.title, 1)
        self.minimize = ConsoleIconButton('minimize', '最小化弹幕控制台')
        self.minimize.setObjectName('SendMinimize')
        self.minimize.setAccessibleName('最小化发送台')
        self.minimize.setToolTip('收起发送控制台，保留草稿并扩展弹幕栏。')
        self.minimize.clicked.connect(lambda: self.set_collapsed(True))
        heading.addWidget(self.minimize)
        layout.addWidget(self.toolbar)
        self.restore_bar = QWidget(viewer.panel)
        self.restore_bar.setObjectName('DanmakuBar')
        restore_layout = QHBoxLayout(self.restore_bar)
        restore_layout.setContentsMargins(10, 2, 10, 2)
        self.restore = QPushButton('展开发送', self.restore_bar)
        self.restore.setFixedWidth(90)
        self.restore.setStyleSheet(console_button_style('QPushButton'))
        self.restore.clicked.connect(lambda: self.set_collapsed(False))
        restore_layout.addWidget(self.restore)
        restore_layout.addStretch(1)
        viewer.panel.layout().addWidget(self.restore_bar)
        self.restore_bar.hide()
        self.restore.hide()
        self.rooms = QListWidget()
        self.rooms.setAccessibleName('左键勾选主播；选一位单发，选多位同文发送')
        self.rooms.setWrapping(False)
        self.rooms.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        self.rooms.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.rooms.setMouseTracking(True)
        self.rooms.setItemDelegate(RecipientDelegate(self.rooms))
        self.rooms.itemChanged.connect(self._room_checked)
        body = QWidget()
        self.body_layout = QBoxLayout(QBoxLayout.LeftToRight, body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.body_layout.setSpacing(12)
        self.body_layout.addWidget(self.rooms)
        self.divider = QFrame()
        self.divider.setObjectName('SendDivider')
        self.divider.setFixedWidth(1)
        self.divider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.body_layout.addWidget(self.divider)
        self.work = QWidget()
        self.work.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        work_layout = QVBoxLayout(self.work)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(6)
        self.body_layout.addWidget(self.work, 1)
        self.body_scroll = QScrollArea()
        self.body_scroll.setFrameShape(QFrame.NoFrame)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setWidget(body)
        layout.addWidget(self.body_scroll, 1)
        self.stack = QStackedWidget()
        self.pages = QStackedWidget()
        page_policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        page_policy.setRetainSizeWhenHidden(True)
        self.pages.setSizePolicy(page_policy)
        self.pages.setFixedHeight(48)
        self.pages.addWidget(self.stack)
        self.batch_panel = BatchSend(self)
        self.pages.addWidget(self.batch_panel)
        compose_layout = QHBoxLayout()
        compose_layout.setContentsMargins(0, 0, 0, 0)
        compose_layout.setSpacing(4)
        compose_layout.addWidget(self.pages, 1)
        self.save_text = ConsoleIconButton('save', '暂存当前输入')
        self.save_text.setToolTip('将当前弹幕输入框的内容保存为暂存文本；Enter 仍用于发送。')
        self.save_text.clicked.connect(self.store_text)
        compose_layout.addWidget(self.save_text, 0, Qt.AlignVCenter)
        work_layout.addLayout(compose_layout)
        self.saved_texts = SavedTextList()
        self.saved_texts.setObjectName('SavedTextList')
        self.saved_texts.setViewMode(QListView.IconMode)
        self.saved_texts.setFlow(QListView.LeftToRight)
        self.saved_texts.setWrapping(True)
        self.saved_texts.setResizeMode(QListView.Adjust)
        self.saved_texts.setMovement(QListView.Static)
        self.saved_texts.setSpacing(4)
        self.saved_texts.setMouseTracking(True)
        self.saved_texts.setCursor(Qt.PointingHandCursor)
        self.saved_texts.setItemDelegate(SavedTextDelegate(self.saved_texts))
        self.saved_texts.setAccessibleName('暂存文本，左键发送，长按排序，右键显示删除小标')
        self.saved_texts.setMinimumHeight(36)
        self.saved_texts.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.saved_texts.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.saved_texts.setTextElideMode(Qt.ElideRight)
        self.saved_texts.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.saved_texts.changed.connect(self.persist_texts)
        self.saved_texts.itemClicked.connect(lambda item: self.batch_panel.begin(item.text()))
        for text in viewer.context.setting('saved_send_texts', []):
            self.add_saved_text(text)
        work_layout.addWidget(self.saved_texts, 1)
        self.feedback = QLabel('')
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setObjectName('SendFeedback')
        self.feedback.setWordWrap(True)
        self.feedback.setMaximumHeight(self.feedback.fontMetrics().height() * 2 + 4)
        work_layout.addWidget(self.feedback)
        self.feedback.hide()
        self.work.hide()
        self.refresh_design()

    def add_saved_text(self, text):
        item = QListWidgetItem(text)
        item.setToolTip(text)
        self.saved_texts.addItem(item)

    def active_editor(self):
        if self.pages.currentWidget() is self.batch_panel:
            return self.batch_panel.edit
        row = self.rows.get(self.selected_room)
        return row.tile._plugin_controls._repeat_editor() if row is not None else None

    def refresh_store_state(self):
        editor = self.active_editor()
        self.save_text.setEnabled(bool(editor is not None and editor.isEnabled() and not editor.isReadOnly()
                                       and editor.text().strip() and not self.batch_panel.timer.isActive()))

    def store_text(self):
        editor = self.active_editor()
        if editor is None or editor.isReadOnly() or not editor.isEnabled():
            return
        text = editor.text().strip()
        if not text.strip():
            return
        self.store_phrase(text)
        editor.clear()

    def store_phrase(self, text):
        if not text.strip():
            return
        if not any(self.saved_texts.item(i).text() == text for i in range(self.saved_texts.count())):
            self.add_saved_text(text)
            self.persist_texts()

    def persist_texts(self):
        self.viewer.context.set_setting('saved_send_texts',
            [self.saved_texts.item(i).text() for i in range(self.saved_texts.count())])

    def create_chat_toolbar(self, event):
        text = str(event.get('text') or '')
        menu = CommentToolbar(self.viewer.panel)
        menu.copy_action.setEnabled(bool(text))
        keys = self.checked_rooms()
        available = bool(text and keys and all(self.rows[key].tile._plugin_controls.can_repeat(event) for key in keys)
                         and not self.batch_panel.timer.isActive())
        menu.repeat_action.setEnabled(available)
        menu.repeat_button.setToolTip('发送给当前选中的主播' if available else '请先选择主播，并确认所选房间的发送入口可用')
        menu.save_action = QAction('暂存', menu)
        save_button = menu._button(menu.save_action, menu)
        menu.save_action.setEnabled(bool(text.strip()))
        save_button.setToolTip('转存为暂存文本')
        menu.layout().addWidget(save_button)
        return menu

    def show_chat_actions(self, event, position, finished=None):
        started = time.perf_counter()
        event = dict(event)
        text = str(event.get('text') or '')
        menu = self.create_chat_toolbar(event)
        def complete(_result):
            try:
                if menu.chosen is menu.copy_action:
                    QApplication.clipboard().setText(text)
                elif menu.chosen is menu.save_action:
                    self.store_phrase(text)
                elif menu.chosen is menu.repeat_action and menu.repeat_action.isEnabled():
                    self.batch_panel.begin(text, event=event)
            finally:
                if finished is not None:
                    finished()
                menu.deleteLater()
        menu.finished.connect(complete)
        menu.popup(position)
        self.viewer.playback_probe.timings.record('chat_action_open', time.perf_counter() - started)
        return menu

    def refresh_design(self):
        style = stylesheet()
        if self.styleSheet() != style:
            self.setStyleSheet(style)
        c = colors()
        self.divider.setStyleSheet(f'QFrame#SendDivider {{background: {theme.BORDER_STRONG}; border: none;}}')
        self.toolbar.setFixedHeight(self.viewer.console_toolbar.sizeHint().height())
        for row in self.rows.values():
            for entry in row.tile._plugin_controls.applicable():
                align_composer(entry.control.widget, self)
        style_compose(self.batch_panel.edit, self.batch_panel.send)
        for editor in self.findChildren(QLineEdit):
            palette = editor.palette()
            palette.setColor(QPalette.Text, QColor(c['text']))
            palette.setColor(QPalette.PlaceholderText, QColor(c['secondary']))
            if editor.palette() != palette:
                editor.setPalette(palette)
        self.rooms.viewport().update()
        restore_style = console_button_style('QPushButton')
        if self.restore.styleSheet() != restore_style:
            self.restore.setStyleSheet(restore_style)
        self.refresh_interactions()
        self._layout_workspace()
        self.update()

    def make_interactions(self, parent):
        bar = QWidget(parent)
        bar.setObjectName('SendInteractions')
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for kind, label in (('emote', '选择平台表情'), ('gift', '选择平台礼物')):
            button = ConsoleIconButton(kind, label, bar)
            button.setProperty('interactionKind', kind)
            button.clicked.connect(lambda _checked=False, b=button, k=kind: self.show_interactions(k, b))
            layout.addWidget(button)
        self.refresh_interactions()
        return bar

    def interaction_target(self, kind):
        keys = self.checked_rooms()
        label = '表情' if kind == 'emote' else '礼物'
        if not keys:
            return None, f'请先选择主播，再使用{label}'
        if len(keys) != 1:
            return None, f'请选择一位主播使用{label}；文字弹幕仍可多选发送'
        key = keys[0]
        row = self.rows[key]
        entries = row.tile._plugin_controls.applicable()
        if not entries:
            return None, '发送控件不可用，请检查平台插件是否已启用'
        platform = row.tile.room.get('platform', 'bilibili')
        platform_name = {'bilibili': '哔哩哔哩', 'douyu': '斗鱼', 'huya': '虎牙', 'douyin': '抖音'}.get(platform, platform)
        composer = next((entry.control.widget for entry in entries
                         if callable(getattr(entry.control.widget, 'open_emoticons' if kind == 'emote' else 'open_gifts', None))), None)
        if composer is None or kind == 'gift' and platform in ('huya', 'douyin'):
            return None, f'{platform_name}暂不支持{label}'
        return composer, f'给 {self.viewer.rows[key].label()} 选择{label}（{platform_name}）'

    def refresh_interactions(self):
        states = {kind: self.interaction_target(kind) for kind in ('emote', 'gift')}
        for button in self.findChildren(ConsoleIconButton):
            kind = button.property('interactionKind')
            if kind in states:
                composer, tooltip = states[kind]
                button.setEnabled(composer is not None)
                button.setToolTip(tooltip)
                button.setAccessibleName(tooltip)

    @staticmethod
    def open_interaction(composer, kind, anchor):
        attribute = 'emote_button' if kind == 'emote' else 'gift_button' if hasattr(composer, 'gift_button') else 'gifts'
        original = getattr(composer, attribute)
        setattr(composer, attribute, anchor)
        try:
            getattr(composer, 'open_emoticons' if kind == 'emote' else 'open_gifts')()
        finally:
            setattr(composer, attribute, original)

    def show_interactions(self, kind, anchor):
        composer, _tooltip = self.interaction_target(kind)
        self.refresh_interactions()
        if composer is not None:
            self.open_interaction(composer, kind, anchor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_workspace()

    def _layout_workspace(self):
        if not hasattr(self, 'body_layout'):
            return
        regular = self.width() >= 520
        compact = not regular
        if bool(self.rooms.property('avatarOnly')) != compact:
            self.rooms.setProperty('avatarOnly', compact)
            self.rooms.doItemsLayout()
            self.rooms.viewport().update()
        self.body_layout.setContentsMargins(16 if regular else 12, 12 if regular else 8,
                                           16 if regular else 12, 12 if regular else 8)
        self.body_layout.setSpacing(12 if regular else 6)
        self.body_layout.setDirection(QBoxLayout.LeftToRight)
        self.rooms.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        self.rooms.setFixedWidth(176 if regular else 56)
        self.rooms.setMinimumHeight(0)
        self.rooms.setMaximumHeight(16777215)
        self.rooms.setFlow(QListView.TopToBottom)
        self.rooms.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.rooms.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        stacked = self.width() - (32 if regular else 24) - self.rooms.width() - 2 * self.body_layout.spacing() - self.divider.width() < 280
        self.pages.setFixedHeight(84 if stacked else 48)
        composers = [self.batch_panel.compose]
        self.batch_panel.compose.setFixedHeight(76 if stacked else 40)
        for row in self.rows.values():
            row.composer_bar.setFixedHeight(76 if stacked else 40)
            composers.extend(entry.control.widget for entry in row.tile._plugin_controls.applicable())
        for composer in composers:
            actions = getattr(composer, '_match_actions', None)
            if actions is not None:
                composer.layout().setDirection(QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight)
                composer.layout().setStretch(0, 0 if stacked else 1)
                composer.setFixedHeight(76 if stacked else 40)
                actions.setFixedWidth(actions.layout().sizeHint().width())

    def sync_rooms(self):
        had_rows = bool(self.rows)
        checked = set(self.checked_rooms())
        for key in tuple(self.rows):
            if key not in self.viewer.rows:
                row = self.rows.pop(key)
                self.stack.removeWidget(row)
                row.shutdown()
                self.statuses.pop(key, None)
                self.rooms.itemDelegate().forget(key)
                self.send_watches.pop(key, None)
                timer = self.status_timers.pop(key, None)
                if timer is not None:
                    timer.stop()
                    timer.deleteLater()
        for key, room in self.viewer.rows.items():
            if key not in self.rows:
                self.rows[key] = SendRow(self.viewer, room)
                self.stack.addWidget(self.rows[key])
                room.alias.textChanged.connect(lambda _text: self.sync_rooms())
                editor = self.rows[key].tile._plugin_controls._repeat_editor()
                if editor is not None:
                    editor.textChanged.connect(lambda _text, room_id=key: self.refresh_room(room_id))
                    editor.returnPressed.connect(lambda room_id=key, edit=editor: self.watch_send(room_id, edit))
                    for button in editor.parentWidget().findChildren(QPushButton):
                        if button.text() == '发送' and button.window() is editor.window():
                            button.setProperty('sendPrimary', True)
                            button.clicked.connect(lambda _checked=False, room_id=key, edit=editor:
                                                   self.watch_send(room_id, edit))
            self.rows[key].update_name(room)
        if not had_rows and self.rows:
            checked = {next(iter(self.rows))}
        self.batch_targets = checked.intersection(self.rows)
        blocked = self.rooms.blockSignals(True)
        try:
            for index in range(self.rooms.count() - 1, -1, -1):
                if self.rooms.item(index).data(Qt.UserRole) not in self.rows:
                    self.rooms.takeItem(index)
            for index, key in enumerate(self.viewer.rows):
                current = next((i for i in range(self.rooms.count())
                                if self.rooms.item(i).data(Qt.UserRole) == key), None)
                if current is None:
                    item = QListWidgetItem()
                    item.setData(Qt.UserRole, key)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    self.rooms.insertItem(index, item)
                else:
                    item = self.rooms.item(current)
                    if current != index:
                        self.rooms.insertItem(index, self.rooms.takeItem(current))
                item.setCheckState(Qt.Checked if key in self.batch_targets else Qt.Unchecked)
                self.refresh_room(key)
        finally:
            self.rooms.blockSignals(blocked)
        self.batch_targets.intersection_update(self.rows)
        self._update_selection()
        self._layout_workspace()
        self.work.setVisible(bool(self.rows))
        self.viewer.replay_bar.refresh(force=True)
        self.refresh_design()

    def checked_rooms(self):
        return [key for key in self.rows if key in self.batch_targets]

    def refresh_room(self, key):
        row = self.viewer.rows.get(key)
        if row is None:
            return
        controls = self.rows[key].tile._plugin_controls
        editor = controls._repeat_editor()
        draft = editor is not None and bool(editor.text())
        for index in range(self.rooms.count()):
            item = self.rooms.item(index)
            if item.data(Qt.UserRole) == key:
                text = self.statuses.get(key, '')
                detail = text or ('有草稿' if draft else '')
                item.setText(f"{row.label()}\n{detail}")
                item.setToolTip(f'{row.label()} · {key}\n{item.text().partition(chr(10))[2]}')
                self.refresh_avatar(key)
        self.refresh_store_state()

    def set_status(self, key, text):
        if key in self.status_timers:
            self.status_timers[key].stop()
        self.statuses[key] = text
        self.refresh_room(key)
        self.feedback.hide()
        if text == '✓':
            if key not in self.status_timers:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda room_id=key: self.clear_status(room_id))
                self.status_timers[key] = timer
            self.status_timers[key].start(2500)

    def clear_status(self, key):
        self.statuses.pop(key, None)
        self.refresh_room(key)

    def watch_send(self, key, editor):
        self.set_status(key, '')
        if editor.isReadOnly() or not editor.isEnabled():
            self.send_watches[key] = editor
            self.set_status(key, '发送中…')
            self.watch_timer.start()

    def check_sends(self):
        for key, editor in tuple(self.send_watches.items()):
            if key not in self.rows:
                self.send_watches.pop(key)
            elif not editor.isReadOnly() and editor.isEnabled():
                self.send_watches.pop(key)
                self.set_status(key, '✓' if not editor.text() else '')
        if not self.send_watches:
            self.watch_timer.stop()

    def refresh_avatar(self, key):
        row = self.viewer.rows.get(key)
        if row is None:
            return
        pixmap = row.avatar.pixmap()
        if pixmap is None or pixmap.isNull():
            return
        for index in range(self.rooms.count()):
            item = self.rooms.item(index)
            if item.data(Qt.UserRole) == key and item.data(Qt.UserRole + 3) != pixmap.cacheKey():
                item.setData(Qt.DecorationRole, pixmap)
                item.setData(Qt.UserRole + 3, pixmap.cacheKey())

    def _room_checked(self, item):
        started = time.perf_counter()
        key = item.data(Qt.UserRole)
        checked = item.checkState() == Qt.Checked
        if checked == (key in self.batch_targets):
            return
        if checked:
            self.batch_targets.add(key)
        else:
            self.batch_targets.discard(key)
            self.rooms.clearSelection()
            if self.rooms.currentItem() is item:
                self.rooms.setCurrentItem(None)
            self.rooms.viewport().update()
        self.rooms.itemDelegate().animate(key, checked)
        self._update_selection()
        self.viewer.playback_probe.timings.record('send_recipient_selection', time.perf_counter() - started)

    def _update_selection(self):
        keys = self.checked_rooms()
        single = len(keys) == 1 and self.rows[keys[0]].tile._plugin_controls._repeat_editor() is not None
        self.pages.setCurrentIndex(0 if single else 1)
        self.pages.setVisible(bool(self.rows))
        if not keys:
            self.selected_room = None
        elif len(keys) == 1:
            self.selected_room = keys[0]
            self.stack.setCurrentWidget(self.rows[keys[0]])
            for index in range(self.rooms.count()):
                if self.rooms.item(index).data(Qt.UserRole) == keys[0]:
                    self.rooms.setCurrentRow(index)
        else:
            self.selected_room = None
        self.batch_panel.update_targets()
        bookmark_layout = self.batch_panel.compose._match_actions.layout()
        if single:
            editor = self.rows[keys[0]].tile._plugin_controls._repeat_editor()
            if isinstance(editor.parentWidget().layout(), QHBoxLayout):
                composer = editor.parentWidget()
                bookmark_layout = getattr(composer, '_match_actions', composer).layout()
        if bookmark_layout.indexOf(self.save_text) < 0:
            self.save_text.parentWidget().layout().removeWidget(self.save_text)
            bookmark_layout.addWidget(self.save_text, 0, Qt.AlignVCenter)
            self.save_text.show()
        self._layout_workspace()
        self.saved_texts.setEnabled(not self.batch_panel.timer.isActive())
        self.refresh_store_state()
        self.refresh_interactions()

    def sync_height(self):
        settings = self.viewer.settings_panel
        if not settings.isHidden():
            self.expanded_without_settings = False
            self.setFixedHeight(settings.height())
        visible = not self.collapsed and (not settings.isHidden() or self.expanded_without_settings)
        self.setVisible(visible)
        if self.viewer.panel.property('joined') != visible:
            self.viewer.panel.setProperty('joined', visible)
            self.viewer.panel.style().unpolish(self.viewer.panel)
            self.viewer.panel.style().polish(self.viewer.panel)
            self.viewer.panel.update()
        self.restore.setVisible(not visible)
        self.restore_bar.setVisible(not visible)
        self.parentWidget().layout().activate()

    def set_collapsed(self, collapsed):
        self.collapsed = collapsed
        self.expanded_without_settings = not collapsed and self.viewer.settings_panel.isHidden()
        self.sync_height()

    def hideEvent(self, event):
        self.batch_panel.stop()
        super().hideEvent(event)
