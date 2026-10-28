"""Offline Huya account scope, official VIP gates, token signing and actual 4K validation."""
import argparse
import base64
import hashlib
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    host = parser.parse_args().host.resolve()
    os.chdir(host)
    sys.path.insert(0, str(host))
    os.environ['DDM_NO_SAVE'] = '1'
    from ddm import plugins
    from ddm.account_store import AccountStore
    manager = plugins.PluginManager(plugins_dir=str(ROOT / 'plugins'), enabled=['domestic_live'])
    manager.load()
    platform = manager.platforms['huya']
    module = sys.modules[type(platform).__module__]
    saved = ['auth=fixture; Domain=.huya.com; Path=/; Secure; HttpOnly',
             'expired=old; Domain=.huya.com; Expires=Sat, 01 Jan 2000 00:00:00 GMT',
             'foreign=secret; Domain=.example.com; Path=/',
             'suffix=secret; Domain=.nothuya.com; Path=/',
             'hostonly=secret; Path=/']
    try:
        with patch.object(AccountStore, 'load', return_value=saved) as load:
            assert not platform._playback_cookies()
            load.assert_not_called()
            with patch.dict(os.environ, {'DDM_NO_SAVE': '0'}):
                cookies = platform._playback_cookies()
            load.assert_called_once_with()
        assert cookies.get_dict() == {'auth': 'fixture'}
        window = SimpleNamespace(_platform_login_sessions={'huya': {'cookies': [
            'auth=current; Domain=.huya.com; Path=/; Secure'], 'remember': False}})
        with patch.object(platform, '_context', SimpleNamespace(window=window)), \
                patch.dict(os.environ, {'DDM_NO_SAVE': '0'}), patch.object(AccountStore, 'load') as load:
            assert platform._playback_cookies().get_dict() == {'auth': 'current'}
            window._platform_login_sessions['huya']['cookies'] = []
            assert not platform._playback_cookies(), 'Logout must not restore stale saved cookies'
            load.assert_not_called()

        data = {'data': [{'gameLiveInfo': {'uid': 123}}]}
        session = module.requests.Session()
        session.cookies.update(cookies)
        with patch.object(platform, 'account_info', return_value={'uid': '1099511627777'}), \
                patch.object(module, 'huya_wup_request') as wup:
            for vip in ({0: 0, 3: 1}, {0: 1, 3: 0}, {}):
                wup.reset_mock()
                wup.return_value = vip
                try:
                    platform._authorize_4k(session, data, 20000)
                except RuntimeError as error:
                    assert '4K VIP' in str(error)
                    assert not isinstance(error, plugins.LoginRequiredError), 'No VIP is not expired login'
                else:
                    raise AssertionError('Non-VIP and room-ineligible accounts must not request 4K')
                assert wup.call_count == 1
            for tier in (None, {1: 20000, 5: 0, 6: 4}):
                wup.side_effect = [{0: 1, 3: 1}, {1: [] if tier is None else [tier]}]
                try:
                    platform._authorize_4k(session, data, 20000)
                except RuntimeError as error:
                    assert '未授权' in str(error)
                else:
                    raise AssertionError('Unavailable or scan-gated tiers must not be treated as VIP 4K')
            for enabled in (0, 1):
                wup.side_effect = [{0: 1, 3: 1}, {1: [{1: 20000, 5: enabled, 6: 5}]}]
                assert platform._authorize_4k(session, data, 20000) == '1099511627777'
        with patch.object(platform, 'account_info', side_effect=RuntimeError('expired')), \
                patch.object(module, 'huya_wup_request') as wup:
            try:
                platform._authorize_4k(session, data, 20000)
            except plugins.LoginRequiredError:
                pass
            else:
                raise AssertionError('Expired login must reach the host login notice')
            wup.assert_not_called()

        info = {'sFlvUrl': 'https://cdn.test', 'sStreamName': 'fixture-live'}
        template = 'prefix_$0_$1_$2_$3'
        token = ('wsSecret=old&wsTime=ffffffff&ctype=huya_live&fs=fixture&fm=' +
                 base64.b64encode(template.encode()).decode())
        parser = module.Huya(module.Streamlink(), 'https://www.huya.com/vctcn')
        try:
            with patch.object(module, 'huya_wup_request', return_value={0: token, 1: 600}) as wup, \
                    patch.object(module.time, 'time', return_value=1700000000):
                params = platform._account_stream_params(session, '1099511627777', info, 20000, parser)
            assert wup.call_args.args[1:4] == ('liveui', 'getCdnTokenInfoEx', '1099511627777')
            req = module.tars_fields(wup.call_args.args[4])[0]
            assert req[0] == info['sFlvUrl'] and req[1] == info['sStreamName']
            assert req[3][0] == 1099511627777 and 'auth=fixture' in req[3][4]
            assert params['u'] == 1099511628032, 'Account UID high bits must survive rotation'
            assert params['seqid'] == 1099511627777 + 1700000000000 and params['ratio'] == 20000
            digest = hashlib.md5(f"{params['seqid']}|huya_live|100".encode()).hexdigest()
            signed = f"prefix_{params['u']}_fixture-live_{digest}_ffffffff"
            assert params['wsSecret'] == hashlib.md5(signed.encode()).hexdigest()
            assert 'fm' not in params and 'fixture' not in str(params.get('Cookie', ''))
        finally:
            parser.session.http.close()

        def response(method, fields):
            values = (b'\x08' + module.tars_int(0, 2) + module.huya_string(0, '') +
                      module.tars_bytes(1, module.tars_int(0, 0)) + module.huya_string(0, 'tRsp') +
                      module.tars_bytes(1, b'\x0a' + fields + b'\x0b'))
            body = module.tars_int(1, 3) + module.huya_string(6, method) + module.tars_bytes(7, values)
            result = Mock()
            result.content = struct.pack('>I', len(body) + 4) + body
            result.__enter__ = Mock(return_value=result)
            result.__exit__ = Mock(return_value=False)
            return result

        with patch.object(session, 'post', return_value=response('getPlatformVIPUserInfo',
                module.tars_int(0, 1) + module.tars_int(3, 1))) as post:
            result = module.huya_wup_request(session, 'wupui', 'getPlatformVIPUserInfo', '1', b'\x0a\x0b')
            assert result == {0: 1, 3: 1}
            assert post.call_args.args[0] == 'https://cdnws.api.huya.com/'
            envelope = module.tars_fields(post.call_args.kwargs['data'][4:])
            assert envelope[5] == 'wupui' and envelope[6] == 'getPlatformVIPUserInfo'
        with patch.object(session, 'post', return_value=response('wrongMethod', module.tars_int(0, 1))):
            try:
                module.huya_wup_request(session, 'wupui', 'getPlatformVIPUserInfo', '1', b'\x0a\x0b')
            except RuntimeError:
                pass
            else:
                raise AssertionError('Mismatched official responses must be rejected')
        with patch.object(session, 'post', side_effect=module.requests.HTTPError('secret-baseinfo')):
            try:
                module.huya_wup_request(session, 'wupui', 'getPlatformVIPUserInfo', '1', b'\x0a\x0b')
            except RuntimeError as error:
                assert 'secret-baseinfo' not in str(error) and error.__suppress_context__
            else:
                raise AssertionError('Official request failures must be sanitized')
        session.close()

        import av
        headers = {'User-Agent': 'fixture', 'Referer': 'https://www.huya.com/'}
        for width, height, expected in ((3840, 2160, True), (2560, 1440, False), (1920, 1080, False)):
            container = Mock()
            container.__enter__ = Mock(return_value=container)
            container.__exit__ = Mock(return_value=False)
            container.demux.return_value = [SimpleNamespace(decode=lambda: [SimpleNamespace(width=width, height=height)])]
            with patch.object(av, 'open', return_value=container):
                assert platform._verify_4k('https://cdn.test/live.m3u8', headers) is expected
        container.demux.return_value = []
        with patch.object(av, 'open', return_value=container):
            assert not platform._verify_4k('https://cdn.test/live.m3u8', headers)
        with patch.object(av, 'open', side_effect=ValueError('no video')):
            assert not platform._verify_4k('https://cdn.test/live.m3u8', headers)

        stream = SimpleNamespace(ddm_quality=1020000, to_url=lambda: 'https://cdn.test/live.m3u8')
        platform._qualities['huya:vctcn'] = [{'qn': 1020000, 'rate': 20000, 'restricted': True}]
        session = module.Streamlink()
        result = Mock()
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=False)
        result.iter_content.return_value = iter([b'#EXTM3U'])
        result.url = stream.to_url()
        try:
            with patch.object(platform, '_streams', return_value=(session, None, {'al': stream})), \
                    patch.object(platform, '_probe_hls', return_value=result.url) as probe, \
                    patch.object(platform, '_verify_4k', return_value=True):
                url, qn, _profile, out_headers = platform.play_url('huya:vctcn', 1020000)
                assert qn == 1020000 and url == result.url
                assert set(out_headers) == {'User-Agent', 'Referer'}
                assert 'Cookie' not in probe.call_args.args[1]
            result.iter_content.return_value = iter([b'#EXTM3U'])
            with patch.object(platform, '_streams', return_value=(session, None, {'al': stream})), \
                    patch.object(platform, '_probe_hls', return_value=result.url), \
                    patch.object(platform, '_verify_4k', return_value=False):
                try:
                    platform.play_url('huya:vctcn', 1020000)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('An HTTP-200 downgraded stream must not be reported as 4K')
        finally:
            session.http.close()
        print('PASS: scoped Huya login, VIP/room gates, token wire format, account signing, 4K frame fixtures and no CDN Cookie leak')
    finally:
        manager.unload()


if __name__ == '__main__':
    main()
