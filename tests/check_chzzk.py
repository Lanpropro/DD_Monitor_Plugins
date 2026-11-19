"""Offline CHZZK links, metadata, live-only parsing, quality and registration."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import requests
from streamlink import Streamlink
from streamlink.stream.hls import HLSStream
from streamlink.stream.hls.m3u8 import parse_m3u8

CHANNEL = 'a' * 32
ROOM = 'chzzk:' + CHANNEL


def response(payload):
    reply = requests.Response()
    reply.status_code = 200
    reply._content = json.dumps({'code': 200, 'content': payload}).encode()
    reply._content_consumed = True
    return reply


class ChzzkTests(unittest.TestCase):
    def setUp(self):
        self.platform = chzzk.ChzzkPlatform()

    def test_links(self):
        for link in [ROOM, 'chzzk:' + CHANNEL.upper(), CHANNEL,
                     'https://chzzk.naver.com/' + CHANNEL,
                     'https://chzzk.naver.com/live/' + CHANNEL + '/?from=share']:
            self.assertEqual(self.platform.normalize(link), ROOM)
        self.assertEqual(self.platform.room_url(ROOM), 'https://chzzk.naver.com/live/' + CHANNEL)
        self.assertTrue(self.platform.matches(self.platform.room_url(ROOM)))
        self.assertFalse(self.platform.matches('https://chzzk.naver.com.evil.test/' + CHANNEL))
        for link in ['https://chzzk.naver.com/video/123', 'chzzk:../' + CHANNEL,
                     'https://chzzk.naver.com/live/' + 'z' * 32,
                     'https://user@chzzk.naver.com/' + CHANNEL,
                     'https://chzzk.naver.com:444/' + CHANNEL,
                     'https://chzzk.naver.com.evil.test/' + CHANNEL,
                     'ftp://chzzk.naver.com/' + CHANNEL]:
            with self.subTest(link=link), self.assertRaises(ValueError):
                self.platform.normalize(link)

    def metadata(self, detail, channel_id=CHANNEL):
        channel = {'channelId': channel_id, 'channelName': '치지직',
                   'channelImageUrl': 'https://nng-phinf.pstatic.net/avatar.png'}
        with patch.object(chzzk, 'public_request', side_effect=[response(channel), response(detail)]) as get:
            info = self.platform.room_info(ROOM)
            self.assertEqual(get.call_args.kwargs['timeout'], (4, 8))
            return info

    def test_live_metadata_and_thumbnail(self):
        info = self.metadata({'status': 'OPEN', 'liveTitle': '방송', 'concurrentUserCount': 23,
            'liveImageUrl': 'https://livecloud-thumb.akamaized.net/image_{type}.jpg'})
        self.assertTrue(info.live)
        self.assertEqual(info.uname, '치지직')
        self.assertEqual(info.viewers, '23')
        self.assertTrue(info.cover_url.endswith('image_480.jpg'))
        self.assertTrue(info.face)
        self.assertTrue(info.as_dict()['live_known'])

    def test_offline_and_invalid_channels(self):
        self.assertFalse(self.metadata(None).live)
        self.assertFalse(self.metadata({'status': 'CLOSE'}).live)
        with self.assertRaises(RuntimeError):
            self.metadata(None, channel_id=None)
        with self.assertRaises(RuntimeError):
            self.metadata({'status': 'UNKNOWN'})

    def test_errors_do_not_become_offline(self):
        with patch.object(chzzk, 'public_request', side_effect=requests.Timeout()):
            with self.assertRaises(RuntimeError):
                self.platform.room_info(ROOM)
        with patch.object(self.platform, 'room_info', side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):
                self.platform.rooms_status([ROOM])

    def parser(self):
        session = Streamlink()
        self.addCleanup(session.http.close)
        return chzzk.LiveChzzk(session, self.platform.room_url(ROOM))

    def test_time_machine_uses_live_hls(self):
        parser = self.parser()
        media = [('LLHLS', 'HLS', 'https://cdn.test/ll.m3u8'),
                 ('HLS', 'HLS', 'https://cdn.test/live.m3u8')]
        parser._api.get_live_detail = Mock(return_value=('success',
            (media, 'OPEN', 42, 'fixture', 'talk', 'title', False, True)))
        parser._api.get_live_playback = Mock(side_effect=AssertionError('Must not use rewind'))
        with patch.object(HLSStream, 'parse_variant_playlist', return_value={'high': object()}) as parse:
            self.assertIn('high', dict(parser._get_live(CHANNEL)))
            self.assertEqual(parse.call_args.args[1], 'https://cdn.test/live.m3u8')
        parser._api.get_live_playback.assert_not_called()

    def test_restricted_and_offline_do_not_expose_streams(self):
        parser = self.parser()
        for result, error in [(('success', None), RuntimeError),
                (('success', (None, 'OPEN', 1, 'name', None, None, True, False)), api.LoginRequiredError),
                (('error', 'fixture'), RuntimeError)]:
            parser._api.get_live_detail = Mock(return_value=result)
            with self.assertRaises(error):
                dict(parser._get_live(CHANNEL))
        with patch.object(chzzk.LiveChzzk, 'streams', side_effect=api.LoginRequiredError('年龄验证')):
            with self.assertRaisesRegex(api.LoginRequiredError, '年龄验证'):
                self.platform.play_url(ROOM)

    def test_quality_selection_preview_and_no_url_cache(self):
        session = Streamlink()
        self.addCleanup(session.http.close)
        manifest = '#EXTM3U\n'
        for key, size, rate, codec in [('high', '1920x1080', 6000000, 'avc1.640028'),
                ('low', '640x360', 900000, 'avc1.64001e'), ('hevc', '3840x2160', 9000000, 'hvc1.1.6.L120')]:
            manifest += f'#EXT-X-STREAM-INF:BANDWIDTH={rate},CODECS="{codec},mp4a.40.2",RESOLUTION={size}\nhttps://cdn.test/{key}.m3u8\n'
        master = parse_m3u8(manifest, base_uri='https://cdn.test/master.m3u8')
        streams = {key: HLSStream(session, f'https://cdn.test/{key}.m3u8', multivariant=master)
                   for key in ('high', 'low', 'hevc')}
        with patch.object(chzzk.LiveChzzk, 'streams', return_value=streams):
            self.assertTrue(self.platform.play_url(ROOM)[0].endswith('/high.m3u8'))
            tiers = self.platform.room_quality_options(ROOM)
            self.assertEqual(len(tiers), 3)
            low = tiers[-1]['qn']
            self.assertTrue(self.platform.play_url(ROOM, low)[0].endswith('/low.m3u8'))
            preview = self.platform.preview_url(ROOM)
            self.assertEqual(preview[1:3], (low, 'chzzk'))
            self.assertEqual(preview[3]['Referer'], self.platform.origin)
            self.assertEqual(self.platform.play_url(ROOM, -1)[1], low)
        self.assertNotIn('m3u8', json.dumps(self.platform._qualities))

    def test_registration_and_no_unimplemented_chat(self):
        self.assertIsInstance(self.platform, module.PublicLivePlatform)
        context = Mock(window=None)
        module.GlobalLivePlugin().on_load(context)
        platforms = [call.args[0] for call in context.register_platform.call_args_list]
        self.assertEqual({platform.kind for platform in platforms}, {'twitch', 'youtube', 'chzzk'})
        self.assertIsNone(self.platform.danmaku_client(ROOM))
        self.assertEqual(self.platform.oauth_provider, '')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    from ddm import plugins as api
    source = Path(__file__).resolve().parents[1] / 'plugins/global_live/plugin.py'
    spec = importlib.util.spec_from_file_location('chzzk_test_plugin', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    import importlib
    chzzk = importlib.import_module(spec.name + '.chzzk')
    unittest.main(argv=[sys.argv[0]])
