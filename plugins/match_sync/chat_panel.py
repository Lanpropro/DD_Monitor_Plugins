"""Chat panel. Existing component ownership and lifecycle are preserved."""
from __future__ import annotations

from ddm.widgets import DanmakuPanel

from .panels import ChatConnectionIcon
from PySide6.QtGui import QCursor, QFont, QImage, QPainter, QPen, QTextCursor
from PySide6.QtCore import QEvent, QPointF, QPropertyAnimation, QTimer, Qt
from ddm import motion, theme


class SendingDanmakuPanel(DanmakuPanel):
    def __init__(self, viewer):
        super().__init__(viewer)
        previous = self.count
        self.count = ChatConnectionIcon(self.header)
        self.count.setObjectName('DanmakuCount')
        self.header.layout().replaceWidget(previous, self.count)
        previous.hide()
        previous.deleteLater()
        self._refresh_count()
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
        self._divider_resize = QTimer(self)
        self._divider_resize.setSingleShot(True)
        self._divider_resize.timeout.connect(self._resize_dividers)
        self.body.viewport().installEventFilter(self)

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

    def eventFilter(self, watched, event):
        if watched is self.body.viewport() and event.type() == QEvent.Resize:
            if event.size().width() != event.oldSize().width():
                self._divider_resize.start(0)
        return super().eventFilter(watched, event)

    def _resize_dividers(self):
        document = self.body.document()
        width = max(1, self.body.viewport().width() - 24)
        scroll_state = self._scroll_state()
        for index, entry in enumerate(self._blocks):
            if not entry.get('_replay_divider'):
                continue
            block = document.findBlockByNumber(index)
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
            image = cursor.charFormat().toImageFormat()
            if image.isValid() and image.width() != width:
                self._block_html(entry)  # Replace this separator's image resource only.
                image.setWidth(width)
                cursor.setCharFormat(image)
        self._restore_scroll_state(scroll_state)

    def _message_clicked(self, index, position):
        if not 0 <= index < len(self._blocks) or self._blocks[index].get('_replay_divider'):
            return
        self._scroll_revision += 1
        self._scroll_trim_remainder = 0.0
        self._follow_tail = False
        self._update_bottom_button()
        self.body._action_block = self.body.document().findBlockByNumber(index)
        self.body.viewport().update()
        def finished():
            self.body._action_block = None
            self.body._hover_at(QPointF(self.body.viewport().mapFromGlobal(QCursor.pos())))
        self.viewer.send_console.show_chat_actions(self._blocks[index], position, finished)
