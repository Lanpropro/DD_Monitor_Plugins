"""Coordinate routing, profile downloads, core lifecycle and local preferences."""
from pathlib import Path
from dataclasses import replace
import sys
import logging
from uuid import uuid4
from collections import deque
from datetime import datetime

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .configuration import MAX_PROFILE, TEST_TARGETS, Options, load_options, save_options
from .core import Core
from .environment import Environment
from .network import ConnectionProbe, Download
from .latency import Latency, MAX_CONCURRENT
from .prepare_core import URL, ASSET
from .profiles import Profile, load_profiles, save_profiles
from .metadata import display_name
from .connections import ConnectionMonitor, ERRORS, DNS_REASONS
from .traffic import parse_userinfo


class Controller(QObject):
    status = Signal(str)
    busy = Signal(bool)
    nodes = Signal(object, str)
    prepared = Signal(str)
    resources = Signal(str)
    profilesChanged = Signal(object, str)
    runningChanged = Signal(bool)
    selectionChanged = Signal(str)
    routingChanged = Signal(str)
    testingChanged = Signal(bool)
    targetChanged = Signal(str)
    eventsChanged = Signal(object)
    resultsChanged = Signal(object)

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.environment = Environment()
        self.options = load_options(self.directory)
        if not self.options.core:
            self.options.core = str(self.directory / 'runtime/mihomo.exe')
        self.profiles = load_profiles(self.directory, self.options)
        if self.profiles and not self.options.profile_id:
            self.options.profile_id = self.profiles[0].id
        current = next((profile for profile in self.profiles if profile.id == self.options.profile_id), None)
        if current:
            self.options.profile_name = current.name
            self.options.user_agent = current.user_agent
        save_profiles(self.directory, self.profiles)
        save_options(self.directory, self.options)
        self.core = Core(self)
        self.events = deque(maxlen=64)
        self.connections = ConnectionMonitor(self.core, self)
        self.runningChanged.connect(self.connections.set_running)
        self.core.diagnostic.connect(lambda kind: self._event('连接错误：' + ERRORS[kind]))
        self.core.ready.connect(self._ready)
        self.core.failed.connect(self._failed)
        self.core.selected.connect(self._selected)
        self.core.modeChanged.connect(self._mode_changed)
        self.download = Download(self)
        self.download.finished.connect(self._downloaded)
        self.latency = Latency(self)
        self._test_names = None
        self._testing_offline = False
        self._test_stats = {}
        self._results = {}
        self._test_key = None
        self._menu_visible = False
        self._auto_test_pending = True
        self.latency.running.connect(self._testing_changed)
        self.latency.result.connect(self._test_result)
        self.latency.detail.connect(self._test_detail)
        self.probe = ConnectionProbe(self)
        self.probe.finished.connect(self._probe_finished)
        self.task = ''
        self.pending = None
        self._pending_usage = None
        self.catalog_only = False
        self.installer = None
        self.endpoint = ''
        self.message = '在“配置”中添加服务商订阅，即可启动代理'
        self.working = False
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._resources)

    def _result_key(self):
        return (self.options.profile_id, self.options.source, self.options.profile,
                self.options.user_agent, self.options.test_target)

    def result_snapshot(self):
        return dict(self._results.get(self._result_key(), {}))

    def menu_visible(self, visible):
        self._menu_visible = visible
        if visible:
            self._maybe_auto_test()
        else:
            self._auto_test_pending = False

    def _maybe_auto_test(self):
        if (self._auto_test_pending and self._menu_visible and not self.working
                and not self.latency.testing and self.node_snapshot()[0]):
            self._auto_test_pending = False
            self.test_nodes(self.node_snapshot()[0])

    def _test_detail(self, name, detail):
        if self._test_key is not None:
            records = self._results.setdefault(self._test_key, {})
            delay, state, _ = records.get(name, (None, 'idle', ''))
            records[name] = (delay, state, detail)

    def apply(self, options, connect=True):
        try:
            options.validate()
        except ValueError as error:
            self._status(str(error))
            return
        try:
            self.stop(announce=False)
            self.catalog_only = not connect
            self.pending = replace(options, enabled=connect)
            self._pending_usage = None
            if self.pending.profile_name in ('', '我的配置'):
                self.pending.profile_name = display_name(options.source, options.profile)
            if not self.pending.profile_id:
                self.pending.profile_id = uuid4().hex
            self._busy(True)
            self._trace('connect' if connect else 'catalog')
            if options.source == 'url':
                self.task = 'profile'
                self._status('正在更新订阅…')
                self.download.start(options.profile.strip(), self.directory / 'subscription.txt', MAX_PROFILE,
                                    user_agent=options.user_agent)
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
        try:
            self.core.start(self.pending or self.options, self.directory, self.environment.child_environment(), profile,
                            routing='global' if self.catalog_only else None)
        except (ValueError, OSError) as error:
            self._failed(str(error) if isinstance(error, ValueError) else '无法准备代理内核的会话资料')
            return
        self.timer.start()

    def _ready(self, endpoint, nodes, selected):
        options = self.pending or self.options
        if not self.catalog_only:
            self.environment.apply(endpoint)
            self.endpoint = endpoint
        preferred = options.node if options.node in nodes else selected
        options.node = preferred
        try:
            previous = next((profile for profile in self.profiles if profile.id == options.profile_id
                             and profile.source == options.source and profile.profile == options.profile), None)
            usage = self._pending_usage if self._pending_usage is not None else (previous.usage if previous else {})
            record = Profile(options.profile_id, options.profile_name.strip() or '我的配置',
                             options.source, options.profile, preferred, dict(self.core.types), options.user_agent, usage)
            profiles = [record if profile.id == record.id else profile for profile in self.profiles]
            if not any(profile.id == record.id for profile in self.profiles):
                profiles.append(record)
            save_profiles(self.directory, profiles)
            save_options(self.directory, options)
        except OSError:
            self._failed('无法保存代理设置，请检查数据目录')
            return
        self.profiles = profiles
        self.options, self.pending = options, None
        self.profilesChanged.emit(self.profiles, record.id)
        if self.catalog_only:
            self.catalog_only = False
            if self._test_names is None:
                self.core.stop()
                self.timer.stop()
            self.nodes.emit(nodes, preferred)
            self.runningChanged.emit(False)
            self._busy(False)
            if self._test_names is not None:
                names, self._test_names = self._test_names, None
                self._testing_offline = True
                self._begin_tests(names)
                return
            self._status(f'已载入 {len(nodes)} 个节点，可先选择节点再启动代理')
            self._resources()
            self._trace('catalog-ready')
            return
        self.nodes.emit(nodes, selected)
        self.runningChanged.emit(True)
        self._busy(False)
        self._status(f'已导入 {len(nodes)} 个节点，代理已启动；正在播放的直播间请重新播放')
        self._resources()
        self._trace('ready')

    def node_snapshot(self):
        if self.endpoint:
            return self.core.nodes, self.core.current, self.core.types
        profile = next((profile for profile in self.profiles if profile.id == self.options.profile_id), None)
        return (list(profile.nodes), profile.node, profile.nodes) if profile else ([], '', {})

    def load_nodes(self):
        if not self.working and not self.endpoint:
            self.apply(self.options, connect=False)

    def select(self, name):
        if self.working:
            return
        if self.endpoint:
            self.core.select(name)
        elif name in self.node_snapshot()[0]:
            self._selected(name)

    def activate_profile(self, identity):
        profile = next((profile for profile in self.profiles if profile.id == identity), None)
        if profile is not None and not self.working:
            self.apply(replace(self.options, source=profile.source, profile=profile.profile,
                               node=profile.node, profile_id=profile.id, profile_name=profile.name,
                               user_agent=profile.user_agent),
                       connect=bool(self.endpoint))

    def set_auto_start(self, enabled):
        self.options.auto_start = enabled
        if self.pending is not None:
            self.pending.auto_start = enabled
        try:
            save_options(self.directory, self.options)
        except OSError:
            self._status('自动连接设置未能保存，请检查数据目录')

    def set_mode(self, mode):
        if mode not in ('rule', 'global', 'direct') or self.working:
            self.routingChanged.emit(self.options.routing)
            return
        if self.endpoint:
            if not self.core.set_mode(mode):
                self.routingChanged.emit(self.options.routing)
        else:
            self._mode_changed(mode)

    def _mode_changed(self, mode):
        if mode:
            self.options.routing = mode
            try:
                save_options(self.directory, self.options)
            except OSError:
                self._status('模式已切换，但设置未能保存')
            else:
                self._status('出站模式已更新；已有直播连接请重新播放后验证')
        else:
            self._status('模式切换失败，保留原模式')
        self.routingChanged.emit(self.options.routing)
        self._trace('mode')

    def test_nodes(self, names):
        self._auto_test_pending = False
        if self.latency.testing or self._test_names is not None or self.probe.reply is not None:
            self.cancel_tests()
            return
        names = list(dict.fromkeys(name for name in names if name in self.node_snapshot()[0]))
        if not names:
            self._status('当前没有可测试的节点，请先载入配置')
            return
        if self.endpoint:
            self._begin_tests(names)
        elif not self.working:
            self.apply(self.options, connect=False)
            if self.working:
                self._test_names = names
                self.testingChanged.emit(True)
                self._status('正在准备节点延迟测试，不启用软件代理…')
                self._trace('latency-prepare')

    def _begin_tests(self, names):
        names = [name for name in names if name in self.core.nodes]
        self._test_key = self._result_key()
        self._results[self._test_key] = {}
        self.resultsChanged.emit(self.result_snapshot())
        self._test_stats = {'total': len(names), 'done': 0, 'ready': 0, 'timeout': 0, 'failed': 0}
        self._status(f'正在测试 0/{len(names)} 个节点，最多同时测试 {MAX_CONCURRENT} 个…')
        self._trace('latency-start')
        target = TEST_TARGETS[self.options.test_target]
        logging.getLogger('ddm.proxy_control').info('proxy latency target=%s timeout_ms=8000', self.options.test_target)
        self.latency.start(self.core.port, self.core.secret, names, target=target)

    def set_test_target(self, target):
        if target in TEST_TARGETS and not self.latency.testing and not self.working and self.probe.reply is None:
            changed = target != self.options.test_target
            self.options.test_target = target
            if changed:
                self._test_stats = {}
                names, selected, _types = self.node_snapshot()
                self.nodes.emit(names, selected)
                self.resultsChanged.emit(self.result_snapshot())
                self._status('检测地址已切换，请重新测延迟')
            try:
                save_options(self.directory, self.options)
            except OSError:
                self._status('检测地址选项未能保存')
        self.targetChanged.emit(self.options.test_target)

    def _test_result(self, name, delay, state):
        if self._test_key is not None:
            records = self._results.setdefault(self._test_key, {})
            detail = records.get(name, (None, 'idle', ''))[2] if state in ('failed', 'timeout') else ''
            records[name] = (delay, state, detail)
            self.resultsChanged.emit(self.result_snapshot())
        if state not in ('ready', 'timeout', 'failed') or not self._test_stats:
            return
        self._test_stats['done'] += 1
        self._test_stats[state] += 1
        stats = self._test_stats
        self._status(f'已测试 {stats["done"]}/{stats["total"]}：成功 {stats["ready"]}，'
                     f'超时 {stats["timeout"]}，失败 {stats["failed"]}')

    def _testing_changed(self, testing):
        if not testing and self._testing_offline:
            stats = self._test_stats
            if stats['total'] and stats['done'] == stats['total'] and stats['ready'] == 0:
                self._busy(True)
                self._status('延迟检测均未成功，正在检查当前预选节点的连接错误…')
                self.probe.start(self.core.endpoint, self.latency.target)
                self.testingChanged.emit(True)
                return
            self._release_test_core()
        self.testingChanged.emit(testing)
        if not testing and self._test_stats:
            logging.getLogger('ddm.proxy_control').info('proxy latency summary %s', self._test_stats)

    def _release_test_core(self):
        self._testing_offline = False
        self.core.stop()
        self.timer.stop()
        self._busy(False)
        self._resources()

    def _probe_finished(self, reachable, code):
        kind = self.core.last_diagnostic or 'unknown'
        reason = self.core.last_dns_reason or 'unknown'
        logging.getLogger('ddm.proxy_control').info('proxy connection diagnostic reachable=%s http=%d kind=%s',
                                                   reachable, code, kind)
        self._release_test_core()
        self.testingChanged.emit(False)
        explanation = ERRORS[kind]
        if kind == 'dns':
            explanation += '（' + DNS_REASONS[reason] + '）'
        self._status('目标地址可连接，但内核未返回有效延迟' if reachable else
                     f'延迟测试失败；当前预选节点诊断：{explanation}。不据此判定全部节点不可用')

    def cancel_tests(self):
        if self.probe.reply is not None:
            self.probe.cancel()
            self._release_test_core()
            self.testingChanged.emit(False)
            self._status('诊断已取消，软件代理保持未启动')
        elif self._test_names is not None:
            self.stop(announce=False)
            self._status('延迟测试已取消，软件代理保持未启动')
        elif self.latency.testing:
            offline = self._testing_offline
            self._testing_offline = False
            self.latency.stop()
            if offline:
                self._release_test_core()
            self._status('延迟测试已取消，软件代理保持未启动' if offline else '延迟测试已取消')

    def _selected(self, name):
        if name:
            self.options.node = name
            try:
                save_options(self.directory, self.options)
                self.profiles = [replace(profile, node=name) if profile.id == self.options.profile_id
                                 else profile for profile in self.profiles]
                save_profiles(self.directory, self.profiles)
            except OSError:
                self._status('节点已切换，但设置未能保存')
                return
            self.selectionChanged.emit(name)
            self._status('节点已切换；已有连接请重新播放后验证' if self.endpoint else '已保存节点选择，启动代理后生效')
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
                self._pending_usage = parse_userinfo(self.download.subscription_userinfo)
                if self.pending.profile_name in ('', '我的配置', display_name(self.pending.source, self.pending.profile)):
                    self.pending.profile_name = display_name(self.pending.source, self.pending.profile,
                                                             self.download.profile_title, self.download.content_disposition)
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
            self._status('内核已校验并准备好，请填写资料并点击输入框下方的导入按钮')
        else:
            self._status('内核准备失败：下载校验或解压失败，可稍后重试')

    def _failed(self, message):
        self.stop(announce=False)
        self._trace('failed')
        logging.getLogger('ddm.proxy_control').warning('proxy failure=%s', message)
        self._status(message + '；当前使用原有系统／环境代理')

    def _status(self, message):
        self.message = message
        self._event(message)
        self.status.emit(message)

    def _event(self, message):
        self.events.append(datetime.now().strftime('%H:%M:%S') + '  ' + message)
        self.eventsChanged.emit(list(self.events))

    def _trace(self, event):
        logging.getLogger('ddm.proxy_control').info('proxy event=%s mode=%s running=%s profiles=%d nodes=%d',
            event, (self.pending or self.options).routing, bool(self.endpoint),
            len(self.profiles), len(self.node_snapshot()[0]))

    def _busy(self, busy):
        self.working = busy
        self.busy.emit(busy)
        if not busy:
            QTimer.singleShot(0, self._maybe_auto_test)

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
        self.connections.set_running(False)
        self.timer.stop()
        self.probe.cancel()
        self._test_names = None
        self._testing_offline = False
        self.latency.stop()
        self.testingChanged.emit(False)
        self.download.cancel()
        self.task = ''
        self.pending = None
        self.catalog_only = False
        installer, self.installer = self.installer, None
        if installer is not None:
            installer.kill()
            installer.waitForFinished(1000)
            installer.deleteLater()
        self.core.stop()
        self.environment.restore()
        self.endpoint = ''
        self._busy(False)
        nodes, selected, _types = self.node_snapshot()
        self.nodes.emit(nodes, selected)
        self.runningChanged.emit(False)
        self.routingChanged.emit(self.options.routing)
        self._resources()
        if announce:
            self.options.enabled = False
            try:
                save_options(self.directory, self.options)
            except OSError:
                self._status('内置代理已停止，但未能保存停用状态')
                return
            self._status('已停止插件代理，恢复启动插件前的系统／环境代理')
            self._trace('stopped')
