"""Platform account boundaries, shared identity state and cancellation; offline only."""
import argparse
import ast
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
for bundle in ('domestic_live', 'global_live'):
    package = ModuleType('accounts_' + bundle)
    package.__path__ = [str(ROOT / 'plugins' / bundle)]
    sys.modules[package.__name__] = package


def module(bundle, name):
    return importlib.import_module('accounts_' + bundle + '.' + name)


def response(payload):
    reply = Mock()
    reply.json.return_value = payload
    reply.text = 'ddmAccount(' + json.dumps(payload) + ');'
    reply.__enter__ = Mock(return_value=reply)
    reply.__exit__ = Mock(return_value=False)
    return reply


class AccountTests(unittest.TestCase):
    def test_accounts_import_without_platform_adapters_or_plugin_entry(self):
        for kind in ('douyu', 'huya', 'douyin', 'twitch', 'youtube'):
            bundle = 'domestic_live' if kind in ('douyu', 'huya', 'douyin') else 'global_live'
            source = ROOT / 'plugins' / bundle
            with self.subTest(kind=kind):
                tree = ast.parse((source / (kind + '_accounts.py')).read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn(node.module, ('plugin', 'douyu_composer', 'platform_logo'))
                        self.assertFalse((node.module or '').endswith('_platform'))
                        self.assertFalse((node.module or '').startswith('PySide6.QtWidgets'))
                code = '''import importlib, sys, types
sys.path.insert(0, sys.argv[1])
package = types.ModuleType('isolated_accounts')
package.__path__ = [sys.argv[2]]
sys.modules[package.__name__] = package
importlib.import_module(package.__name__ + '.' + sys.argv[3])
assert not any(name.startswith(package.__name__ + '.') and
    (name.endswith('_platform') or name.endswith('.plugin')) for name in sys.modules)
'''
                result = subprocess.run([sys.executable, '-c', code, str(args.host.resolve()),
                    str(source), kind + '_accounts'], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_cancelled_accounts_and_follows_never_request(self):
        for kind, name in (('douyu', 'DouyuPlatform'), ('huya', 'HuyaPlatform'),
                           ('douyin', 'DouyinPlatform'), ('twitch', 'TwitchPlatform'),
                           ('youtube', 'YouTubePlatform')):
            bundle = 'domestic_live' if kind in ('douyu', 'huya', 'douyin') else 'global_live'
            with self.subTest(kind=kind):
                platform = getattr(module(bundle, kind + '_platform'), name)()
                session = Mock()
                self.assertEqual(platform.account_info(session, lambda: True), {})
                self.assertEqual(platform.follow_rooms(session, lambda: True), [])
                self.assertEqual(session.mock_calls, [])

    def test_huya_identity_validation_stays_in_account_protocol(self):
        platform = module('domestic_live', 'huya_platform').HuyaPlatform()
        payload = {'isLogined': True, 'uid': 42, 'userNick': 'Fixture',
                   'userLogo': '//huyaimg.msstatic.com/fixture.png'}
        session = Mock(get=Mock(return_value=response(payload)))
        self.assertEqual(platform.account_info(session, lambda: False), {
            'uid': '42', 'uname': 'Fixture', 'face': 'https://huyaimg.msstatic.com/fixture.png'})
        self.assertEqual(session.get.call_args.kwargs['timeout'], (4, 8))
        session.get.return_value = response(dict(payload, isLogined=False))
        with self.assertRaises(RuntimeError):
            platform.account_info(session, lambda: False)

    def test_huya_follows_keep_adapter_overrides_and_cancellation(self):
        platform = module('domestic_live', 'huya_platform').HuyaPlatform()
        platform.account_info = Mock(return_value={'uid': '42'})
        room = {'room_id': 'huya:123', 'platform': 'huya', 'live': True}
        platform.room_info = Mock(return_value=SimpleNamespace(as_dict=lambda: room))
        profile = {0: {0: {0: 7}, 1: {10: 123}}}
        protocol = module('domestic_live', 'huya_accounts')
        with patch.object(protocol, 'huya_follow_request', side_effect=[{1: [7, 7]}, profile]):
            self.assertEqual(platform.follow_rooms(Mock(), lambda: False), [room])
        platform.room_info.assert_called_once_with('huya:123')
        platform.room_info.reset_mock()
        with patch.object(protocol, 'huya_follow_request', side_effect=[{1: [7, 8]}, profile]) as request:
            self.assertEqual(platform.follow_rooms(Mock(), lambda: request.call_count == 2), [])
        self.assertEqual(platform.room_info.call_count, 1)

    def test_douyin_follows_update_the_original_identity_maps(self):
        adapter = module('domestic_live', 'douyin_platform').DouyinPlatform
        platform, other = adapter(), adapter()
        owners, profiles, faces = platform._anchor_uids, platform._follow_profiles, platform._follow_faces
        platform._context = SimpleNamespace(set_setting=Mock())
        platform.restore_follow_rooms([{'room_id': 'douyin:123', 'anchor_uid': '42',
            'sec_uid': 'MS4wLjAB' + 'a' * 12, 'face': 'https://p.douyinpic.com/fixture.jpg'}])
        self.assertIs(platform._anchor_uids, owners)
        self.assertIs(platform._follow_profiles, profiles)
        self.assertIs(platform._follow_faces, faces)
        self.assertEqual(owners, {'douyin:123': '42'})
        self.assertEqual(other._anchor_uids, {})
        reference = platform.follow_account_reference({'anchor_uid': '42'})
        self.assertEqual(reference['room_id'], 'douyin:uid_42')
        self.assertEqual(platform.normalize(reference['room_id']), 'douyin:123')
        self.assertEqual(platform._context.set_setting.call_args_list[0].args,
                         ('douyin_room_owners', {'douyin:123': '42'}))


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
