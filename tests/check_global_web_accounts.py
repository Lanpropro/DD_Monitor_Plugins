"""Offline website-login identity, complete follow pagination and cancellation regression."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

import requests


def response(payload, status=200):
    reply = requests.Response()
    reply.status_code = status
    reply._content = json.dumps(payload).encode()
    reply._content_consumed = True
    return reply


class WebAccountsTests(unittest.TestCase):
    def twitch_session(self, *payloads):
        session = requests.Session()
        session.cookies.set('auth-token', 'fixture-only', domain='.twitch.tv', secure=True)
        session.post = Mock(side_effect=[response(payload) for payload in payloads])
        self.addCleanup(session.close)
        return session

    def follows(self, names, *, next_page=False, cursor='next'):
        return {'data': {'currentUser': {'follows': {'edges': [
            {'cursor': cursor, 'node': {'login': name, 'displayName': name}} for name in names],
            'pageInfo': {'hasNextPage': next_page}}}}}

    def test_twitch_no_client_configuration_and_identity(self):
        session = self.twitch_session({'data': {'currentUser': {'id': '123', 'login': 'fixture'}}})
        self.assertEqual(web.twitch_account(session, lambda: False)['uid'], '123')
        self.assertEqual(session.post.call_args.kwargs['headers']['Authorization'], 'OAuth fixture-only')

    def test_twitch_pagination_deduplication_and_progress(self):
        session = self.twitch_session(self.follows(['One'], next_page=True), self.follows(['one', 'Two']))
        session.ddm_follow_progress = Mock()
        result = web.twitch_follows(session, lambda: False)
        self.assertEqual([room['room_id'] for room in result], ['twitch:one', 'twitch:two'])
        self.assertEqual(session.post.call_args.kwargs['json']['variables']['after'], 'next')
        self.assertEqual([call.args[0] for call in session.ddm_follow_progress.call_args_list], [1, 2])

    def test_twitch_no_partial_success_on_failed_second_page(self):
        session = self.twitch_session(self.follows(['One'], next_page=True))
        session.post.side_effect = [response(self.follows(['One'], next_page=True)), response({}, 503)]
        with self.assertRaises(requests.HTTPError):
            web.twitch_follows(session, lambda: False)

    def test_twitch_stalled_cursor(self):
        session = self.twitch_session(self.follows(['One'], next_page=True), self.follows(['Two'], next_page=True))
        with self.assertRaisesRegex(RuntimeError, '分页没有前进'):
            web.twitch_follows(session, lambda: False)

    def test_twitch_signed_out_and_cancelled(self):
        session = self.twitch_session({'data': {'currentUser': None}})
        with self.assertRaises(api.LoginRequiredError):
            web.twitch_account(session, lambda: False)
        self.assertEqual(web.twitch_follows(session, lambda: True), [])
        self.assertEqual(session.post.call_count, 1)

    def youtube_session(self, data, *, logged=True):
        session = requests.Session()
        session.cookies.set('SAPISID', 'fixture-cookie', domain='.youtube.com', secure=True)
        config = {'LOGGED_IN': logged, 'DATASYNC_ID': 'fixture-internal-identity',
                  'INNERTUBE_CONTEXT': {'client': {'clientName': 'WEB', 'clientVersion': 'fixture'}}}
        page = response({})
        page._content = ('ytcfg.set(' + json.dumps(config) + ');var ytInitialData = ' + json.dumps(data) + ';').encode()
        session.get = Mock(return_value=page)
        self.addCleanup(session.close)
        return session

    def channel(self, letter):
        return {'channelRenderer': {'channelId': 'UC' + letter * 22, 'title': {'simpleText': letter}}}

    def continuation(self, token):
        return {'continuationItemRenderer': {'continuationEndpoint': {'continuationCommand': {'token': token}}}}

    def test_youtube_account_requires_verified_header(self):
        session = self.youtube_session({})
        session.post = Mock(return_value=response({'activeAccountHeaderRenderer': {
            'accountName': {'simpleText': 'Fixture'}, 'accountPhoto': {'thumbnails': []}}}))
        account = web.youtube_account(session, lambda: False)
        self.assertEqual(account['uname'], 'Fixture')
        self.assertNotIn('fixture-internal-identity', json.dumps(account))
        self.assertNotIn('fixture-cookie', json.dumps(account))
        session.post.return_value = response({})
        with self.assertRaises(api.LoginRequiredError):
            web.youtube_account(session, lambda: False)

    def test_youtube_pagination_deduplicates_and_skips_sidebar(self):
        session = self.youtube_session({'contents': {'items': [self.channel('a'), self.continuation('next')]},
                                        'sidebar': self.channel('z')})
        session.post = Mock(return_value=response({'onResponseReceivedActions': [{
            'appendContinuationItemsAction': {'continuationItems': [self.channel('a'), self.channel('b')]}}]}))
        rooms = web.youtube_follows(session, lambda: False)
        self.assertEqual([room['room_id'] for room in rooms], ['youtube:UC' + 'a' * 22, 'youtube:UC' + 'b' * 22])
        self.assertEqual(session.post.call_args.kwargs['json']['continuation'], 'next')
        self.assertTrue(session.post.call_args.kwargs['headers']['Authorization'].startswith('SAPISIDHASH '))

    def test_youtube_empty_and_unrecognized_are_distinct(self):
        session = self.youtube_session({'contents': {'messageRenderer': {'text': {'simpleText': 'empty'}}}})
        self.assertEqual(web.youtube_follows(session, lambda: False), [])
        session = self.youtube_session({'contents': {}})
        with self.assertRaisesRegex(RuntimeError, '未识别'):
            web.youtube_follows(session, lambda: False)

    def test_youtube_signed_out_and_stalled_cursor(self):
        session = self.youtube_session({}, logged=False)
        with self.assertRaises(api.LoginRequiredError):
            web.youtube_follows(session, lambda: False)
        session = self.youtube_session({'contents': {'items': [self.channel('a'), self.continuation('same')]}})
        session.post = Mock(return_value=response({'items': [self.channel('b'), self.continuation('same')]}))
        with self.assertRaisesRegex(RuntimeError, '分页没有前进'):
            web.youtube_follows(session, lambda: False)

    def test_cancellation_never_publishes_partial_list(self):
        session = self.youtube_session({'contents': {'items': [self.channel('a'), self.continuation('next')]}})
        session.ddm_follow_progress = lambda _count: setattr(session, 'stopped', True)
        session.post = Mock(return_value=response({'items': [self.channel('b')]}))
        self.assertEqual(web.youtube_follows(session, lambda: getattr(session, 'stopped', False)), [])
        session.post.assert_not_called()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    from ddm import plugins as api
    path = Path(__file__).resolve().parents[1] / 'plugins/global_live/web_accounts.py'
    spec = importlib.util.spec_from_file_location('global_web_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    web = module
    unittest.main(argv=[sys.argv[0]])
