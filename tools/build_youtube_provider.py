"""Package a compiled, npm-ci prepared bgutil tree; no dependencies run in the host Python."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {
    'repository': 'https://github.com/Brainicism/bgutil-ytdlp-pot-provider',
    'commit': '26475e9d3665b972c4aeb83b4dbaafcf64b88438',
    'version': '2.0.2',
    'local_changes': ['Remove optional canvas native addon from package.json and regenerate package-lock.json; use jsdom without canvas.'],
    'build': ['npm ci --ignore-scripts', 'node node_modules/typescript/bin/tsc'],
}


def build(source):
    server = source / 'server'
    dependencies = subprocess.check_output(
        ['npm.cmd', 'ls', '--omit=dev', '--parseable', '--all'], cwd=server, text=True).splitlines()
    files = {}
    for directory in map(Path, dependencies[1:]):
        for path in directory.rglob('*'):
            if path.is_file() and 'node_modules' not in path.relative_to(directory).parts:
                files['provider/server/' + path.relative_to(server).as_posix()] = path
    for directory in ('build', 'src', 'types'):
        for path in (server / directory).rglob('*'):
            if path.is_file():
                files['provider/server/' + path.relative_to(server).as_posix()] = path
    for name in ('package.json', 'package-lock.json', 'tsconfig.json'):
        files['provider/server/' + name] = server / name
    files['provider/LICENSE'] = source / 'LICENSE'
    files['provider/README.md'] = source / 'README.md'
    for name in ('getpot_bgutil.py', 'getpot_bgutil_script.py'):
        files['provider/plugins/script/yt_dlp_plugins/extractor/' + name] = source / 'plugin/yt_dlp_plugins/extractor' / name
    output = ROOT / 'plugins/youtube_live/youtube-provider.zip'
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        metadata = zipfile.ZipInfo('provider/SOURCE.json', (2026, 11, 17, 0, 0, 0))
        metadata.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(metadata, json.dumps(SOURCE, indent=2).encode('utf-8'))
        for name, path in sorted(files.items()):
            entry = zipfile.ZipInfo(name, (2026, 11, 17, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, path.read_bytes())
    print(json.dumps({'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', type=Path, required=True)
    build(parser.parse_args().provider.resolve())
