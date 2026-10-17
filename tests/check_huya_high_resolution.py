"""Huya defaults to advertised 2K, marks 4K authorization and preserves manual/preview tiers."""
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
            for quality, preview, rate in ((10000, False, 14000), (250, False, 14000), (1020000, False, 20000),
                                           (1014000, False, 14000), (10000, True, 500)):
                stream = platform._streams('huya:vctcn', quality, preview=preview)[2]['al_source']
                assert params.call_args.args[-1] == rate
                assert stream.ddm_quality == 1000000 + rate
            options = platform.room_quality_options('huya:vctcn')
            assert [x['label'] for x in options] == ['2K', '蓝光4M', '流畅', '4K']
            four_k = next(x for x in options if x['qn'] == 1020000)
            assert 'VIP授权未接入' in four_k['desc'] and '暂不保证可用' in four_k['desc']
            from PySide6.QtWidgets import QApplication
            from ddm.widgets import Tile
            app = QApplication.instance() or QApplication([])
            tile = Tile({'room_id': 'huya:vctcn', 'live': False})
            tile.set_quality_options(options)
            assert any('VIP授权未接入' in label for label, _qn in tile._quality_choices())
            assert tile._quality_name(1020000, short=True) == '4K'
            tile.close()
            data['vMultiStreamInfo'] = [x for x in data['vMultiStreamInfo'] if x['sDisplayName'] != '2K']
            page.text = 'var hyPlayerConfig = {stream:' + json.dumps(data) + '};</script>'
            platform._streams('huya:no2k', 10000)
            assert params.call_args.args[-1] == 4000, 'Default must not choose restricted 4K'
            platform._streams('huya:no2k', 10000, preview=True)
            assert params.call_args.args[-1] == 500, 'Menu ordering must not turn 4K into preview quality'
            data['vMultiStreamInfo'].append({'iBitRate': 0, 'sDisplayName': '原画'})
            page.text = 'var hyPlayerConfig = {stream:' + json.dumps(data) + '};</script>'
            platform._streams('huya:original', 10000)
            assert params.call_args.args[-1] == 0, 'Normal rooms must retain original-quality selection'
            data['vMultiStreamInfo'] = []
            page.text = 'var hyPlayerConfig = {stream:' + json.dumps(data) + '};</script>'
            stream = platform._streams('huya:empty')[2]['al_source']
            assert stream.ddm_quality == 10000 and params.call_args.args[-1] == 0
        print('PASS: default 2K, 4K authorization warning in actual menu, manual 4K, lowest preview and normal-room fallbacks')
    finally:
        session.http.close()
        manager.unload()


if __name__ == '__main__':
    main()
