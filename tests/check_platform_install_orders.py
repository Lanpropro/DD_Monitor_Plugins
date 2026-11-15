"""Installation order and independent disable across startup; isolated from GUI lifetime tests."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

parser=argparse.ArgumentParser()
parser.add_argument('--host',type=Path,required=True)
args, remaining=parser.parse_known_args()
sys.path.insert(0,str(args.host.resolve()))
os.environ['DDM_NO_SAVE']='1'
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
from ddm.plugins import PluginManager

KINDS=('huya','douyu','douyin','twitch','youtube','chzzk')


class InstallOrderTests(unittest.TestCase):
    def test_installation_orders_and_each_disabled_platform_preserve_migration(self):
        singles=tuple(kind+'_live' for kind in KINDS)
        orders=(('domestic_live','global_live')+singles,
                singles+('global_live','domestic_live'),
                ('domestic_live','huya_live','twitch_live','global_live','douyu_live',
                 'youtube_live','douyin_live','chzzk_live'))
        for order in orders:
            with self.subTest(order=order), tempfile.TemporaryDirectory() as directory:
                installer=PluginManager(plugins_dir=directory,enabled=['domestic_live','global_live'])
                installer.plugin_settings={'domestic_live':{'show_platform_logo':False,
                    'douyin_room_owners':{'douyin:12345':'98765'}},
                    'global_live':{'show_platform_logo':False}}
                for plugin_id in order:
                    version=json.loads((ROOT/"plugins"/plugin_id/"plugin.json").read_text(encoding="utf-8"))["version"]
                    installer.install_zip(str(ROOT/"dist"/f"{plugin_id}-{version}.zip"))
                manager=PluginManager(plugins_dir=directory,enabled=list(installer.enabled))
                manager.plugin_settings=deepcopy(installer.plugin_settings)
                try:
                    manager.load()
                    self.assertEqual(manager._platform_owner,{kind:kind+'_live' for kind in KINDS})
                    self.assertFalse(manager.skipped)
                    for kind in KINDS:
                        self.assertFalse(manager.plugin_settings[kind+'_live']['show_platform_logo'])
                    self.assertEqual(manager.plugin_settings['douyin_live']['douyin_room_owners'],
                                     {'douyin:12345':'98765'})
                    settings=deepcopy(manager.plugin_settings)
                    enabled=set(manager.enabled)
                finally:
                    manager.unload()
                for kind in KINDS:
                    restored=PluginManager(plugins_dir=directory,enabled=list(enabled-{kind+'_live'}))
                    restored.plugin_settings=deepcopy(settings)
                    try:
                        restored.load()
                        self.assertEqual(set(restored.platforms),set(KINDS)-{kind})
                        self.assertNotIn(kind+'_live',restored.enabled)
                        self.assertEqual(restored.plugin_settings,settings)
                        self.assertTrue((Path(directory)/'domestic_live/plugin.py').is_file())
                        self.assertTrue((Path(directory)/'global_live/plugin.py').is_file())
                    finally:
                        restored.unload()


if __name__=="__main__":
    unittest.main(argv=[sys.argv[0]]+remaining)
