"""Contribution workflow and local documentation links; standard library only."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContributionGuideTests(unittest.TestCase):
    def test_accepted_douyu_quality_change_records_scope_and_source(self):
        source = (ROOT / 'docs/changes/domestic_live.md').read_text(encoding='utf-8')
        for text in ('2026-11-02', '清晰度验收通过', '9f533b6', 'ba058ef',
                     '尚未合入 develop', 'BUG-071 仍待定位'):
            self.assertIn(text, source)

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
        source = (ROOT / 'CONTRIBUTING.md').read_text(encoding='utf-8')
        for link in re.findall(r'\]\(([^)]+)\)', source):
            if '://' not in link and not link.startswith('#'):
                self.assertTrue((ROOT / link.split('#', 1)[0]).is_file(), link)


if __name__ == '__main__':
    unittest.main()
