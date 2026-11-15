"""Overseas module ownership and isolated protocol imports; no accounts/network."""
import argparse
import ast
import importlib
import os
from pathlib import Path
import sys
import types
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('isolated_global')
package.__path__ = [str(ROOT / 'plugins/global_live')]
sys.modules[package.__name__] = package


class ModuleTests(unittest.TestCase):
    def test_platforms_have_separate_modules_without_running_entry(self):
        for module, name, kind in [('twitch_platform', 'TwitchPlatform', 'twitch'),
                ('youtube_platform', 'YouTubePlatform', 'youtube'), ('chzzk', 'ChzzkPlatform', 'chzzk')]:
            implementation = importlib.import_module('isolated_global.' + module)
            platform = getattr(implementation, name)()
            self.assertEqual(platform.kind, kind)
            self.assertEqual(type(platform).__module__, 'isolated_global.' + module)
        self.assertFalse(hasattr(package, 'plugin'))

    def test_quality_state_belongs_to_each_instance(self):
        public = importlib.import_module('isolated_global.public_live')
        first, second = public.PublicLivePlatform(), public.PublicLivePlatform()
        first._qualities['fixture'] = []
        self.assertNotIn('fixture', second._qualities)

    def test_account_protocols_import_without_other_platform(self):
        for module in ('twitch_accounts', 'youtube_accounts'):
            tree = ast.parse((ROOT / 'plugins/global_live' / (module + '.py')).read_text(encoding='utf-8'))
            other = 'youtube' if module.startswith('twitch') else 'twitch'
            self.assertFalse(any(isinstance(node, ast.ImportFrom) and other in (node.module or '')
                                 for node in ast.walk(tree)))
            importlib.import_module('isolated_global.' + module)

    def test_entry_only_composes_and_keeps_legacy_exports(self):
        path = ROOT / 'plugins/global_live/plugin.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        self.assertEqual([node.name for node in tree.body if isinstance(node, ast.ClassDef)], ['GlobalLivePlugin'])
        self.assertFalse(any(isinstance(node, ast.FunctionDef) for node in tree.body))
        from ddm.plugins import PluginManager
        manager = PluginManager(plugins_dir=str(ROOT / 'plugins'), enabled=['global_live'])
        try:
            manager.load()
            entry = sys.modules['ddm_plugin_global_live']
            for kind, name in [('twitch', 'TwitchPlatform'), ('youtube', 'YouTubePlatform')]:
                self.assertIsInstance(manager.platforms[kind], getattr(entry, name))
        finally:
            manager.unload()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]] + remaining)
