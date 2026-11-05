"""仅用 Git 跟踪文件生成可导入的单插件 ZIP。"""
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build():
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archives = []
    for manifest_path in sorted((ROOT / "plugins").glob("*/plugin.json")):
        source_dir = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plugin_id = manifest['id']
        assert re.fullmatch(r"[a-z][a-z0-9_]*", plugin_id)
        assert source_dir.name == plugin_id or (source_dir.name == 'DDCut_Replay' and plugin_id == 'ddcut_replay')
        version = manifest["version"]
        assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version)
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z", "--", str(source_dir.relative_to(ROOT)) + '/'], cwd=ROOT
        ).decode("utf-8").split("\0")
        paths = [Path(path) for path in tracked if path]
        assert {source_dir.relative_to(ROOT) / name for name in ("plugin.py", "plugin.json")} <= set(paths), \
            "Add plugin files to Git before building"
        archive_path = output / f"{plugin_id}-{version}.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                assert "__pycache__" not in path.parts and path.suffix != ".pyc"
                archive.write(ROOT / path, f"{plugin_id}/{path.relative_to(source_dir.relative_to(ROOT)).as_posix()}")
            for name in ("LICENSE", "NOTICE.md"):
                archive.write(ROOT / name, f"{plugin_id}/{name}")
        archives.append(archive_path)
        print(archive_path.name)
    assert archives, "No plugins found"
    return archives


if __name__ == "__main__":
    build()
