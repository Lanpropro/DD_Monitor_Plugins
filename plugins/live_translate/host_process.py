"""Launch plugin helpers under the host process name for Task Manager grouping."""
import os
from pathlib import Path
import shutil
import sys
import tempfile


def _host_name():
    try:
        from PySide6.QtCore import QCoreApplication
        path = QCoreApplication.applicationFilePath()
    except (ImportError, RuntimeError):
        path = ''
    return Path(path).name if path else (Path(sys.executable).name or 'DD监控室CE.exe')


def _link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


class HostProcess:
    """Create a temporary host-named launch image for one native helper."""

    def __init__(self, target, *, name=None, include_dlls=False):
        self.target = Path(target)
        self.name = name or _host_name()
        self.include_dlls = include_dlls
        self.directory = None
        self.executable = None

    def prepare(self):
        if os.name != 'nt':
            return self.target
        if self.executable is not None:
            return self.executable
        target = self.target.resolve()
        if not target.is_file():
            raise FileNotFoundError(target)
        try:
            directory = Path(tempfile.mkdtemp(prefix='.live-translate-', dir=target.parent))
        except OSError:
            directory = Path(tempfile.mkdtemp(prefix='live-translate-'))
        try:
            executable = directory / self.name
            _link_or_copy(target, executable)
            if self.include_dlls:
                for item in target.parent.iterdir():
                    if item.is_file() and item.suffix.lower() == '.dll':
                        _link_or_copy(item, directory / item.name)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        self.directory = directory
        self.executable = executable
        return executable

    def cleanup(self):
        directory, self.directory = self.directory, None
        self.executable = None
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
