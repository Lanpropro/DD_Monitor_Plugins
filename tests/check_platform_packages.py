"""Single-platform ZIP autonomy and legacy migration; no accounts/network/messages."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

parser=argparse.ArgumentParser()
parser.add_argument('--host',type=Path,required=True)
args, remaining=parser.parse_known_args()
sys.path.insert(0,str(args.host.resolve()))
os.environ['DDM_NO_SAVE']='1'
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
from ddm.plugins import PluginManager

KINDS=('huya','douyu','douyin','twitch','youtube','chzzk')


class PackageTests(unittest.TestCase):
    def install(self,manager,plugin_id):
        version='2.0' if plugin_id=='domestic_live' else '1.2' if plugin_id=='global_live' else '0.1'
        manager.install_zip(str(ROOT/'dist'/f'{plugin_id}-{version}.zip'))

    def test_each_zip_runs_without_bundle_or_sibling_plugins(self):
        for kind in KINDS:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                plugin_id=kind+'_live'
                manager=PluginManager(plugins_dir=directory,enabled=[plugin_id])
                self.install(manager,plugin_id)
                try:
                    manager.load()
                    self.assertEqual(set(manager.platforms),{kind})
                    self.assertEqual(manager._platform_owner,{kind:plugin_id})
                    self.assertFalse(manager.skipped)
                    self.assertEqual(len(manager.plugins),1)
                    self.assertEqual(set(path.name for path in Path(directory).iterdir()),{plugin_id})
                    loaded={name.rsplit('.',1)[-1] for name in sys.modules
                            if name.startswith('ddm_plugin_'+plugin_id+'.')}
                    package=Path(directory)/plugin_id
                    for other in KINDS:
                        if other!=kind:
                            self.assertFalse((package/(other+'_platform.py')).exists())
                            self.assertFalse(loaded.intersection({other+'_platform',other+'_accounts',other+'_playback'}))
                finally:
                    manager.unload()

    def test_individual_upgrade_and_backup_rollback_keep_settings_and_other_packages(self):
        from ddm import plugin_updates
        from ddm.file_ops import replace_directory
        for kind in KINDS:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve()
                installed=root/'plugins'
                plugin_id=kind+'_live'
                installer=PluginManager(plugins_dir=str(installed),enabled=[])
                for other in KINDS:
                    self.install(installer,other+'_live')
                settings={other+'_live':{'show_platform_logo':False} for other in KINDS}
                settings['douyin_live'].update(douyin_room_owners={'douyin:12345':'98765'},
                    douyin_follow_profiles={'98765':'MS4wLjAB'+'a'*12})
                installer.plugin_settings=deepcopy(settings)
                original={other:(installed/(other+'_live')/'plugin.json').read_bytes() for other in KINDS}
                upgraded_archive=root/'fixture-upgrade.zip'
                with zipfile.ZipFile(ROOT/'dist'/f'{plugin_id}-0.1.zip') as source, \
                        zipfile.ZipFile(upgraded_archive,'w',zipfile.ZIP_DEFLATED) as destination:
                    for item in source.infolist():
                        content=source.read(item.filename)
                        if item.filename==plugin_id+'/plugin.json':
                            manifest=json.loads(content)
                            manifest['version']='0.2'
                            content=json.dumps(manifest).encode('utf-8')
                        destination.writestr(item,content)
                plugin_updates.stage(installer,str(upgraded_archive),plugin_id,'0.2')
                self.assertEqual(plugin_updates.pending_versions(installer),{plugin_id:'0.2'})
                self.assertEqual((installed/plugin_id/'plugin.json').read_bytes(),original[kind])
                upgraded=PluginManager(plugins_dir=str(installed),enabled=list(settings))
                upgraded.plugin_settings=deepcopy(settings)
                try:
                    upgraded.load()
                    self.assertEqual(set(upgraded.platforms),set(KINDS))
                    current=next(plugin for plugin in upgraded.plugins if plugin.context.name==plugin_id)
                    self.assertEqual(current.version,'0.2')
                    self.assertEqual(upgraded.plugin_settings,settings)
                    restored_settings=deepcopy(upgraded.plugin_settings)
                    disabled=upgraded.enabled-{plugin_id}
                finally:
                    upgraded.unload()
                target=installed/plugin_id
                backup=installed/'.updates'/(plugin_id+'.backup')
                candidate=installed/'.updates'/(plugin_id+'.candidate')
                for path in (target,backup,candidate):
                    self.assertTrue(path.resolve().is_relative_to(root))
                self.assertEqual((backup/'plugin.json').read_bytes(),original[kind])
                replace_directory(target,candidate)
                replace_directory(backup,target)
                restored=PluginManager(plugins_dir=str(installed),enabled=list(disabled))
                restored.plugin_settings=restored_settings
                try:
                    restored.load()
                    self.assertEqual(set(restored.platforms),set(KINDS)-{kind})
                    self.assertEqual(restored.plugin_settings,settings)
                    for other in KINDS:
                        self.assertEqual((installed/(other+'_live')/'plugin.json').read_bytes(),original[other])
                finally:
                    restored.unload()

    def test_all_packages_migrate_without_duplicate_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            installer=PluginManager(plugins_dir=directory,enabled=[])
            for plugin_id in ('domestic_live','global_live')+tuple(kind+'_live' for kind in KINDS):
                self.install(installer,plugin_id)
            manager=PluginManager(plugins_dir=directory,enabled=['domestic_live','global_live'])
            manager.plugin_settings={'domestic_live':{'show_platform_logo':False,
                'douyin_room_owners':{'douyin:12345':'98765'}},'global_live':{'show_platform_logo':False}}
            try:
                manager.load()
                self.assertEqual(set(manager.platforms),set(KINDS))
                self.assertFalse(manager.skipped)
                self.assertEqual(set(manager._platform_owner.values()),{kind+'_live' for kind in KINDS})
                for kind in KINDS:
                    self.assertFalse(manager.plugin_settings[kind+'_live']['show_platform_logo'])
                self.assertEqual(manager.plugin_settings['douyin_live']['douyin_room_owners'],{'douyin:12345':'98765'})
                self.assertNotIn('douyin_room_owners',manager.plugin_settings['huya_live'])
                enabled=manager.enabled-{'youtube_live'}
                settings=deepcopy(manager.plugin_settings)
            finally:
                manager.unload()
            restored=PluginManager(plugins_dir=directory,enabled=list(enabled))
            restored.plugin_settings=settings
            try:
                restored.load()
                self.assertNotIn('youtube',restored.platforms)
                self.assertEqual(set(restored.platforms),set(KINDS)-{'youtube'})
                self.assertNotIn('global_live',{entry['id'] for entry in restored.catalog()})
            finally:
                restored.unload()

    def test_tile_controls_and_logo_settings_have_independent_owners(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication, QCheckBox
        from ddm import plugins as plugin_api
        from ddm.app import MainWindow
        from ddm.dialogs import SettingsDialog
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            installer=PluginManager(plugins_dir=directory,enabled=[])
            for kind in KINDS:
                self.install(installer,kind+'_live')
            state={'plugins_enabled':[kind+'_live' for kind in KINDS],
                'settings':{'recording_enabled':False,'recording_replay_enabled':False,
                            'preview_on_hover':False,'danmaku_enabled':False}}
            with patch.object(plugin_api,'DEFAULT_PLUGINS_DIR',directory), \
                    patch('ddm.app.QTimer.singleShot'), \
                    patch.object(MainWindow,'refresh_status'), patch.object(MainWindow,'refresh_stats'):
                window=MainWindow([],[],state=state,layout_id='corner')
            try:
                self.assertFalse(window.plugins.skipped)
                tile=window.wall.tiles[0]
                for kind in KINDS:
                    tile.set_room({'room_id':kind+':12345','platform':kind,'live':True,'uname':'fixture'})
                    app.processEvents()
                    entries=tile._plugin_controls.entries
                    visible={entry.owner for entry in entries if not entry.control.widget.isHidden()}
                    self.assertEqual(visible,{kind+'_live'} if kind in ('huya','douyu','douyin') else set())
                dialog=SettingsDialog(dict(window.settings),{},window,plugin_manager=window.plugins,preview=True)
                try:
                    dialog.show()
                    app.processEvents()
                    checks=[check for check in dialog.findChildren(QCheckBox)
                            if check.objectName().endswith('_show_platform_logo')]
                    self.assertEqual(len(checks),6)
                    self.assertEqual(len({check.objectName() for check in checks}),6)
                finally:
                    dialog.close()
            finally:
                window.close()
                app.processEvents()

    def test_selected_build_leaves_other_platform_archives_untouched(self):
        import subprocess
        other=ROOT/'dist/twitch_live-0.1.zip'
        before=other.stat().st_mtime_ns
        result=subprocess.run([sys.executable,str(ROOT/'tools/build_plugins.py'),'--plugin','youtube_live'],
                              capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'youtube_live-0.1.zip')
        self.assertEqual(other.stat().st_mtime_ns,before)

    def test_bundled_files_match_the_single_maintained_source(self):
        recipes=json.loads((ROOT/'tools/platform_bundles.json').read_text(encoding='utf-8'))
        for plugin_id,recipe in recipes.items():
            with zipfile.ZipFile(ROOT/'dist'/f'{plugin_id}-0.1.zip') as archive:
                for destination,source in recipe.items():
                    self.assertEqual(archive.read(plugin_id+'/'+destination),(ROOT/source).read_bytes())
                self.assertIsNone(archive.testzip())
                self.assertLess(len(archive.namelist()),256)


if __name__=='__main__':
    unittest.main(argv=[sys.argv[0]]+remaining)
