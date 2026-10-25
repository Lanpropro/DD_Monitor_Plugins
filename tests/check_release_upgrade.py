"""验证旧版本插件升级至当前清单版本，保留源码一致性与用户数据。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    os.chdir(args.host.resolve())
    os.environ["DDM_NO_SAVE"] = "1"
    from ddm.plugins import PluginManager, read_manifest
    from ddm.plugin_updates import apply_pending, pending_versions, stage
    versions = {path.parent.name: json.loads(path.read_text(encoding="utf-8"))["version"]
                for path in (ROOT / "plugins").glob("*/plugin.json")}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manager = PluginManager(plugins_dir=str(root), enabled=[])
        for plugin_id in ("domestic_live", "global_live", "match_sync"):
            old_zip = root / (plugin_id + "-1.0.zip")
            paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "252a51f",
                "--", "plugins/" + plugin_id], cwd=ROOT, text=True).splitlines()
            with zipfile.ZipFile(old_zip, "w") as archive:
                for source in paths:
                    data = subprocess.check_output(["git", "show", "252a51f:" + source], cwd=ROOT)
                    archive.writestr(source.removeprefix("plugins/"), data)
            manager.install_zip(str(old_zip))
            target = root / plugin_id
            assert read_manifest(str(target), plugin_id)["version"] == "1.0"
            sentinel = target / "user-data.txt"
            sentinel.write_bytes(b"preserve user data")
            stage(manager, str(ROOT / "dist" / (plugin_id + "-" + versions[plugin_id] + ".zip")), plugin_id, versions[plugin_id])
            assert pending_versions(manager)[plugin_id] == versions[plugin_id]
            assert read_manifest(str(target), plugin_id)["version"] == "1.0"
        apply_pending(manager)
        assert not pending_versions(manager)
        for plugin_id in ("domestic_live", "global_live", "match_sync"):
            target = root / plugin_id
            assert read_manifest(str(target), plugin_id)["version"] == versions[plugin_id]
            assert (target / "user-data.txt").read_bytes() == b"preserve user data"
            assert (target / "plugin.py").read_bytes() == (ROOT / "plugins" / plugin_id / "plugin.py").read_bytes()
        manager.install_zip(str(ROOT / 'dist/bili_live-1.0.zip'))
        manager.enabled = set(versions)
        manager.load()
        assert len(manager.plugins) == len(versions) and not manager.skipped
        assert all(plugin.version == versions[plugin.context.name] for plugin in manager.plugins)
        manager.unload()
    for old_version, ref in (("1.1", "v1.1"), ("1.2", "domestic-live-v1.2")):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_zip = root / f"domestic_live-{old_version}.zip"
            paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", ref,
                "--", "plugins/domestic_live"], cwd=ROOT, text=True).splitlines()
            with zipfile.ZipFile(old_zip, "w") as archive:
                for source in paths:
                    data = subprocess.check_output(["git", "show", ref + ":" + source], cwd=ROOT)
                    archive.writestr(source.removeprefix("plugins/"), data)
            manager = PluginManager(plugins_dir=str(root / "plugins"), enabled=[])
            manager.install_zip(str(old_zip))
            target = root / "plugins/domestic_live"
            assert read_manifest(str(target), "domestic_live")["version"] == old_version
            (target / "user-data.txt").write_bytes(b"preserve previous data")
            current = versions["domestic_live"]
            stage(manager, str(ROOT / "dist" / f"domestic_live-{current}.zip"), "domestic_live", current)
            assert read_manifest(str(target), "domestic_live")["version"] == old_version
            apply_pending(manager)
            assert read_manifest(str(target), "domestic_live")["version"] == current
            assert (target / "user-data.txt").read_bytes() == b"preserve previous data"
            assert (target / "plugin.py").read_bytes() == (ROOT / "plugins/domestic_live/plugin.py").read_bytes()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        old_zip = root / 'match_sync-1.3.zip'
        paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'match-sync-v1.3',
            '--', 'plugins/match_sync'], cwd=ROOT, text=True).splitlines()
        with zipfile.ZipFile(old_zip, 'w') as archive:
            for source in paths:
                data = subprocess.check_output(['git', 'show', 'match-sync-v1.3:' + source], cwd=ROOT)
                archive.writestr(source.removeprefix('plugins/'), data)
        manager = PluginManager(plugins_dir=str(root / 'plugins'), enabled=[])
        manager.install_zip(str(old_zip))
        target = root / 'plugins/match_sync'
        assert read_manifest(str(target), 'match_sync')['version'] == '1.3'
        assert not (target / 'fullscreen.py').exists()
        (target / 'user-data.txt').write_bytes(b'preserve 1.3 settings')
        stage(manager, str(ROOT / 'dist' / ('match_sync-' + versions['match_sync'] + '.zip')),
              'match_sync', versions['match_sync'])
        apply_pending(manager)
        assert read_manifest(str(target), 'match_sync')['version'] == versions['match_sync']
        for name in ('viewer.py', 'fullscreen.py'):
            assert (target / name).read_bytes() == (ROOT / 'plugins/match_sync' / name).read_bytes()
        assert (target / 'user-data.txt').read_bytes() == b'preserve 1.3 settings'
    print("PASS: old plugins upgrade to current versions, including domestic 1.1/1.2 and match_sync 1.3; source and user data preserved")


if __name__ == "__main__":
    main()
