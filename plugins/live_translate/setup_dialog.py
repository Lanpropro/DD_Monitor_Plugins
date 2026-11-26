"""User-confirmed first-use model preparation; no download on construction."""
from pathlib import Path
from dataclasses import replace
import threading
from PySide6.QtCore import QEvent, QObject, QStandardPaths, QTimer, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout
from ddm.ui import HintLabel, SettingsProgressBar, TitleLabel
from .options import Options, with_bundled_defaults
from .panel_controls import RoundedAction
from .runtime_setup import install_models
from .embedded_recognition import runtime_paths

_jobs = set()


class SetupJob(QObject):
    progress = Signal(str, int, int)
    completed = Signal(dict)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, directory, operation=install_models):
        super().__init__()
        self.directory, self.operation = directory, operation
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='model-setup')
        self.finished.connect(self._retire)

    def start(self):
        _jobs.add(self)
        self.thread.start()

    def cancel(self):
        self.cancelled.set()

    def _run(self):
        try:
            values = self.operation(self.directory, self.progress.emit, self.cancelled)
            if not self.cancelled.is_set():
                Options(**values).validate()
                self.completed.emit(values)
        except InterruptedError:
            self.failed.emit('已取消，可稍后重试；已完成的模型文件会复用')
        except Exception as error:
            self.failed.emit(f'模型准备失败（{type(error).__name__}），请检查网络、保存位置和磁盘空间后重试')
        finally:
            self.finished.emit()

    def _retire(self):
        if self.thread.is_alive():
            QTimer.singleShot(20, self, self._retire)
        else:
            _jobs.discard(self)
            self.deleteLater()


class SetupDialog(QDialog):
    prepared = Signal(dict)
    existingRequested = Signal()

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.job = None
        self.setWindowTitle('准备实时翻译模型')
        self.resize(480, 370)
        box = QVBoxLayout(self)
        box.addWidget(TitleLabel('准备本地模型'))
        description = HintLabel('Qwen3-ASR 1.7B 识别 · 音乐检测 · Qwen3 1.7B 翻译\n'
            '全新安装约下载 4.5 GB，建议预留 6 GB 空间。确认后开始，已下载的完整模型文件可复用。')
        box.addWidget(description)
        self.directory = QLineEdit(directory, self)
        self.directory.setAccessibleName('模型保存位置')
        row = QHBoxLayout()
        row.addWidget(self.directory, 1)
        self.browse = RoundedAction('选择位置', self)
        self.browse.clicked.connect(self._browse)
        row.addWidget(self.browse)
        box.addLayout(row)
        self.status = HintLabel('安装插件和打开此窗口不会开始下载。')
        box.addWidget(self.status)
        self.progress = SettingsProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        box.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.existing = RoundedAction('使用已有模型', self)
        self.existing.clicked.connect(self._existing)
        self.later = RoundedAction('稍后', self)
        self.later.clicked.connect(self._later)
        self.download = RoundedAction('下载默认模型', self)
        self.download.setObjectName('PrimaryButton')
        self.download.clicked.connect(self._start)
        for button in (self.existing, self.later, self.download):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        box.addLayout(buttons)

    def _browse(self):
        directory = QFileDialog.getExistingDirectory(self, '模型保存位置', self.directory.text())
        if directory:
            self.directory.setText(directory)

    def _existing(self):
        self.existingRequested.emit()
        self.close()

    def _later(self):
        if self.job is not None:
            self.job.cancel()
            self.status.setText('正在取消，等待当前网络读取结束…')
        else:
            self.close()

    def _start(self):
        if self.job is not None:
            return
        if not self.directory.text().strip():
            self.status.setText('请选择模型保存位置')
            return
        job = self.job = SetupJob(self.directory.text().strip())
        job.progress.connect(self._progress)
        job.failed.connect(self.status.setText)
        job.completed.connect(self._completed)
        job.finished.connect(self._finished)
        for control in (self.download, self.directory, self.browse, self.existing):
            control.setEnabled(False)
        self.later.setText('取消准备')
        self.progress.setRange(0, 0)
        job.start()

    def _progress(self, stage, done, total):
        self.status.setText(stage + (f' · {done / 1024**2:.1f} / {total / 1024**2:.1f} MiB' if total else '…'))
        self.progress.setRange(0, 100 if total else 0)
        if total:
            self.progress.setValue(min(100, done * 100 // total))

    def _finished(self):
        self.job = None
        for control in (self.download, self.directory, self.browse, self.existing):
            control.setEnabled(True)
        self.later.setText('关闭')
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

    def _completed(self, values):
        self.status.setText('模型已准备完成，已填入识别与翻译设置。')
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.prepared.emit(values)

    def closeEvent(self, event):
        if self.job is not None:
            self.job.cancel()
        super().closeEvent(event)


class SetupCoordinator(QObject):
    def __init__(self, context, prepared, existing):
        super().__init__(context.window)
        self.context, self.prepared, self.existing = context, prepared, existing
        self.dialog = None
        self.closed = False
        context.window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.context.window and event.type() == QEvent.Show:
            QTimer.singleShot(0, self, self.offer)
        return False

    def offer(self):
        if self.closed or not self.context.window.isVisible() or self.context.setting('setup_offered', False):
            return
        options = Options(**with_bundled_defaults(self.context.setting('options', {})))
        try:
            replace(options, translation_mode='original' if options.translation_mode != 'local' else 'local',
                    local_prompt='', api_prompt='').validate()
            if not any((path / 'numpy').is_dir() and (path / 'sherpa_onnx').is_dir() for path in runtime_paths(options)):
                self.open()
            return
        except ValueError:
            self.open()

    def open(self):
        if self.closed:
            return
        if self.dialog is None:
            base = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / 'live_translate'
            directory = self.context.setting('setup_directory', str(base))
            self.dialog = SetupDialog(directory, self.context.window)
            self.dialog.prepared.connect(self._prepared)
            self.dialog.existingRequested.connect(self.existing)
        self.context.set_setting('setup_offered', True)
        self.dialog.show()
        self.dialog.raise_()

    def _prepared(self, values):
        self.context.set_setting('setup_directory', self.dialog.directory.text().strip())
        self.prepared(values)

    def close(self):
        self.closed = True
        self.context.window.removeEventFilter(self)
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
