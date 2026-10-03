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
        plugin_id = manifest_path.parent.name
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert re.fullmatch(r"[a-z][a-z0-9_]*", plugin_id)
        assert manifest["id"] == plugin_id
        version = manifest["version"]
        assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version)
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z", "--", f"plugins/{plugin_id}/"], cwd=ROOT
        ).decode("utf-8").split("\0")
        paths = [Path(path) for path in tracked if path]
        assert {Path(f"plugins/{plugin_id}/{name}") for name in ("plugin.py", "plugin.json")} <= set(paths), \
            "Add plugin files to Git before building"
        archive_path = output / f"{plugin_id}-{version}.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                assert "__pycache__" not in path.parts and path.suffix != ".pyc"
                archive.write(ROOT / path, str(path.relative_to("plugins")).replace("\\", "/"))
            for name in ("LICENSE", "NOTICE.md"):
                archive.write(ROOT / name, f"{plugin_id}/{name}")
        archives.append(archive_path)
        print(archive_path.name)
    assert archives, "No plugins found"
    return archives


if __name__ == "__main__":
    build()
