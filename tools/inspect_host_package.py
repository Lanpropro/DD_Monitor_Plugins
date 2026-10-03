"""Read a PyInstaller host ZIP without launching it or reading user settings."""
import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def inspect_package(package):
    required = ["ddm.live_danmaku", "ddm.global_danmaku", "ddm.auto_quality"]
    with zipfile.ZipFile(package) as archive, tempfile.TemporaryDirectory() as temp:
        executable = next(name for name in archive.namelist()
                          if name.endswith("DD监控室CE-v0.2.exe"))
        path = Path(temp) / "host.exe"
        path.write_bytes(archive.read(executable))
        reader = CArchiveReader(str(path))
        pyz = reader.open_embedded_archive(next(name for name in reader.toc
                                              if name.endswith(".pyz")))
        modules = set(pyz.toc)
        external = archive.namelist()
        missing = [module for module in required if module not in modules
                   and not any(name.endswith(tuple(module.replace(".", "/") + ext
                                                   for ext in (".py", ".pyc", ".pyd")))
                               for name in external)]
    with Path(package).open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"sha256": digest, "required_modules": required,
            "missing_modules": missing,
            "result": "missing_required_host_modules" if missing else "runtime_verification_required"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect_package(args.package), ensure_ascii=False, indent=2))
