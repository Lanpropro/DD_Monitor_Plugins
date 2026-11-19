"""On-demand Bilibili subtitles: bounded read-only API/CDN access, pure SRT.

The caller starts a worker only after a subtitle-button click. Listing does
not download files; a worker selects exactly one language, without fallback
requests/retries. Network/format failures return an empty result; cancellation
propagates as BiliCancelled. Credentials and signed URLs are never reported.
"""
from __future__ import annotations

import json
import math
import threading
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests

from .bili_api import UA, VIEW_ENDPOINT, BiliCancelled
from .models import safe_int, valid_bvid
from .vod_stream import (
    NAV_ENDPOINT,
    TIMEOUT,
    WEB_HOST,
    _api_json,
    _fetch_mixin_key,
    wbi_signed_params,
)

PLAYER_V2_ENDPOINT = "https://api.bilibili.com/x/player/wbi/v2"
MAX_API_BYTES = 1024 * 1024
MAX_SUBTITLE_BYTES = 4 * 1024 * 1024
MAX_LANGUAGES = 64
MAX_CUES = 10000
MAX_TEXT_CHARS = 2000
MAX_TOTAL_TEXT_CHARS = 1000000
MAX_LABEL_CHARS = 128
MAX_URL_CHARS = 8192
MAX_TIME_SECONDS = 7 * 24 * 3600
CDN_HOSTS = frozenset({
    "aisubtitle.hdslb.com", "subtitle.hdslb.com",
    "i0.hdslb.com", "i1.hdslb.com", "i2.hdslb.com",
})
_HEADERS = {
    "User-Agent": UA,
    "Referer": WEB_HOST + "/",
    "Accept": "application/json, text/plain, */*",
    # Reject unexpected compression rather than inflating unbounded input.
    "Accept-Encoding": "identity",
}


def _check_cancelled(cancelled) -> None:
    if cancelled is not None:
        is_set = getattr(cancelled, "is_set", None)
        stopped = is_set() if callable(is_set) else cancelled()
        if stopped:
            raise BiliCancelled()


def _close(resource) -> None:
    try:
        resource.close()
    except Exception:
        pass


def _https(url: Any) -> str:
    """Accept only HTTPS official subtitle CDN paths, never arbitrary hosts."""
    if not isinstance(url, str) or len(url) > MAX_URL_CHARS:
        return ""
    text = url.strip()
    if any(ord(char) <= 32 or ord(char) == 127 for char in text) or "\\" in text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    try:
        parts = urlsplit(text)
        if (parts.scheme != "https" or parts.hostname not in CDN_HOSTS
                or parts.port not in (None, 443) or parts.username is not None
                or parts.password is not None or parts.fragment
                or not parts.path.startswith(("/bfs/subtitle/", "/bfs/ai_subtitle/"))):
            return ""
    except ValueError:
        return ""
    return text


def _read_json(response, limit: int, cancelled) -> Any:
    """Consume at most limit bytes and always close, including status failures."""
    try:
        _check_cancelled(cancelled)
        if response.status_code != 200:
            raise ValueError("字幕请求失败")
        if response.headers.get("Content-Encoding", "").lower() not in ("", "identity"):
            raise ValueError("字幕响应编码不支持")
        length = response.headers.get("Content-Length")
        if length is not None and (not str(length).isdigit() or int(length) > limit):
            raise ValueError("字幕响应过大或长度异常")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            _check_cancelled(cancelled)
            if len(body) + len(chunk) > limit:
                raise ValueError("字幕响应过大")
            body.extend(chunk)
        _check_cancelled(cancelled)
        return json.loads(body)
    finally:
        _close(response)


def _reject_status(response, **_kwargs):
    # requests otherwise consumes redirect bodies while preparing response.next,
    # even with allow_redirects=False. Reject before that unbounded read.
    if response.status_code != 200:
        _close(response)
        raise ValueError("字幕请求失败")
    return response


class _JSONResponse:
    """Detached response consumed by vod_stream's existing JSON helpers."""

    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _BoundedAPI:
    """Apply streaming limits even to the existing nav/view helper requests."""

    def __init__(self, http, cancelled):
        self.http = http
        self.cancelled = cancelled

    def get(self, url, **kwargs):
        if url not in (NAV_ENDPOINT, VIEW_ENDPOINT, PLAYER_V2_ENDPOINT):
            raise ValueError("字幕接口不受支持")
        _check_cancelled(self.cancelled)
        kwargs.update(headers=dict(_HEADERS), stream=True,
                      timeout=TIMEOUT, allow_redirects=False)
        response = self.http.get(url, **kwargs)
        return _JSONResponse(_read_json(response, MAX_API_BYTES, self.cancelled))


def parse_subtitle_list(payload: Any) -> list[dict]:
    """Parse wbi/v2 data.subtitle.subtitles; retain API order, bound all fields."""
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return []
    data = payload.get("data")
    if not isinstance(data, dict) or "v_voucher" in payload or "v_voucher" in data:
        return []
    subtitle = data.get("subtitle")
    if not isinstance(subtitle, dict):
        return []
    entries = subtitle.get("subtitles")
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries[:MAX_LANGUAGES]:
        if not isinstance(entry, dict):
            continue
        url = _https(entry.get("subtitle_url"))
        if not url:
            continue
        lan = entry.get("lan")
        lan = lan[:64].strip() if isinstance(lan, str) else ""
        label = entry.get("lan_doc")
        label = label if isinstance(label, str) else lan
        label = " ".join(label[:MAX_LABEL_CHARS].split()) or lan or "字幕"
        out.append({
            "lan": lan, "label": label, "url": url,
            "is_ai": lan.lower().startswith("ai-") or safe_int(entry.get("ai_type")) > 0,
        })
    return out


def select_subtitle(entries: list[dict]) -> dict | None:
    """Stable preference: manual simplified/zh/other Chinese, AI Chinese, first."""
    def rank(entry):
        lan = entry.get("lan", "").lower().replace("_", "-")
        ai = entry.get("is_ai", False) or lan.startswith("ai-")
        language = lan[3:] if lan.startswith("ai-") else lan
        if language != "zh" and not language.startswith("zh-"):
            return 5
        if ai:
            return 3 if language in ("zh", "zh-cn", "zh-hans") else 4
        return 0 if language in ("zh-cn", "zh-hans") else 1 if language == "zh" else 2

    return min(entries, key=rank) if entries else None


def fetch_subtitle_list(
    bvid: str,
    cid: int = 0,
    *,
    cookies: Mapping[str, Any] | None = None,
    session: requests.Session | None = None,
    cancelled: Callable[[], bool] | threading.Event | None = None,
) -> list[dict]:
    """List only. Missing cid uses view data.cid, then its first pages entry.

    vod_stream exposes _api_json(http, url, *, params, cookies, cancelled,
    strategy), not a dedicated view resolver. The bounded adapter preserves
    that signature and bounds both view and WBI-nav responses.
    """
    http = None
    owns = session is None
    try:
        _check_cancelled(cancelled)
        clean = valid_bvid(bvid)
        if not clean:
            return []
        part = safe_int(cid)
        jar = {str(k): str(v) for k, v in (cookies or {}).items() if v is not None}
        http = requests.Session() if owns else session
        api = _BoundedAPI(http, cancelled)
        if part <= 0:
            view = _api_json(api, VIEW_ENDPOINT, params={"bvid": clean},
                             cookies=jar, cancelled=cancelled, strategy="subtitle")
            data = view.get("data")
            if not isinstance(data, dict):
                return []
            part = safe_int(data.get("cid"))
            pages = data.get("pages")
            if part <= 0 and isinstance(pages, list) and pages and isinstance(pages[0], dict):
                part = safe_int(pages[0].get("cid"))
            if part <= 0:
                return []
        _check_cancelled(cancelled)
        key = _fetch_mixin_key(api, jar, cancelled, "subtitle")
        if not key:
            return []
        signed = wbi_signed_params({"bvid": clean, "cid": part}, key)
        payload = _api_json(api, PLAYER_V2_ENDPOINT, params=signed,
                            cookies=jar, cancelled=cancelled, strategy="subtitle")
        _check_cancelled(cancelled)
        return parse_subtitle_list(payload)
    except BiliCancelled:
        raise
    except Exception:
        return []
    finally:
        if owns and http is not None:
            _close(http)


def _fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _time(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= MAX_TIME_SECONDS else None


def subtitle_json_to_srt(payload: Any) -> str:
    """Bounded pure conversion; malformed/NaN/Inf times and bad cues are skipped."""
    if isinstance(payload, (str, bytes, bytearray)):
        if len(payload) > MAX_SUBTITLE_BYTES:
            return ""
        try:
            if isinstance(payload, str) and len(payload.encode("utf-8")) > MAX_SUBTITLE_BYTES:
                return ""
            payload = json.loads(payload)
        except (ValueError, RecursionError):
            return ""
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), list):
        return ""
    lines = []
    index = total_text = 0
    for item in payload["body"][:MAX_CUES]:
        if not isinstance(item, dict):
            continue
        start, end = _time(item.get("from")), _time(item.get("to"))
        if start is None or end is None or end <= start or round(end * 1000) <= round(start * 1000):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        content = content[:MAX_TEXT_CHARS].replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
        # Avoid blank-line cue injection and invalid UTF-8 temporary files.
        content = "\n".join(line.strip() for line in content.split("\n") if line.strip())
        content = content.encode("utf-8", "replace").decode("utf-8")
        if not content:
            continue
        if total_text + len(content) > MAX_TOTAL_TEXT_CHARS:
            break
        total_text += len(content)
        index += 1
        lines.extend((str(index), f"{_fmt_ts(start)} --> {_fmt_ts(end)}", content, ""))
    return "\n".join(lines)


def download_subtitle_srt(
    url: str,
    *,
    session: requests.Session | None = None,
    cancelled=None,
) -> str:
    """Download one allowlisted subtitle; no cookies, redirects or retries.

    Preparing Request directly (not Session.prepare_request) also excludes
    injected-session cookies, auth, default headers and netrc credentials.
    """
    http = None
    owns = session is None
    try:
        _check_cancelled(cancelled)
        target = _https(url)
        if not target:
            return ""
        http = requests.Session() if owns else session
        request = requests.Request("GET", target, headers=dict(_HEADERS)).prepare()
        response = http.send(request, stream=True, timeout=TIMEOUT, allow_redirects=False)
        payload = _read_json(response, MAX_SUBTITLE_BYTES, cancelled)
        result = subtitle_json_to_srt(payload)
        _check_cancelled(cancelled)
        return result
    except BiliCancelled:
        raise
    except Exception:
        return ""
    finally:
        if owns and http is not None:
            _close(http)
