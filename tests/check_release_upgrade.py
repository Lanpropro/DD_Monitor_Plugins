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
    historical = {path.split('/')[1] for path in subprocess.check_output(
        ['git','ls-tree','-r','--name-only','252a51f','--','plugins'], cwd=ROOT,
        text=True).splitlines() if path.endswith('/plugin.json')}
    upgrade_ids = set(versions) & historical
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manager = PluginManager(plugins_dir=str(root), enabled=[])
        for plugin_id in sorted(upgrade_ids):
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
        for plugin_id in sorted(upgrade_ids):
            target = root / plugin_id
            assert read_manifest(str(target), plugin_id)["version"] == versions[plugin_id]
            assert (target / "user-data.txt").read_bytes() == b"preserve user data"
            assert (target / "plugin.py").read_bytes() == (ROOT / "plugins" / plugin_id / "plugin.py").read_bytes()
        for plugin_id in sorted(set(versions) - upgrade_ids):
            manager.install_zip(str(ROOT / 'dist' / (plugin_id + '-' + versions[plugin_id] + '.zip')))
        manager.enabled = set(versions)
        manager.load()
        assert len(manager.plugins) == len(versions) and not manager.skipped
        assert all(plugin.version == versions[plugin.context.name] for plugin in manager.plugins)
        manager.unload()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        old_zip = root / "domestic_live-1.1.zip"
        paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "0ee6568",
            "--", "plugins/domestic_live"], cwd=ROOT, text=True).splitlines()
        with zipfile.ZipFile(old_zip, "w") as archive:
            for source in paths:
                data = subprocess.check_output(["git", "show", "0ee6568:" + source], cwd=ROOT)
                archive.writestr(source.removeprefix("plugins/"), data)
        manager = PluginManager(plugins_dir=str(root / "plugins"), enabled=[])
        manager.install_zip(str(old_zip))
        target = root / "plugins/domestic_live"
        assert read_manifest(str(target), "domestic_live")["version"] == "1.1"
        (target / "user-data.txt").write_bytes(b"preserve 1.1 data")
        stage(manager, str(ROOT / "dist" / "domestic_live-1.2.zip"), "domestic_live", "1.2")
        assert read_manifest(str(target), "domestic_live")["version"] == "1.1"
        apply_pending(manager)
        assert read_manifest(str(target), "domestic_live")["version"] == "1.2"
        assert (target / "user-data.txt").read_bytes() == b"preserve 1.1 data"
        assert (target / "plugin.py").read_bytes() == (ROOT / "plugins/domestic_live/plugin.py").read_bytes()
    print("PASS: 1.0 plugins upgrade to current versions; domestic 1.1 -> 1.2; source and user data preserved")


if __name__ == "__main__":
    main()
