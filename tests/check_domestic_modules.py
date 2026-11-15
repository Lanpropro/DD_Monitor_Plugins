"""Domestic feature boundaries and ownership; isolated data, no live requests."""
import argparse
import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
parser.add_argument('--plugins-dir', type=Path)
args, remaining = parser.parse_known_args()
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (args.plugins_dir or ROOT / 'plugins') / 'domestic_live'
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, Signal
from ddm import plugins as api


class Player(QObject):
    stateChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self.decode_mode = 'auto'
        self.player = SimpleNamespace(register_event_callback=Mock(), unregister_event_callback=Mock())


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.manager = api.PluginManager(plugins_dir=str(SOURCE.parent), enabled=['domestic_live'])
        self.manager.load()
        self.addCleanup(self.manager.unload)
        self.assertEqual(set(self.manager.platforms), {"huya", "douyu", "douyin"})
        self.assertNotIn("domestic_live", [name for name, _reason in self.manager.skipped])
        self.entry = sys.modules['ddm_plugin_domestic_live']

    def test_implementation_modules_import_without_running_the_entry(self):
        for name in ('platform_base', 'huya_protocol', 'douyin_errors', 'diagnostic_errors', 'huya_platform', 'douyu_platform',
                     'douyin_platform', 'platform_logo', 'playback_diagnostics'):
            code = '''import importlib, sys, types
from pathlib import Path
sys.path.insert(0, sys.argv[1])
package=types.ModuleType('isolated_domestic')
package.__path__=[sys.argv[2]]
sys.modules[package.__name__]=package
importlib.import_module(package.__name__+'.'+sys.argv[3])
assert not hasattr(package, 'plugin')
'''
            result = subprocess.run([sys.executable, '-c', code, str(args.host.resolve()), str(SOURCE), name],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, (name, result.stderr))

    def test_old_exports_keep_the_same_class_and_protocol_identity(self):
        for name, module in (('LiveQualityPlatform', 'platform_base'), ('NumericLivePlatform', 'platform_base'),
                             ('HuyaPlatform', 'huya_platform'), ('DouyuPlatform', 'douyu_platform'),
                             ('DouyinPlatform', 'douyin_platform'), ('DouyinLookupError', 'douyin_errors'),
                             ('DouyinVerificationRequired', 'douyin_errors'), ('PlatformLogoControl', 'platform_logo'),
                             ('huya_string', 'huya_protocol'), ('huya_wup_request', 'huya_protocol')):
            implementation = importlib.import_module('ddm_plugin_domestic_live.' + module)
            self.assertIs(getattr(self.entry, name), getattr(implementation, name))

    def test_protocol_and_platforms_do_not_depend_on_the_entry_or_ui(self):
        for name in ('platform_base', 'huya_protocol', 'douyin_errors', 'diagnostic_errors', 'huya_platform', 'douyu_platform', 'douyin_platform'):
            tree = ast.parse((SOURCE / (name + '.py')).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, 'plugin', name)
                    self.assertNotIn(node.module, ('platform_logo', 'douyu_composer', 'ddm.dialogs'), name)
                    self.assertFalse((node.module or '').startswith('PySide6.QtWidgets'), name)

    def test_platform_state_is_owned_by_each_adapter_instance(self):
        douyu, douyin = self.manager.platforms['douyu'], self.manager.platforms['douyin']
        other = self.entry.DouyuPlatform()
        douyu._qualities['douyu:1'] = [{'qn': 10000}]
        self.assertEqual(other._qualities, {})
        self.assertEqual(douyin._qualities, {})
        self.assertIsNot(douyu._cdn_lock, other._cdn_lock)
        douyu.report_playback_failure('douyu:1', 'https://cdn.test/opaque', 'STALL')
        self.assertEqual(other._failed_cdns, {})
        self.assertIsNot(self.entry.DouyinPlatform()._chat_delivery, douyin._chat_delivery)

    def test_diagnostic_delegation_preserves_single_owner_and_disconnects_on_unload(self):
        plugin = self.entry.LivePlatformsPlugin()
        tile, player, logs = object(), Player(), []
        plugin.context = SimpleNamespace(window=SimpleNamespace(players={tile: player}), log=logs.append)
        plugin._diagnostic_enabled, plugin._diagnostic_players = True, {}
        records = plugin._diagnostic_players
        payload = {'tile': tile, 'source': SimpleNamespace(room_id='douyin:123')}
        plugin.on_event(api.EVENT_STREAM_RESOLVED, payload)
        plugin.on_event(api.EVENT_STREAM_RESOLVED, payload)
        player.player.register_event_callback.assert_called_once()
        self.assertIs(plugin._diagnostics.host, plugin)
        self.assertIs(plugin._diagnostic_players, records)
        self.assertEqual(len(records), 1)
        player.stateChanged.emit('buffering')
        self.assertEqual(sum('player_state' in line for line in logs), 1)
        plugin.on_unload()
        player.player.unregister_event_callback.assert_called_once()
        self.assertEqual(records, {})
        previous = list(logs)
        player.stateChanged.emit('playing')
        plugin.on_event(api.EVENT_STREAM_RESOLVED, payload)
        self.assertEqual(logs, previous)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
