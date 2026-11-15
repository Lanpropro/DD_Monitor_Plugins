"""Verify a plugin ZIP against its integrated, tagged Git source; never execute plugin code."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)


def verify(root, plugin_id, tag, archive, integrated_refs=('origin/develop', 'origin/main'), build=False):
    root, archive = Path(root), Path(archive)
    if not re.fullmatch(r'[a-z][a-z0-9_]*', plugin_id):
        raise ValueError('Invalid plugin ID')
    if not re.fullmatch(re.escape(plugin_id.replace('_', '-')) + r'-v\d+(?:\.\d+){0,3}', tag):
        raise ValueError('Tag must use <plugin-id>-v<version>')
    commit = git(root, 'rev-parse', '--verify', f'refs/tags/{tag}^{{commit}}').decode().strip()
    for ref in integrated_refs:
        result = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, ref], cwd=root,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise ValueError(f'Tag source is not integrated into {ref}')
    prefix = f'plugins/{plugin_id}/'
    tracked = set(git(root, 'ls-tree', '-r', '--name-only', '-z', commit).decode().split('\0')) - {''}
    paths = sorted(path for path in tracked if path.startswith(prefix))
    manifest = json.loads(git(root, 'show', f'{commit}:{prefix}plugin.json'))
    version = tag.rsplit('-v', 1)[1]
    if manifest.get('id') != plugin_id or manifest.get('version') != version:
        raise ValueError('Manifest ID/version does not match the tag')
    if archive.name != f'{plugin_id}-{version}.zip':
        raise ValueError('Archive filename does not match the tagged manifest')
    expected = {plugin_id + '/' + path[len(prefix):]: path for path in paths}
    bundle_path = 'tools/platform_bundles.json'
    if bundle_path in tracked:
        bundles = json.loads(git(root, 'show', f'{commit}:{bundle_path}'))
        for name, source in bundles.get(plugin_id, {}).items():
            destination = plugin_id + '/' + name
            if (not re.fullmatch(r'[^/\\]+', name) or name in ('.', '..')
                    or source not in tracked or destination in expected
                    or name in ('LICENSE', 'NOTICE.md')):
                raise ValueError('Invalid or conflicting tagged bundle input: ' + name)
            expected[destination] = source
    expected.update({f'{plugin_id}/{name}': name for name in ('LICENSE', 'NOTICE.md')})
    if f'{plugin_id}/plugin.py' not in expected:
        raise ValueError('Missing plugin entry')
    if any('__pycache__' in name.split('/') or name.endswith('.pyc') for name in expected):
        raise ValueError('Tracked Python caches must not ship')
    if build:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as package:
            for name, path in expected.items():
                package.writestr(name, git(root, 'show', f'{commit}:{path}'))
    with zipfile.ZipFile(archive) as package:
        files = [item for item in package.infolist() if not item.is_dir()]
        names = [item.filename for item in files]
        if (len(files) > 256 or sum(item.file_size for item in files) > 100 * 1024 * 1024
                or len(names) != len(set(names)) or set(names) != set(expected)):
            raise ValueError('Archive file list/limits differ from tagged source')
        for item in files:
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Archive contains a symlink')
            if package.read(item) != git(root, 'show', f'{commit}:{expected[item.filename]}'):
                raise ValueError('Archive bytes differ from tagged source: ' + item.filename)
    return dict(plugin=plugin_id, version=version, tag=tag, commit=commit,
                size=archive.stat().st_size, sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                files=len(files), integrated_refs=list(integrated_refs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--build', action='store_true', help='Build from tagged Git blobs before verifying; never overwrite')
    args = parser.parse_args()
    print(json.dumps(verify(ROOT, args.plugin, args.tag, args.archive, build=args.build), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
