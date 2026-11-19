"""Bilibili on-demand (VOD) direct-stream resolver.

Pure Python: no Qt imports, so this module is fully offline-testable.

Design rules (see 内部说明 §5):

* three strategies, each attempted **exactly once**, no retry of any kind;
* requests only ever go to ``api.bilibili.com`` / ``www.bilibili.com``;
* cookies are never logged, never embedded in a message, an exception or a
  ``repr``, and never written to disk;
* every failure is mapped to a readable Chinese sentence;
* quality selection (``qn``) only *asks* the API for a tier: what is
  actually available depends on login state and 大会员 status, and the
  plugin plays whatever the API returns — no risk-control bypass, no
  privilege escalation, no paywall circumvention.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

import requests

from .bili_api import UA, BiliAPIError, BiliCancelled
from .models import safe_int, valid_bvid

__all__ = [
    "VodCancelled",
    "VodStream",
    "VodStreamError",
    "clear_wbi_cache",
    "qualities_from_stream",
    "quality_label",
    "resolve_vod",
    "wbi_mixin_key",
    "wbi_signed_params",
]

TIMEOUT = (5, 8)

API_HOST = "https://api.bilibili.com"
WEB_HOST = "https://www.bilibili.com"

PLAYURL_ENDPOINT = API_HOST + "/x/player/playurl"
WBI_PLAYURL_ENDPOINT = API_HOST + "/x/player/wbi/playurl"
PAGELIST_ENDPOINT = API_HOST + "/x/player/pagelist"
NAV_ENDPOINT = API_HOST + "/x/web-interface/nav"

BASE_HEADERS = {
    "User-Agent": UA,
    "Referer": WEB_HOST + "/",
    "Accept": "application/json, text/plain, */*",
}
# mpv/libmpv needs these two or the CDN answers 403.
STREAM_HEADERS = {
    "User-Agent": UA,
    "Referer": WEB_HOST + "/",
}

STRATEGY_DURL = "playurl-durl"
STRATEGY_DASH = "wbi-dash"
STRATEGY_PLAYINFO = "playinfo"
STRATEGY_ORDER: tuple[str, ...] = (STRATEGY_DURL, STRATEGY_DASH, STRATEGY_PLAYINFO)
_STRATEGY_LABELS = {
    STRATEGY_DURL: "durl 直链",
    STRATEGY_DASH: "WBI DASH",
    STRATEGY_PLAYINFO: "网页 playinfo",
}

MSG_INTERCEPTED = "请求被 B 站拦截（412），请稍后重试；如持续出现，请先在浏览器打开一次 B 站"
MSG_RISK = "B 站风控校验未通过（-352），请稍后重试"
MSG_LOGIN = "该视频需要登录后才能播放"
MSG_PERMISSION = "没有权限播放该视频（可能需要大会员）"
MSG_GONE = "视频不存在或已失效"
MSG_RATE = "请求过于频繁，请稍后再试"
MSG_NETWORK = "网络连接失败，请检查网络/代理后重试"
MSG_NO_URL = "B 站未返回可播放地址，请用浏览器打开"
MSG_BAD_BVID = "视频 BV 号无效，无法取流"
MSG_BAD_PAYLOAD = "数据格式异常，请更新插件或反馈"
MSG_NO_CID = "无法获取视频分 P 信息，请用浏览器打开"
MSG_NO_KEY = "B 站未返回签名密钥，请稍后重试"

_PLAYINFO_RE = re.compile(r"window\.__playinfo__=(.*?)</script>", re.S)
_STRIP_RE = re.compile(r"[!'()*]")

# Standard 64-entry WBI mixin permutation table.
MIXIN_KEY_ENC_TAB: tuple[int, ...] = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

WBI_CACHE_TTL = 600.0
_WBI_CACHE: dict[str, tuple[float, str]] = {}

HQ_DEFAULT_QN = 80
# DASH | HDR | 4K | Dolby audio | Dolby Vision | 8K | AV1.  These are
# response capabilities, NOT quality/permission overrides.  Request the menu
# candidates but open only one video (1080P by default), preferring AVC.
# fnval=16 alone requests DASH without advertising these extra capabilities.
HQ_DASH_FNVAL = 16 | 64 | 128 | 256 | 512 | 1024 | 2048  # 4048


# --------------------------------------------------------------------- errors
class VodCancelled(BiliCancelled):
    """Raised when the caller cancelled between two requests."""


class VodStreamError(BiliAPIError):
    """A readable VOD failure, plus which strategy produced it.

    ``terminal`` means "do not try another strategy": the answer would be the
    same everywhere (412, 429, login/permission/missing video, network down).
    """

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        http_status: int | None = None,
        endpoint: str = "",
        strategy: str = "",
        terminal: bool = False,
    ) -> None:
        super().__init__(
            message, code=code, http_status=http_status, endpoint=endpoint
        )
        self.strategy = strategy
        self.terminal = terminal


# ---------------------------------------------------------------------- model
@dataclass(slots=True)
class VodStream:
    """One playable direct URL plus everything mpv needs to open it.

    ``accept_quality`` / ``accept_description`` mirror the playurl ``data``
    lists for legacy/durl streams.  HQ DASH restricts them to positive video
    ids with URLs in this response, not advertised/locked tiers; this does
    not claim that the CDN or local decoder has been tested.  ``qn`` is the
    selected DASH video ``id`` or durl ``data.quality``, never the requested
    tier.  The API alone decides which URLs the account may receive.

    ``cid`` is the supplied/resolved part id for subtitle/preview callers;
    0 means unknown (e.g. legacy durl succeeds without a cid lookup).  All
    added fields have defaults and are appended for positional compatibility.
    """

    url: str
    backup_urls: tuple[str, ...] = ()
    quality: int = 0
    strategy: str = ""
    is_dash: bool = False
    audio_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    accept_quality: tuple[int, ...] = ()
    accept_description: tuple[str, ...] = ()
    qn: int = 0
    cid: int = 0


# Fallback labels for the well-known qn tiers, used when the API does not
# provide ``accept_description``.  UI text: Chinese, no emoji.
_QUALITY_LABELS: dict[int, str] = {
    127: "8K 超高清",
    126: "杜比视界",
    125: "HDR 真彩",
    120: "4K 超清",
    116: "1080P60",
    112: "1080P 高码率",
    80: "1080P",
    74: "720P60",
    64: "720P",
    32: "480P",
    16: "360P",
}


def quality_label(qn: int) -> str:
    """Chinese fallback label for one quality tier (pure, never raises).

    Known tiers map to their usual Chinese names; anything else renders as
    ``qn{value}`` so an unexpected API value still produces readable text.
    """
    value = safe_int(qn)
    return _QUALITY_LABELS.get(value, f"qn{value}")


def qualities_from_stream(stream: VodStream) -> list[tuple[int, str]]:
    """Pair a stream's accept lists into menu-ready ``(qn, label)`` items.

    Rules (pure function, no I/O):

    * zip ``accept_quality`` with ``accept_description`` positionally; a
      missing or blank description falls back to :func:`quality_label`;
    * duplicate qn values are dropped, first occurrence wins, order kept;
    * when the accept list is empty, a single item for ``stream.qn`` is
      returned if it is positive, otherwise ``[]``.

    Tolerates stream-like objects without the v1.0 fields (treated as
    empty), so legacy fakes keep working.  Available tiers always reflect
    what the API returned for the current login/大会员 state.
    """
    raw_quality = getattr(stream, "accept_quality", ()) or ()
    raw_description = getattr(stream, "accept_description", ()) or ()
    qualities = tuple(raw_quality) if isinstance(raw_quality, (list, tuple)) else ()
    descriptions = (
        tuple(raw_description) if isinstance(raw_description, (list, tuple)) else ()
    )
    pairs: list[tuple[int, str]] = []
    seen: set[int] = set()
    for index, raw_qn in enumerate(qualities):
        qn = safe_int(raw_qn)
        if qn in seen:
            continue
        seen.add(qn)
        label = ""
        if index < len(descriptions):
            candidate = descriptions[index]
            if isinstance(candidate, str) and candidate.strip():
                label = candidate
        if not label:
            label = quality_label(qn)
        pairs.append((qn, label))
    if not pairs:
        current = safe_int(getattr(stream, "qn", 0))
        if current > 0:
            pairs.append((current, quality_label(current)))
    return pairs


# ------------------------------------------------------------------- helpers
def _check_cancelled(
    cancelled: Callable[[], bool] | threading.Event | None,
) -> None:
    if cancelled is None:
        return
    is_set = getattr(cancelled, "is_set", None)
    stopped = is_set() if callable(is_set) else cancelled()
    if stopped:
        raise VodCancelled()


def _cookie_dict(cookies: Mapping[str, Any] | None) -> dict[str, str]:
    """Copy the credential snapshot; never keep a reference to the caller's."""
    result: dict[str, str] = {}
    if not isinstance(cookies, Mapping):
        return result
    for key, value in cookies.items():
        if value is None:
            continue
        result[str(key)] = str(value)
    return result


def _message_from(payload: Mapping[str, Any]) -> str:
    value = payload.get("message")
    if value is None:
        value = payload.get("msg")
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text in ("", "0") else text


def _has_voucher(payload: Mapping[str, Any]) -> bool:
    """``v_voucher`` anywhere in the payload means risk control kicked in."""
    if "v_voucher" in payload:
        return True
    data = payload.get("data")
    return isinstance(data, dict) and "v_voucher" in data


# --------------------------------------------------------------------- WBI
def wbi_mixin_key(img_url: str, sub_url: str) -> str:
    """Derive the 32-char mixin key from the two nav image URLs."""
    img = str(img_url or "").rsplit("/", 1)[-1].split(".")[0]
    sub = str(sub_url or "").rsplit("/", 1)[-1].split(".")[0]
    raw = img + sub
    if len(raw) < 64:
        return ""
    return "".join(raw[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def wbi_signed_params(
    params: Mapping[str, Any],
    mixin_key: str,
    *,
    wts: int | None = None,
) -> dict[str, str]:
    """Sign ``params``: add ``wts``, sort, urlencode, append ``w_rid``.

    ``wts`` is injectable so the result is deterministic in tests.
    """
    signed: dict[str, str] = {}
    for key, value in params.items():
        text = "" if value is None else str(value)
        signed[str(key)] = _STRIP_RE.sub("", text)
    signed["wts"] = str(int(time.time()) if wts is None else int(wts))
    query = urlencode(sorted(signed.items()))
    digest = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    signed["w_rid"] = digest
    return signed


def clear_wbi_cache() -> None:
    """Drop the cached mixin key (used by tests and after a login change)."""
    _WBI_CACHE.clear()


def _cached_mixin_key(now: float | None = None) -> str:
    entry = _WBI_CACHE.get("mixin")
    if not entry:
        return ""
    expires_at, key = entry
    current = time.time() if now is None else now
    if expires_at <= current:
        _WBI_CACHE.pop("mixin", None)
        return ""
    return key


def _store_mixin_key(key: str, now: float | None = None) -> None:
    current = time.time() if now is None else now
    _WBI_CACHE["mixin"] = (current + WBI_CACHE_TTL, key)


# ---------------------------------------------------------------------- HTTP
def _send(
    http: requests.Session,
    url: str,
    *,
    params: Mapping[str, Any] | None,
    cookies: Mapping[str, str],
    cancelled: Callable[[], bool] | threading.Event | None,
    strategy: str,
):
    """One GET.  Never retried; transport and status errors are mapped here."""
    _check_cancelled(cancelled)
    try:
        response = http.get(
            url,
            params=dict(params) if params else None,
            headers=dict(BASE_HEADERS),
            cookies=dict(cookies),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.Timeout:
        raise VodStreamError(
            MSG_NETWORK, endpoint=url, strategy=strategy, terminal=True
        ) from None
    except requests.RequestException:
        raise VodStreamError(
            MSG_NETWORK, endpoint=url, strategy=strategy, terminal=True
        ) from None
    _check_cancelled(cancelled)

    status = safe_int(getattr(response, "status_code", 0))
    if status == 412:
        raise VodStreamError(
            MSG_INTERCEPTED,
            code=-412,
            http_status=status,
            endpoint=url,
            strategy=strategy,
            terminal=True,
        )
    if status == 429:
        raise VodStreamError(
            MSG_RATE,
            code=-509,
            http_status=status,
            endpoint=url,
            strategy=strategy,
            terminal=True,
        )
    if not 200 <= status < 300:
        raise VodStreamError(
            f"B 站接口返回 HTTP {status}，请稍后重试",
            http_status=status,
            endpoint=url,
            strategy=strategy,
        )
    return response


def _http_json(
    http: requests.Session,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    cookies: Mapping[str, str],
    cancelled: Callable[[], bool] | threading.Event | None,
    strategy: str,
) -> dict[str, Any]:
    response = _send(
        http,
        url,
        params=params,
        cookies=cookies,
        cancelled=cancelled,
        strategy=strategy,
    )
    try:
        payload = response.json()
    except ValueError:
        raise VodStreamError(
            MSG_BAD_PAYLOAD, endpoint=url, strategy=strategy
        ) from None
    if not isinstance(payload, dict):
        raise VodStreamError(MSG_BAD_PAYLOAD, endpoint=url, strategy=strategy)
    return payload


def _error_for_code(
    code: int,
    payload: Mapping[str, Any],
    *,
    endpoint: str,
    strategy: str,
    http_status: int,
) -> VodStreamError:
    if code == -412:
        return VodStreamError(
            MSG_INTERCEPTED,
            code=code,
            http_status=http_status,
            endpoint=endpoint,
            strategy=strategy,
            terminal=True,
        )
    if code == -352:
        return VodStreamError(
            MSG_RISK,
            code=code,
            http_status=http_status,
            endpoint=endpoint,
            strategy=strategy,
            terminal=True,
        )
    if code == -401:
        return VodStreamError(
            MSG_LOGIN,
            code=code,
            http_status=http_status,
            endpoint=endpoint,
            strategy=strategy,
            terminal=True,
        )
    if code == -403:
        return VodStreamError(
            MSG_PERMISSION,
            code=code,
            http_status=http_status,
            endpoint=endpoint,
            strategy=strategy,
            terminal=True,
        )
    if code == -404:
        return VodStreamError(
            MSG_GONE,
            code=code,
            http_status=http_status,
            endpoint=endpoint,
            strategy=strategy,
            terminal=True,
        )
    detail = _message_from(payload)
    message = f"B 站接口返回错误码 {code}"
    if detail:
        message = f"{message}（{detail}）"
    return VodStreamError(
        message,
        code=code,
        http_status=http_status,
        endpoint=endpoint,
        strategy=strategy,
    )


def _api_json(
    http: requests.Session,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    cookies: Mapping[str, str],
    cancelled: Callable[[], bool] | threading.Event | None,
    strategy: str,
) -> dict[str, Any]:
    """GET + ``code`` check + ``v_voucher`` risk-control check."""
    payload = _http_json(
        http,
        url,
        params=params,
        cookies=cookies,
        cancelled=cancelled,
        strategy=strategy,
    )
    raw_code = payload.get("code")
    code = safe_int(raw_code) if raw_code is not None else 0
    if code != 0:
        raise _error_for_code(
            code, payload, endpoint=url, strategy=strategy, http_status=200
        )
    if _has_voucher(payload):
        raise VodStreamError(
            MSG_RISK,
            code=-352,
            http_status=200,
            endpoint=url,
            strategy=strategy,
            terminal=True,
        )
    return payload


# ------------------------------------------------------------------ payloads
def _accept_quality(data: Mapping[str, Any]) -> tuple[int, ...]:
    """Parse ``data.accept_quality`` into an int tuple, positionally.

    Bad entries become 0 via ``safe_int`` (never raise); a missing or
    non-list field yields ``()``.  Positions are preserved so the tuple
    stays aligned with ``accept_description``.
    """
    raw = data.get("accept_quality")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(safe_int(item) for item in raw)


def _accept_description(data: Mapping[str, Any]) -> tuple[str, ...]:
    """Parse ``data.accept_description`` into a str tuple, positionally.

    Non-string entries become ``""`` (later filled by ``quality_label``);
    a missing or non-list field yields ``()``.
    """
    raw = data.get("accept_description")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item if isinstance(item, str) else "" for item in raw)


def _first_url(entry: Mapping[str, Any]) -> str:
    for key in ("baseUrl", "base_url", "url"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _backup_urls(entry: Mapping[str, Any]) -> tuple[str, ...]:
    raw = entry.get("backup_url")
    if raw is None:
        raw = entry.get("backupUrl")
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple)):
        return ()
    collected: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            collected.append(item.strip())
    return tuple(collected)


def _from_durl(data: Mapping[str, Any], strategy: str, endpoint: str) -> VodStream:
    durl = data.get("durl")
    if not isinstance(durl, list) or not durl:
        raise VodStreamError(MSG_NO_URL, endpoint=endpoint, strategy=strategy)
    first = durl[0]
    if not isinstance(first, dict):
        raise VodStreamError(MSG_NO_URL, endpoint=endpoint, strategy=strategy)
    url = _first_url(first)
    if not url:
        raise VodStreamError(MSG_NO_URL, endpoint=endpoint, strategy=strategy)
    quality = safe_int(data.get("quality"))
    return VodStream(
        url=url,
        backup_urls=_backup_urls(first),
        quality=quality,
        strategy=strategy,
        headers=dict(STREAM_HEADERS),
        accept_quality=_accept_quality(data),
        accept_description=_accept_description(data),
        qn=quality,
    )


def _pick_dash_video(
    videos: list, qn: int, *, best: bool
) -> Mapping[str, Any] | None:
    """Choose one DASH video entry.

    ``best`` enables HQ selection, not an unbounded maximum: ``qn<=0`` means
    1080P.  Prefer an exact positive id, then the highest id below it, then
    the lowest available if every tier exceeds the request.  Within that
    tier prefer AVC, then HEVC, then AV1 (local hwdec support may vary).
    Legacy (``best=False``): exact id match else first usable entry.
    """
    usable = [
        entry for entry in videos
        if isinstance(entry, dict) and _first_url(entry)
        and (not best or safe_int(entry.get("id")) > 0)
    ]
    if not usable:
        return None
    if not best:
        if qn > 0:
            for entry in usable:
                if safe_int(entry.get("id")) == qn:
                    return entry
        return usable[0]

    def tier(entry) -> int:
        return safe_int(entry.get("id"))

    requested = qn if qn > 0 else HQ_DEFAULT_QN
    below = [tier(entry) for entry in usable if tier(entry) <= requested]
    target = max(below) if below else min(tier(entry) for entry in usable)
    candidates = [entry for entry in usable if tier(entry) == target]

    def codec_rank(entry) -> int:
        codecid = safe_int(entry.get("codecid"))
        codecs = str(entry.get("codecs") or "").lower()
        if codecid == 7 or codecs.startswith(("avc1", "avc3")):
            return 0  # AVC: widest hwdec support
        if codecid == 12 or codecs.startswith(("hev1", "hvc1")):
            return 1
        if codecid == 13 or codecs.startswith("av01"):
            return 2
        return 3

    return min(candidates, key=codec_rank)


def _dash_accept_qualities(
    data: Mapping[str, Any], videos: list,
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """HQ menu: only ids with URLs, preserving API order/description pairing.

    Advertised tiers need not have been granted to this account.  Conversely,
    playable ids omitted from accept_quality are appended with fallback labels.
    """
    playable = {
        safe_int(entry.get("id")) for entry in videos
        if isinstance(entry, dict) and _first_url(entry)
        and safe_int(entry.get("id")) > 0
    }
    descriptions = _accept_description(data)
    qualities: list[int] = []
    labels: list[str] = []
    for index, qn in enumerate(_accept_quality(data)):
        if qn not in playable:
            continue
        playable.remove(qn)
        qualities.append(qn)
        label = descriptions[index] if index < len(descriptions) else ""
        labels.append(label if label.strip() else quality_label(qn))
    for qn in sorted(playable, reverse=True):
        qualities.append(qn)
        labels.append(quality_label(qn))
    return tuple(qualities), tuple(labels)


def _pick_dash_audio(audios: list) -> str:
    """Select one URL from dash.audio, independently of video qn.

    Prefer ordinary audio over Dolby (30250)/FLAC (30251).  Highest positive
    bandwidth wins within the pool; missing/invalid bandwidth ranks as zero,
    with id as a tie-breaker (also the fallback when all bandwidths are absent).
    Nested dash.dolby/dash.flac tracks are not auto-selected.
    """
    usable = [
        entry for entry in audios
        if isinstance(entry, dict) and _first_url(entry)
    ]
    if not usable:
        return ""
    plain = [
        entry for entry in usable
        if safe_int(entry.get("id")) not in (30250, 30251)  # dolby / flac
    ]
    pool = plain or usable
    best_entry = max(
        pool,
        key=lambda e: (max(0, safe_int(e.get("bandwidth"))), safe_int(e.get("id"))),
    )
    return _first_url(best_entry)


def _from_dash(
    data: Mapping[str, Any], strategy: str, endpoint: str, *, qn: int = 0,
    best: bool = False,
) -> VodStream:
    """Build one video/audio pair solely from API-provided URLs.

    Legacy selection prefers an exact qn, otherwise the first usable video.
    HQ (``best=True``) uses a 1080P default, bounded tier/codec preference and
    a URL-backed quality menu; see _pick_dash_video.  HQ qn/quality is the
    chosen positive video id, never the requested qn; legacy id-less entries
    retain the API quality fallback.  Audio is independent of video qn and
    follows _pick_dash_audio, not list order.
    """
    dash = data.get("dash")
    if not isinstance(dash, dict):
        raise VodStreamError(MSG_NO_URL, endpoint=endpoint, strategy=strategy)

    chosen: Mapping[str, Any] | None = None
    videos = dash.get("video")
    if isinstance(videos, list):
        chosen = _pick_dash_video(videos, qn, best=best)
    if chosen is None:
        raise VodStreamError(MSG_NO_URL, endpoint=endpoint, strategy=strategy)
    video_url = _first_url(chosen)
    backup = _backup_urls(chosen)
    quality = safe_int(chosen.get("id"))

    audio_url = ""
    audios = dash.get("audio")
    if isinstance(audios, list):
        audio_url = _pick_dash_audio(audios)

    if quality <= 0:  # Legacy payloads may omit the video id; HQ rejects these.
        quality = safe_int(dash.get("quality")) or safe_int(data.get("quality"))
    accept, descriptions = (
        _dash_accept_qualities(data, videos) if best
        else (_accept_quality(data), _accept_description(data))
    )
    return VodStream(
        url=video_url,
        backup_urls=backup,
        quality=quality,
        strategy=strategy,
        is_dash=True,
        audio_url=audio_url,
        headers=dict(STREAM_HEADERS),
        accept_quality=accept,
        accept_description=descriptions,
        qn=quality,
    )


# ---------------------------------------------------------------- strategies
def _resolve_cid(
    http: requests.Session,
    bvid: str,
    cookies: Mapping[str, str],
    cancelled: Callable[[], bool] | threading.Event | None,
    strategy: str,
    page: int = 0,
) -> int:
    payload = _api_json(
        http,
        PAGELIST_ENDPOINT,
        params={"bvid": bvid},
        cookies=cookies,
        cancelled=cancelled,
        strategy=strategy,
    )
    data = payload.get("data")
    if isinstance(data, list) and data:
        # v1.0 需求：：多 P 视频要能指定第几 P（0 基）；越界时退回第一 P。
        index = page if 0 <= int(page) < len(data) else 0
        entry = data[index]
        if isinstance(entry, dict):
            cid = safe_int(entry.get("cid"))
            if cid > 0:
                return cid
    raise VodStreamError(
        MSG_NO_CID, endpoint=PAGELIST_ENDPOINT, strategy=strategy
    )


def _fetch_mixin_key(
    http: requests.Session,
    cookies: Mapping[str, str],
    cancelled: Callable[[], bool] | threading.Event | None,
    strategy: str,
) -> str:
    cached = _cached_mixin_key()
    if cached:
        return cached
    # ``nav`` answers code -101 when logged out but still carries wbi_img, so
    # this request deliberately does not go through the ``code`` check.
    payload = _http_json(
        http,
        NAV_ENDPOINT,
        cookies=cookies,
        cancelled=cancelled,
        strategy=strategy,
    )
    data = payload.get("data")
    wbi = data.get("wbi_img") if isinstance(data, dict) else None
    if not isinstance(wbi, dict):
        raise VodStreamError(MSG_NO_KEY, endpoint=NAV_ENDPOINT, strategy=strategy)
    img_url = wbi.get("img_url")
    sub_url = wbi.get("sub_url")
    if not isinstance(img_url, str) or not isinstance(sub_url, str):
        raise VodStreamError(MSG_NO_KEY, endpoint=NAV_ENDPOINT, strategy=strategy)
    key = wbi_mixin_key(img_url, sub_url)
    if not key:
        raise VodStreamError(MSG_NO_KEY, endpoint=NAV_ENDPOINT, strategy=strategy)
    _store_mixin_key(key)
    return key


def _strategy_durl(
    http: requests.Session,
    bvid: str,
    cid: int,
    cookies: Mapping[str, str],
    cancelled: Callable[[], bool] | threading.Event | None,
    qn: int = 0,
) -> VodStream:
    params: dict[str, Any] = {"bvid": bvid, "fnval": 0, "fnver": 0, "fourk": 0}
    if cid > 0:
        params["cid"] = cid
    if qn > 0:
        params["qn"] = qn
    payload = _api_json(
        http,
        PLAYURL_ENDPOINT,
        params=params,
        cookies=cookies,
        cancelled=cancelled,
        strategy=STRATEGY_DURL,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise VodStreamError(
            MSG_NO_URL, endpoint=PLAYURL_ENDPOINT, strategy=STRATEGY_DURL
        )
    return _from_durl(data, STRATEGY_DURL, PLAYURL_ENDPOINT)


def _strategy_dash(
    http: requests.Session,
    bvid: str,
    cid: int,
    cookies: Mapping[str, str],
    cancelled: Callable[[], bool] | threading.Event | None,
    qn: int = 0,
    hq: bool = False,
    page: int = 0,
) -> VodStream:
    if cid <= 0:
        cid = _resolve_cid(http, bvid, cookies, cancelled, STRATEGY_DASH,
                           page=page)
    key = _fetch_mixin_key(http, cookies, cancelled, STRATEGY_DASH)
    if hq and qn <= 0:
        qn = HQ_DEFAULT_QN
    unsigned: dict[str, Any] = {
        "bvid": bvid, "cid": cid, "fnval": HQ_DASH_FNVAL if hq else 16,
        "fnver": 0,
        # Include higher-tier candidates for the quality menu; selection is
        # still bounded by qn and account permissions, not by this flag.
        "fourk": 1 if hq else 0,
    }
    if qn > 0:
        unsigned["qn"] = qn
    signed = wbi_signed_params(unsigned, key)
    payload = _api_json(
        http,
        WBI_PLAYURL_ENDPOINT,
        params=signed,
        cookies=cookies,
        cancelled=cancelled,
        strategy=STRATEGY_DASH,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise VodStreamError(
            MSG_NO_URL, endpoint=WBI_PLAYURL_ENDPOINT, strategy=STRATEGY_DASH
        )
    stream = _from_dash(
        data, STRATEGY_DASH, WBI_PLAYURL_ENDPOINT, qn=qn, best=hq
    )
    stream.cid = cid
    return stream


def _strategy_playinfo(
    http: requests.Session,
    bvid: str,
    cookies: Mapping[str, str],
    cancelled: Callable[[], bool] | threading.Event | None,
    *,
    qn: int = 0,
    hq: bool = False,
    page: int = 0,
) -> VodStream:
    url = f"{WEB_HOST}/video/{bvid}"
    response = _send(
        http,
        url,
        params={"p": page + 1} if page > 0 else None,
        cookies=cookies,
        cancelled=cancelled,
        strategy=STRATEGY_PLAYINFO,
    )
    text = getattr(response, "text", "")
    if not isinstance(text, str):
        text = ""
    match = _PLAYINFO_RE.search(text)
    if not match:
        raise VodStreamError(MSG_NO_URL, endpoint=url, strategy=STRATEGY_PLAYINFO)
    try:
        payload = json.loads(match.group(1).strip().rstrip(";").strip())
    except ValueError:
        raise VodStreamError(
            MSG_NO_URL, endpoint=url, strategy=STRATEGY_PLAYINFO
        ) from None
    if not isinstance(payload, dict):
        raise VodStreamError(MSG_NO_URL, endpoint=url, strategy=STRATEGY_PLAYINFO)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise VodStreamError(MSG_NO_URL, endpoint=url, strategy=STRATEGY_PLAYINFO)
    if _has_voucher(payload):
        raise VodStreamError(
            MSG_RISK, code=-352, endpoint=url, strategy=STRATEGY_PLAYINFO,
            terminal=True,
        )
    if isinstance(data.get("durl"), list):
        return _from_durl(data, STRATEGY_PLAYINFO, url)
    if isinstance(data.get("dash"), dict):
        return _from_dash(
            data, STRATEGY_PLAYINFO, url, qn=qn if hq else 0, best=hq
        )
    raise VodStreamError(MSG_NO_URL, endpoint=url, strategy=STRATEGY_PLAYINFO)


def _combined_message(errors: list[VodStreamError]) -> str:
    tried = "、".join(_STRATEGY_LABELS[name] for name in STRATEGY_ORDER)
    message = f"无法获取播放地址：已尝试 {tried}，均未成功"
    if errors:
        message = f"{message}（最后一次失败：{errors[-1].message}）"
    return message + "，请用浏览器打开该视频"


# ------------------------------------------------------------------ public
def resolve_vod(
    bvid: str,
    *,
    cid: int = 0,
    cookies: Mapping[str, Any] | None = None,
    session: requests.Session | None = None,
    cancelled: Callable[[], bool] | threading.Event | None = None,
    qn: int = 0,
    hq: bool = False,
    page: int = 0,
) -> VodStream:
    """Resolve a playable direct URL for one Bilibili VOD.

    Legacy order is ``playurl-durl`` -> ``wbi-dash`` -> ``playinfo``; each
    strategy is attempted at most once.  Terminal failures (412/429, login,
    permission, missing video, network) abort immediately.

    ``hq=True`` defaults an unspecified/nonpositive ``qn`` to 80 (1080P),
    not the highest tier.  Explicit positive qn values from the quality menu
    are honored when offered.  HQ DASH chooses that tier or the nearest lower
    one (lowest available if all are higher), preferring AVC within a tier.
    This also applies to DASH in the playinfo fallback; durl reports the tier
    actually returned.  ``hq=False`` keeps legacy selection/accept lists.

    HQ with a nonempty ``SESSDATA`` snapshot tries WBI DASH first; cookie
    presence does NOT establish session validity.  fnval=4048/fourk=1 request
    candidate formats for the menu, never permission or maximum playback.
    Only API-provided URLs are selected, with no login/VIP-limit bypass.
    Without that cookie the legacy durl-first order remains.

    The result's ``cid`` carries the supplied/resolved part id, even after a
    DASH failure and fallback; unknown stays 0 without an extra legacy lookup.
    """
    clean = valid_bvid(bvid)
    if not clean:
        raise VodStreamError(MSG_BAD_BVID, terminal=True)

    part = max(0, safe_int(cid))
    tier = max(0, safe_int(qn))
    if hq and tier == 0:
        tier = HQ_DEFAULT_QN
    jar = _cookie_dict(cookies)
    order = STRATEGY_ORDER
    if hq and jar.get("SESSDATA"):
        order = (STRATEGY_DASH, STRATEGY_DURL, STRATEGY_PLAYINFO)
    owns_session = session is None
    http = requests.Session() if owns_session else session
    try:
        if part <= 0 and page > 0:
            part = _resolve_cid(http, clean, jar, cancelled, STRATEGY_DASH, page=page)
        errors: list[VodStreamError] = []
        for strategy in order:
            _check_cancelled(cancelled)
            try:
                if strategy == STRATEGY_DURL:
                    stream = _strategy_durl(http, clean, part, jar, cancelled, tier)
                elif strategy == STRATEGY_DASH:
                    if part <= 0:
                        # Keep the resolved cid even if DASH later fails, so
                        # fallbacks and subtitle/preview callers can reuse it.
                        part = _resolve_cid(http, clean, jar, cancelled, strategy, page=page)
                    stream = _strategy_dash(
                        http, clean, part, jar, cancelled, tier, hq=hq
                    )
                else:
                    stream = _strategy_playinfo(
                        http, clean, jar, cancelled, qn=tier, hq=hq, page=page
                    )
                stream.cid = part
                return stream
            except VodStreamError as error:
                if error.terminal:
                    raise
                errors.append(error)
        raise VodStreamError(_combined_message(errors))
    finally:
        if owns_session:
            try:
                http.close()
            except Exception:
                pass
