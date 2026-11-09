"""Download the pinned official Windows x64 core and verify its published hash."""
import argparse
import hashlib
from pathlib import Path
import shutil
import urllib.request
import zipfile

VERSION = 'v1.19.33'
ASSET = 'mihomo-windows-amd64-compatible-v1.19.33.zip'
URL = 'https://github.com/MetaCubeX/mihomo/releases/download/' + VERSION + '/' + ASSET
SHA256 = '7aaf636a4f2172dcd8ec34ac4f52797b059195e5afd6409508c963e545f8864d'


def install(archive, destination):
    digest = hashlib.sha256()
    with Path(archive).open('rb') as stream:
        for chunk in iter(lambda: stream.read(262144), b''):
            digest.update(chunk)
    if digest.hexdigest() != SHA256:
        raise ValueError('Official core archive checksum mismatch')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        executables = [item for item in package.infolist() if item.filename.endswith('.exe')]
        if len(executables) != 1 or executables[0].file_size > 128 * 1024**2:
            raise ValueError('Unexpected core archive contents')
        temporary = destination.with_suffix('.part')
        with package.open(executables[0]) as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target, 262144)
        temporary.replace(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    archive = args.archive or args.directory / ASSET
    if not args.archive:
        request = urllib.request.Request(URL, headers={'User-Agent': 'DDM-Proxy-Setup'})
        with urllib.request.urlopen(request, timeout=30) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output, 262144)
    destination = args.directory / 'mihomo.exe'
    install(archive, destination)
    print(destination)


if __name__ == '__main__':
    main()
