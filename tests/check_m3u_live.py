"""Playlist parsing, HTTP redirect, stored channel IDs and media request headers."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch


parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, unittest_args = parser.parse_known_args()
sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1] / 'plugins')]
from m3u_live.platform import PlaylistPlatform
from m3u_live.playlist import Channel, PlaylistError, http_url, parse_playlist, read_playlist
from m3u_live.branding import channel_branding


PLAYLIST = '''#EXTM3U
#EXTINF:-1 tvg-id="five" tvg-name="CCTV-5" tvg-logo="../art/five.png" group-title="体育,高清",CCTV-5
sub/five.m3u8
#EXTINF:-1 tvg-id="five-plus" tvg-name="CCTV-5",CCTV-5+
#EXTGRP:赛事
#EXTVLCOPT:http-referrer=https://sport.example/
#EXTVLCOPT:http-user-agent=TestAgent/1
https://media.example/five-plus.m3u8
#EXTINF:-1,坏频道
rtmp://unavailable.example/live
'''.encode('utf-8-sig')
HLS = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nsub/index.m3u8\n'


class Sources(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/shortcut':
            self.send_response(302)
            self.send_header('Location', '/nested/list.m3u')
            self.end_headers()
        elif self.path in ('/nested/list.m3u', '/hls'):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(PLAYLIST if self.path.endswith('.m3u') else HLS)
        else:
            self.send_error(404)

    def log_message(self, *unused):
        pass


class LiveSources(Sources):
    def do_GET(self):
        if self.headers.get('Referer') != 'https://sport.example/':
            self.send_error(403)
            return
        media = b'#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nlast.ts\n'
        routes = {
            '/master': HLS,
            '/sub/index.m3u8': media,
            '/sub/last.ts': b'\x47' + b'\x00' * 511,
            '/closed': media + b'#EXT-X-ENDLIST\n',
            '/missing-segment': media.replace(b'last.ts', b'gone.ts'),
            '/webpage': b'<html>Not a stream</html>',
            '/direct': (b'\x47' + b'\x00' * 187) * 4,
        }
        if self.path not in routes:
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(routes[self.path])


class Context:
    def __init__(self):
        self.settings = {}

    def setting(self, key, default):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value


class PlaylistTests(unittest.TestCase):
    def test_bom_metadata_group_headers_and_bad_channel(self):
        channels, hls, skipped = parse_playlist(PLAYLIST, 'https://example.org/folder/list.m3u')
        self.assertEqual((hls, skipped), (False, 1))
        self.assertEqual([channel.name for channel in channels], ['CCTV-5', 'CCTV-5+'])
        first, second = channels
        self.assertEqual((first.tvg_id, first.tvg_name, first.group), ('five', 'CCTV-5', '体育,高清'))
        self.assertEqual(first.url, 'https://example.org/folder/sub/five.m3u8')
        self.assertEqual(first.logo, 'https://example.org/art/five.png')
        self.assertEqual(second.group, '赛事')
        self.assertEqual(second.headers, {'Referer': 'https://sport.example/', 'User-Agent': 'TestAgent/1'})

    def test_http_redirect_base_and_direct_hls(self):
        with ThreadingHTTPServer(('127.0.0.1', 0), Sources) as api:
            worker = Thread(target=api.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{api.server_port}'
            try:
                channels, hls, _ = read_playlist(base + '/shortcut', lambda: False)
                self.assertFalse(hls)
                self.assertEqual(channels[0].url, base + '/nested/sub/five.m3u8')
                context = Context()
                platform = PlaylistPlatform(context)
                result = platform.room_list('m3u:' + base + '/hls', lambda: False)
                self.assertFalse(result.choose)
                self.assertEqual(len(result.rooms), 1)
                room_id = result.rooms[0].room_id
                result.store([room_id])
                self.assertEqual(platform.play_url(room_id)[0], base + '/hls')
                self.assert_restored_room(PlaylistPlatform(context), result.rooms[0])
                with self.assertRaisesRegex(PlaylistError, '404'):
                    read_playlist(base + '/missing', lambda: False)
            finally:
                api.shutdown()
                worker.join()

    def test_local_import_persists_only_selected_channels(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / '播放 列表.m3u8'
            path.write_bytes(PLAYLIST)
            context = Context()
            platform = PlaylistPlatform(context)
            normalized = platform.normalize(str(path))
            loaded = platform.room_list(normalized, lambda: False)
            self.assertTrue(loaded.choose)
            self.assertIn('1', loaded.warning)
            first, second = loaded.rooms
            self.assertEqual(first.extra['import_group'], '体育,高清')
            loaded.store([second.room_id])
            restored = PlaylistPlatform(context)
            self.assert_restored_room(restored, second)
            self.assertEqual(restored.play_url(second.room_id)[3], {
                'Referer': 'https://sport.example/', 'User-Agent': 'TestAgent/1'})
            with self.assertRaises(PlaylistError):
                restored.room_info(first.room_id)

    def test_built_in_presets_are_playable_without_import_and_keep_stable_ids(self):
        context = Context()
        platform = PlaylistPlatform(context)
        restored = PlaylistPlatform(context)
        rooms = platform.room_input_presets()
        self.assertEqual([room.uname for room in rooms],
                         ['CCTV-1 综合', 'CCTV-1 综合（备用）', 'CCTV-5+', 'CCTV-5+（备用）',
                          'CCTV-6 电影', 'CCTV-6 电影（备用）', 'CCTV-13 新闻'])
        self.assertEqual(len({room.room_id for room in rooms}), len(rooms))
        for room in rooms:
            with self.subTest(room=room.uname):
                self.assertTrue(restored.matches(room.room_id))
                self.assertEqual(restored.normalize(room.room_id), room.room_id)
                self.assertEqual(restored.room_info(room.room_id), room)
                self.assertTrue(http_url(restored.play_url(room.room_id)[0]))
                result = restored.room_list(room.room_id, lambda: False)
                self.assertEqual(result.rooms, [room])
                self.assertFalse(result.choose)
                self.assertIsNone(result.store)
                self.assert_restored_room(restored, room)
        self.assertEqual(context.settings, {})

    def assert_restored_room(self, platform, expected):
        room = {'room_id': expected.room_id, 'uname': f'房间 {expected.room_id}'}
        platform.restore_follow_rooms([room])
        self.assertEqual((room['uname'], room['title'], room['face']),
                         (expected.uname, expected.title, expected.face))
        self.assertTrue(room['live_from_playback'])
        self.assertFalse(room['live_known'])
        self.assertFalse(expected.live)
        self.assertEqual(expected.extra['playback_mode'], 'stream')

    def test_backup_presets_use_independent_sources(self):
        platform = PlaylistPlatform(Context())
        for channel in ('cctv1', 'cctv5plus', 'cctv6'):
            with self.subTest(channel=channel):
                primary = 'm3u:preset:' + channel
                backup = primary + '-backup'
                self.assertNotEqual(platform.play_url(primary)[0], platform.play_url(backup)[0])
                self.assertEqual(platform.room_info(backup).title, platform.room_info(primary).title)
                self.assertEqual(platform.room_card_branding(primary), platform.room_card_branding(backup))

    def test_cctv_channel_artwork_overrides_playlist_logo_and_retains_platform(self):
        platform = PlaylistPlatform(Context())
        channels, _, _ = parse_playlist(PLAYLIST, 'https://example.org/folder/list.m3u')
        for channel, expected in zip(channels, ('cctv5.png', 'cctv5plus.png'), strict=True):
            with self.subTest(channel=channel.name):
                artwork = channel_branding(channel)
                self.assertEqual(Path(artwork['avatar_path']).name, expected)
                self.assertTrue(Path(artwork['avatar_path']).is_file())
                self.assertEqual(artwork['label'], 'CCTV')
                self.assertEqual(Path(artwork['badge_path']).name, 'cctv.png')
                room = platform._room('m3u:test', channel)
                self.assertEqual((room.platform, room.face, room.cover_url), ('m3u', '', ''))
        self.assertEqual(Path(channel_branding(Channel('', '央视－１６'))['avatar_path']).name, 'cctv.png')
        self.assertEqual(Path(channel_branding(Channel('', 'CCTV5TEST'))['badge_path']).name, 'iptv.png')
        self.assertTrue(platform.capture_first_playback_cover)

    def test_local_station_uses_playlist_artwork_after_reopen(self):
        logo = 'https://example.org/station.png'
        channel = Channel('https://example.org/live.m3u8', '某地卫视', logo=logo)
        context = Context()
        room_id = 'm3u:' + 'a' * 64
        context.set_setting('channels', {room_id: vars(channel)})
        platform = PlaylistPlatform(context)
        room = {'room_id': room_id}
        platform.restore_follow_rooms([room])
        self.assertEqual(room['face'], logo)
        artwork = platform.room_card_branding(room_id)
        self.assertTrue(artwork['badge_from_avatar'])
        self.assertEqual(artwork['label'], '某地卫视')
        self.assertEqual(Path(artwork['badge_path']).name, 'iptv.png')
        self.assertTrue(Path(artwork['badge_path']).is_file())
        for key in ('guangzhou-tv', 'hebei-tv'):
            artwork = platform.room_card_branding('m3u:preset:' + key)
            self.assertEqual(artwork['avatar_path'], artwork['badge_path'])
            self.assertTrue(Path(artwork['badge_path']).is_file())

    def test_backup_headers_do_not_inherit_unrelated_platform_referer(self):
        platform = PlaylistPlatform(Context())
        for room_id in ('m3u:preset:cctv1-backup', 'm3u:preset:cctv5plus-backup'):
            self.assertEqual(platform.play_url(room_id)[3], {'User-Agent': 'Mozilla/5.0'})

    def test_previous_presets_still_play_for_existing_follow_cards(self):
        platform = PlaylistPlatform(Context())
        visible = {room.room_id for room in platform.room_input_presets()}
        for key in ('cgtn', 'guangzhou-tv', 'hebei-tv'):
            room_id = 'm3u:preset:' + key
            with self.subTest(room_id=room_id):
                self.assertNotIn(room_id, visible)
                self.assertTrue(platform.matches(room_id))
                self.assertEqual(platform.normalize(room_id), room_id)
                self.assertEqual(platform.room_info(room_id).room_id, room_id)
                self.assertTrue(http_url(platform.play_url(room_id)[0]))

    def test_media_playlist_segments_are_not_channels(self):
        payload = b'#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.ts\n'
        source = 'https://example.org/live'
        channels, hls, _ = parse_playlist(payload, source)
        self.assertTrue(hls)
        self.assertEqual([channel.url for channel in channels], [source])
        with self.assertRaisesRegex(PlaylistError, '网页'):
            parse_playlist(b'<html>Denied</html>', source)
        with self.assertRaises(PlaylistError):
            parse_playlist(b'#EXTM3U\n#EXTINF:-1,Local\nfile:///C:/secret.m3u8', source)


    def test_status_checks_selected_http_sources_without_starting_playback(self):
        with ThreadingHTTPServer(('127.0.0.1', 0), LiveSources) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                paths = ('master', 'direct', 'closed', 'missing-segment', 'webpage', 'missing', 'denied')
                room_ids = ['m3u:' + str(index) * 64 for index in range(len(paths))]
                context = Context()
                channel_rows = {}
                for room_id, path in zip(room_ids, paths, strict=True):
                    url = base + '/' + ('master' if path == 'denied' else path)
                    headers = {} if path == 'denied' else {'Referer': 'https://sport.example/'}
                    channel_rows[room_id] = vars(Channel(url, path, headers=headers))
                context.set_setting('channels', channel_rows)
                platform = PlaylistPlatform(context)
                with patch.object(platform, 'play_url', side_effect=AssertionError('不要启动播放')):
                    status = platform.rooms_status(room_ids + ['m3u:absent'])
                self.assertEqual({rid for rid, room in status.items() if room['live']}, set(room_ids[:2]))
                self.assertEqual({rid for rid, room in status.items() if room['live_known']}, set(room_ids[:2]))
                self.assertEqual(set(status), set(room_ids))
                self.assertEqual(status[room_ids[0]]['uname'], 'master')
            finally:
                server.shutdown()
                worker.join()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *unittest_args])
