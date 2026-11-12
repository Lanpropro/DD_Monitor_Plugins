"""Media signals. Existing component ownership and lifecycle are preserved."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Events(QObject):
    information = Signal(dict)
    state = Signal(str)
    message = Signal(dict)
    reset = Signal()
