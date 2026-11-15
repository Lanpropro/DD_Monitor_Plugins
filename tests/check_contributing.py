"""Contribution workflow and local documentation links; standard library only."""
from datetime import date
from pathlib import Path
import hashlib
import json
import re
import unittest
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]

METADATA = re.compile(
    r"^> 类型：(?P<type>\S+) ｜ 状态：(?P<status>\S+) ｜ 适用版本：(?P<version>[^｜]+) "
    r"｜ 更新：(?P<updated>\d{4}-\d{2}-\d{2}) ｜ 读者：(?P<readers>.+)$",
    re.MULTILINE,
)


def read(path):
    return path.read_text(encoding='utf-8')


def markdown_links(path):
    return re.findall(r"\]\(([^)]+)\)", read(path))


def resolve(source, target):
    destination = unquote(target.split('#', 1)[0])
    if not destination:
        return None
    return (source.parent / destination).resolve()


def slug(title):
    text = title.strip().lower()
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    return text.strip().replace(" ", "-")


def anchors(path):
    found, result = {}, set()
    for heading in re.findall(r"^#{1,6}\s+(.+)$", read(path), re.MULTILINE):
        base = slug(heading)
        count = found.get(base, 0)
        found[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result



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
            'ddcut_replay': set(),
        }
        for path in manifests:
            manifest = json.loads(path.read_text(encoding='utf-8'))
            with self.subTest(plugin=manifest['id']):
                matches = [card for card in cards
                           if f'href="{path.parent.relative_to(ROOT).as_posix()}/README.md"' in card]
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
                if entry['status'] == 'online':
                    self.assertTrue(entry['last_edit_verified'])
                    self.assertEqual(entry['source_kind'], 'github_release')
                    self.assertIn('release_updated_at', entry)
                else:
                    self.assertFalse(entry['last_edit_verified'])
                    self.assertIn(entry['source_kind'], ('release_backup', 'repository_history'))
                    if entry['source_kind'] == 'repository_history':
                        self.assertRegex(entry['source_commit'], r'^[0-9a-f]{40}$')

    def test_each_plugin_has_an_incremental_change_log(self):
        directory = ROOT / 'docs/changes'
        index = (directory / 'README.md').read_text(encoding='utf-8')
        for manifest in (ROOT / 'plugins').glob('*/plugin.json'):
            plugin_id = json.loads(manifest.read_text(encoding='utf-8'))['id']
            with self.subTest(plugin=plugin_id):
                note = directory / f'{plugin_id}.md'
                source = note.read_text(encoding='utf-8')
                self.assertIn(f']({note.name})', index)
                self.assertIn(f'`{plugin_id}`', source)
                self.assertIn('## 待发布', source)
                if plugin_id == 'ddcut_replay':
                    self.assertIn('尚无公开发布基线', source)
                else:
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
        archive = index.split('## 2026-11-03 已归档的在线说明', 1)[1].split('## 已删除 Release', 1)[0]
        tags = re.findall(
            r'https://github\.com/Lanpropro/DD_Monitor_Plugins/releases/tag/([^\s)]+)',
            archive)
        self.assertTrue(tags)
        self.assertEqual(len(tags), len(set(tags)))
        for tag in tags:
            with self.subTest(tag=tag):
                note = directory / f'RELEASE-{tag}.md'
                self.assertIn(f']({note.name})', index)
                heading = note.read_text(encoding='utf-8').splitlines()[0]
                self.assertTrue(heading.startswith('# '))
                self.assertTrue(heading.endswith(' v' + tag.rsplit('-v', 1)[1]))

    def test_current_release_index_has_one_version_per_plugin(self):
        index = read(ROOT / 'docs/releases/README.md')
        current = index.split('## 当前正式 Releases', 1)[1].split('## 2026-11-03 已归档', 1)[0]
        rows = re.findall(
            r'^\| ([^|]+) \| v([\d.]+) \| \[[^]]+\]\(https://github\.com/Lanpropro/DD_Monitor_Plugins/releases/tag/([^)]+)\) \|$',
            current, re.MULTILINE)
        self.assertTrue(rows)
        self.assertEqual(len({name for name, _, _ in rows}), len(rows))
        for name, version, tag in rows:
            with self.subTest(plugin=name):
                self.assertTrue(tag.endswith('-v' + version))


    def test_documentation_center_indexes_every_document(self):
        readme = read(ROOT / 'README.md')
        contributing = read(ROOT / 'CONTRIBUTING.md')
        self.assertIn('(docs/README.md)', readme)
        self.assertIn('(docs/README.md)', contributing)
        self.assertIn('(docs/standards/documentation.md)', readme)
        self.assertIn('(docs/development/README.md)', readme)
        start = ROOT / 'docs/README.md'
        seen, queue = set(), [start]
        while queue:
            document = queue.pop()
            if document in seen:
                continue
            seen.add(document)
            for target in markdown_links(document):
                if '://' in target or target.startswith('mailto:'):
                    continue
                destination = resolve(document, target)
                if (destination is not None and destination.is_file()
                        and destination.suffix.lower() == '.md'
                        and destination.is_relative_to(ROOT / 'docs')):
                    queue.append(destination)
        tracked = {path for path in (ROOT / 'docs').rglob('*.md')}
        missing = sorted(path.relative_to(ROOT).as_posix() for path in tracked - seen)
        self.assertEqual(missing, [], 'documents not linked from docs/README.md')

    def test_maintained_documents_have_metadata(self):
        documents = [
            'docs/README.md', 'docs/development/README.md', 'docs/standards/documentation.md',
            'docs/repository-layout.md', 'docs/changes/README.md', 'docs/releases/README.md',
            'docs/archive/README.md', 'docs/development/match-live-evidence.md',
            'docs/assets/platforms/README.md', 'WEBSITE.md',
        ]
        for relative in documents:
            with self.subTest(document=relative):
                header = '\n'.join(read(ROOT / relative).splitlines()[:8])
                match = METADATA.search(header)
                self.assertIsNotNone(match, 'missing metadata blockquote')
                data = {name: value.strip() for name, value in match.groupdict().items()}
                self.assertIn(data['type'], {'规范', '指南', '参考', '记录', '索引'})
                self.assertIn(data['status'], {'草案', '维护中', '稳定', '冻结', '历史'})
                self.assertTrue(data['version'] in ('全部版本', '不适用')
                                or re.fullmatch(r'\d+\.\d+(\.x)?', data['version']))
                self.assertLessEqual(date.fromisoformat(data['updated']), date.today())
                self.assertTrue(data['readers'])

    def test_all_document_links_and_anchors_resolve(self):
        documents = list((ROOT / 'docs').rglob('*.md'))
        documents += [ROOT / name for name in ('README.md', 'CONTRIBUTING.md', 'WEBSITE.md')]
        for document in documents:
            for target in markdown_links(document):
                target = target.strip()
                if not target or target.startswith('#') or '://' in target or target.startswith('mailto:'):
                    continue
                destination = resolve(document, target)
                if destination is None:
                    continue
                with self.subTest(document=document.relative_to(ROOT).as_posix(), target=target):
                    self.assertTrue(destination.exists(), 'missing link target')
                    if '#' in target and destination.suffix.lower() == '.md':
                        fragment = unquote(target.split('#', 1)[1])
                        self.assertIn(fragment, anchors(destination), 'missing anchor')

    def test_documents_do_not_contain_machine_specific_data(self):
        documents = list((ROOT / 'docs').rglob('*.md'))
        documents += [ROOT / name for name in ('README.md', 'CONTRIBUTING.md', 'WEBSITE.md')]
        for document in documents:
            text = read(document)
            for pattern in ('C:\\Users\\', 'F:\\CodexAppManager', 'SESSDATA=', 'bili_jct='):
                with self.subTest(document=document.relative_to(ROOT).as_posix(), pattern=pattern):
                    self.assertNotIn(pattern, text)


if __name__ == '__main__':
    unittest.main()
