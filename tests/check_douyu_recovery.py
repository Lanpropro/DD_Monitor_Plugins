"""Offline current-CDN failover and unchanged native quality; no account or live playback."""
import argparse
import os
from pathlib import Path
import sys
from unittest.mock import patch

os.environ['DDM_NO_SAVE'] = '1'


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--host', type=Path, required=True)
    sys.path.insert(0, str(args.parse_args().host.resolve()))
    from ddm import plugins
    root = Path(__file__).resolve().parents[1]
    manager = plugins.PluginManager(plugins_dir=str(root / 'plugins'), enabled=['domestic_live'])
    manager.load()
    platform = manager.platforms['douyu']
    module = sys.modules[type(platform).__module__]
    session = module.Streamlink()
    calls = []
    clock = [100.]
    source = {'rtmp_url': 'https://edge.test.edgesrv.com', 'rtmp_live': 'live?token=private',
              'rate': 0, 'cdnsWithName': [{'cdn': 'scdn-new'}, {'cdn': 'hw-h5'}],
              'multirates': [{'rate': 0, 'bit': 16000, 'name': '原画'},
                             {'rate': 8, 'bit': 8000, 'name': '蓝光8M'}]}

    def request(parser, raw, cdn='', *, rate=0):
        calls.append((raw, cdn, rate))
        return dict(source, rate=rate, rtmp_url='https://hw.test' if cdn == 'hw-h5'
                    else 'https://edge.test.edgesrv.com')

    try:
        with patch.object(platform, 'room_info', side_effect=lambda room: plugins.RoomInfo(room, live=True)), \
                patch.object(platform, '_request_source', side_effect=request), \
                patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
            stream = platform._streams(session, 'douyu:123', 10000)['source']
            assert stream.to_url().startswith('https://hw.test/')
            assert stream.ddm_quality == 10000
            platform.report_playback_failure('douyu:123', stream.to_url(), 'LIVE_EOF')
            stream = platform._streams(session, 'douyu:123', 10000)['source']
            assert calls[-1] == ('123', 'scdn-new', 0), calls
            assert stream.ddm_quality == 10000
            assert 'private' not in str(platform._source_cdns) + str(platform._failed_cdns)
            # Avoid a CDN ID even when its next host changes; use only current API entries.
            source['rtmp_url'] = 'https://another.test.edgesrv.com'
            platform._streams(session, 'douyu:123', 2000008)
            assert calls[-1] == ('123', 'scdn-new', 8)
            platform._streams(session, 'douyu:456', 10000)
            assert calls[-1] == ('456', 'hw-h5', 0), 'Failures must be room scoped'
            source['cdnsWithName'] = [{'cdn': 'hw-h5'}]
            platform._streams(session, 'douyu:123', 10000)
            assert calls[-1] == ('123', 'hw-h5', 0), 'No stale CDN lock when the list changes'
            source['cdnsWithName'] = [{'cdn': 'scdn-new'}, {'cdn': 'hw-h5'}]
            clock[0] += 61
            platform._streams(session, 'douyu:123', 10000)
            assert calls[-1] == ('123', 'hw-h5', 0), 'Failure expires after 60 seconds'
            # Real 18:49:57 response: requested Hefei, returned Beijing. Avoid both
            # the failed request route and the CDN that actually served the stream.
            source['cdnsWithName'] = [{'cdn': 'scdncmccanhhf'}, {'cdn': 'scdncttbej'}, {'cdn': 'hw-h5'}]
            source['rtmp_cdn'] = 'scdncttbej'
            source['multirates'].append({'rate': 15, 'bit': 15000, 'name': '2K'})
            platform._failed_cdns['douyu:789'] = {'hw-h5': clock[0]}
            stream = platform._streams(session, 'douyu:789', 2000015)['source']
            assert calls[-1] == ('789', 'scdncmccanhhf', 15) and stream.ddm_quality == 2000015
            platform.report_playback_failure('douyu:789', stream.to_url(), 'LIVE_EOF')
            failed = platform._failed_cdns['douyu:789']
            assert 'scdncttbej' in failed, 'Actual returned CDN must be rejected even when it differs from the request'
            assert 'scdncmccanhhf' in failed, 'The request route that led to the failed stream must also be avoided'
        plugin = module.LivePlatformsPlugin()
        plugin.context = plugins.PluginContext(manager, 'domestic_live')
        plugin._diagnostic_enabled = False
        with patch.object(platform, 'report_playback_failure') as report:
            plugin.on_event(plugins.EVENT_STREAM_FAILED, {'room': {'room_id': 'douyu:123'},
                'source_url': 'https://hw.test/live', 'reason': 'STALL'})
            report.assert_called_once_with('douyu:123', 'https://hw.test/live', 'STALL')
    finally:
        session.http.close()
        manager.unload()
    print('PASS: Douyu failure feedback, room-scoped current-CDN failover, quality retained, expiry, no signed URL storage')


if __name__ == '__main__':
    main()
