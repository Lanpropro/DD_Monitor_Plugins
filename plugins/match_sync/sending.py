"""Independent platform composers below the merged chat, one per live room."""
from PySide6.QtCore import QPointF, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QImage, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QApplication, QBoxLayout, QFrame, QHBoxLayout, QListView, QListWidget,
    QListWidgetItem, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from ddm.widgets import DanmakuPanel, Tile
from ddm.danmaku_actions import CommentToolbar
from ddm import motion, theme
from .panels import ConsoleIconButton, SettingsPanel, console_button_style
from .send_design import ACCESSORY_WIDTH, INPUT_HEIGHT, RecipientDelegate, SavedTextDelegate, SavedTextList, align_composer, colors, style_compose, stylesheet


class SendingDanmakuPanel(DanmakuPanel):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self._adding_divider = False
        self._replay_section = False
        self._scroll_animation = QPropertyAnimation(self.body.verticalScrollBar(), b'value', self)
        self._scroll_animation.setDuration(220)
        self._scroll_animation.setEasingCurve(motion.THEME_WAVE_EASING)
        self.body.userScrolled.connect(self._scroll_animation.stop)
        self.bottom_button.setAutoDefault(False)
        self.bottom_button.setDefault(False)
        self.bottom_button.setFocusPolicy(Qt.NoFocus)

    def begin_replay_section(self):
        self._scroll_animation.stop()
        self._replay_section = True
        self._section_scroll = self.body.verticalScrollBar().value()
        self._scroll_revision += 1

    def _restore_scroll_state(self, state):
        if self._replay_section:
            self._scroll_restore_id += 1
            self._follow_tail = True
            self.body.verticalScrollBar().setValue(self._section_scroll)
            return
        if self._scroll_animation.state() == QPropertyAnimation.Running and state[0]:
            self._scroll_animation.setEndValue(self.body.verticalScrollBar().maximum())
            return
        super()._restore_scroll_state(state)

    def end_replay_section(self):
        self._replay_section = False
        self._update_bottom_button()
        bar = self.body.verticalScrollBar()
        if motion.enabled():
            self._scroll_animation.setStartValue(bar.value())
            self._scroll_animation.setEndValue(bar.maximum())
            self._scroll_animation.start()
        else:
            bar.setValue(bar.maximum())

    def add_event(self, event):
        self._adding_divider = bool(event.get('_replay_divider'))
        super().add_event(event)
        self._blocks[-1]['source_room'] = str(event.get('source_room') or '')
        self._blocks[-1]['_replay_divider'] = self._adding_divider
        self._adding_divider = False

    def _block_html(self, entry):
        if self._adding_divider or entry.get('_replay_divider'):
            width = max(1, self.body.viewport().width() - 24)
            line = QImage(width, 80, QImage.Format_ARGB32)
            line.fill(Qt.transparent)
            painter = QPainter(line)
            painter.setPen(QPen(theme.qcolor(theme.TEXT1), 1))
            font = QFont(self.body.font())
            font.setPixelSize(theme.FONT_CONTROL)
            painter.setFont(font)
            text = entry.get('text', '')
            half = painter.fontMetrics().horizontalAdvance(text) / 2 + 12
            painter.drawLine(0, 40, max(0, int(width / 2 - half)), 40)
            painter.drawLine(min(width, int(width / 2 + half)), 40, width, 40)
            painter.drawText(line.rect(), Qt.AlignCenter, text)
            painter.end()
            key = 'replay-separator-' + str(abs(hash(text)))
            self._register_image(key, line)
            return f'<p align="center" style="margin:0;"><img src="{key}" width="{width}" height="80"></p>'
        return super()._block_html(entry)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if any(entry.get('_replay_divider') for entry in getattr(self, '_blocks', ())):
            self._render_all()

    def _message_clicked(self, index, position):
        if not 0 <= index < len(self._blocks) or self._blocks[index].get('_replay_divider'):
            return
        self._scroll_revision += 1
        self._scroll_trim_remainder = 0.0
        self._follow_tail = False
        self._update_bottom_button()
        self.body._action_block = self.body.document().findBlockByNumber(index)
        self.body.viewport().update()
        try:
            self.viewer.send_console.show_chat_actions(self._blocks[index], position)
        finally:
            self.body._action_block = None
            self.body._hover_at(QPointF(self.body.viewport().mapFromGlobal(QCursor.pos())))


class SendRow(QFrame):
    def __init__(self, viewer, row):
        super().__init__()
        self.manager = viewer.context.manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignTop)
        self.composer_bar = QWidget(self)
        self.composer_bar.setFixedHeight(40)
        composer_layout = QHBoxLayout(self.composer_bar)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.composer_bar)
        self._tile_controls = None
        room = next((dict(item) for item in getattr(viewer.context.window, 'rooms', [])
                     if str(item.get('room_id') or '') == row.room_id), {})
        room.update(room_id=row.room_id, uname=row.label(),
                    platform=row.platform.kind if row.platform is not None else 'bilibili')
        self.tile = Tile(room, self)
        self.tile.hide()
        self.manager.attach_tile(self.tile)
        self.tile._plugin_controls.set_panel(self)
        self.unavailable = QLabel('当前平台的发送控件不可用，请检查平台插件是否已启用。')
        self.unavailable.setWordWrap(True)
        layout.addWidget(self.unavailable)
        self.update_name(row)

    def update_name(self, row):
        self.unavailable.setVisible(not self.tile._plugin_controls.applicable())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._tile_controls is not None:
            self._tile_controls.layout_panel()

    def shutdown(self):
        self.manager.detach_tile(self.tile)
        self.hide()
        self.deleteLater()


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
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        self.title = QLabel('弹幕控制台')
        self.title.setObjectName('SendTitle')
        heading.addWidget(self.title, 1)
        self.minimize = ConsoleIconButton('minimize', '最小化弹幕控制台')
        self.minimize.setObjectName('SendMinimize')
        self.minimize.setAccessibleName('最小化发送台')
        self.minimize.setToolTip('收起发送控制台，保留草稿并扩展弹幕栏。')
        self.minimize.clicked.connect(lambda: self.set_collapsed(True))
        heading.addWidget(self.minimize)
        layout.addLayout(heading)
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
        self.body_layout.setSpacing(12)
        self.body_layout.addWidget(self.rooms)
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

    def show_chat_actions(self, event, position):
        event = dict(event)
        text = str(event.get('text') or '')
        menu = self.create_chat_toolbar(event)
        try:
            chosen = menu.exec(position)
            if chosen is menu.copy_action:
                QApplication.clipboard().setText(text)
            elif chosen is menu.save_action:
                self.store_phrase(text)
            elif chosen is menu.repeat_action and menu.repeat_action.isEnabled():
                self.batch_panel.begin(text, event=event)
        finally:
            menu.deleteLater()

    def refresh_design(self):
        self.setStyleSheet(stylesheet())
        c = colors()
        for row in self.rows.values():
            for entry in row.tile._plugin_controls.applicable():
                align_composer(entry.control.widget, self)
        style_compose(self.batch_panel.edit, self.batch_panel.send)
        for editor in self.findChildren(QLineEdit):
            palette = editor.palette()
            palette.setColor(QPalette.Text, QColor(c['text']))
            palette.setColor(QPalette.PlaceholderText, QColor(c['secondary']))
            editor.setPalette(palette)
        self.rooms.viewport().update()
        self.restore.setStyleSheet(console_button_style('QPushButton'))
        self.update()

    def make_interactions(self, parent):
        bar = QWidget(parent)
        bar.setObjectName('SendInteractions')
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for kind, label in (('emote', '选择平台表情'), ('gift', '选择平台礼物')):
            button = ConsoleIconButton(kind, label, bar)
            button.clicked.connect(lambda _checked=False, b=button, k=kind: self.show_interactions(k, b))
            layout.addWidget(button)
        return bar

    def interaction_menu(self, kind, anchor):
        menu = QMenu(anchor)
        platforms = {}
        for key, row in self.rows.items():
            platform = row.tile.room.get('platform', 'bilibili')
            label = {'bilibili': '哔哩哔哩', 'douyu': '斗鱼', 'huya': '虎牙', 'douyin': '抖音'}.get(platform, platform)
            if platform not in platforms:
                platforms[platform] = menu.addMenu(label)
            action = platforms[platform].addAction(self.viewer.rows[key].label())
            composer = next((entry.control.widget for entry in row.tile._plugin_controls.applicable()
                             if callable(getattr(entry.control.widget, 'open_emoticons' if kind == 'emote' else 'open_gifts', None))), None)
            supported = composer is not None and (kind != 'gift' or platform not in ('huya', 'douyin'))
            action.setEnabled(supported)
            if supported:
                action.triggered.connect(lambda _checked=False, w=composer, k=kind: self.open_interaction(w, k, anchor))
            else:
                action.setText(action.text() + '（平台暂不支持）')
        if not platforms:
            menu.addAction('暂无直播间').setEnabled(False)
        return menu

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
        menu = self.interaction_menu(kind, anchor)
        try:
            menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        finally:
            menu.deleteLater()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'body_layout'):
            return
        regular = self.width() >= 520
        self.layout().setContentsMargins(16 if regular else 12, 10 if regular else 8,
                                        16 if regular else 12, 10 if regular else 8)
        self.layout().setSpacing(10 if regular else 6)
        self.body_layout.setSpacing(12 if regular else 6)
        self.body_layout.setDirection(QBoxLayout.LeftToRight if regular else QBoxLayout.TopToBottom)
        row_height = max(52, self.rooms.fontMetrics().height() * 2 + 12)
        if regular:
            self.rooms.setFixedWidth(176)
            self.rooms.setMinimumHeight(0)
            self.rooms.setMaximumHeight(16777215)
            self.rooms.setFlow(QListView.TopToBottom)
            self.rooms.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.rooms.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.rooms.setMinimumWidth(0)
            self.rooms.setMaximumWidth(16777215)
            self.rooms.setFixedHeight(row_height + 8)
            self.rooms.setFlow(QListView.LeftToRight)
            self.rooms.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.rooms.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

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
        self.rooms.blockSignals(True)
        self.rooms.clear()
        for key in self.rows:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if key in self.batch_targets else Qt.Unchecked)
            self.rooms.addItem(item)
            self.refresh_room(key)
        self.rooms.blockSignals(False)
        self.batch_targets.intersection_update(self.rows)
        self._update_selection()
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
        self.saved_texts.setEnabled(not self.batch_panel.timer.isActive())

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

    def set_collapsed(self, collapsed):
        self.collapsed = collapsed
        self.expanded_without_settings = not collapsed and self.viewer.settings_panel.isHidden()
        self.sync_height()

    def hideEvent(self, event):
        self.batch_panel.stop()
        super().hideEvent(event)


class BatchSend(QWidget):
    """One explicit send per checked room; separate composers keep drafts intact."""
    def __init__(self, console):
        super().__init__(console)
        self.console = console
        self.active = None
        self.pending = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setAlignment(Qt.AlignTop)
        self.compose = QWidget(self)
        self.compose.setFixedHeight(40)
        compose = QHBoxLayout(self.compose)
        compose.setContentsMargins(0, 0, 16, 0)
        compose.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText('输入弹幕，Enter 发送')
        self.edit.textChanged.connect(lambda _text: self.update_targets())
        self.edit.returnPressed.connect(self.start)
        compose.addWidget(self.edit, 1)
        self.send = QPushButton('发送')
        self.send.setProperty('sendPrimary', True)
        self.send.clicked.connect(self.start)
        accessories = QWidget(self.compose)
        accessories.setFixedWidth(ACCESSORY_WIDTH)
        extras = QHBoxLayout(accessories)
        extras.setContentsMargins(0, 0, 0, 0)
        self.interactions = console.make_interactions(accessories)
        extras.addWidget(self.interactions)
        self.cancel = QPushButton('取消')
        self.cancel.clicked.connect(self.stop)
        self.cancel.hide()
        self.cancel.setFixedHeight(INPUT_HEIGHT)
        extras.addWidget(self.cancel)
        compose.addWidget(accessories)
        compose.addWidget(self.send)
        layout.addWidget(self.compose)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)

    def update_targets(self):
        if not self.timer.isActive():
            count = len(self.console.checked_rooms())
            self.send.setText('发送')
            self.send.setEnabled(bool(count and self.edit.text().strip()))

    def start(self, *_args):
        self.begin(self.edit.text())

    def begin(self, text, event=None):
        if self.timer.isActive() or not text.strip():
            return
        self.pending = self.console.checked_rooms()
        if not self.pending:
            return
        self.text = text
        self.event = dict(event) if event is not None else {'text': text}
        self.send.setEnabled(False)
        self.cancel.show()
        self.interactions.hide()
        self.edit.setReadOnly(True)
        self.console.rooms.setEnabled(False)
        self.console.saved_texts.setEnabled(False)
        for key in self.pending:
            self.console.set_status(key, '待处理')
        self.timer.start()
        self.advance()

    def advance(self):
        if self.active is not None:
            key, row, editor = self.active
            if key in self.console.viewer.rows and (editor.isReadOnly() or not editor.isEnabled()):
                return
            self.console.set_status(key, ('✓' if not editor.text() and self.event.get('dm_type') != 1 else '')
                                    if key in self.console.viewer.rows else '已移除，已取消')
            row.shutdown()
            self.active = None
        while self.pending:
            key = self.pending.pop(0)
            room = self.console.viewer.rows.get(key)
            if room is None:
                self.console.set_status(key, '已移除，未发送')
                continue
            row = SendRow(self.console.viewer, room)
            row.setParent(self)
            row.hide()
            controls = row.tile._plugin_controls
            editor = controls._repeat_editor()
            if editor is None or not controls.can_repeat(self.event) or (self.event.get('dm_type') != 1 and len(self.text) > editor.maxLength()):
                self.console.set_status(key, '入口不可用或超出长度限制')
                row.shutdown()
                continue
            self.active = (key, row, editor)
            if not controls.repeat_message(self.event, key):
                self.console.set_status(key, '入口不可用或未开始发送')
                row.shutdown()
                self.active = None
                continue
            self.console.set_status(key, '正在处理…')
            return
        self.stop()

    def stop(self, *_args):
        self.timer.stop()
        for key in self.pending:
            self.console.set_status(key, '已取消，未发送')
        self.pending.clear()
        if self.active is not None:
            self.console.set_status(self.active[0], '已取消后续处理，请核对平台结果')
            self.active[1].shutdown()
            self.active = None
        self.edit.setReadOnly(False)
        self.cancel.hide()
        self.interactions.show()
        self.console.rooms.setEnabled(True)
        self.console.saved_texts.setEnabled(True)
        self.update_targets()
