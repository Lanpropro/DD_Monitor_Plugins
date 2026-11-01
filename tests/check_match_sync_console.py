"""The send console must never paint its room strip over the composer."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _host():
    """The console imports ddm, so the host checkout has to be importable."""
    candidates = [os.environ.get('DDM_HOST'), str(ROOT.parent / 'DD_Monitor_CE'),
                  str(ROOT.parent / 'DD_Monitor_CE-main')]
    for candidate in candidates:
        if candidate and (Path(candidate) / 'ddm' / 'widgets.py').is_file():
            return Path(candidate)
    return None


HOST = _host()
if HOST is not None:
    sys.path.insert(0, str(HOST))
if str(ROOT / 'plugins') not in sys.path:
    sys.path.insert(0, str(ROOT / 'plugins'))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

APP = QApplication.instance() or QApplication([])


class _Controls(QObject):
    def set_panel(self, panel):
        self.panel = panel

    def layout_panel(self):
        return None

    def applicable(self):
        return []

    def _repeat_editor(self):
        return None


class _Tile(QWidget):
    def __init__(self, room, parent=None):
        super().__init__(parent)
        self._plugin_controls = _Controls(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText('输入弹幕，Enter 发送')
        layout.addWidget(self.edit, 1)
        self.send = QPushButton('发送', self)
        layout.addWidget(self.send)
        self.hide()


class _Alias(QObject):
    textChanged = Signal(str)

    def text(self):
        return '房间'


class _Avatar(QObject):
    def pixmap(self):
        return None


class _Row:
    def __init__(self, room_id):
        self.room_id = room_id
        self.platform = None
        self.alias = _Alias()
        self.avatar = _Avatar()

    def label(self):
        return '房间'


class _Panel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel('弹幕列表', self), 1)


class _Settings(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(150)


class _Context:
    def __init__(self):
        self.manager = type('_Manager', (), {'attach_tile': lambda self, tile: None,
                                             'detach_tile': lambda self, tile: None})()
        self.window = type('_Window', (), {'rooms': []})()

    def setting(self, key, default=None):
        return default


class _Viewer(QWidget):
    def __init__(self):
        super().__init__()
        self.context = _Context()
        self.panel = _Panel(self)
        self.settings_panel = _Settings()
        self.rows = {'a': _Row('a')}
        self.replay_bar = type('_Bar', (), {'refresh': lambda self, force=False: None})()
        self.skin_visible = False
        self.panel_alpha = 238
        self._split_dragging = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel, 1)


def _console(width, height=150):
    """Build the real console with a stubbed host tile so layout can settle."""
    import match_sync.sending as sending
    with patch.object(sending, 'Tile', _Tile):
        viewer = _Viewer()
        console = sending.SendConsole(viewer)
        viewer.layout().addWidget(console)
        viewer.resize(width, height + 60)
        viewer.show()
        console.set_collapsed(False)
        console.expanded_without_settings = True
        APP.processEvents()
        console.sync_rooms()
        APP.processEvents()
        console.setFixedHeight(height)
        APP.processEvents()
        console._apply_responsive_layout()
        APP.processEvents()
    return console


@unittest.skipIf(HOST is None, 'set DDM_HOST to a DD_Monitor_CE checkout')
class ConsoleLayoutTests(unittest.TestCase):
    """Narrowing the splitter used to stack the strip on top of the composer."""

    def test_the_narrow_layout_keeps_the_room_strip_above_the_composer(self):
        console = _console(348)
        try:
            self.assertFalse(console._regular_layout)
            self.assertGreaterEqual(console.rooms.height(), 52)
            self.assertGreaterEqual(console.work.y(), console.rooms.height(),
                                    'the room strip must not be painted over the composer')
            self.assertGreater(console.work.geometry().top(), console.rooms.geometry().bottom(),
                               'strip and composer must not share a band')
            self.assertLessEqual(console.work.geometry().top() - console.rooms.geometry().bottom(),
                                 console.body_layout.spacing() + 2,
                                 'the two bands should be adjacent, not pushed apart')
        finally:
            console.deleteLater()

    def test_the_regular_layout_keeps_a_176_wide_room_column(self):
        console = _console(653)
        try:
            self.assertTrue(console._regular_layout)
            self.assertEqual(console.rooms.width(), 176)
            self.assertGreaterEqual(console.work.x(), console.rooms.width(),
                                    'the composer must start to the right of the room column')
        finally:
            console.deleteLater()

    def test_the_console_grows_by_the_strip_height_when_narrow(self):
        console = _console(348)
        try:
            self.assertGreater(console.strip_height(), 0)
        finally:
            console.deleteLater()
        console = _console(653)
        try:
            self.assertEqual(console.strip_height(), 0)
        finally:
            console.deleteLater()


if __name__ == '__main__':
    unittest.main(verbosity=2)
