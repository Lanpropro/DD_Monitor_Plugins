"""Display-only node regions; retain the original node names for core requests."""
from functools import lru_cache
from pathlib import Path
import re

from PySide6.QtGui import QIcon

FLAGS = Path(__file__).with_name('flags')
EMOJI_FLAG = re.compile('[\U0001f1e6-\U0001f1ff]{2}')
REGIONS = {
    'cn': ('台湾', '台灣', '台北', '台中', '高雄', 'taiwan', 'taipei', '中国', '中國', '大陆', '大陸', 'china'),
    'hk': ('香港', 'hong kong', 'hongkong'), 'mo': ('澳门', '澳門', 'macau', 'macao'),
    'jp': ('日本', '东京', '東京', '大阪', 'japan', 'tokyo'), 'sg': ('新加坡', '狮城', '獅城', 'singapore'),
    'us': ('美国', '美國', '洛杉矶', '洛杉磯', '西雅图', 'united states', 'los angeles', 'usa'),
    'gb': ('英国', '英國', '伦敦', '倫敦', 'united kingdom', 'britain', 'london'),
    'de': ('德国', '德國', '法兰克福', 'germany', 'frankfurt'), 'fr': ('法国', '法國', '巴黎', 'france', 'paris'),
    'kr': ('韩国', '韓國', '首尔', '首爾', 'korea', 'seoul'), 'ca': ('加拿大', 'canada'),
    'au': ('澳大利亚', '澳洲', '悉尼', 'australia', 'sydney'), 'nl': ('荷兰', '荷蘭', 'netherlands'),
    'in': ('印度', 'india'), 'ru': ('俄罗斯', '俄羅斯', 'russia'), 'vn': ('越南', 'vietnam'),
    'th': ('泰国', '泰國', 'thailand'), 'my': ('马来西亚', '馬來西亞', 'malaysia'),
    'id': ('印度尼西亚', '印尼', 'indonesia'), 'tr': ('土耳其', 'turkey'), 'br': ('巴西', 'brazil'),
    'ph': ('菲律宾', '菲律賓', 'philippines'), 'ae': ('阿联酋', '阿聯酋', '迪拜', 'uae', 'dubai'),
}


def region_code(name):
    text = name.casefold()
    # The explicit Taiwan mapping takes priority over any flag already in a label.
    if any(alias in text for alias in REGIONS['cn'][:7]) or re.search(r'(?<![a-z0-9])tw(?![a-z])', text):
        return 'cn'
    emoji = EMOJI_FLAG.search(name)
    if emoji:
        code = ''.join(chr(ord(char) - 0x1f1e6 + ord('a')) for char in emoji.group())
        if code == 'tw':
            return 'cn'
        if code in REGIONS:
            return code
    # Longer names must win (for example Indonesia before India).
    matches = [(len(alias), code) for code, aliases in REGIONS.items() for alias in aliases if alias in text]
    if matches:
        return max(matches)[1]
    for code in (*REGIONS, 'uk', 'tw'):
        if re.search(r'(?<![a-z0-9])' + code + r'(?![a-z])', text):
            return {'tw': 'cn', 'uk': 'gb'}.get(code, code)
    return ''


def display_node_name(name):
    return EMOJI_FLAG.sub('', name).strip()


@lru_cache(maxsize=23)
def flag_icon(code):
    return QIcon(str(FLAGS / f'{code}.svg')) if code else QIcon()
