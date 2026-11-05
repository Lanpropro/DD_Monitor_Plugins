"""Contribution workflow and local documentation links; standard library only."""
from pathlib import Path
import hashlib
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContributionGuideTests(unittest.TestCase):
    def test_accepted_douyu_quality_change_records_scope_and_source(self):
        source = (ROOT / 'docs/changes/domestic_live.md').read_text(encoding='utf-8')
        for text in ('2026-11-02', '清晰度验收通过', '9f533b6', 'ba058ef',
                     '尚未合入 develop', 'BUG-071 仍待定位'):
            self.assertIn(text, source)

    def test_bili_live_1_1_publication_records_agree(self):
        tag = 'bili-live-v1.1'
        url = f'https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/{tag}'
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        changes = (ROOT / 'docs/changes/bili_live.md').read_text(encoding='utf-8')
        self.assertIn(url, readme)
        self.assertIn('1.1 已发布', readme)
        self.assertIn('## 已发布 v1.1（2026-11-05）', changes)
        self.assertIn(url, (ROOT / 'docs/changes/README.md').read_text(encoding='utf-8'))
        entries = json.loads((ROOT / 'docs/releases/sources.json').read_text(encoding='utf-8'))['entries']
        entry = next(item for item in entries if item['tag'] == tag)
        self.assertEqual(entry['status'], 'online')
        self.assertEqual(entry['release_url'], url)
        self.assertTrue(entry['last_edit_verified'])

    def test_workflow_and_validation_commands(self):
        source = (ROOT / 'CONTRIBUTING.md').read_text(encoding='utf-8')
        for text in ('`main`', '`develop`', 'function/', 'release/', 'archive/',
                     'Draft PR', 'Squash and merge', '--force-with-lease', 'git revert',
                     'git pull --ff-only origin develop',
                     'https://github.com/Lanpropro/DD_Monitor_Website', 'python tests/check_contributing.py',
                     'python tests/check_packages.py --host ../DD_Monitor_CE',
                     '源码清单必须与公开附件一致'):
            with self.subTest(text=text):
                self.assertIn(text, source)

    def test_website_is_maintained_in_the_standalone_repository(self):
        self.assertFalse((ROOT / 'package.json').exists())
        self.assertFalse((ROOT / 'tests/website.test.mjs').exists())
        self.assertFalse((ROOT / 'docs/.vitepress/config.mjs').exists())
        self.assertIn('https://github.com/Lanpropro/DD_Monitor_Website',
                      (ROOT / 'WEBSITE.md').read_text(encoding='utf-8'))

    def test_readme_entry_and_local_links(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('(CONTRIBUTING.md)', readme)
        documents = ['README.md', 'CONTRIBUTING.md', 'docs/repository-layout.md',
                     'docs/releases/README.md', 'docs/archive/README.md',
                     'plugins/DDCut_Replay/README.md']
        documents += [str(path.relative_to(ROOT)) for path in (ROOT / 'docs/changes').glob('*.md')]
        for relative in documents:
            document = ROOT / relative
            source = document.read_text(encoding='utf-8')
            links = re.findall(r'\]\(([^)]+)\)', source)
            links += re.findall(r'href="([^"]+)"', source)
            links += re.findall(r'src="([^"]+)"', source)
            for link in links:
                if '://' not in link and not link.startswith('#'):
                    with self.subTest(document=relative, link=link):
                        self.assertTrue((document.parent / link.split('#', 1)[0]).is_file(), link)

    def test_readme_showcases_each_plugin_with_author_credit(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        cards = re.findall(r'<td\b[^>]*>(.*?)</td>', readme, re.DOTALL)
        manifests = list((ROOT / 'plugins').glob('*/plugin.json'))
        self.assertEqual(len(cards), len(manifests))
        platforms = {
            'domestic_live': {'huya', 'douyu', 'douyin'},
            'global_live': {'twitch', 'youtube'},
            'match_sync': {'bilibili', 'huya', 'douyu', 'douyin', 'twitch', 'youtube'},
            'bili_live': {'bilibili'},
        }
        for path in manifests:
            manifest = json.loads(path.read_text(encoding='utf-8'))
            with self.subTest(plugin=manifest['id']):
                matches = [card for card in cards
                           if f'href="plugins/{manifest["id"]}/README.md"' in card]
                self.assertEqual(len(matches), 1)
                author = manifest.get('author', 'Lanpropro')
                self.assertIn(f'href="https://github.com/{author}"', matches[0])
                self.assertIn('下载插件', matches[0])
                icons = re.findall(r'<img src="docs/assets/platforms/([^"/]+)\.png"', matches[0])
                self.assertEqual(set(icons), platforms[manifest['id']])

    def test_release_notes_are_indexed_in_one_directory(self):
        self.assertFalse(list(ROOT.glob('RELEASE-*.md')))
        directory = ROOT / 'docs/releases'
        index = (directory / 'README.md').read_text(encoding='utf-8')
        notes = list(directory.glob('RELEASE-*.md'))
        self.assertTrue(notes)
        for note in notes:
            with self.subTest(note=note.name):
                self.assertIn(f']({note.name})', index)

    def test_release_archive_sources_match_saved_bodies(self):
        directory = ROOT / 'docs/releases'
        sources = json.loads((directory / 'sources.json').read_text(encoding='utf-8'))
        entries = sources['entries']
        self.assertEqual(len({entry['tag'] for entry in entries}), len(entries))
        self.assertEqual({entry['file'] for entry in entries},
                         {path.name for path in directory.glob('RELEASE-*.md')})
        for entry in entries:
            with self.subTest(tag=entry['tag']):
                data = (directory / entry['file']).read_bytes().replace(b'\r\n', b'\n')
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                if entry['status'] in ('online', 'archived_verified'):
                    self.assertTrue(entry['last_edit_verified'])
                    self.assertEqual(entry['source_kind'], 'github_release')
                    self.assertIn('release_updated_at', entry)
                    if entry['status'] == 'archived_verified':
                        self.assertRegex(entry['source_commit'], r'^[0-9a-f]{40}$')
                        self.assertIn(entry['superseded_by'], {item['tag'] for item in entries})
                        self.assertTrue(entry['plugin_author'])
                        self.assertTrue(entry['release_author'])
                        self.assertIn('archived_at', entry)
                        self.assertTrue(entry['assets'])
                        for asset in entry['assets']:
                            self.assertGreater(asset['size'], 0)
                            self.assertRegex(asset['sha256'], r'^[0-9a-f]{64}$')
                            self.assertIn('/releases/download/' + entry['tag'] + '/', asset['url'])
                elif entry['status'] == 'prepared':
                    self.assertFalse(entry['last_edit_verified'])
                    self.assertEqual(entry['source_kind'], 'repository_candidate')
                    self.assertRegex(entry['base_commit'], r'^[0-9a-f]{40}$')
                    self.assertNotIn('release_id', entry)
                    self.assertIn(f']({entry["file"]})', (directory / 'README.md').read_text(encoding='utf-8'))
                else:
                    self.assertFalse(entry['last_edit_verified'])
                    self.assertIn(entry['source_kind'], ('release_backup', 'repository_history'))
                    if entry['source_kind'] == 'repository_history':
                        self.assertRegex(entry['source_commit'], r'^[0-9a-f]{40}$')

    def test_each_plugin_has_an_incremental_change_log(self):
        directory = ROOT / 'docs/changes'
        index = (directory / 'README.md').read_text(encoding='utf-8')
        for manifest in (ROOT / 'plugins').glob('*/plugin.json'):
            plugin_id = manifest.parent.name
            with self.subTest(plugin=plugin_id):
                note = directory / f'{plugin_id}.md'
                source = note.read_text(encoding='utf-8')
                self.assertIn(f']({note.name})', index)
                self.assertIn(f'`{plugin_id}`', source)
                self.assertIn('## 待发布', source)
                self.assertRegex(source, r'https://github\.com/Lanpropro/DD_Monitor_Plugins/releases/tag/')
        self.assertIn('(docs/changes/README.md)', (ROOT / 'README.md').read_text(encoding='utf-8'))
        self.assertIn('(docs/changes/README.md)', (ROOT / 'CONTRIBUTING.md').read_text(encoding='utf-8'))

    def test_completed_plan_is_archived(self):
        relative = 'plans/2026-10-23-bili-live-experience.md'
        self.assertFalse((ROOT / 'docs/superpowers' / relative).exists())
        self.assertTrue((ROOT / 'docs/archive' / relative).is_file())

    def test_public_release_sources_have_matching_local_notes(self):
        directory = ROOT / 'docs/releases'
        index = (directory / 'README.md').read_text(encoding='utf-8')
        tags = re.findall(
            r'https://github\.com/Lanpropro/DD_Monitor_Plugins/releases/tag/([^\s)]+)',
            index)
        self.assertTrue(tags)
        self.assertEqual(len(tags), len(set(tags)))
        for tag in tags:
            with self.subTest(tag=tag):
                note = directory / f'RELEASE-{tag}.md'
                self.assertIn(f']({note.name})', index)
                heading = note.read_text(encoding='utf-8').splitlines()[0]
                self.assertTrue(heading.startswith('# '))
                self.assertTrue(heading.endswith(' v' + tag.rsplit('-v', 1)[1]))


if __name__ == '__main__':
    unittest.main()
