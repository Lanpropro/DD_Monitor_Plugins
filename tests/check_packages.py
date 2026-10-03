"""校验 ZIP、源码一致、真实宿主导入及五个平台注册。"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_plugins import build  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    os.environ["DDM_NO_SAVE"] = "1"
    from ddm.plugins import PluginManager
    with tempfile.TemporaryDirectory() as directory:
        manager = PluginManager(plugins_dir=directory, enabled=[])
        for path in build():
            with zipfile.ZipFile(path) as archive:
                assert archive.testzip() is None
                plugin_id = path.name.rsplit("-", 1)[0]
                names = archive.namelist()
                assert all(name.startswith(plugin_id + "/") for name in names)
                assert len(names) == len(set(names))
                manifest = json.loads(archive.read(f"{plugin_id}/plugin.json"))
                assert manifest["id"] == plugin_id
                assert f"{plugin_id}-{manifest['version']}.zip" == path.name
                assert archive.read(f"{plugin_id}/LICENSE") == (ROOT / "LICENSE").read_bytes()
                for name in names:
                    source = ROOT / "plugins" / name
                    if source.is_file():
                        assert archive.read(name) == source.read_bytes()
            assert manager.install_zip(str(path)) == plugin_id
        assert not manager.plugins  # 安装阶段不执行插件代码
        manager.load()
        assert len(manager.plugins) == 2 and not manager.skipped
        assert set(manager.platforms) == {"huya", "douyu", "douyin", "twitch", "youtube"}
        assert manager._platform_owner["huya"] == "domestic_live"
        assert manager._platform_owner["youtube"] == "global_live"
        assert {entry["id"] for entry in manager.catalog()} == {"domestic_live", "global_live"}
        manager.unload()
    print("PASS: package integrity, source consistency, install without execution and five platforms")


if __name__ == "__main__":
    main()
