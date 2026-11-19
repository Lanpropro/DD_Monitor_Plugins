"""Room input validation and platform normalization; no GUI or network requests."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def parse_room(text: str, manager=None) -> str:
    text = text.strip()
    platform = manager.platform_for(text) if manager is not None else None
    if platform is not None:
        if platform.playback_mode != "stream":
            raise ValueError("此平台不提供直播流，无法加入比赛二路")
        return platform.normalize(text)
    if not text.isdigit():
        link = urlsplit(text)
        if link.scheme not in ("http", "https") or link.hostname != "live.bilibili.com":
            raise ValueError("请输入 B 站房间号，或已启用平台的房间号/官方链接")
        found = re.fullmatch(r"/(?:h5/)?([0-9]+)/?", link.path)
        if not found:
            raise ValueError("链接中没有有效的直播房间号")
        text = found[1]
    if int(text) <= 0:
        raise ValueError("直播房间号必须大于零")
    return str(int(text))
