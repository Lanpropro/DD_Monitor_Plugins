"""仅用 Git 跟踪文件生成可导入的单插件 ZIP。"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package_sources(plugin_id):
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", f"plugins/{plugin_id}/"], cwd=ROOT
    ).decode("utf-8").split("\0")
    sources = {str(Path(path).relative_to("plugins")).replace("\\", "/"): ROOT / path
               for path in tracked if path}
    bundles = json.loads((ROOT / "tools/platform_bundles.json").read_text(encoding="utf-8"))
    extra = bundles.get(plugin_id, {})
    tracked_extra = set(subprocess.check_output(["git", "ls-files", "-z", "--", *extra.values()], cwd=ROOT).decode("utf-8").split("\0")) if extra else set()
    for name, source in extra.items():
        assert source in tracked_extra, "Bundle inputs must be tracked by Git"
        assert Path(name).name == name and (ROOT / source).is_file()
        destination = plugin_id + "/" + name
        assert destination not in sources
        sources[destination] = ROOT / source
    return sources


def build(plugin_ids=None):
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archives = []
    selected = set(plugin_ids or ())
    known = {path.parent.name for path in (ROOT / "plugins").glob("*/plugin.json")}
    if selected - known:
        raise ValueError("Unknown plugin IDs: " + ", ".join(sorted(selected - known)))
    for manifest_path in sorted((ROOT / "plugins").glob("*/plugin.json")):
        plugin_id = manifest_path.parent.name
        if selected and plugin_id not in selected:
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert re.fullmatch(r"[a-z][a-z0-9_]*", plugin_id)
        assert manifest["id"] == plugin_id
        version = manifest["version"]
        assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version)
        sources = package_sources(plugin_id)
        assert {f"{plugin_id}/{name}" for name in ("plugin.py", "plugin.json")} <= set(sources), \
            "Add plugin files to Git before building"
        archive_path = output / f"{plugin_id}-{version}.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, source in sorted(sources.items()):
                assert "__pycache__" not in Path(name).parts and source.suffix != ".pyc"
                archive.write(source, name)
            for name in ("LICENSE", "NOTICE.md"):
                archive.write(ROOT / name, f"{plugin_id}/{name}")
        archives.append(archive_path)
        print(archive_path.name)
    assert archives, "No plugins found"
    return archives


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", action="append", help="Build only this plugin ID; may be repeated")
    args = parser.parse_args()
    build(args.plugin)
