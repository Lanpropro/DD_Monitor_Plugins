"""抖音添加/关注的离线回归：缓存回退、页面解析、单项资料损坏和明确错误。"""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import requests


def page(*, status=2, uid='101', rid='439082987915'):
    room = {'id_str': '90001', 'status': status, 'title': 'fixture',
            'owner': {'id_str': uid, 'web_rid': rid, 'nickname': 'fixture anchor'}}
    payload = [{'state': {'roomStore': {'roomInfo': {'room': room}}}}]
    chunk = json.dumps('1:' + json.dumps(payload))
    return '<script>self.__pace_f.push( [ 1, ' + chunk + ' ] );</script>'


def response(*, text='', payload=None, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = (json.dumps(payload) if payload is not None else text).encode()
    result.encoding = 'utf-8'
    result._content_consumed = True
    return result


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.provider = provider_type()
        self.canonical = 'douyin:439082987915'

    def test_room_does_not_need_stream_data_or_login(self):
        for status in (2, 4):
            with self.subTest(status=status), patch.object(module.requests, 'get',
                    return_value=response(text=page(status=status))) as get:
                result = self.provider.room_info(self.canonical)
            self.assertEqual(result.live, status == 2)
            self.assertEqual(result.uname, 'fixture anchor')
            self.assertNotIn('auth', get.call_args.kwargs)
            self.assertEqual(set(get.call_args.kwargs['cookies']), {'__ac_nonce'})

    def test_bad_tail_chunk_does_not_hide_valid_room(self):
        malformed = '<script>self.__pace_f.push([2,' + json.dumps('2:{"roomStore":null}') + ']);</script>'
        self.assertEqual(self.provider._page_info(page() + malformed)['room']['status'], 2)

    def test_cached_lookup_failure_falls_back_once_and_refreshes_owner(self):
        for error in (requests.Timeout('private-url'), RuntimeError('private-response'),
                      ValueError('invalid JSON')):
            with self.subTest(error=type(error).__name__):
                self.provider._anchor_uids[self.canonical] = '999'
                with patch.object(self.provider, '_share_room', side_effect=error), patch.object(
                        module.requests, 'get', return_value=response(text=page())) as get:
                    self.assertTrue(self.provider.room_info(self.canonical).live)
                self.assertEqual(get.call_count, 1)
                self.assertEqual(self.provider._anchor_uids[self.canonical], '101')

    def test_wrong_cached_room_falls_back_without_adding_wrong_room(self):
        self.provider._anchor_uids[self.canonical] = '101'
        with patch.object(self.provider, '_share_room', return_value={
                'owner': {'web_rid': '999'}}), patch.object(module.requests, 'get',
                return_value=response(text=page())):
            self.assertEqual(self.provider.room_info(self.canonical).room_id, self.canonical)

    def test_valid_cached_room_does_not_requery(self):
        self.provider._anchor_uids[self.canonical] = '101'
        room = self.provider._page_info(page())['room']
        with patch.object(self.provider, '_share_room', return_value=room), patch.object(
                module.requests, 'get', side_effect=AssertionError('Unnecessary network')):
            self.assertTrue(self.provider.room_info(self.canonical).live)

    def test_mismatched_page_does_not_poison_cache(self):
        with patch.object(module.requests, 'get', return_value=response(text=page(rid='999'))):
            with self.assertRaises(RuntimeError):
                self.provider.room_info(self.canonical)
        self.assertEqual(self.provider._anchor_uids, {})

    def test_cached_anchor_change_requires_explicit_matching_room(self):
        self.provider._anchor_uids[self.canonical] = '999'
        # 删除网页房间号后，不能仅凭不同的主播 UID 覆盖已知关系。
        data = self.provider._page_info(page())
        data['room']['owner'].pop('web_rid')
        with patch.object(self.provider, '_share_room', side_effect=requests.Timeout), patch.object(
                self.provider, '_page_info', return_value=data), patch.object(module.requests, 'get',
                return_value=response(text=page())):
            with self.assertRaises(RuntimeError):
                self.provider.room_info(self.canonical)
        self.assertEqual(self.provider._anchor_uids[self.canonical], '999')

    def test_errors_are_actionable_and_do_not_expose_response(self):
        for error, expected in ((requests.Timeout('secret-cookie'), '超时'),
                (requests.exceptions.SSLError('secret-cookie'), '安全连接'),
                (requests.ConnectionError('secret-cookie'), '连接失败'),
                (requests.HTTPError('secret-cookie', response=response(status=403)), 'HTTP 403'),
                (requests.HTTPError('secret-cookie', response=response(status=429)), 'HTTP 429')):
            with self.subTest(expected=expected), patch.object(module.requests, 'get', side_effect=error):
                with self.assertRaises(RuntimeError) as caught:
                    self.provider.room_info(self.canonical)
                self.assertIn(expected, caught.exception.user_message)
                self.assertNotIn('secret-cookie', caught.exception.user_message)
        with patch.object(module.requests, 'get', return_value=response(text='private verification page')):
            with self.assertRaises(RuntimeError) as caught:
                self.provider.room_info(self.canonical)
            self.assertIn('未返回可识别', caught.exception.user_message)
            self.assertNotIn('private', caught.exception.user_message)

    def test_both_routes_failed_report_both_without_retry_loop(self):
        self.provider._anchor_uids[self.canonical] = '101'
        with patch.object(self.provider, '_share_room', side_effect=requests.Timeout), patch.object(
                module.requests, 'get', return_value=response(text='missing')) as get:
            with self.assertRaises(RuntimeError) as caught:
                self.provider.room_info(self.canonical)
        self.assertIn('超时', str(caught.exception))
        self.assertIn('回退也失败', str(caught.exception))
        self.assertEqual(get.call_count, 1)

    def read_accounts(self, users, **fields):
        session = Mock(get=Mock(return_value=response(payload=dict(
            status_code=0, has_more=0, followings=users, **fields))))
        with patch.object(self.provider, 'account_info', return_value={'uid': '42'}):
            return self.provider.follow_accounts(session, lambda: False)

    def test_bad_optional_room_data_preserves_followed_accounts(self):
        for data in ('{broken', '[]', [], 'null', 123):
            with self.subTest(data=data), patch.object(self.provider, '_follow_room',
                    side_effect=AssertionError('List must not fetch individual rooms')):
                accounts = self.read_accounts([
                    {'uid': '101', 'nickname': 'unresolved', 'room_data': data},
                    {'uid': '102', 'nickname': 'valid', 'web_rid': '1002'}])
            self.assertEqual([item['anchor_uid'] for item in accounts], ['101', '102'])
            self.assertEqual(accounts[0]['room_id'], '')
            self.assertFalse(accounts[0]['live_known'])

    def test_follow_supplement_failure_preserves_known_room(self):
        session = Mock(get=Mock(return_value=response(payload={'status_code': 0, 'has_more': 0,
            'followings': [{'uid': '101', 'web_rid': '1001'}]})))
        with patch.object(self.provider, 'account_info', return_value={'uid': '42'}), patch.object(
                self.provider, '_follow_room', side_effect=RuntimeError('unrecognized share page')):
            rooms = self.provider.follow_rooms(session, lambda: False)
        self.assertEqual(rooms[0]['room_id'], 'douyin:1001')
        self.assertFalse(rooms[0]['live_known'])

    def test_follow_refresh_falls_back_to_known_page_with_identity_check(self):
        account = {'anchor_uid': '101', 'room_id': self.canonical, 'uname': 'fixture'}
        with patch.object(self.provider, '_follow_room', side_effect=requests.Timeout), patch.object(
                module.requests, 'get', return_value=response(text=page(status=4))):
            result = self.provider.resolve_follow_account(account, lambda: False)
        self.assertTrue(result['live_known'])
        self.assertFalse(result['live'])
        with patch.object(self.provider, '_follow_room', side_effect=requests.Timeout), patch.object(
                module.requests, 'get', return_value=response(text=page(uid='999'))):
            with self.assertRaisesRegex(RuntimeError, '不匹配'):
                self.provider.resolve_follow_account(account, lambda: False)
        self.assertEqual(self.provider._anchor_uids[self.canonical], '101')

    def test_follow_cancel_and_unknown_room_do_not_fabricate_identity(self):
        account = {'anchor_uid': '101', 'room_id': ''}
        with patch.object(self.provider, '_follow_room', side_effect=requests.Timeout), patch.object(
                module.requests, 'get', side_effect=AssertionError('No known room')):
            self.assertEqual(self.provider.resolve_follow_account(account, lambda: True), account)
            with self.assertRaisesRegex(RuntimeError, '超时'):
                self.provider.resolve_follow_account(account, lambda: False)

    def test_follow_response_errors_are_not_empty_success(self):
        for reply in (response(text='<html>private response</html>'),
                      response(payload=[]), response(status=403)):
            session = Mock(get=Mock(return_value=reply))
            with patch.object(self.provider, 'account_info', return_value={'uid': '42'}):
                with self.assertRaises(RuntimeError) as caught:
                    self.provider.follow_accounts(session, lambda: False)
                self.assertNotIn('private response', str(caught.exception))

    def test_follow_pagination_keeps_accounts_and_rejects_stuck_cursor(self):
        first = {'status_code': 0, 'has_more': 1, 'offset': 20, 'min_time': 1,
                 'max_time': 2, 'followings': [{'uid': '101'}]}
        last = {'status_code': 0, 'has_more': 0, 'followings': [{'uid': '101'}, {'uid': '102'}]}
        session = Mock(get=Mock(side_effect=[response(payload=first), response(payload=last)]))
        with patch.object(self.provider, 'account_info', return_value={'uid': '42'}):
            accounts = self.provider.follow_accounts(session, lambda: False)
        self.assertEqual([item['anchor_uid'] for item in accounts], ['101', '102'])
        self.assertEqual(session.get.call_args.kwargs['params']['offset'], 20)
        session.get.side_effect = [response(payload=first), response(payload=first)]
        with patch.object(self.provider, 'account_info', return_value={'uid': '42'}):
            with self.assertRaisesRegex(RuntimeError, '分页没有前进'):
                self.provider.follow_accounts(session, lambda: False)

    def test_follow_cancellation_preserves_no_partial_result(self):
        session = Mock(get=Mock(return_value=response(payload={
            'status_code': 0, 'has_more': 0, 'followings': [{'uid': '101'}]})))
        with patch.object(self.provider, 'account_info', return_value={'uid': '42'}):
            self.assertEqual(self.provider.follow_accounts(session, lambda: session.get.called), [])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    sys.path.insert(0, str(args.host.resolve()))
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from ddm.plugins import PluginManager
    manager = PluginManager(plugins_dir=str(Path(__file__).resolve().parents[1] / 'plugins'),
                            enabled=['domestic_live'])
    manager.load()
    provider_type = type(manager.platforms['douyin'])
    module = sys.modules[provider_type.__module__]
    try:
        outcome = unittest.main(argv=[sys.argv[0], *remaining], exit=False)
    finally:
        manager.unload()
    sys.exit(not outcome.result.wasSuccessful())
