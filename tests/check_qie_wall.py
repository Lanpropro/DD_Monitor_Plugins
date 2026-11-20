"""Check mobile HLS on the native wall with a local HTTP 200 → 403 → 200 fixture."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import os
from pathlib import Path
import shutil
import tempfile
from threading import Event, Thread
from time import monotonic
import traceback
from urllib.parse import urlsplit
from unittest.mock import Mock, patch

import av
import requests
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from check_qie_live import HLS, KEY, ONLINE, ROOM, args, flv

os.environ['DDM_NO_SAVE'] = '1'
os.chdir(args.host.resolve())
from ddm import config, images, plugins, theme
from ddm.app import MainWindow


def wait_for(check, seconds, message):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if result := check():
            return result
        QTest.qWait(80)
    raise AssertionError(message)


def on_wall(window, previous_url=None):
    tile = window.wall.tiles[0]
    player = window.players.get(tile)
    if (player and player.state == 'playing' and player._bound_hwnd and tile.video.isVisible()
            and tile.stream_url != previous_url
            and (player._prop('width'), player._prop('height')) == (1280, 720)
            and (player._prop('time-pos') or 0) > 0):
        return tile.stream_url
    return None


def segments(luma):
    with av.open(BytesIO(flv(frames=60, luma=luma, with_audio=True))) as source:
        packets = [packet for packet in source.demux() if packet.dts is not None]
        result = []
        for index in range(4):
            data = BytesIO()
            with av.open(data, 'w', format='mpegts') as target:
                streams = {stream.index: target.add_stream_from_template(stream) for stream in source.streams}
                for packet in packets:
                    copy = av.Packet(bytes(packet))
                    copy.stream = streams[packet.stream.index]
                    copy.time_base = packet.time_base
                    offset = int(index * 4 / packet.time_base)
                    copy.pts, copy.dts = packet.pts + offset, packet.dts + offset
                    copy.duration, copy.is_keyframe = packet.duration, packet.is_keyframe
                    target.mux(copy)
            result.append(data.getvalue())
        return result


def main():
    cut = Event()
    initial, recovered = segments(40), segments(100)
    statuses = []

    class CDN(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parts = urlsplit(self.path)
            old = parts.query.endswith('old') or parts.path.startswith('/old/')
            status = 403 if old and cut.is_set() else 200
            statuses.append((old, status))
            self.send_response(status)
            if status == 403:
                self.end_headers()
                return
            if parts.path.endswith('.m3u8'):
                prefix = 'old' if old else 'fresh'
                data = ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n'
                        '#EXT-X-MEDIA-SEQUENCE:0\n' + ''.join(
                            f'#EXTINF:4,\n/{prefix}/{index}.ts\n' for index in range(4))).encode()
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
            else:
                index = int(Path(parts.path).stem)
                data = (initial if old else recovered)[index]
                self.send_header('Content-Type', 'video/mp2t')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(('127.0.0.1', 0), CDN) as cdn, tempfile.TemporaryDirectory() as profile:
        listener = Thread(target=cdn.serve_forever, daemon=True)
        listener.start()
        request = requests.Session.request

        old, fresh = HLS.replace('issued', 'old'), HLS.replace('issued', 'fresh')

        def local_url(url):
            parts = urlsplit(url)
            return f'http://127.0.0.1:{cdn.server_port}{parts.path}?{parts.query}'

        def local_request(session, method, url, **options):
            return request(session, method, local_url(url) if url in (old, fresh) else url, **options)

        original_open = av.open

        def open_fixture(source, *options, **kwargs):
            if source in (old, fresh):
                source = local_url(source)
                kwargs['options']['protocol_whitelist'] += ',http'
            return original_open(source, *options, **kwargs)

        try:
            config.DATA_ROOT, images.REPO, plugins.DEFAULT_PLUGINS_DIR = profile, profile, profile
            config.CONFIG_PATH = str(Path(profile) / 'config.json')
            source = Path(__file__).resolve().parents[1] / 'plugins' / 'qie_live'
            installed = Path(profile) / 'qie_live'
            shutil.copytree(source, installed, ignore=shutil.ignore_patterns('__pycache__'))

            app = QApplication([])
            app.setStyleSheet(theme.qss())
            state = {'plugins_enabled': ['qie_live'], 'settings': {'auto_update': False,
                     'default_muted': True, 'danmaku_enabled': False, 'preview_on_hover': False,
                     'close_behavior': 'exit'}}
            with (patch.object(MainWindow, '_restore_platform_accounts'),
                  patch.object(MainWindow, 'refresh_account'),
                  patch.object(MainWindow, '_check_startup_update'),
                  patch.object(requests.Session, 'request', local_request)):
                window = MainWindow([], [], layout_id='1x1', state=state)
                try:
                    window.setAttribute(Qt.WA_ShowWithoutActivating)
                    window.resize(900, 540)
                    window.show()
                    platform = window.plugins.platform_for(KEY)
                    assert platform.kind == 'qie'
                    platform._room = Mock(return_value=ONLINE)
                    module = __import__(type(platform).__module__.rsplit('.', 1)[0] + '.mobile_source',
                                        fromlist=['fetch_mobile_room'])
                    play_url = platform.play_url

                    def report_play(room_id, quality=720, *, cancelled=None):
                        try:
                            return play_url(room_id, quality, cancelled=cancelled)
                        except Exception:
                            traceback.print_exc()
                            raise

                    platform.play_url = report_play

                    def room(number):
                        expired = any(was_old and status == 403 for was_old, status in statuses)
                        return {'room_id': number, 'show_status': 1, 'hls_url': fresh if expired else old}

                    with (patch.object(module, 'fetch_mobile_room', side_effect=room) as resolve,
                          patch.object(av, 'open', side_effect=open_fixture)):
                        window.add_to_wall(platform.room_info(KEY).as_dict())
                        first_url = wait_for(lambda: on_wall(window), 20, 'initial 720p playback timed out')
                        assert (True, 200) in statuses, statuses
                        print('PASS: native wall displayed initial 1280x720', flush=True)
                        cut.set()
                        wait_for(lambda: on_wall(window, first_url), 45, '720p recovery timed out')
                        assert (True, 403) in statuses and (False, 200) in statuses, statuses
                        assert resolve.call_count >= 2 and all(call.args == (ROOM,) for call in resolve.call_args_list)
                        print('PASS: HLS HTTP 200 → 403 → 200, native 1280x720 recovered', flush=True)
                finally:
                    cut.set()
                    window.close()
                    app.processEvents()
        finally:
            cdn.shutdown()
            listener.join()


if __name__ == '__main__':
    main()
