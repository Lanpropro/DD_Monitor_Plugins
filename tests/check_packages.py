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
    os.chdir(args.host.resolve())  # Resolve the host's bundled VLC DLLs on Windows.
    os.environ["DDM_NO_SAVE"] = "1"
    from ddm.plugins import PluginManager
    versions = {path.parent.name: json.loads(path.read_text(encoding="utf-8"))["version"]
                for path in (ROOT / "plugins").glob("*/plugin.json")}
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert all(f"| `{plugin_id}` | {version} |" in readme for plugin_id, version in versions.items())
    domestic_version = versions["domestic_live"]
    domestic_readme = (ROOT / "plugins/domestic_live/README.md").read_text(encoding="utf-8")
    assert domestic_readme.startswith(f"# 国内直播平台 v{domestic_version}\n")
    assert f"domestic_live-{domestic_version}.zip" in domestic_readme
    assert (ROOT / f"RELEASE-domestic-live-v{domestic_version}.md").read_text(encoding="utf-8").startswith(
        f"# 国内直播平台 v{domestic_version}\n")
    match_version = versions["match_sync"]
    match_readme = (ROOT / "plugins/match_sync/README.md").read_text(encoding="utf-8")
    assert match_readme.startswith(f"# 比赛二路同步 v{match_version}\n")
    assert f"match_sync-{match_version}.zip" in match_readme
    notes = ROOT / f"RELEASE-match-sync-v{match_version}.md"
    assert notes.read_text(encoding="utf-8").startswith(f"# 比赛二路同步 v{match_version}\n")
    display_names = {"domestic_live": "国内直播平台", "global_live": "海外直播平台", "match_sync": "比赛二路同步", "bili_live": "B站直播互动"}
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
                assert manifest["name"] == display_names[plugin_id]
                assert manifest["version"] == versions[plugin_id]
                if plugin_id == 'bili_live':
                    assert manifest['author'] == 'reon-nynes'
                    assert manifest['min_app_version'] == '0.5.1'
                if plugin_id == 'match_sync':
                    assert 'match_sync/fullscreen.py' in names, 'Fullscreen shell integration must ship with the viewer'
                assert f"{plugin_id}-{manifest['version']}.zip" == path.name
                assert archive.read(f"{plugin_id}/LICENSE") == (ROOT / "LICENSE").read_bytes()
                for name in names:
                    source = ROOT / "plugins" / name
                    if source.is_file():
                        assert archive.read(name) == source.read_bytes()
            assert manager.install_zip(str(path)) == plugin_id
        assert not manager.plugins  # 安装阶段不执行插件代码
        manager.load()
        assert len(manager.plugins) == len(versions) and not manager.skipped
        assert all(plugin.version == versions[plugin.context.name] for plugin in manager.plugins)
        assert set(manager.platforms) == {"huya", "douyu", "douyin", "twitch", "youtube"}
        assert all(platform.room_input_hint for platform in manager.platforms.values())
        assert manager.platforms['douyin'].room_input_hint == '抖音直播间链接'
        assert '__ddmDouyinProfile' in manager.platforms['douyin'].profile_browser_init_script
        assert manager._platform_owner["huya"] == "domestic_live"
        assert manager._platform_owner["youtube"] == "global_live"
        assert {entry["id"] for entry in manager.catalog()} == set(versions)
        assert all(entry["name"] == display_names[entry["id"]] for entry in manager.catalog())
        assert all(entry["version"] == versions[entry["id"]] for entry in manager.catalog())
        manager.unload()
    print("PASS: package integrity, source consistency, install without execution and five platforms")


if __name__ == "__main__":
    main()
