"""Direct-media validation and Bilibili request headers."""
from __future__ import annotations
from typing import Any

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Referer": "https://www.bilibili.com/",
}


_WEBPAGE_MARKERS = (
    "bilibili.com/video/",
    "bilibili.com/bangumi/",
    "bilibili.com/list/",
    "bilibili.com/watchlater",
)


def is_direct_url(url: Any) -> bool:
    """True 仅当 http(s) 直链且不是 B 站网页。注入宿主前必须过这道闸。"""
    if not isinstance(url, str):
        return False
    text = url.strip()
    if not text or not text.lower().startswith(("http://", "https://")):
        return False
    lowered = text.lower()
    return not any(marker in lowered for marker in _WEBPAGE_MARKERS)


def merge_headers(headers: Any) -> dict[str, str]:
    """始终带 UA + Referer 的非空头字典（大小写不敏感去重）。

    宿主 ``_play_on`` 对空 headers 会回退到直播 Referer
    （``live.bilibili.com``），点播 CDN 会 403。调用方若自带任意大小写
    的 referer/user-agent，尊重它，不再叠加默认值造成重复头。
    """
    merged: dict[str, str] = {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            if isinstance(key, str) and isinstance(value, str) and key.strip():
                merged[key] = value
    lowered = {key.lower() for key in merged}
    if "referer" not in lowered:
        merged["Referer"] = DEFAULT_HEADERS["Referer"]
    if "user-agent" not in lowered:
        merged["User-Agent"] = DEFAULT_HEADERS["User-Agent"]
    return merged
