"""Verify and extract the downloaded core without an external Python runtime."""
from PySide6.QtCore import QThread

from .prepare_core import install


class CoreInstall(QThread):
    def __init__(self, archive, destination, parent=None):
        super().__init__(parent)
        self.archive, self.destination = archive, destination
        self.code = -1

    def run(self):
        try:
            install(self.archive, self.destination, self.isInterruptionRequested)
        except (OSError, ValueError):
            return
        self.code = 0
