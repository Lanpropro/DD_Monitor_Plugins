"""Offline mobile-share parsing, background resolution and fixed Douyin room identities."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def response(status=200, *, target='', page=''):
    result = MagicMock(status_code=status, text=page, headers={'Location': target})
    result.cookies.get.return_value = ''
    result.__enter__.return_value = result
    return result


def reflow(internal='7699814551382657834', uid='10001', rid='458897981613', status=2):
    room = {'idStr': internal, 'status': status, 'title': 'fixture live',
            'owner': {'idStr': uid, 'webRid': rid, 'nickname': 'fixture anchor'}}
    chunk = '1:' + json.dumps(['$', 'component', None, {'data': {'room': room}}])
    return 'self.__rsc_f.push([1,' + json.dumps(chunk) + '])'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    host = parser.parse_args().host.resolve()
    sys.path.insert(0, str(host))
    os.chdir(host)
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from ddm.plugins import PluginManager
    from ddm.bili import InfoResolver
    from ddm.app import MainWindow
    from ddm.room_input import extract_room_input

    app = QApplication.instance() or QApplication([])
    manager = PluginManager(plugins_dir=str(ROOT / 'plugins'), enabled=['domestic_live'])
    manager.load()
    providers = manager.platforms
    module = sys.modules[type(providers['huya']).__module__]
    shares = {'huya': 'https://hy.fan/jfhFHM', 'douyu': 'https://s.douyu.com/c/fixture',
              'douyin': 'https://v.douyin.com/lprC02DTYsA/'}
    target = 'https://webcast.amemv.com/douyin/webcast/reflow/7699814551382657834?sec_user_id=sharer'
    try:
        with patch.object(module.requests, 'get', side_effect=AssertionError('GUI must not access network')):
            for kind, url in shares.items():
                assert manager.platform_for(url) is providers[kind]
                assert providers[kind].normalize(url) == url
                text = '主播(房间号314957)正在直播，分享自平台' + url + '。'
                assert MainWindow._normalize_room_input(SimpleNamespace(plugins=manager), text) == url
            assert providers['huya'].normalize('https://m.huya.com/314957?shareid=1') == 'huya:314957'
            assert providers['douyu'].normalize('https://www.douyu.com/6657?dyshid=1') == 'douyu:6657'
            assert providers['douyin'].normalize('https://live.douyin.com/458897981613') == 'douyin:458897981613'
            assert extract_room_input('0- #直播# https://v.douyin.com/lprC02DTYsA/ 0@1.com :8pm') == shares['douyin']

        pages = {
            'huya': [response(301, target='https://m.huya.com/314957?source=ios'), response(page=
                'var TT_ROOM_DATA = {"isOn":true,"introduction":"fixture"};'
                'var TT_PROFILE_INFO = {"nick":"fixture anchor"};')],
            'douyu': [response(302, target='https://www.douyu.com/6657?shareid=1')],
            'douyin': [response(302, target=target), response(page=reflow())],
        }
        expected = {'huya': 'huya:314957', 'douyu': 'douyu:6657', 'douyin': 'douyin:458897981613'}
        with patch.object(providers['douyu'], '_room_data', return_value={
                'room_id': 6657, 'show_status': 1, 'nickname': 'fixture', 'room_name': 'fixture'}):
            for kind, url in shares.items():
                seen = []

                def get(current, **kwargs):
                    assert QThread.currentThread() != app.thread(), 'Share expansion must stay in InfoResolver'
                    seen.append((current, kwargs))
                    return pages[kind][len(seen) - 1]

                results, errors = [], []
                worker = InfoResolver(url, platform=providers[kind])
                worker.resolved.connect(results.append)
                worker.failed.connect(errors.append)
                with patch.object(module.requests, 'get', side_effect=get):
                    worker.start()
                    deadline = time.monotonic() + 5
                    while worker.isRunning() and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(.01)
                    assert worker.wait(1000), 'Background link query did not finish'
                    app.processEvents()
                assert not errors and len(results) == 1, (kind, errors)
                assert results[0]['room_id'] == expected[kind] and results[0]['live'] is True
                assert len(seen) == len(pages[kind]), 'Do not requery Douyin after reading complete share metadata'
                assert seen[0][1]['allow_redirects'] is False and seen[0][1]['timeout'] == (3, 5)
                assert not ({'cookies', 'auth'} & seen[0][1].keys())
                worker.deleteLater()
        assert providers['douyin']._anchor_uids['douyin:458897981613'] == '10001'

        huya = providers['huya']
        for target_url in ('https://127.0.0.1/123', 'https://www.huya.com.evil.test/123',
                           'https://www.douyu.com/123', 'file:///123', 'huya:123',
                           'https://user:secret@www.huya.com/123', 'https://www.huya.com:8443/123',
                           'https://www.huya.com/video/123'):
            with patch.object(module.requests, 'get', return_value=response(302, target=target_url)) as get:
                try:
                    huya._resolve_share_input(shares['huya'])
                except ValueError:
                    pass
                else:
                    raise AssertionError('Non-room redirect was accepted: ' + target_url)
                assert get.call_count == 1, 'Invalid next hop must be rejected before fetching it'
        for url in ('https://u:p@hy.fan/abc', 'https://hy.fan:8443/abc', 'https://hy.fan/'):
            with patch.object(module.requests, 'get') as get:
                try:
                    huya.normalize(url)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Invalid short link was accepted')
                get.assert_not_called()
        cases = ([response(302, target='/jfhFHM')], [response()],
                 [response(302, target=f'/hop{i}') for i in range(4)])
        for replies in cases:
            with patch.object(module.requests, 'get', side_effect=replies) as get:
                try:
                    huya._resolve_share_input(shares['huya'])
                except ValueError:
                    pass
                else:
                    raise AssertionError('Loop, missing redirect or excessive hops must fail')
                assert get.call_count <= 4
        with patch.object(module.requests, 'get', side_effect=module.requests.Timeout):
            try:
                huya._resolve_share_input(shares['huya'])
            except module.requests.Timeout:
                pass
            else:
                raise AssertionError('Timeout must not fabricate a numeric room ID')

        douyin = providers['douyin']
        for page in (reflow(internal='999'), reflow(rid=''), reflow(uid=''), 'verification page'):
            with patch.object(module.requests, 'get', side_effect=[response(302, target=target), response(page=page)]):
                try:
                    douyin.room_info(shares['douyin'])
                except (RuntimeError, ValueError):
                    pass
                else:
                    raise AssertionError('Missing or mismatched fixed room identity must fail')
        with patch.object(module.requests, 'get', side_effect=[response(302, target=target), response(page=reflow(status=4))]):
            assert douyin.room_info(shares['douyin']).live is False
        try:
            douyin._share_info(reflow(), '7699814551382657834', '999', '')
        except RuntimeError:
            pass
        else:
            raise AssertionError('Existing follow-account identity validation must remain strict')
        print('PASS: GUI has no network; three background providers; fixed Douyin ID; redirects, identity, timeout and offline checks')
    finally:
        manager.unload()


if __name__ == '__main__':
    main()
