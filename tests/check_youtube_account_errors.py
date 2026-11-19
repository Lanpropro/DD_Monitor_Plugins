"""Offline official subscription failures: actionable reasons and no partial success."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

import requests


def response(payload, status=403):
    reply = requests.Response()
    reply.status_code = status
    reply._content = json.dumps(payload).encode()
    reply._content_consumed = True
    return reply


class YouTubeAccountErrorsTests(unittest.TestCase):
    def test_distinct_causes_have_distinct_actions(self):
        for reason, action in (
                ('insufficientPermissions', '勾选'), ('accessNotConfigured', '尚未启用'),
                ('quotaExceeded', '配额'), ('youtubeSignupRequired', '频道设置'),
                ('subscriptionForbidden', '当前账号'), ('authError', '重新授权')):
            with self.subTest(reason=reason):
                message = errors.subscription_error(response({'error': {'errors': [{'reason': reason}]}}))
                self.assertIn(action, message)
                self.assertIn('HTTP 403 / ' + reason, message)

    def test_google_rpc_details_are_recognized(self):
        reply = response({'error': {'details': [{'reason': 'SERVICE_DISABLED'}]}})
        self.assertIn('HTTP 403 / SERVICE_DISABLED', errors.subscription_error(reply))

    def test_response_messages_and_unknown_reasons_are_never_displayed(self):
        reply = response({'error': {'message': 'private-token-and-email',
                                   'errors': [{'reason': 'private-token-and-email'}]}})
        message = errors.subscription_error(reply)
        self.assertIn('HTTP 403', message)
        self.assertNotIn('private', message)

    def test_non_json_and_malformed_payloads_keep_http_status(self):
        for payload in ([], {'error': 'private'}, {'error': {'errors': 'private'}}):
            self.assertIn('HTTP 403', errors.subscription_error(response(payload)))
        reply = response({})
        reply._content = b'private non-json proxy error'
        self.assertNotIn('private', errors.subscription_error(reply))

    def test_unauthorized_without_reason_requests_new_authorization(self):
        message = errors.subscription_error(response({}, 401))
        self.assertIn('重新授权', message)
        self.assertIn('HTTP 401', message)

    def test_provider_second_page_failure_raises_without_partial_results(self):
        session = requests.Session()
        self.addCleanup(session.close)
        session.get = Mock(side_effect=[response({'items': [{'snippet': {
            'resourceId': {'channelId': 'UC' + 'a' * 22}, 'title': 'Fixture'}}],
            'nextPageToken': 'fixture-page'}, 200),
            response({'error': {'errors': [{'reason': 'insufficientPermissions'}]}})])
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403 / insufficientPermissions'):
            provider.follow_rooms(session, lambda: False)
        self.assertEqual(session.get.call_count, 2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    os.environ['DDM_NO_SAVE'] = '1'
    sys.path.insert(0, str(args.host.resolve()))
    from ddm.plugins import PluginManager
    root = Path(__file__).resolve().parents[1]
    manager = PluginManager(plugins_dir=str(root / 'plugins'), enabled=['global_live'])
    manager.load()
    provider = manager.platforms['youtube']
    path = root / 'plugins/global_live/youtube_account_errors.py'
    spec = importlib.util.spec_from_file_location('youtube_account_errors_test', path)
    errors = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(errors)
    unittest.main(argv=[sys.argv[0]])
