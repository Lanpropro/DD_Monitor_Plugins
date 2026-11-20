"""Load the real plugin against an isolated host and fake account."""
import argparse
import importlib
import os
from pathlib import Path
import sys
import zipfile

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--source-fixture', action='store_true',
                    help='test installation from a temporary source ZIP; skips release archive validation')
args, rest = parser.parse_known_args()
if args.source_fixture:
    print('[测试] 本次安装使用源码临时 ZIP，发行归档另行验收。', flush=True)
sys.argv = [sys.argv[0], *rest]
HOST = args.host.resolve()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
os.chdir(HOST)
os.environ.setdefault('DDM_NO_SAVE', '1')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, Signal
from ddm.plugins import PluginManager, CredentialSnapshot

manager = PluginManager(plugins_dir=str(ROOT / 'plugins'), enabled=['bili_live'])
entry = manager._load_module('bili_live', str(ROOT / 'plugins/bili_live/plugin.py'))
module_name = 'ddm_plugin_bili_live'
bili = importlib.import_module(module_name + '.bili_api')
bili._device = 'device-test'  # Never discover a real device during offline tests.


def package_tab(popup, key):
    tabs = popup.tabs
    return [tabs.tabData(i) for i in range(tabs.count())].index(key)


def plugin_test_archive(directory):
    if not args.source_fixture:
        return ROOT / 'dist' / 'bili_live-1.1.zip'
    archive = Path(directory) / 'bili-live-fixture.zip'
    source = ROOT / 'plugins' / 'bili_live'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as fixture:
        for path in sorted(source.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
                fixture.write(path, path.relative_to(source.parent))
        for name in ('LICENSE', 'NOTICE.md'):
            fixture.write(ROOT / name, f'bili_live/{name}')
    return archive


class TestSession(QObject):
    changed = Signal(object)
    def __init__(self):
        super().__init__()
        self.SESSION_DATA = self.BILI_JCT = self.BUVID3 = ''
        self.generation = 1
    def _cookies(self):
        return {k:v for k,v in {'SESSDATA':self.SESSION_DATA, 'bili_jct':self.BILI_JCT, 'buvid3':self.BUVID3}.items() if v}
    def snapshot(self):
        return CredentialSnapshot('bilibili', self.generation, self._cookies())
    def set_login_cookies(self, session, csrf='', device=None):
        old = self.SESSION_DATA, self.BILI_JCT
        self.SESSION_DATA, self.BILI_JCT = session, csrf if session else ''
        if device is not None:
            self.BUVID3 = device
        if old != (self.SESSION_DATA, self.BILI_JCT):
            self.generation += 1
    def set_sessdata(self, value):
        self.set_login_cookies(value, self.BILI_JCT if value == self.SESSION_DATA else '')
    def publish_device(self, generation, value):
        if generation == self.generation and not self.BUVID3:
            self.BUVID3 = value

session = TestSession()
