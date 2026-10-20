"""Load the real plugin against an isolated host and fake account."""
import argparse
import importlib
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--host', type=Path, required=True)
args, rest = parser.parse_known_args()
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
