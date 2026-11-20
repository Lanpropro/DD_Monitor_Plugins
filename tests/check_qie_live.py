"""Room metadata, guest access states and fresh playback resolution."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import av


parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, unittest_args = parser.parse_known_args()
sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1] / 'plugins')]
from qie_live.media import open_720p
from qie_live.danmaku import QieDanmakuClient, chat_event, frames, packet
from qie_live.platform import QieError, QiePlatform, room_payload


ROOM = '10199288'
KEY = 'qie:' + ROOM
ONLINE = {'is_live': 1, 'nickname': '奥利奥有点甜', 'room_name': '篮球',
          'owner_avatar': 'https://uc.qiecdn.com/avatar.png'}, {}
OLD = f'https://test.qiecdn.com/live/{ROOM}.flv?test=old'
FRESH = f'https://test.qiecdn.com/live/{ROOM}.flv?test=fresh'
HLS = f'https://test.qiecdn.com/live/{ROOM}Mobile.m3u8?test=issued'
HLS_MANIFEST = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\npart.ts\n'


def flv(width=1280, height=720, frames=8, luma=40, *, with_audio=False):
    data = BytesIO()
    with av.open(data, 'w', format='flv') as container:
        stream = container.add_stream('libx264', rate=15)
        stream.width, stream.height, stream.pix_fmt = width, height, 'yuv420p'
        stream.bit_rate = 500_000
        stream.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                          'x264-params': 'nal-hrd=cbr:vbv-bufsize=500:vbv-maxrate=500'}
        if with_audio:
            audio = container.add_stream('aac', rate=48000, layout='stereo')
        frame = av.VideoFrame(width, height, 'yuv420p')
        for plane, value in zip(frame.planes, (luma, 128, 128)):
            plane.update(bytes([value]) * plane.buffer_size)
        for index in range(frames):
            frame.pts = index
            container.mux(stream.encode(frame))
            if with_audio:
                silence = av.AudioFrame(format='fltp', layout='stereo', samples=3200)
                silence.sample_rate, silence.pts = 48000, index * 3200
                for plane in silence.planes:
                    plane.update(bytes(plane.buffer_size))
                container.mux(audio.encode(silence))
        container.mux(stream.encode())
        if with_audio:
            container.mux(audio.encode())
    return data.getvalue()


def page(data):
    state = {'props': {'initialState': {'roomInfo': {'roomInfo': data}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(state) + '</script>'


class QieTests(unittest.TestCase):
    def setUp(self):
        self.resolve = Mock()
        self.context = Mock()
        self.context.retain_media_source.return_value = 'http://127.0.0.1/media/check'
        self.platform = QiePlatform(self.context, self.resolve)

    def test_official_numeric_rooms_only(self):
        self.assertEqual(self.platform.normalize('https://www.qie.tv/10202119?from=mm'), 'qie:10202119')
        self.assertEqual(self.platform.room_url('qie:10202119'), 'https://www.qie.tv/10202119')
        for bad in ('10202119', 'https://www.qie.tv.evil.example/10202119',
                    'https://www.qie.tv/news/10202119', 'https://login@www.qie.tv/10202119'):
            self.assertFalse(self.platform.matches(bad), bad)

    def test_room_payload_not_a_recommended_room(self):
        room = {'room_id': '10202119', 'nickname': 'RSPF1', 'is_live': 0}
        self.assertEqual(room_payload(page({'code': 0, 'room_info': room}), '10202119'), (room, {}))
        with self.assertRaisesRegex(QieError, '不存在'):
            room_payload(page({'code': 404}), '10202119')
        with self.assertRaisesRegex(QieError, '不完整'):
            room_payload(page({'code': 0, 'room_info': room}), '10199288')

    def test_offline_and_restricted_do_not_open_the_player(self):
        with patch.object(self.platform, '_room', return_value=({'is_live': 0}, {})):
            with self.assertRaisesRegex(QieError, '尚未开播'):
                self.platform.play_url('qie:10202119')
        with patch.object(self.platform, '_room', return_value=(
                {'is_live': 1}, {'status': 1, 'is_allow': 0})):
            with self.assertRaisesRegex(QieError, '限制访客'):
                self.platform.play_url('qie:10202119')
        self.resolve.assert_not_called()

    def test_mobile_live_page_hands_off_verified_hls_without_browser(self):
        platform = QiePlatform(self.context)
        old_hls = HLS.replace('issued', 'expired')
        mobile = MagicMock(status_code=200, text='<script>window.$ROOM_INFO = ' + json.dumps(
            {'room_id': ROOM, 'show_status': 1, 'hls_url': old_hls}) + ';</script>')
        fresh_page = MagicMock(status_code=200, text=mobile.text.replace(old_hls, HLS))
        expired = MagicMock(status_code=403)
        manifest = MagicMock(status_code=200, text=HLS_MANIFEST)
        for response in (mobile, fresh_page, expired, manifest):
            response.__enter__.return_value = response
        real_open = av.open

        def open_fixture(source, *options, **kwargs):
            if source == HLS:
                return real_open(BytesIO(flv()))
            return real_open(source, *options, **kwargs)

        with (patch.object(platform, '_room', return_value=ONLINE),
              patch('qie_live.mobile_source.requests.get') as get,
              patch('qie_live.mobile_source.av.open', side_effect=open_fixture) as opened):
            get.side_effect = [mobile, expired, fresh_page, manifest]
            info = platform.room_info(KEY)
            self.assertTrue(info.live)
            self.assertEqual(info.uname, '奥利奥有点甜')
            self.assertEqual(platform.room_quality_options(info.room_id),
                             [{'qn': 720, 'desc': '720p', 'label': '720p'}])
            with self.assertRaisesRegex(QieError, 'HTTP 403'):
                platform.play_url(info.room_id)
            self.assertEqual(platform.play_url(info.room_id),
                             ('http://127.0.0.1/media/check', 720, 'qie'))
            room_url = 'https://m.qie.tv/' + ROOM
            self.assertEqual(get.call_args.kwargs['headers']['Referer'], room_url)
            self.assertEqual([call.args[0] for call in get.call_args_list],
                             [room_url, old_hls, room_url, HLS])
            self.assertEqual(opened.call_args_list[0].args, (HLS,))
            self.assertIn(f'Referer: {room_url}\r\n', opened.call_args_list[0].kwargs['options']['headers'])
        reader = self.context.retain_media_source.call_args.args[1]
        self.addCleanup(reader.close)
        payload = BytesIO(b''.join(iter(lambda: reader.read(4096), b'')))
        with av.open(payload) as playback:
            frame = next(playback.decode(video=0))
            self.assertEqual((frame.width, frame.height), (1280, 720))

    def test_mobile_parser_rejects_wrong_room_and_unofficial_source(self):
        from qie_live.mobile_source import mobile_hls_url, mobile_room_payload

        def markup(room_id, source, online=1):
            return '<script>window.$ROOM_INFO = ' + json.dumps(
                {'room_id': room_id, 'show_status': online, 'hls_url': source}) + ';</script>'

        self.assertEqual(mobile_hls_url(mobile_room_payload(markup(ROOM, HLS), ROOM), ROOM), HLS)
        for html in (markup('10202119', HLS), markup(ROOM, HLS, 0),
                     markup(ROOM, HLS.replace(ROOM, ROOM + '1')),
                     markup(ROOM, HLS.replace('test.qiecdn.com', 'qiecdn.com.example')),
                     markup(ROOM, HLS.split('?')[0])):
            with self.subTest(html=html), self.assertRaises(QieError):
                mobile_hls_url(mobile_room_payload(html, ROOM), ROOM)

    def test_uses_mobile_avatar_when_desktop_omits_it_and_local_platform_branding(self):
        desktop = dict(ONLINE[0], owner_avatar='')
        with (patch.object(self.platform, '_room', return_value=(desktop, {})),
              patch('qie_live.mobile_source.fetch_mobile_room',
                    return_value={'owner_avatar': '//uc.qiecdn.com/avatar.php?id=fixture'}) as mobile):
            room = self.platform.room_info(KEY)
            self.assertEqual(room.face, 'https://uc.qiecdn.com/avatar.php?id=fixture')
            mobile.assert_called_once_with(ROOM)
            mobile.side_effect = QieError('mobile temporarily unavailable')
            self.assertTrue(self.platform.room_info(KEY).live)
            self.assertEqual(self.platform.room_info(KEY).face, '')
        branding = self.platform.room_card_branding(KEY)
        self.assertEqual(branding['label'], '企鹅体育')
        self.assertTrue(Path(branding['badge_path']).is_file())
        self.assertNotIn('avatar_path', branding)

    def test_403_then_next_play_resolves_fresh_url_and_reuses_probed_bytes(self):
        payload = flv()
        rejected = Mock(status_code=403)
        accepted = Mock(status_code=200, raw=BytesIO(payload))
        extra = Mock(status_code=200, raw=BytesIO(payload))
        self.resolve.side_effect = [OLD, FRESH, FRESH + 'er']
        with (patch.object(self.platform, '_room', return_value=ONLINE),
              patch('qie_live.media.requests.Session') as session):
            session.return_value.get.side_effect = [rejected, accepted, extra]
            with self.assertRaisesRegex(QieError, 'HTTP 403'):
                self.platform.play_url(KEY)
            rejected.close.assert_called_once()
            session.return_value.close.assert_called_once()
            self.context.retain_media_source.assert_not_called()

            self.assertEqual(self.platform.play_url(KEY, 10000),
                             ('http://127.0.0.1/media/check', 720, 'qie'))
            reopen, prepared = self.context.retain_media_source.call_args.args
            self.addCleanup(prepared.close)
            self.assertGreater(accepted.raw.tell(), 0)
            self.assertEqual(session.return_value.get.call_count, 2)
            self.assertEqual(b''.join(iter(lambda: prepared.read(1024), b'')), payload)
            prepared.close()
            prepared.close()
            accepted.close.assert_called_once()
            self.assertEqual(prepared.read(1024), b'')

            reader = reopen(lambda: False)
            self.addCleanup(reader.close)
            self.assertEqual(b''.join(iter(lambda: reader.read(1024), b'')), payload)
            self.assertEqual([call.args[0] for call in session.return_value.get.call_args_list],
                             [OLD, FRESH, FRESH + 'er'])
        self.assertEqual([call.args[0] for call in self.resolve.call_args_list], [ROOM] * 3)

    def test_rejects_non_720p_and_closes_connections(self):
        for width, height in [(854, 480), (1920, 1080)]:
            with (self.subTest(height=height), patch('qie_live.media.requests.Session') as session):
                response = session.return_value.get.return_value
                response.status_code, response.raw = 200, BytesIO(flv(width, height))
                with self.assertRaisesRegex(QieError, '1280×720'):
                    open_720p(FRESH, ROOM, lambda: False)
                response.close.assert_called_once()
                session.return_value.close.assert_called_once()

    def test_rejects_wrong_source_before_request(self):
        with patch('qie_live.media.requests.Session') as session:
            for address in (FRESH.replace('test.qiecdn.com', 'qiecdn.com.example'),
                            FRESH.replace('test.qiecdn.com', 'name@test.qiecdn.com'),
                            FRESH.replace(ROOM, '10202119'), FRESH.split('?')[0],
                            FRESH.replace('.flv', '.m3u8')):
                with self.subTest(address=address), self.assertRaises(QieError):
                    open_720p(address, ROOM, lambda: False)
            session.assert_not_called()

    def test_preview_cancellation_before_resolution(self):
        with patch.object(self.platform, '_room', return_value=ONLINE):
            with self.assertRaisesRegex(QieError, '已取消'):
                self.platform.preview_url(KEY, cancelled=lambda: True)
        self.resolve.assert_not_called()

    def test_failed_handoff_closes_prepared_reader(self):
        self.context.retain_media_source.side_effect = RuntimeError('handoff failed')
        with (patch.object(self.platform, '_room', return_value=ONLINE),
              patch('qie_live.media.open_720p') as prepare):
            with self.assertRaisesRegex(RuntimeError, 'handoff failed'):
                self.platform.play_url(KEY)
            prepare.return_value.close.assert_called_once()


class QieDanmakuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.payload = {'from': {'u': 99, 'n': '测试观众'},
                        'msg': {'content': '测试弹幕', 'color': 'ffcc11'}}
        self.body = json.dumps(self.payload, ensure_ascii=False).encode('utf-8')

    def test_batched_frames_and_truncated_packets(self):
        combined = packet(9, 0, packet(3, 0) + packet(2000, 1, self.body))
        self.assertEqual(list(frames(combined)), [(3, b''), (2000, self.body)])
        with self.assertRaises(ValueError):
            list(frames(combined[:-1]))
        self.assertIsNone(chat_event(b'{"msg":null}'))

    async def test_guest_subscription_only_delivers_plain_room_chat(self):
        client = QiePlatform().danmaku_client(KEY)
        messages, statuses = [], []
        client.message.connect(messages.append)
        client.status.connect(statuses.append)
        socket = Mock(send_bytes=AsyncMock(), receive=AsyncMock(side_effect=[
            SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=packet(8, 0, b'{"status":0}')),
            SimpleNamespace(type=aiohttp.WSMsgType.BINARY,
                            data=packet(9, 1, packet(2000, 1, self.body) + packet(2001, 2, self.body))),
            SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None),
        ]))
        with self.assertRaises(OSError):
            await client._receive(socket)
        self.assertEqual(statuses, ['已连接'])
        self.assertEqual(messages, [{'kind': 'danmaku', 'uid': '99', 'uname': '测试观众',
                                     'text': '测试弹幕', 'color': '#ffcc11'}])
        sent = [next(frames(call.args[0])) for call in socket.send_bytes.await_args_list]
        self.assertEqual([operation for operation, _ in sent], [7, 2])
        self.assertEqual(json.loads(sent[0][1])['roomId'], 'online://' + ROOM)
        self.assertEqual(json.loads(sent[0][1])['token'], '')
        client.stop()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *unittest_args])
