"""用实际 1.0 代码验证 1.1 插件暂存、重启应用及数据保留。"""
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
            stage(manager, str(ROOT / "dist" / (plugin_id + "-1.1.zip")), plugin_id, "1.1")
            assert pending_versions(manager)[plugin_id] == "1.1"
            assert read_manifest(str(target), plugin_id)["version"] == "1.0"
        apply_pending(manager)
        assert not pending_versions(manager)
        for plugin_id in ("domestic_live", "global_live", "match_sync"):
            target = root / plugin_id
            assert read_manifest(str(target), plugin_id)["version"] == "1.1"
            assert (target / "user-data.txt").read_bytes() == b"preserve user data"
            assert (target / "plugin.py").read_bytes() == (ROOT / "plugins" / plugin_id / "plugin.py").read_bytes()
        manager.enabled = {"domestic_live", "global_live", "match_sync"}
        manager.load()
        assert len(manager.plugins) == 3 and not manager.skipped
        assert all(plugin.version == "1.1" for plugin in manager.plugins)
        manager.unload()
    print("PASS: three plugins 1.0 -> staged 1.1 -> restarted 1.1; source and user data preserved")


if __name__ == "__main__":
    main()
