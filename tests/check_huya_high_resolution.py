"""Huya's advertised 4K/2K tiers must outrank an invented rate-zero stream."""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    host = args.host.resolve()
    sys.path.insert(0, str(host))
    os.chdir(host)
    os.environ['DDM_NO_SAVE'] = '1'
    from ddm.plugins import PluginManager
    manager = PluginManager(plugins_dir=str(ROOT / 'plugins'), enabled=['domestic_live'])
    manager.load()
    platform = manager.platforms['huya']
    module = sys.modules[type(platform).__module__]
    session = module.Streamlink()
    data = {'vMultiStreamInfo': [{'iBitRate': rate, 'sDisplayName': name} for rate, name in
            ((20000, '4K'), (14000, '2K'), (4000, '蓝光4M'), (500, '流畅'))],
            'data': [{'gameStreamInfoList': [{'sHlsUrl': 'https://cdn.test', 'sStreamName': 'live',
                      'sHlsUrlSuffix': 'm3u8', 'sHlsAntiCode': 'fm=x&fs=y', 'sCdnType': 'AL'}]}]}
    page = type('Page', (), {'text': 'var hyPlayerConfig = {stream:' + json.dumps(data) + '};</script>'})()
    try:
        with patch.object(module, 'Streamlink', return_value=session), \
                patch.object(session.http, 'get', return_value=page), \
                patch.object(module.Huya, '_get_stream_params', return_value={}) as params:
            for quality, preview, rate in ((10000, False, 20000), (1020000, False, 20000),
                                           (1014000, False, 14000), (10000, True, 500)):
                stream = platform._streams('huya:vctcn', quality, preview=preview)[2]['al_source']
                assert params.call_args.args[-1] == rate
                assert stream.ddm_quality == 1000000 + rate
            assert [x['desc'] for x in platform.room_quality_options('huya:vctcn')] == [
                '4K', '2K', '蓝光4M', '流畅']
            data['vMultiStreamInfo'] = []
            page.text = 'var hyPlayerConfig = {stream:' + json.dumps(data) + '};</script>'
            stream = platform._streams('huya:empty')[2]['al_source']
            assert stream.ddm_quality == 10000 and params.call_args.args[-1] == 0
        print('PASS: native 4K/2K menu, highest selection, manual tiers, preview isolation and missing metadata fallback')
    finally:
        session.http.close()
        manager.unload()


if __name__ == '__main__':
    main()
