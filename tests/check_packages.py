"""校验 ZIP、源码一致、真实宿主导入及平台注册。"""
import argparse
import base64
from base64 import b64decode
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_plugins import build, package_sources  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--plugin", action="append", help="Check only the selected packages")
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    os.chdir(args.host.resolve())  # Resolve the host's bundled VLC DLLs on Windows.
    os.environ["DDM_NO_SAVE"] = "1"
    from ddm.plugins import PluginManager
    from ddm.plugin_icons import decode_catalog_icon
    from PySide6.QtGui import QImage
    versions = {path.parent.name: json.loads(path.read_text(encoding="utf-8"))["version"]
                for path in (ROOT / "plugins").glob("*/plugin.json")}
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert all(f"| `{plugin_id}` | {version} |" in readme for plugin_id, version in versions.items())
    domestic_version = versions["domestic_live"]
    domestic_readme = (ROOT / "plugins/domestic_live/README.md").read_text(encoding="utf-8")
    assert domestic_readme.startswith(f"# 国内直播平台 v{domestic_version}\n")
    assert f"domestic_live-{domestic_version}.zip" in domestic_readme
    assert (ROOT / "docs/releases" / f"RELEASE-domestic-live-v{domestic_version}.md").read_text(encoding="utf-8").startswith(
        f"# 国内直播平台 v{domestic_version}\n")
    match_version = versions["match_sync"]
    match_readme = (ROOT / "plugins/match_sync/README.md").read_text(encoding="utf-8")
    assert match_readme.startswith(f"# 比赛二路同步 v{match_version}\n")
    assert f"match_sync-{match_version}.zip" in match_readme
    notes = ROOT / 'docs/releases' / f"RELEASE-match-sync-v{match_version}.md"
    assert notes.read_text(encoding="utf-8").startswith(f"# 比赛二路同步 v{match_version}\n")
    proxy_version = versions['proxy_control']
    proxy_readme = (ROOT / 'plugins/proxy_control/README.md').read_text(encoding='utf-8')
    assert proxy_readme.startswith(f'# 网络代理 v{proxy_version}\n')
    assert f'proxy_control-{proxy_version}.zip' in proxy_readme
    proxy_notes = ROOT / 'docs/releases' / f'RELEASE-proxy-control-v{proxy_version}.md'
    assert proxy_notes.read_text(encoding='utf-8').startswith(f'# 网络代理 v{proxy_version}\n')
    display_names = {'domestic_live': '国内直播平台', 'global_live': '海外直播平台', 'match_sync': '比赛二路同步', 'bili_live': 'B站直播互动', 'proxy_control': '网络代理', 'huya_live': '虎牙直播', 'douyu_live': '斗鱼直播', 'douyin_live': '抖音直播', 'twitch_live': 'Twitch直播', 'youtube_live': 'YouTube直播', 'chzzk_live': 'CHZZK直播', 'bili_watchlater': 'B站稍后再看', 'm3u_live': 'IPTV Play', 'qie_live': '企鹅体育'}
    display_names['live_translate'] = '实时翻译'
    selected = set(args.plugin or versions)
    with tempfile.TemporaryDirectory() as directory:
        manager = PluginManager(plugins_dir=directory, enabled=[])
        for path in build(args.plugin):
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
                if plugin_id == 'live_translate':
                    assert manifest['icon'] == 'store-icon.png'
                    assert 'live_translate/prepare_runtime.py' in names
                    assert not any('/vendor/' in name or name.endswith(('.onnx', '.gguf', '.exe'))
                                   for name in names)
                    icon = archive.read('live_translate/' + manifest['icon'])
                    assert decode_catalog_icon(base64.b64encode(icon).decode('ascii')) == icon
                if plugin_id in ('m3u_live', 'qie_live'):
                    assert manifest['icon'] == 'icon.png'
                    assert archive.read(f'{plugin_id}/icon.png').startswith(b'\x89PNG\r\n\x1a\n')
                if plugin_id == 'm3u_live':
                    logos = ('cctv', 'cctv1', 'cctv5', 'cctv5plus', 'cctv6', 'cctv13', 'guangzhou', 'hebei', 'iptv')
                    assert all(f'{plugin_id}/logos/{logo}.png' in names for logo in logos)
                if plugin_id == 'qie_live':
                    assert all(f'{plugin_id}/{module}.py' in names for module in ('mobile_source', 'media', 'danmaku'))
                if plugin_id == 'bili_live':
                    assert manifest['author'] == 'reon-nynes'
                    assert manifest['min_app_version'] == '0.5.1'
                if plugin_id == 'match_sync':
                    native = 'match_sync/native/d3d11/'
                    for name in ('presenter.exe', 'mailbox.dll'):
                        assert native + name in names
                        data = archive.read(native + name)
                        offset = int.from_bytes(data[60:64], 'little')
                        assert data[:2] == b'MZ' and data[offset:offset+6] == b'PE\0\0d\x86'
                    modules = ('audio', 'canvas', 'room_widgets', 'comparison', 'room_input',
                               'source_control', 'alignment_control', 'playback_timing', 'chat_control',
                               'presentation', 'picture_control', 'recording_control', 'replay_control',
                               'floating_control', 'viewer_ui', 'console_control', 'host_integration',
                               'session_control', 'chat_panel', 'send_composers', 'chat_clients', 'media_signals',
                               'clock_sync', 'clock_capture', 'clock_dialog', 'clock_recovery', 'danmaku_settings')
                    assert all(f'match_sync/{name}.py' in names for name in modules), \
                        'The refactored Viewer must ship with every owning module'
                    assert 'match_sync/fullscreen.py' in names, 'Fullscreen shell integration must ship with the viewer'
                    assert 'match_sync/recording.py' in names, 'The selectable audio track recorder must ship with the viewer'
                    assert 'match_sync/replay_preview.py' in names and 'match_sync/preview_frames.py' in names, 'Continuous timeline preview must ship with its background frame cache'
                if plugin_id == 'proxy_control':
                    assert 'proxy_control/popover.py' in names and 'proxy_control/profiles.py' in names
                    assert 'proxy_control/visuals.py' in names
                    assert 'proxy_control/core_install.py' in names
                    assert 'proxy_control/metadata.py' in names
                    assert 'proxy_control/regions.py' in names and 'proxy_control/entry_icon.py' in names
                    assert 'proxy_control/connections.py' in names and 'proxy_control/connection_page.py' in names
                    assert 'proxy_control/profile_settings.py' in names
                    assert 'proxy_control/profile_editor.py' in names
                    assert 'proxy_control/vendor/yaml/loader.py' in names
                    assert 'proxy_control/vendor/yaml/LICENSE' in names
                    assert not any(name.endswith('.pyd') for name in names)
                    assert 'proxy_control/flags/cn.svg' in names and 'proxy_control/flags/LICENSE.txt' in names
                    assert 'proxy_control/flags/tw.svg' not in names
                    assert 'proxy_control/drawer.py' not in names
                    assert not any(name.endswith(('preferences.json', 'profiles.json')) for name in names)
                if plugin_id == 'domestic_live':
                    assert 'domestic_live/douyin_native.js' in names, 'Official native input requires its browser script'
                    assert 'domestic_live/douyin_browser.py' in names
                    for module in ('platform_base', 'huya_protocol', 'douyin_errors', 'diagnostic_errors', 'huya_platform', 'douyu_platform',
                                   'douyin_platform', 'platform_logo', 'playback_diagnostics'):
                        assert f'domestic_live/{module}.py' in names, module
                assert f"{plugin_id}-{manifest['version']}.zip" == path.name
                assert archive.read(f"{plugin_id}/LICENSE") == (ROOT / "LICENSE").read_bytes()
                for name in names:
                    source = package_sources(plugin_id).get(name, ROOT / "plugins" / name)
                    if source.is_file():
                        assert archive.read(name) == source.read_bytes()
            assert manager.install_zip(str(path)) == plugin_id
        assert not manager.plugins  # 安装阶段不执行插件代码
        manager.load()
        installed = selected - getattr(manager, '_replaced_plugins', set())
        assert {plugin.context.name for plugin in manager.plugins} == installed and not manager.skipped
        assert all(plugin.version == versions[plugin.context.name] for plugin in manager.plugins)
        if not args.plugin:
            assert set(manager.platforms) == {"huya", "douyu", "douyin", "twitch", "youtube", "chzzk", "m3u", "qie"}
            assert manager.platforms['douyin'].room_input_hint == '抖音直播间链接'
            assert '__ddmDouyinProfile' in manager.platforms['douyin'].profile_browser_init_script
            assert manager._platform_owner['huya'] == 'huya_live'
            assert manager._platform_owner['youtube'] == 'youtube_live'
        assert all(platform.room_input_hint for platform in manager.platforms.values())
        catalog = {entry['id']: entry for entry in manager.catalog()}
        assert set(catalog) == installed
        assert all(entry['name'] == display_names[key] and entry['version'] == versions[key]
                   for key, entry in catalog.items())
        for kind, plugin_id, size in (('m3u', 'm3u_live', 96), ('qie', 'qie_live', 128)):
            if plugin_id in selected:
                assert manager._platform_owner[kind] == plugin_id
                if png := catalog[plugin_id].get('icon_png'):
                    icon = QImage.fromData(b64decode(png), 'PNG')
                    assert icon.size().toTuple() == (size, size)
                    assert icon.pixelColor(0, 0).alpha() == 0
        manager.unload()
    print("PASS: package integrity, source consistency, install without execution and platform registration")


if __name__ == "__main__":
    main()
