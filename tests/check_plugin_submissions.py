"""Offline submission/approval tests with synthetic archives and a fake API."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch
from urllib.request import Request
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import plugin_submission as submission


VALUES = {'id': 'demo_plugin', 'name': '测试插件', 'version': '1.0', 'description': '合成测试插件',
          'source': 'https://github.com/example/demo',
          'download': 'https://github.com/example/demo/releases/download/v1.0/demo_plugin-1.0.zip',
          'requirements': '软件 v0.4，无额外依赖', 'permissions': '无网络，不读取账号'}


def body(values=None):
    values = values or VALUES
    return '\n\n'.join('### ' + label + '\n\n' + values[key] for label, key in submission.FIELDS.items())


def issue(values=None):
    return {'number': 12, 'state': 'open', 'title': '[插件投稿] demo', 'body': body(values),
            'user': {'login': 'example'}}


def archive(extra=(), manifest=None, code=b'raise AssertionError("Never execute submitted code")'):
    stream = io.BytesIO()
    manifest = dict(id=VALUES['id'], name=VALUES['name'], version=VALUES['version'], description='test') if manifest is None else manifest
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('demo_plugin/plugin.json', json.dumps(manifest))
        package.writestr('demo_plugin/plugin.py', code)
        for name, data in extra:
            if isinstance(name, str):
                info = zipfile.ZipInfo('entry')
                info.filename = name  # Keep raw backslashes even on Windows.
                name = info
            package.writestr(name, data)
    return stream.getvalue()


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.issue = issue()
        self.registry = {'schema_version': 1, 'plugins': []}
        self.permission = 'write'
        self.prs = []
        self.refs = []

    def __call__(self, path, payload=None, method=None):
        self.calls.append((path, deepcopy(payload), method))
        if payload is not None:
            return {'html_url': 'https://github.com/example/plugins/pull/9'}
        if path.endswith('/permission'):
            return {'permission': self.permission}
        if path.startswith('/pulls?'):
            return self.prs
        if path == '/issues/12':
            return self.issue
        if path == '/git/ref/heads/develop':
            return {'object': {'sha': 'trusted-base'}}
        if path.startswith('/contents/catalog/community.json?ref='):
            return {'sha': 'registry-blob', 'content': base64.b64encode(submission.canonical(self.registry)).decode()}
        if path == '/contents/plugins?ref=trusted-base':
            return [{'type': 'dir', 'name': 'domestic_live'}]
        if path.startswith('/git/matching-refs/'):
            return self.refs
        raise AssertionError(path)


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.package = archive()
        self.api = FakeAPI()

    def fingerprint(self):
        return submission.candidate(issue(), self.api.registry, set(), lambda _: self.package)[1]

    def test_parse_and_archive_never_imports_plugin(self):
        self.assertEqual(submission.parse_submission(body()), VALUES)
        self.assertEqual(submission.parse_submission(body().replace('\n', '\r\n')), VALUES)
        self.assertEqual(submission.inspect_package(self.package, VALUES), hashlib.sha256(self.package).hexdigest())

    def test_missing_duplicate_and_oversized_fields(self):
        for text in ('', body() + '\n### 插件 ID\ndemo', body().replace('### 版本', '### Missing'), 'x' * 20001):
            with self.subTest(text=text[:30]), self.assertRaises(submission.InvalidSubmission):
                submission.parse_submission(text)

    def test_bad_ids_names_versions(self):
        for key, value in [('id', '../demo'), ('id', '_demo'), ('id', 'Demo'),
                           ('version', '$(run)'), ('version', '1.0-beta'), ('name', 'two\nlines')]:
            with self.subTest(key=key, value=value), self.assertRaises(submission.InvalidSubmission):
                submission.parse_submission(body(dict(VALUES, **{key: value})))

    def test_only_public_github_release_of_same_source_repo(self):
        for key, value in [('source', 'http://github.com/example/demo'),
                           ('source', 'https://[invalid'),
                           ('source', 'https://user:pass@github.com/example/demo'),
                           ('source', 'https://github.com/example/demo/tree/main'),
                           ('download', 'https://127.0.0.1/plugin.zip'),
                           ('download', 'https://github.com/example/other/releases/download/v1/file.zip'),
                           ('download', 'https://github.com/example/demo/archive/main.zip'),
                           ('download', VALUES['download'] + '?token=private'),
                           ('download', VALUES['download'] + '#ignored')]:
            with self.subTest(value=value), self.assertRaises(submission.InvalidSubmission):
                submission.parse_submission(body(dict(VALUES, **{key: value})))

    def test_redirect_allowlist_blocks_ssrf_and_http(self):
        redirect = submission.ReleaseRedirect()
        for url in ('http://github.com/test.zip', 'https://127.0.0.1/file', 'https://evil.example/file',
                    'https://release-assets.githubusercontent.com.evil.example/x',
                    'https://github.com:443/file', 'https://user@github.com/file'):
            with self.subTest(url=url), self.assertRaises(submission.InvalidSubmission):
                redirect.redirect_request(Request(VALUES['download']), None, 302, '', {}, url)
        submission.safe_download_url('https://release-assets.githubusercontent.com/test?signature=example')

    def test_downloader_does_not_send_repository_token(self):
        response = io.BytesIO(self.package)
        with patch.object(submission, 'build_opener') as opener, patch.dict('os.environ', {'GH_TOKEN': 'private-test-token'}):
            opener.return_value.open.return_value = response
            self.assertEqual(submission.download_package(VALUES['download']), self.package)
            request = opener.return_value.open.call_args.args[0]
            self.assertNotIn('Authorization', request.headers)
            self.assertNotIn('private-test-token', str(request.headers))

    def test_package_size_limits(self):
        with patch.object(submission, 'MAX_PACKAGE', 5), self.assertRaises(submission.InvalidSubmission):
            submission.inspect_package(self.package, VALUES)
        with patch.object(submission, 'MAX_UNPACKED', 5), self.assertRaises(submission.InvalidSubmission):
            submission.inspect_package(self.package, VALUES)
        with patch.object(submission, 'MAX_PACKAGE', 5), patch.object(submission, 'build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(self.package)
            with self.assertRaises(submission.InvalidSubmission):
                submission.download_package(VALUES['download'])

    def test_traversal_absolute_multiple_roots_and_windows_paths(self):
        for name in ('../outside', '/outside', 'other/plugin.py', 'demo_plugin/../outside',
                     'demo_plugin//file', 'demo_plugin/C:file', 'demo_plugin\\file'):
            with self.subTest(name=name), self.assertRaises(submission.InvalidSubmission):
                submission.inspect_package(archive([(name, b'test')]), VALUES)

    def test_duplicate_and_case_collisions(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            for name in ('demo_plugin/plugin.py', 'demo_plugin/PLUGIN.py'):
                with self.subTest(name=name), self.assertRaises(submission.InvalidSubmission):
                    submission.inspect_package(archive([(name, b'other')]), VALUES)

    def test_symlinks_and_special_files(self):
        link = zipfile.ZipInfo('demo_plugin/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(submission.InvalidSubmission):
            submission.inspect_package(archive([(link, b'../../outside')]), VALUES)

    def test_windows_reserved_names_and_file_directory_conflicts(self):
        for name in ('demo_plugin/CON.txt', 'demo_plugin/trailing.', 'demo_plugin/file?',
                     'demo_plugin/trailing ', 'demo_plugin'):
            with self.subTest(name=name), self.assertRaises(submission.InvalidSubmission):
                submission.inspect_package(archive([(name, b'test')]), VALUES)
        with self.assertRaises(submission.InvalidSubmission):
            submission.inspect_package(archive([('demo_plugin/file', b'test'), ('demo_plugin/file/child', b'test')]), VALUES)

    def test_too_many_entries_large_manifest_and_missing_entrypoint(self):
        with self.assertRaises(submission.InvalidSubmission):
            submission.inspect_package(archive([(f'demo_plugin/{i}', b'') for i in range(255)]), VALUES)
        manifest = dict(id=VALUES['id'], name=VALUES['name'], version='1.0', description='x' * 65536)
        with self.assertRaises(submission.InvalidSubmission):
            submission.inspect_package(archive(manifest=manifest), VALUES)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as package:
            package.writestr('demo_plugin/plugin.json', '{}')
        with self.assertRaises(submission.InvalidSubmission):
            submission.inspect_package(stream.getvalue(), VALUES)

    def test_manifest_mismatch_and_invalid_json_shapes(self):
        for manifest in ([], dict(id='other', name=VALUES['name'], version='1.0'),
                         dict(id=VALUES['id'], name='other', version='1.0'),
                         dict(id=VALUES['id'], name=VALUES['name'], version=1.0)):
            with self.subTest(manifest=manifest), self.assertRaises(submission.InvalidSubmission):
                submission.inspect_package(archive(manifest=manifest), VALUES)
        with self.assertRaises(submission.InvalidSubmission):
            submission.inspect_package(b'not a zip', VALUES)

    def test_closed_issues_prs_and_duplicate_ids_are_rejected_before_download(self):
        for changed in (dict(issue(), state='closed'), dict(issue(), pull_request={})):
            if 'pull_request' in changed:
                changed['pull_request'] = {'url': 'test'}
            with self.assertRaises(submission.InvalidSubmission):
                submission.candidate(changed, self.api.registry, set(), lambda _: self.fail('Must not download'))
        for registry, official in ((self.api.registry, {'demo_plugin'}),
                                   ({'plugins': [{'id': 'demo_plugin'}]}, set())):
            with self.assertRaises(submission.InvalidSubmission):
                submission.candidate(issue(), registry, official, lambda _: self.fail('Must not download'))

    def test_fingerprint_changes_for_metadata_author_and_package(self):
        original = self.fingerprint()
        for change in (dict(issue(), body=body(dict(VALUES, requirements='new requirement'))),
                       dict(issue(), user={'login': 'other'})):
            self.assertNotEqual(original, submission.candidate(change, self.api.registry, set(), lambda _: self.package)[1])
        self.assertNotEqual(original, submission.candidate(issue(), self.api.registry, set(), lambda _: archive(code=b'changed'))[1])

    def test_approval_checks_permission_and_stale_fingerprint_without_writes(self):
        for allowed in (False, True):
            self.api = FakeAPI()
            self.api.permission = 'write' if allowed else 'read'
            with patch.object(submission, 'download_package', return_value=self.package), self.assertRaises(submission.InvalidSubmission):
                submission.approve(self.api, 'example/plugins', 'maintainer', 12, '0' * 64)
            self.assertFalse(any(payload is not None for _, payload, _ in self.api.calls))

    def test_approval_creates_only_registry_pr_against_develop(self):
        with patch.object(submission, 'download_package', return_value=self.package):
            url = submission.approve(self.api, 'example/plugins', 'maintainer', 12, self.fingerprint())
        self.assertEqual(url, 'https://github.com/example/plugins/pull/9')
        writes = [(path, payload) for path, payload, _ in self.api.calls if payload is not None]
        self.assertEqual([p for p, _ in writes], ['/git/refs', '/contents/catalog/community.json', '/pulls'])
        self.assertEqual(writes[0][1]['sha'], 'trusted-base')
        self.assertEqual(writes[-1][1]['base'], 'develop')
        registry = json.loads(base64.b64decode(writes[1][1]['content']))
        self.assertEqual(registry['plugins'][0]['sha256'], hashlib.sha256(self.package).hexdigest())

    def test_repeat_approval_returns_existing_pr_and_partial_branch_can_resume(self):
        self.api.prs = [{'html_url': 'existing-pr'}]
        self.assertEqual(submission.approve(self.api, 'example/plugins', 'maintainer', 12, self.fingerprint()), 'existing-pr')
        self.assertFalse(any(payload is not None for _, payload, _ in self.api.calls))
        self.api = FakeAPI()
        branch = f'function/plugin-submission-12-{self.fingerprint()[:12]}'
        self.api.refs = [{'ref': 'refs/heads/' + branch}]
        with patch.object(submission, 'download_package', return_value=self.package):
            submission.approve(self.api, 'example/plugins', 'maintainer', 12, self.fingerprint())
        self.assertFalse(any(path == '/git/refs' for path, _, _ in self.api.calls))

    def test_workflow_never_interpolates_issue_body_or_executes_submitted_code(self):
        workflow = (ROOT / '.github/workflows/plugin-submissions.yml').read_text(encoding='utf-8')
        self.assertIn('permissions: {}', workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn('ref: ${{ github.event.repository.default_branch }}', workflow)
        self.assertNotIn('${{ github.event.issue.body', workflow)
        self.assertNotIn('pull_request_target', workflow)
        self.assertNotIn('secrets.', workflow)
        self.assertIn('EXPECTED_FINGERPRINT: ${{ inputs.fingerprint }}', workflow)
        source = (ROOT / 'tools/plugin_submission.py').read_text(encoding='utf-8')
        for unsafe in ('extractall(', 'subprocess.', 'eval(', 'exec('):
            self.assertNotIn(unsafe, source)

    def test_committed_registry_and_form_contract(self):
        registry = json.loads((ROOT / 'catalog/community.json').read_text(encoding='utf-8'))
        self.assertEqual(registry['schema_version'], 1)
        self.assertEqual(len({p['id'] for p in registry['plugins']}), len(registry['plugins']))
        official = {path.name for path in (ROOT / 'plugins').iterdir() if path.is_dir()}
        for entry in registry['plugins']:
            self.assertEqual(submission.parse_submission(body(entry)), {key: entry[key] for key in VALUES})
            self.assertNotIn(entry['id'], official | {'danmaku_log'})
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
            self.assertIs(type(entry['issue']), int)
            self.assertGreater(entry['issue'], 0)
            self.assertRegex(entry['author'], r'^[A-Za-z0-9-]+$')
        form = (ROOT / '.github/ISSUE_TEMPLATE/plugin-submission.yml').read_text(encoding='utf-8')
        for label in submission.FIELDS:
            self.assertIn('label: ' + label, form)
        self.assertIn('title: "[插件投稿] "', form)


if __name__ == '__main__':
    unittest.main()
