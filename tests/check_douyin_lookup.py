"""抖音添加/关注的离线回归：缓存回退、页面解析、单项资料损坏和明确错误。"""
import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
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

    def test_profile_add_uses_verified_page_once_when_http_is_unavailable(self):
        user = {'uid': '101', 'web_rid': '439082987915', 'nickname': 'profile anchor',
                'status': 2, 'title': 'profile live',
                'avatar': {'url_list': ['https://p.douyinpic.com/avatar.jpg']}}
        with patch.object(module.requests, 'get', side_effect=requests.Timeout):
            canonical = self.provider.profile_room_from_page(user, lambda: False)
            result = self.provider.room_info(canonical)
            self.assertEqual(result.uname, 'profile anchor')
            self.assertEqual(result.title, 'profile live')
            self.assertTrue(result.live)
            self.assertTrue(result.extra['live_known'])
            self.assertEqual(result.extra['anchor_uid'], '101')
            self.assertEqual(result.face, 'https://p.douyinpic.com/avatar.jpg')
            # 正常刷新仍查询实际状态，不能永久用主页快照冒充成功。
            with self.assertRaises(RuntimeError):
                self.provider.room_info(canonical)

    def test_profile_snapshot_unknown_status_expiry_and_cancellation(self):
        user = {'uid': '101', 'web_rid': '439082987915'}
        canonical = self.provider.profile_room_from_page(user, lambda: False)
        self.assertFalse(self.provider.room_info(canonical).extra['live_known'])
        with patch.object(module.time, 'monotonic', return_value=1):
            self.provider.profile_room_from_page(user, lambda: False)
        with patch.object(module.time, 'monotonic', return_value=62), patch.object(
                module.requests, 'get', side_effect=requests.Timeout):
            with self.assertRaises(RuntimeError):
                self.provider.room_info(canonical)
        self.assertEqual(self.provider.profile_room_from_page(user, lambda: True), '')
        self.assertFalse(self.provider._profile_additions)

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
                (requests.exceptions.JSONDecodeError('secret-cookie', 'secret-body', 0), '不是有效 JSON'),
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
        self.assertEqual(get.call_count, 2)

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

    def test_diagnostics_preserve_results_and_exceptions_without_secrets(self):
        plugin = module.LivePlatformsPlugin()
        context = Mock()
        error = RuntimeError('private signed URL')
        error.__cause__ = requests.exceptions.SSLError('private CERTIFICATE_VERIFY_FAILED cookie')
        with patch.object(self.provider, 'room_info', side_effect=error), patch.object(
                self.provider, 'play_url', return_value=('private-stream-url', 10000, 'douyin', {})):
            plugin._enable_diagnostics(context, self.provider)
            self.assertEqual(self.provider.play_url(self.canonical)[0], 'private-stream-url')
            context.log.assert_not_called()
            with self.assertRaises(RuntimeError) as caught:
                self.provider.room_info(self.canonical)
            self.assertIs(caught.exception, error)
            message = context.log.call_args.args[0]
            self.assertIn('room_info', message)
            self.assertIn(self.canonical, message)
            self.assertIn('UID映射=无', message)
            self.assertIn('证书校验失败', message)
            self.assertNotIn('private', message)
            self.assertNotIn('cookie', message)

    def test_diagnostic_categories_and_invalid_link_redaction(self):
        plugin = module.LivePlatformsPlugin()
        for error, expected in ((requests.exceptions.ProxyError('private-proxy-password'), '代理连接失败'),
                (requests.ConnectionError('private getaddrinfo failed'), '域名解析失败'),
                (requests.HTTPError('private', response=response(status=403)), 'HTTP 403'),
                (requests.Timeout('private'), 'Timeout')):
            result = plugin._diagnostic_reason(error)
            self.assertIn(expected, result)
            self.assertNotIn('private', result)
        context = Mock()
        with patch.object(self.provider, 'room_info', side_effect=ValueError('private-link')):
            plugin._enable_diagnostics(context, self.provider)
            with self.assertRaises(ValueError):
                self.provider.room_info('https://user:private@evil.invalid/?token=private')
        self.assertNotIn('private', context.log.call_args.args[0])
        self.assertIn('无效输入', context.log.call_args.args[0])

    def test_response_features_do_not_log_body_headers_or_query(self):
        logger = Mock()
        self.provider._diagnostic_log = logger
        reply = response(text='<html>private cookie captcha __rsc_f</html>')
        reply.url = 'https://live.douyin.com/439082987915?private=secret'
        reply.headers.update({'Content-Type': 'text/html; private=secret', 'Set-Cookie': 'private-secret'})
        with patch.object(module.requests, 'get', return_value=reply):
            with self.assertRaises(RuntimeError):
                self.provider.room_info(self.canonical)
        message = logger.call_args.args[0]
        self.assertIn('HTTP=200', message)
        self.assertIn('类型=text/html', message)
        self.assertIn('字节数=', message)
        self.assertIn('"__rsc_f": true', message)
        self.assertIn('"verification_text": true', message)
        self.assertNotIn('private', message)
        self.assertNotIn('secret', message)
        self.assertNotIn('https://', message)
        self.assertNotIn('<html>', message)

    def test_response_features_are_opt_in(self):
        self.assertFalse(hasattr(self.provider, '_diagnostic_log'))
        with patch.object(module.requests, 'get', return_value=response(text='missing')):
            with self.assertRaises(RuntimeError):
                self.provider.room_info(self.canonical)

    def test_player_categories_do_not_repeat_raw_messages(self):
        plugin = module.LivePlatformsPlugin()
        examples = [('ffmpeg', 'HTTP error 403 Forbidden private-url', 'HTTP_403'),
            ('ffmpeg', 'TLS handshake failed private-host', 'TLS_OR_CERTIFICATE'),
            ('ffmpeg', 'Connection timed out private-url', 'TIMEOUT'),
            ('ffmpeg', 'Failed to resolve private-host', 'DNS'),
            ('vd', 'Cannot initialize decoder private', 'VIDEO_DECODE'),
            ('vo/gpu', 'D3D11 failed private', 'VIDEO_OUTPUT'),
            ('ao/wasapi', 'Cannot initialize private', 'AUDIO_OUTPUT'),
            ('demux', 'Invalid data private', 'DEMUX_OR_FORMAT'),
            ('stream', 'Failed to open private', 'NETWORK_OR_OPEN'),
            ('cplayer', 'private unknown failure', 'OTHER_MPV_ERROR')]
        for prefix, text, expected in examples:
            self.assertEqual(plugin._player_error_category(prefix, text), expected)

    def test_player_observers_follow_room_and_detach_without_changing_playback(self):
        plugin = module.LivePlatformsPlugin()
        tile = object()
        core = Mock(spec=['register_event_callback', 'unregister_event_callback'])
        signal = Mock(spec=['connect', 'disconnect'])
        player = SimpleNamespace(player=core, stateChanged=signal, decode_mode='d3d11va')
        context = SimpleNamespace(window=SimpleNamespace(players={tile: player}), log=Mock())
        plugin.context = context
        plugin._diagnostic_enabled = True
        plugin._diagnostic_players = {}
        source = SimpleNamespace(room_id='huya:199300')
        payload = {'tile': tile, 'source': source}
        plugin.on_event(module.api.EVENT_STREAM_RESOLVED, payload)
        callback = core.register_event_callback.call_args.args[0]
        state_callback = signal.connect.call_args.args[0]
        event = SimpleNamespace(event_id=SimpleNamespace(value=2), data=SimpleNamespace(
            level='error', prefix='ffmpeg', text='HTTP error 403 private-cookie signed-url'))
        callback(event)
        callback(event)
        self.assertEqual(sum('player_error' in c.args[0] for c in context.log.call_args_list), 1)
        state_callback('error')
        plugin.on_event(module.api.EVENT_STREAM_RESOLVED, payload)
        core.register_event_callback.assert_called_once()
        signal.connect.assert_called_once()
        source.room_id = 'huya:dank1ng'
        plugin.on_event(module.api.EVENT_STREAM_RESOLVED, payload)
        callback(event)
        self.assertIn('huya:dank1ng', context.log.call_args.args[0])
        all_messages = '\n'.join(call.args[0] for call in context.log.call_args_list)
        self.assertIn('HTTP_403', all_messages)
        self.assertIn('解码方式=d3d11va', all_messages)
        self.assertNotIn('private', all_messages)
        self.assertNotIn('signed-url', all_messages)
        self.assertIs(payload['tile'], tile)
        self.assertEqual(player.decode_mode, 'd3d11va')
        source.room_id = '123'  # 格子改播 B 站时不能继续归因到上一个虎牙房间。
        plugin.on_event(module.api.EVENT_STREAM_RESOLVED, payload)
        plugin.on_event(module.api.EVENT_TILE_STOPPED, {'tile': tile})
        core.unregister_event_callback.assert_called_once_with(callback)
        signal.disconnect.assert_called_once_with(state_callback)
        self.assertEqual(plugin._diagnostic_players, {})

    def test_unloaded_plugin_ignores_player_events(self):
        plugin = module.LivePlatformsPlugin()
        context = Mock()
        plugin.context = context
        plugin.on_event(module.api.EVENT_STREAM_RESOLVED, {'source': object()})
        context.log.assert_not_called()

    def test_verification_response_has_existing_browser_recovery_instructions(self):
        with patch.object(module.requests, 'get', side_effect=[
                response(text='<html>captcha verifycenter</html>'), response(text='not JSON')]) as get:
            with self.assertRaisesRegex(RuntimeError, '主播个人主页') as caught:
                self.provider.room_info(self.canonical)
        self.assertIn('官网页面完成验证', str(caught.exception))
        self.assertIsInstance(caught.exception, module.DouyinVerificationRequired)
        self.assertTrue(caught.exception.browser_verification)
        self.assertTrue(all(call.kwargs['headers']['User-Agent'] == module.useragents.DEFAULT
                            for call in get.call_args_list))
        self.assertNotEqual(module.useragents.DEFAULT, 'Mozilla/5.0')

    def test_sdk_flight_text_is_preserved_for_original_quality(self):
        sdk = json.dumps({'data': {'origin': {'main': {'flv': 'https://cdn.test/origin.flv'}}},
                          'fixture': '中文'}, ensure_ascii=False)
        room = {'idStr': '901', 'status': 2,
                'owner': {'idStr': '101', 'webRid': '123'},
                'streamUrl': {'liveCoreSdkData': {'pullData': {'streamData': '$8',
                    'options': {'qualities': [{'sdkKey': 'origin', 'name': '原画'}]}}}}}
        node = '5:' + json.dumps(['$', '$L7', None, {'data': {'room': room}}])
        page = ''.join('self.__rsc_f.push([1,' + json.dumps(part) + ']);' for part in
                       (node + '\n8:T' + format(len(sdk.encode()), 'x') + ',', sdk))
        parsed = module.DouyinPlatform._share_info(page, '901', '101', '')
        pull = parsed['stream_url']['live_core_sdk_data']['pull_data']
        self.assertEqual(pull['stream_data'], sdk)
        self.assertEqual(pull['options']['qualities'][0]['sdk_key'], 'origin')
        self.assertEqual(module.DouyinPlatform._sdk_stream_data(page, '$9'), '')

    def test_original_sdk_stream_is_available_above_flat_blue_quality(self):
        from streamlink import Streamlink
        provider = module.DouyinPlatform()
        qualities = [{'sdk_key': 'uhd', 'name': '蓝光', 'resolution': '1920x1080'},
                     {'sdk_key': 'origin', 'name': '原画', 'fps': 60, 'resolution': ''}]
        raw = json.dumps({'data': {'origin': {'main': {'flv': 'https://cdn.test/origin.flv',
            'hls': 'https://cdn.test/origin.m3u8'}}, 'uhd': {'main': {'flv': 'https://cdn.test/blue.flv'}},
            'ao': {'main': {'flv': 'https://cdn.test/audio.flv'}}}})
        room = {'status': 2, 'stream_url': {'flv_pull_url': {'FULL_HD1': 'https://cdn.test/blue.flv'},
                'live_core_sdk_data': {'pull_data': {'stream_data': raw,
                    'options': {'qualities': qualities}}}}}
        session = Streamlink()
        try:
            with patch.object(provider, 'room_data', return_value={'room': room}):
                streams = provider._streams(session, 'douyin:123')
                self.assertEqual(streams['source'].to_url(), 'https://cdn.test/origin.flv')
                self.assertEqual(streams['hls'].to_url(), 'https://cdn.test/origin.m3u8')
                options = provider.room_quality_options('douyin:123')
                self.assertEqual([o['label'] for o in options], ['原画', '蓝光'])
                self.assertEqual(options[0]['qn'], 10000)
                self.assertNotIn('1920x1080', options[0]['desc'])
                self.assertEqual(provider._streams(session, 'douyin:123', options[1]['qn'])['source'].to_url(),
                                 'https://cdn.test/blue.flv')
                self.assertEqual(provider._streams(session, 'douyin:123', preview=True)['source'].to_url(),
                                 'https://cdn.test/blue.flv')
                original = qualities.pop()
                self.assertEqual(provider._streams(session, 'douyin:123')['source'].to_url(),
                                 'https://cdn.test/origin.flv')
                qualities.append(original)
                qualities[1]['disable'] = 1
                self.assertEqual(provider._streams(session, 'douyin:123')['source'].to_url(),
                                 'https://cdn.test/blue.flv')
        finally:
            session.http.close()

    def test_verified_uid_mapping_survives_normal_app_close(self):
        self.provider.restore_follow_rooms([{'room_id': self.canonical, 'anchor_uid': '101'}])
        context = SimpleNamespace(platform=Mock(return_value=self.provider), set_setting=Mock())
        self.provider._context = context
        plugin = module.LivePlatformsPlugin()
        plugin.context = context
        plugin.on_event(module.api.EVENT_CLOSING, {})
        context.set_setting.assert_called_once_with('douyin_room_owners', {self.canonical: '101'})
        restored = provider_type()
        saved = context.set_setting.call_args.args[1]
        restored.restore_follow_rooms([{'room_id': rid, 'anchor_uid': uid} for rid, uid in saved.items()])
        self.assertEqual(restored._anchor_uids, self.provider._anchor_uids)

    def test_missing_page_falls_back_to_public_api_and_preserves_identity(self):
        payload = {'status_code': 0, 'data': {'data': [{
            'id_str': '90001', 'status': 2, 'title': 'API room',
            'owner': {'id_str': '101', 'web_rid': '439082987915', 'nickname': 'API anchor'}}]}}
        with patch.object(module.requests, 'get', side_effect=[
                response(text='no room data'), response(payload=payload)]) as get:
            info = self.provider.room_info(self.canonical)
        self.assertEqual(info.uname, 'API anchor')
        self.assertTrue(info.live)
        self.assertEqual(self.provider._anchor_uids[self.canonical], '101')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs['params']['web_rid'], '439082987915')
        self.assertEqual(get.call_args.kwargs['cookies'], {})
        payload['data']['data'][0]['owner']['web_rid'] = '999'
        self.provider._anchor_uids.clear()
        with patch.object(module.requests, 'get', side_effect=[response(text='missing'), response(payload=payload)]):
            with self.assertRaisesRegex(RuntimeError, '身份匹配'):
                self.provider.room_info(self.canonical)
        self.assertEqual(self.provider._anchor_uids, {})

    def test_public_api_failure_is_not_an_empty_or_offline_success(self):
        for payload in ({'status_code': 8}, {'status_code': 0, 'data': []},
                {'status_code': 0, 'data': {'data': []}},
                {'status_code': 0, 'data': {'data': [{'status': 2, 'id_str': '1', 'owner': []}]}}):
            with self.subTest(payload=payload), patch.object(module.requests, 'get',
                    side_effect=[response(text='missing'), response(payload=payload)]):
                with self.assertRaises(RuntimeError):
                    self.provider.room_info(self.canonical)

    def test_huya_manifest_with_forbidden_segment_uses_next_cdn_same_quality(self):
        provider = module.HuyaPlatform()
        session = SimpleNamespace(http=SimpleNamespace(headers={'User-Agent': 'fixture'}, close=Mock()))
        streams = {name: SimpleNamespace(ddm_quality=1014000, to_url=lambda name=name:
            'https://' + name + '.test/live.m3u8') for name in ('first', 'second')}
        def reply(url, text='', status=200):
            result = response(text=text, status=status)
            result.url = url
            return result
        replies = [reply('https://first.test/live.m3u8', '#EXTM3U\n#EXTINF:2,\nsegment.ts'),
                   reply('https://first.test/segment.ts', status=403),
                   reply('https://second.test/live.m3u8', '#EXTM3U\n#EXTINF:2,\nsegment.ts'),
                   reply('https://second.test/segment.ts', 'Gfixture-media')]
        with patch.object(provider, '_streams', return_value=(session, None, streams)), patch.object(
                module.requests, 'get', side_effect=replies) as get:
            result = provider.play_url('huya:199300', 1014000)
        self.assertEqual(result[:3], ('https://second.test/live.m3u8', 1014000, 'huya'))
        self.assertEqual(get.call_count, 4)
        self.assertTrue(all('cookies' not in call.kwargs for call in get.call_args_list))
        session.http.close.assert_called_once()

    def test_huya_probe_rejects_empty_or_html_media_and_follows_master(self):
        def reply(url, text):
            result = response(text=text)
            result.url = url
            return result
        url = 'https://cdn.test/live.m3u8'
        for text in ('', '<html>error</html>'):
            with patch.object(module.requests, 'get', side_effect=[
                    reply(url, '#EXTM3U\n#EXTINF:2,\nmedia.ts'), reply('https://cdn.test/media.ts', text)]):
                self.assertFalse(module.HuyaPlatform._probe_hls(url, {}))
        with patch.object(module.requests, 'get', side_effect=[
                reply(url, '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4000000\nvariant.m3u8'),
                reply('https://cdn.test/variant.m3u8', '#EXTM3U\n#EXTINF:2,\nmedia.ts'),
                reply('https://cdn.test/media.ts', 'Gfixture-media')]):
            self.assertEqual(module.HuyaPlatform._probe_hls(url, {}), url)

    def test_huya_does_not_discard_a_working_fourth_cdn(self):
        provider = module.HuyaPlatform()
        session = SimpleNamespace(http=SimpleNamespace(headers={'User-Agent': 'fixture'}, close=Mock()))
        streams = {str(i): SimpleNamespace(ddm_quality=10000, to_url=lambda i=i:
            f'https://cdn{i}.test/live.m3u8') for i in range(4)}
        replies = []
        for i in range(4):
            reply = response(text='#EXTM3U' if i < 3 else '#EXTM3U\n#EXTINF:2,\nmedia.ts')
            reply.url = f'https://cdn{i}.test/live.m3u8'
            replies.append(reply)
        segment = response(text='Gfixture-media')
        segment.url = 'https://cdn3.test/media.ts'
        replies.append(segment)
        with patch.object(provider, '_streams', return_value=(session, None, streams)), patch.object(
                module.requests, 'get', side_effect=replies) as get:
            self.assertEqual(provider.play_url('huya:199300')[0], 'https://cdn3.test/live.m3u8')
            self.assertEqual(get.call_count, 5)  # Four playlists and the successful media probe.

    def test_huya_playback_rejection_changes_cdn_but_not_other_rooms(self):
        provider = module.HuyaPlatform()
        session = SimpleNamespace(http=SimpleNamespace(headers={'User-Agent': 'fixture'}, close=Mock()))
        streams = {name: SimpleNamespace(ddm_quality=10000, to_url=lambda name=name:
            'https://' + name + '.test/live.m3u8?signature=private') for name in ('first', 'second')}
        logs = provider._diagnostic_log = Mock()
        with patch.object(provider, '_streams', return_value=(session, None, streams)), patch.object(
                provider, '_probe_hls', side_effect=lambda url, headers: url), patch.object(
                module.time, 'monotonic', return_value=10):
            first = provider.play_url('huya:123')[0]
            provider.report_playback_failure('huya:123', first, 'HTTP_403')
            self.assertIn('second.test', provider.play_url('huya:123')[0])
            self.assertIn('first.test', provider.play_url('huya:456')[0])
            provider.report_playback_failure('huya:123', first, 'VIDEO_DECODE')
        with patch.object(provider, '_streams', return_value=(session, None, streams)), patch.object(
                provider, '_probe_hls', side_effect=lambda url, headers: url), patch.object(
                module.time, 'monotonic', return_value=71):
            self.assertIn('first.test', provider.play_url('huya:123')[0])
        self.assertNotIn('private', str(logs.call_args_list))
        self.assertNotIn('signature=', str(logs.call_args_list))

    def test_huya_player_failure_event_is_forwarded_only_while_loaded(self):
        plugin = module.LivePlatformsPlugin()
        provider = module.HuyaPlatform()
        plugin.context = SimpleNamespace(platform=lambda kind: provider)
        plugin._diagnostic_enabled = True
        payload = {'room': {'room_id': 'huya:123'}, 'source_url': 'https://bad.test/live?private',
                   'reason': 'HTTP_403'}
        plugin.on_event(module.api.EVENT_STREAM_FAILED, payload)
        self.assertIn('bad.test', provider._failed_cdns['huya:123'])
        provider._failed_cdns.clear()
        plugin._diagnostic_enabled = False
        plugin.on_event(module.api.EVENT_STREAM_FAILED, payload)
        self.assertFalse(provider._failed_cdns)


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
