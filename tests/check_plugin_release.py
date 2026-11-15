"""Release gate regression; temporary Git histories and synthetic ZIPs only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_check', ROOT / 'tools/verify_plugin_release.py')
release_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_check)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Release Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        plugin = self.root / 'plugins/example'
        plugin.mkdir(parents=True)
        (plugin / 'plugin.json').write_text(json.dumps(dict(id='example', version='1.0')), encoding='utf-8')
        (plugin / 'plugin.py').write_text("raise RuntimeError('never execute')", encoding='utf-8')
        for name in ('LICENSE', 'NOTICE.md'):
            (self.root / name).write_text(name, encoding='utf-8')
        self.git('add', 'plugins', 'LICENSE', 'NOTICE.md')
        self.git('commit', '-qm', 'source')
        self.git('branch', 'develop')
        self.git('branch', 'main')
        self.git('tag', 'example-v1.0')
        self.archive = self.root / 'example-1.0.zip'
        self.write_zip()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'safe.directory=' + self.root.as_posix(), *args], cwd=self.root)

    def write_zip(self, changed=False, extra=False):
        with zipfile.ZipFile(self.archive, 'w') as package:
            for name in ('plugin.json', 'plugin.py'):
                data = (self.root / 'plugins/example' / name).read_bytes()
                package.writestr('example/' + name, b'changed' if changed and name == 'plugin.py' else data)
            for name in ('LICENSE', 'NOTICE.md'):
                package.write(self.root / name, 'example/' + name)
            if extra:
                package.writestr('example/private.json', 'untracked')

    def verify(self):
        return release_check.verify(self.root, 'example', 'example-v1.0', self.archive, ('develop', 'main'))

    def test_exact_integrated_source(self):
        result = self.verify()
        self.assertEqual(result['files'], 4)
        self.assertEqual(len(result['sha256']), 64)

    def test_unintegrated_tag_rejected(self):
        (self.root / 'plugins/example/plugin.py').write_text('new candidate', encoding='utf-8')
        self.git('add', 'plugins/example/plugin.py')
        self.git('commit', '-qm', 'not integrated')
        self.git('tag', '-f', 'example-v1.0')
        with self.assertRaisesRegex(ValueError, 'not integrated'):
            self.verify()

    def test_changed_zip_rejected(self):
        self.write_zip(changed=True)
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            self.verify()

    def test_build_uses_tagged_bytes_not_dirty_worktree(self):
        self.archive.unlink()
        (self.root / 'plugins/example/plugin.py').write_text('uncommitted', encoding='utf-8')
        result = release_check.verify(self.root, 'example', 'example-v1.0', self.archive,
                                      ('develop', 'main'), build=True)
        self.assertEqual(result['files'], 4)
        with zipfile.ZipFile(self.archive) as package:
            self.assertIn(b'never execute', package.read('example/plugin.py'))

    def test_extra_file_rejected(self):
        self.write_zip(extra=True)
        with self.assertRaisesRegex(ValueError, 'file list'):
            self.verify()

    def test_wrong_filename_rejected(self):
        self.archive = self.archive.rename(self.root / 'example-2.0.zip')
        with self.assertRaisesRegex(ValueError, 'filename'):
            self.verify()

    def test_manifest_version_rejected(self):
        (self.root / 'plugins/example/plugin.json').write_text('{"id":"example","version":"2.0"}', encoding='utf-8')
        self.git('add', 'plugins/example/plugin.json')
        self.git('commit', '-qm', 'version mismatch')
        self.git('branch', '-f', 'develop')
        self.git('branch', '-f', 'main')
        self.git('tag', '-f', 'example-v1.0')
        with self.assertRaisesRegex(ValueError, 'Manifest'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
