"""Choose the station artwork independently of the stream provider."""
from pathlib import Path
import re
from unicodedata import normalize


LOGOS = Path(__file__).resolve().parent / 'logos'
GENERIC_LOGO = str(LOGOS / 'iptv.png')
CCTV_CHANNEL = re.compile(r'^(?:CCTV|中央电视台|央视)[\s_-]*(\d{1,2})(\s*(?:\+|PLUS))?(?![A-Z0-9+])')
CCTV_ART = {'1': 'cctv1.png', '5': 'cctv5.png', '5+': 'cctv5plus.png',
            '6': 'cctv6.png', '13': 'cctv13.png'}
LOCAL_ART = {'广州综合': 'guangzhou.png', '广州综合频道': 'guangzhou.png',
             '河北卫视': 'hebei.png'}


def channel_branding(channel):
    identities = [normalize('NFKC', value).strip().upper()
                  for value in (channel.name, channel.tvg_name, channel.tvg_id) if isinstance(value, str) and value]
    for identity in identities:
        match = CCTV_CHANNEL.match(identity)
        if match is None or not 1 <= int(match[1]) <= 17:
            continue
        number = str(int(match[1])) + ('+' if match[2] else '')
        artwork = CCTV_ART.get(number)
        return {'avatar_path': str(LOGOS / (artwork or 'cctv.png')) if artwork or not channel.logo else '',
                'badge_path': str(LOGOS / 'cctv.png'), 'label': 'CCTV'}
    if channel.logo:
        return {'avatar_path': GENERIC_LOGO, 'badge_path': GENERIC_LOGO,
                'badge_from_avatar': True, 'label': channel.name}
    artwork = next((LOCAL_ART[identity] for identity in identities if identity in LOCAL_ART), None)
    if artwork:
        path = str(LOGOS / artwork)
        return {'avatar_path': path, 'badge_path': path, 'label': channel.name}
    return {'avatar_path': GENERIC_LOGO, 'badge_path': GENERIC_LOGO, 'label': 'IPTV'}
