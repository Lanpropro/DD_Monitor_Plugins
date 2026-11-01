"""Contribution workflow and local documentation links; standard library only."""
from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContributionGuideTests(unittest.TestCase):
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
                     'docs/releases/README.md', 'docs/archive/README.md']
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

    def test_readme_showcases_only_the_maintained_plugin(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('# DD监控室CE · 二路插件（分支修改版）', readme)
        self.assertIn('](plugins/match_sync/README.md)', readme)
        self.assertIn('](https://github.com/Sarff11)', readme)
        self.assertIn('match_sync-', readme)
        for plugin_id in ('domestic_live', 'global_live', 'bili_live'):
            with self.subTest(plugin=plugin_id):
                self.assertNotIn(f'plugins/{plugin_id}/README.md', readme)

    def test_release_notes_are_indexed_in_one_directory(self):
        self.assertFalse(list(ROOT.glob('RELEASE-*.md')))
        directory = ROOT / 'docs/releases'
        index = (directory / 'README.md').read_text(encoding='utf-8')
        notes = list(directory.glob('RELEASE-*.md'))
        self.assertTrue(notes)
        for note in notes:
            with self.subTest(note=note.name):
                self.assertIn(f']({note.name})', index)

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
