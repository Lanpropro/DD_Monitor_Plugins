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
        for relative in ('README.md', 'CONTRIBUTING.md', 'docs/repository-layout.md',
                         'docs/releases/README.md', 'docs/archive/README.md'):
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
