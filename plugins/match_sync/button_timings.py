"""Button input and local Qt feedback in the opt-in diagnostic viewer."""
from collections import deque
import time
from weakref import WeakKeyDictionary

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractButton


class ButtonTimings(QObject):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.buttons = WeakKeyDictionary()
        self.pending_paint = WeakKeyDictionary()
        viewer.comparison_panel.installEventFilter(self)
        self.pending_panel = None
        self.rows = deque(maxlen=64)
        self.sequence = self.button_count = 0

    def identify(self, button):
        if button not in self.buttons:
            self.button_count += 1
            # Attribute names are code identifiers. Button text can contain chat or room names.
            role = type(button).__name__
            owner = button.parentWidget()
            while owner is not None:
                field = next((name for name, value in vars(owner).items() if value is button), None)
                if field is not None:
                    role = type(owner).__name__ + '.' + field
                    break
                if owner is self.viewer:
                    break
                owner = owner.parentWidget()
            self.buttons[button] = self.button_count, role
        return self.buttons[button]

    def inventory(self):
        controls = []
        sidebar = getattr(self.viewer.window(), 'sidebar', None)
        roles = {}
        if sidebar is not None:
            for owner in (sidebar.navigation, sidebar.workspace_controls):
                for name, item in vars(owner).items():
                    if isinstance(item, QAbstractButton) and name.endswith('button'):
                        roles[item] = type(owner).__name__ + '.' + name
            for (plugin, _), (_, item) in sidebar.workspace_controls.entries.items():
                roles[item] = 'workspace.' + plugin
            for (plugin, _), (_, item, _, _) in sidebar.plugin_pages.entries.items():
                roles[item] = 'follow_page.' + plugin
        for button in self.viewer.window().findChildren(QAbstractButton):
            if button.isVisible() and not button.visibleRegion().isEmpty():
                number, role = self.identify(button)
                controls.append(dict(button=number, role=roles.get(button, role), enabled=button.isEnabled()))
        return controls

    def begin(self, button, source):
        number, role = self.identify(button)
        button.installEventFilter(self)
        self.sequence += 1
        row = dict(sequence=self.sequence, button=number, role=role, source=source, at=time.time())
        started = time.perf_counter()
        self.rows.append(row)
        self.pending_paint[button] = row, started
        if button is self.viewer.compare:
            self.pending_panel = None if button.isChecked() else (row, started)
        QTimer.singleShot(0, self, lambda: self.elapsed(row, 'next_turn_ms', started))
        return row, started

    @staticmethod
    def elapsed(row, key, started):
        row[key] = round((time.perf_counter() - started) * 1000, 3)

    def input(self, button, event):
        if not isinstance(button, QAbstractButton) or not self.viewer.window().isAncestorOf(button):
            return
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self.begin(button, 'mouse_release')
        elif (event.type() == QEvent.KeyRelease and not event.isAutoRepeat()
              and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter)):
            self.begin(button, 'key_release')

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Hide:
            pending = self.pending_paint.pop(watched, None)
            if pending is not None:
                self.elapsed(pending[0], 'button_hidden_ms', pending[1])
        elif kind == QEvent.Paint:
            if watched is self.viewer.comparison_panel and self.pending_panel is not None:
                self.elapsed(self.pending_panel[0], 'next_panel_paint_ms', self.pending_panel[1])
                self.pending_panel = None
            pending = self.pending_paint.pop(watched, None)
            if pending is not None:
                self.elapsed(pending[0], 'next_button_paint_ms', pending[1])
        return False

    def click(self, button):
        if not button.isVisible() or not button.isEnabled():
            raise ValueError('Button unavailable')
        row, _ = self.begin(button, 'mcp_click')
        started = time.perf_counter()
        button.click()
        self.elapsed(row, 'click_handler_ms', started)
        return dict(row)
