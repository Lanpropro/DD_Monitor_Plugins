"""关注账号不能被房间解析失败过滤；已知房间映射跨插件重建保留。"""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock, patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    os.chdir(args.host.resolve())
    os.environ['DDM_NO_SAVE'] = '1'
    import requests
    from ddm.plugins import PluginManager
    root = Path(__file__).resolve().parents[1]
    manager = PluginManager(plugins_dir=str(root / 'plugins'), enabled=['domestic_live'])
    manager.plugin_settings = {'domestic_live': {'douyin_room_owners': {'douyin:1002': '102'}}}
    manager.load()
    provider = manager.platforms['douyin']
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({'status_code': 0, 'has_more': 0, 'followings': [
        {'uid': '101', 'nickname': 'Live', 'room_data': {'status': 2, 'web_rid': '1001'}},
        {'uid': '102', 'nickname': 'Previously imported'},
        {'uid': '103', 'nickname': 'Unresolved'}]}).encode()
    session = Mock()
    session.get.return_value = response
    with patch.object(provider, 'account_info', return_value={'uid': '42'}), patch.object(
            provider, '_follow_room', side_effect=AssertionError('List must appear before supplements')):
        accounts = provider.follow_accounts(session, lambda: False)
    assert [account['room_id'] for account in accounts] == ['douyin:1001', 'douyin:1002', '']
    assert not accounts[1]['live_known'] and accounts[2]['anchor_uid'] == '103'
    provider.restore_follow_rooms(accounts)
    settings = manager.plugin_settings
    manager.unload()
    manager = PluginManager(plugins_dir=str(root / 'plugins'), enabled=['domestic_live'])
    manager.plugin_settings = settings
    manager.load()
    provider = manager.platforms['douyin']
    assert provider._anchor_uids == {'douyin:1002': '102', 'douyin:1001': '101'}
    with patch.object(provider, '_follow_room', return_value={}):
        assert provider.resolve_follow_account(accounts[1], lambda: False) == accounts[1]
    with patch.object(provider, '_follow_room', return_value={'web_rid': '1003', 'status': 4}):
        resolved = provider.resolve_follow_account(accounts[2], lambda: False)
    assert resolved['room_id'] == 'douyin:1003' and not resolved['live'] and resolved['live_known']
    with patch.object(provider, '_follow_room', side_effect=AssertionError('Cancelled')):
        assert provider.resolve_follow_account(accounts[2], lambda: True) == accounts[2]
    manager.unload()
    print('PASS: complete follow accounts; offline mapping; independent room identity; restart; empty response; cancellation')


if __name__ == '__main__':
    main()
