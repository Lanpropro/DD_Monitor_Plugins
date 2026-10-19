"""Contribution workflow and local documentation links; standard library only."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContributionGuideTests(unittest.TestCase):
    def test_workflow_and_validation_commands(self):
        source = (ROOT / 'CONTRIBUTING.md').read_text(encoding='utf-8')
        for text in ('`main`', '`develop`', 'function/', 'release/', 'archive/',
                     'Draft PR', 'Squash and merge', '--force-with-lease', 'git revert',
                     'git pull --ff-only origin develop', 'npm run docs:build',
                     'npm run test:website', 'python tests/check_contributing.py',
                     'python tests/check_packages.py --host ../DD_Monitor_CE',
                     '源码清单仍为 1.1'):
            with self.subTest(text=text):
                self.assertIn(text, source)

    def test_readme_entry_and_local_links(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('(CONTRIBUTING.md)', readme)
        source = (ROOT / 'CONTRIBUTING.md').read_text(encoding='utf-8')
        for link in re.findall(r'\]\(([^)]+)\)', source):
            if '://' not in link and not link.startswith('#'):
                self.assertTrue((ROOT / link.split('#', 1)[0]).is_file(), link)


if __name__ == '__main__':
    unittest.main()
