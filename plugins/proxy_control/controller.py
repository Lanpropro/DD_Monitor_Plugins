"""Coordinate routing, profile downloads, core lifecycle and local preferences."""
from pathlib import Path
from dataclasses import replace
import sys

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .configuration import MAX_PROFILE, Options, load_options, save_options
from .core import Core
from .environment import Environment
from .network import Download
from .prepare_core import URL, ASSET


class Controller(QObject):
    status = Signal(str)
    busy = Signal(bool)
    nodes = Signal(object, str)
    prepared = Signal(str)
    resources = Signal(str)

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.environment = Environment()
        self.options = load_options(self.directory)
        if not self.options.core:
            self.options.core = str(self.directory / 'runtime/mihomo.exe')
        save_options(self.directory, self.options)
        self.core = Core(self)
        self.core.ready.connect(self._ready)
        self.core.failed.connect(self._failed)
        self.core.selected.connect(self._selected)
        self.download = Download(self)
        self.download.finished.connect(self._downloaded)
        self.task = ''
        self.installer = None
        self.endpoint = ''
        self.message = '导入订阅或节点后，点击“启动代理”'
        self.working = False
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._resources)

    def apply(self, options):
        try:
            options.validate()
        except ValueError as error:
            self._status(str(error))
            return
        try:
            self.stop(announce=False)
            self.options = replace(options, enabled=True)
            self._busy(True)
            if options.source == 'url':
                self.task = 'profile'
                self._status('正在更新订阅…')
                self.download.start(options.profile.strip(), self.directory / 'subscription.txt', MAX_PROFILE)
            else:
                profile = Path(options.profile).read_bytes() if options.source == 'file' else options.profile.encode('utf-8')
                if len(profile) > MAX_PROFILE:
                    raise ValueError('节点内容超过 4 MiB')
                self._start_core(profile)
        except (ValueError, OSError) as error:
            # Only validation messages are displayed; never raw file/network errors.
            self._failed(str(error) if isinstance(error, ValueError) else '无法读取或保存代理资料，请检查数据目录')

    def _start_core(self, profile=b''):
        self._status('正在加载代理内核和节点…')
        self.core.start(self.options, self.directory, self.environment.child_environment(), profile)
        self.timer.start()

    def _ready(self, endpoint, nodes, selected):
        self.environment.apply(endpoint)
        self.endpoint = endpoint
        try:
            save_options(self.directory, self.options)
        except OSError:
            self._failed('无法保存代理设置，请检查数据目录')
            return
        self.nodes.emit(nodes, selected)
        if self.options.node in nodes and self.options.node != selected:
            self.core.select(self.options.node)
        self._busy(False)
        self._status('代理已就绪；新连接生效，正在播放的直播间请重新播放')
        self._resources()

    def select(self, name):
        self.core.select(name)

    def _selected(self, name):
        if name:
            self.options.node = name
            try:
                save_options(self.directory, self.options)
            except OSError:
                self._status('节点已切换，但设置未能保存')
                return
            self._status('节点已切换；已有连接请重新播放后验证')
        else:
            self._status('节点切换失败，请重试')

    def prepare(self):
        if self.core.process is not None:
            self._status('请先停止内置代理，再准备或更新内核')
            return
        self.download.cancel()
        self.task = 'core'
        self._busy(True)
        self._status('正在下载官方代理内核…')
        try:
            self.download.start(URL, self.directory / 'runtime' / ASSET, 48 * 1024**2)
        except OSError:
            self._failed('无法创建内核下载文件')

    def _downloaded(self, path, error):
        task, self.task = self.task, ''
        if error:
            self._failed(error)
            return
        if task == 'profile':
            try:
                self._start_core(path.read_bytes())
            except OSError:
                self._failed('无法读取订阅缓存')
        elif task == 'core':
            installer = self.installer = QProcess(self)
            installer.setStandardOutputFile(QProcess.nullDevice())
            installer.setStandardErrorFile(QProcess.nullDevice())
            installer.errorOccurred.connect(lambda error: self._installed(installer, -1)
                                            if error == QProcess.FailedToStart else None)
            installer.finished.connect(lambda code, _status: self._installed(installer, code))
            installer.start(sys.executable, [str(Path(__file__).with_name('prepare_core.py')),
                            '--directory', str(self.directory / 'runtime'), '--archive', str(path)])

    def _installed(self, installer, code):
        if self.installer is not installer:
            return
        self.installer = None
        installer.deleteLater()
        self._busy(False)
        if code == 0:
            self.options.core = str(self.directory / 'runtime/mihomo.exe')
            self.prepared.emit(self.options.core)
            self._status('内核已校验并准备好，填写订阅或节点后点击“启动代理”')
        else:
            self._status('内核准备失败：下载校验或解压失败，可稍后重试')

    def _failed(self, message):
        self.stop(announce=False)
        self._status(message + '；当前使用原有系统／环境代理')

    def _status(self, message):
        self.message = message
        self.status.emit(message)

    def _busy(self, busy):
        self.working = busy
        self.busy.emit(busy)

    def _resources(self):
        process = self.core.process
        if process is None or not process.processId():
            self.resources.emit('内核未运行；启动内置代理时加载，停止后释放')
            return
        try:
            import psutil
            rss = psutil.Process(process.processId()).memory_info().rss
            self.resources.emit(f'内核进程 RSS：{rss / 1024**2:.1f} MiB；停止后释放')
        except (ImportError, OSError):
            self.resources.emit('内核运行中；当前无法读取进程内存')
        except psutil.Error:
            self.resources.emit('内核进程已退出')

    def stop(self, announce=True):
        self.timer.stop()
        self.download.cancel()
        self.task = ''
        installer, self.installer = self.installer, None
        if installer is not None:
            installer.kill()
            installer.waitForFinished(1000)
            installer.deleteLater()
        self.core.stop()
        self.environment.restore()
        self.endpoint = ''
        self.nodes.emit([], '')
        self._busy(False)
        self._resources()
        if announce:
            self.options.enabled = False
            try:
                save_options(self.directory, self.options)
            except OSError:
                self._status('内置代理已停止，但未能保存停用状态')
                return
            self._status('已停止插件代理，恢复启动插件前的系统／环境代理')
