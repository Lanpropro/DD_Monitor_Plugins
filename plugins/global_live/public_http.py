"""Public HTTP requests and URL/image validation; no media or UI dependency."""
from urllib.parse import urlsplit
import requests


def public_request(method, url, **kwargs):
    for attempt in range(2):
        response = None
        try:
            response = getattr(requests, method)(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if response is not None:
                response.close()
            if attempt:
                raise

def official_url(text, hosts):
    parts = urlsplit(text)
    if (parts.scheme not in ("http", "https") or parts.hostname not in hosts or
            parts.username or parts.password or parts.port not in (None, 80, 443)):
        raise ValueError("请使用平台官方直播链接")
    return parts

def image_url(value, hosts):
    parts = urlsplit(str(value or ""))
    host = parts.hostname or ""
    return str(value) if (parts.scheme == "https" and not parts.username and not parts.password and
        any(host == domain or host.endswith("." + domain) for domain in hosts)) else ""

def thumbnail(data, hosts):
    choices = sorted(data.get("thumbnails", []), key=lambda item: item.get("width", 0), reverse=True)
    return next((url for item in choices if (url := image_url(item.get("url"), hosts))), "")
