"""Cache one completed OpenF1 session as a local F1 Play ranking file."""
import argparse
import json
from pathlib import Path
from time import sleep
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen


def fetch(endpoint, session_key):
    query = urlencode({'session_key': session_key})
    with urlopen(f'https://api.openf1.org/v1/{endpoint}?{query}', timeout=20) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session_key', type=int)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        sessions = fetch('sessions', args.session_key)
        if len(sessions) != 1:
            parser.exit(1, 'OpenF1 未返回唯一的历史场次。\n')
        data = {'session': sessions[0]}
        endpoints = ['drivers', 'laps', 'stints']
        if data['session']['session_type'] == 'Race':
            endpoints.extend(('position', 'intervals'))
        for endpoint in endpoints:
            sleep(.4)
            data[endpoint] = fetch(endpoint, args.session_key)
    except HTTPError as error:
        reason = '直播时段的匿名历史访问可能暂不可用。' if error.code == 401 else ''
        parser.exit(1, f'OpenF1 请求失败（HTTP {error.code}）。{reason}\n')
    args.output.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
