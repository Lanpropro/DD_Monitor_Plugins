"""Bilibili watch-later HTTP layer.

No Qt imports.  Every caller owns its ``requests.Session``; this module never
logs cookies, never retries POST requests and never contacts any host other
than api.bilibili.com.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests

from .models import WatchLaterItem, WatchLaterSnapshot, safe_int, valid_bvid

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
}
POST_HEADERS = {
    **BASE_HEADERS,
    "Origin": "https://www.bilibili.com/",
}

TIMEOUT = (5, 8)

LIST_ENDPOINT = "https://api.bilibili.com/x/v2/history/toview"
LIST_FALLBACK_ENDPOINT = "https://api.bilibili.com/x/v2/history/toview/web"
REMOVE_ENDPOINT = "https://api.bilibili.com/x/v2/history/toview/del"
ADD_ENDPOINT = "https://api.bilibili.com/x/v2/history/toview/add"
CLEAR_ENDPOINT = "https://api.bilibili.com/x/v2/history/toview/clear"
VIEW_ENDPOINT = "https://api.bilibili.com/x/web-interface/view"

PAGE_SIZE = 30
MAX_PAGES = 10
MAX_ITEMS = 1000


class BiliCancelled(Exception):
    """Raised internally when a worker was cancelled between requests."""


class BiliAPIError(Exception):
    """A user-readable Bilibili API failure with safe diagnostic fields."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        http_status: int | None = None,
        endpoint: str = "",
        format_error: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.endpoint = endpoint
        self.format_error = format_error

    @property
    def login_required(self) -> bool:
        return self.code in (-101, -111)

    @property
    def intercepted(self) -> bool:
        return self.code == -412 or self.http_status == 412

    @property
    def rate_limited(self) -> bool:
        return self.code == -509 or self.http_status == 429


def _message_from(payload: Mapping[str, Any]) -> str:
    value = payload.get("message")
    if value is None:
        value = payload.get("msg")
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text in ("", "0") else text


def describe_error(error: BaseException) -> str:
    """Map transport and API errors to the wording required by the UI spec."""
    if isinstance(error, BiliCancelled):
        return "请求已取消"
    if isinstance(error, BiliAPIError):
        if error.code == -101:
            return "登录已失效，请重新登录 B 站"
        if error.code == -111:
            return "登录信息不完整或已失效，请重新登录后再试"
        if error.intercepted:
            return (
                "请求被 B 站拦截（412），请稍后重试；"
                "如持续出现，请先在浏览器打开一次 B 站"
            )
        if error.rate_limited:
            return "请求过于频繁，请稍后再试"
        if error.format_error:
            return "数据格式异常，请更新插件或反馈"
        if error.message and error.message not in ("0", "0 "):
            return error.message
        if error.http_status:
            return f"B 站接口返回 HTTP {error.http_status}，请稍后重试"
        return "B 站接口返回异常，请稍后重试"
    if isinstance(error, requests.Timeout):
        return "请求超时，请检查网络后重试"
    if isinstance(error, requests.RequestException):
        return "网络连接失败，请检查网络/代理后重试"
    if isinstance(error, ValueError):
        return "数据格式异常，请更新插件或反馈"
    text = str(error).strip()
    return text if text else "请求失败，请重试"


def _check_cancelled(cancelled: Callable[[], bool] | threading.Event | None) -> None:
    if cancelled is None:
        return
    is_set = getattr(cancelled, "is_set", None)
    stopped = is_set() if callable(is_set) else cancelled()
    if stopped:
        raise BiliCancelled()


def _copy_cookies(cookies: Mapping[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(cookies, Mapping):
        return result
    for key, value in cookies.items():
        if value is None:
            continue
        result[str(key)] = str(value)
    return result


class BiliWatchLaterAPI:
    """Small, testable wrapper around the public watch-later endpoints."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session
        self._owns_session = False

    def close(self) -> None:
        if self._session is not None and self._owns_session:
            try:
                self._session.close()
            finally:
                self._session = None
                self._owns_session = False

    def _http(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._owns_session = True
        return self._session

    # ------------------------------------------------------------------ GET
    def _get_json(
        self,
        url: str,
        *,
        cookies: Mapping[str, str],
        params: Mapping[str, Any] | None = None,
        cancelled: Callable[[], bool] | threading.Event | None = None,
    ) -> dict[str, Any]:
        _check_cancelled(cancelled)
        response = self._http().get(
            url,
            params=dict(params) if params else None,
            headers=dict(BASE_HEADERS),
            cookies=dict(cookies),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        status = safe_int(getattr(response, "status_code", 0))
        if status == 412:
            raise BiliAPIError(
                "请求被 B 站拦截（412）",
                code=-412,
                http_status=status,
                endpoint=url,
            )
        if status == 429:
            raise BiliAPIError(
                "请求过于频繁",
                code=-509,
                http_status=status,
                endpoint=url,
            )
        if not 200 <= status < 300:
            raise BiliAPIError(
                f"B 站接口返回 HTTP {status}",
                http_status=status,
                endpoint=url,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=url,
                format_error=True,
            ) from error
        _check_cancelled(cancelled)
        if not isinstance(payload, dict):
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=url,
                format_error=True,
            )
        raw_code = payload.get("code")
        if raw_code is None:
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=url,
                format_error=True,
            )
        code = safe_int(raw_code)
        if code != 0:
            raise BiliAPIError(
                _message_from(payload),
                code=code,
                http_status=status,
                endpoint=url,
            )
        return payload

    def _parse_items(
        self,
        payload: Mapping[str, Any],
        *,
        endpoint: str,
        http_status: int,
    ) -> WatchLaterSnapshot:
        data = payload.get("data")
        if data is None:
            return WatchLaterSnapshot(
                items=[], total=0, endpoint=endpoint, http_status=http_status
            )
        if isinstance(data, list):
            raw_items: Any = data
            total = len(data)
        elif isinstance(data, dict):
            raw_items = data.get("list")
            if raw_items is None:
                raw_items = []
            if not isinstance(raw_items, list):
                raise BiliAPIError(
                    "数据格式异常",
                    http_status=http_status,
                    endpoint=endpoint,
                    format_error=True,
                )
            total = max(0, safe_int(data.get("count")))
        else:
            raise BiliAPIError(
                "数据格式异常",
                http_status=http_status,
                endpoint=endpoint,
                format_error=True,
            )

        items: list[WatchLaterItem] = []
        seen: set[str] = set()
        for raw in raw_items:
            item = WatchLaterItem.from_api(raw)
            if item is None:
                continue
            key = item.key
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)
        return WatchLaterSnapshot(
            items=items,
            total=max(total, len(items)),
            endpoint=endpoint,
            http_status=http_status,
        )

    @staticmethod
    def _can_fallback(error: BiliAPIError) -> bool:
        if error.code in (-352, -401, -403) or error.http_status in (401, 403):
            return False
        if error.login_required or error.intercepted or error.rate_limited:
            return False
        return True

    def _list_pages(
        self,
        cookies: Mapping[str, str],
        *,
        cancelled: Callable[[], bool] | threading.Event | None,
    ) -> WatchLaterSnapshot:
        merged: list[WatchLaterItem] = []
        seen: set[str] = set()
        total = 0
        last_status = 0
        for page in range(1, MAX_PAGES + 1):
            _check_cancelled(cancelled)
            payload = self._get_json(
                LIST_FALLBACK_ENDPOINT,
                cookies=cookies,
                params={
                    "pn": page,
                    "ps": PAGE_SIZE,
                    "viewed": 0,
                    "key": "",
                    "asc": "false",
                },
                cancelled=cancelled,
            )
            last_status = 200
            data = payload.get("data")
            if data is None:
                break
            if not isinstance(data, dict):
                raise BiliAPIError(
                    "数据格式异常",
                    http_status=last_status,
                    endpoint=LIST_FALLBACK_ENDPOINT,
                    format_error=True,
                )
            raw_items = data.get("list")
            if raw_items is None:
                raw_items = []
            if not isinstance(raw_items, list):
                raise BiliAPIError(
                    "数据格式异常",
                    http_status=last_status,
                    endpoint=LIST_FALLBACK_ENDPOINT,
                    format_error=True,
                )
            total = max(total, safe_int(data.get("count")), len(merged))
            if not raw_items:
                break
            for raw in raw_items:
                item = WatchLaterItem.from_api(raw)
                if item is None:
                    continue
                key = item.key
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                if len(merged) >= MAX_ITEMS:
                    break
            total = max(total, len(merged))
            if len(merged) >= total or len(merged) >= MAX_ITEMS:
                break
        return WatchLaterSnapshot(
            items=merged,
            total=max(total, len(merged)),
            endpoint=LIST_FALLBACK_ENDPOINT,
            http_status=last_status,
        )

    def list_all(
        self,
        cookies: Mapping[str, Any] | None,
        *,
        cancelled: Callable[[], bool] | threading.Event | None = None,
    ) -> WatchLaterSnapshot:
        """Read the complete list; fall back only for non-login API errors."""
        snapshot_cookies = _copy_cookies(cookies)
        if not snapshot_cookies.get("SESSDATA"):
            raise BiliAPIError("请先登录 B 站", code=-101)
        _check_cancelled(cancelled)

        try:
            payload = self._get_json(
                LIST_ENDPOINT,
                cookies=snapshot_cookies,
                cancelled=cancelled,
            )
        except BiliAPIError as error:
            if not self._can_fallback(error):
                raise
            return self._list_pages(snapshot_cookies, cancelled=cancelled)

        try:
            return self._parse_items(
                payload, endpoint=LIST_ENDPOINT, http_status=200
            )
        except BiliAPIError as error:
            if not self._can_fallback(error):
                raise
            return self._list_pages(snapshot_cookies, cancelled=cancelled)

    # ----------------------------------------------------------------- POST
    def _post_json(
        self,
        url: str,
        *,
        cookies: Mapping[str, str],
        data: Mapping[str, Any],
        cancelled: Callable[[], bool] | threading.Event | None = None,
    ) -> dict[str, Any]:
        _check_cancelled(cancelled)
        response = self._http().post(
            url,
            data=dict(data),
            headers=dict(POST_HEADERS),
            cookies=dict(cookies),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        status = safe_int(getattr(response, "status_code", 0))
        if status == 412:
            raise BiliAPIError(
                "请求被 B 站拦截（412）",
                code=-412,
                http_status=status,
                endpoint=url,
            )
        if status == 429:
            raise BiliAPIError(
                "请求过于频繁",
                code=-509,
                http_status=status,
                endpoint=url,
            )
        if not 200 <= status < 300:
            raise BiliAPIError(
                f"B 站接口返回 HTTP {status}",
                http_status=status,
                endpoint=url,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=url,
                format_error=True,
            ) from error
        if not isinstance(payload, dict):
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=url,
                format_error=True,
            )
        raw_code = payload.get("code")
        if raw_code is None:
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=url,
                format_error=True,
            )
        code = safe_int(raw_code)
        if code != 0:
            raise BiliAPIError(
                _message_from(payload),
                code=code,
                http_status=status,
                endpoint=url,
            )
        return payload

    @staticmethod
    def _write_cookies(cookies: Mapping[str, Any] | None) -> dict[str, str]:
        snapshot = _copy_cookies(cookies)
        if not snapshot.get("SESSDATA"):
            raise BiliAPIError("请先登录 B 站", code=-101)
        if not snapshot.get("bili_jct"):
            raise BiliAPIError(
                "登录信息不完整，请重新登录后再试", code=-111
            )
        return snapshot

    def remove(
        self,
        cookies: Mapping[str, Any] | None,
        aid: int,
        *,
        cancelled: Callable[[], bool] | threading.Event | None = None,
    ) -> dict[str, Any]:
        """Remove one entry.  POST is issued exactly once."""
        snapshot = self._write_cookies(cookies)
        identifier = safe_int(aid)
        if identifier <= 0:
            raise ValueError("该条目缺少 aid，请到网页版处理")
        csrf = snapshot["bili_jct"]
        return self._post_json(
            REMOVE_ENDPOINT,
            cookies=snapshot,
            data={"aid": str(identifier), "csrf": csrf},
            cancelled=cancelled,
        )

    def add(self, cookies, aid, *, cancelled=None):
        """Re-add an explicitly undone removal; issue one POST without retries."""
        snapshot = self._write_cookies(cookies)
        identifier = safe_int(aid)
        if identifier <= 0:
            raise ValueError("该条目缺少 aid，无法撤回")
        return self._post_json(ADD_ENDPOINT, cookies=snapshot,
            data={"aid": str(identifier), "csrf": snapshot["bili_jct"]}, cancelled=cancelled)

    def clear(
        self,
        cookies: Mapping[str, Any] | None,
        *,
        cancelled: Callable[[], bool] | threading.Event | None = None,
    ) -> dict[str, Any]:
        """Clear the whole list.  POST is issued exactly once."""
        snapshot = self._write_cookies(cookies)
        csrf = snapshot["bili_jct"]
        return self._post_json(
            CLEAR_ENDPOINT,
            cookies=snapshot,
            data={"csrf": csrf},
            cancelled=cancelled,
        )


@dataclass(frozen=True, slots=True)
class VideoStat:
    """One public view request supplies counts and normalized display metadata."""

    danmaku: int = 0
    #: 播放量；接口没给 stat.view 时为 None（界面据此不显示）。
    view: int | None = None
    item: WatchLaterItem | None = None


def video_stat(
    bvid: str,
    *,
    timeout: float = 8.0,
    session: requests.Session | None = None,
    with_view: bool = False,
) -> int | VideoStat:
    """v1.0 需求：：读取单个视频的弹幕数（data.stat.danmaku）。

    公开 view 接口，无需 Cookie；绝不重试，412/429/code≠0/超时统一封装为
    BiliAPIError（调用方静默丢弃）。每次调用用独立 session，不共享账号会话。

    v1.0：``with_view=True`` 时返回 :class:`VideoStat`（同一次请求里顺带取
    ``stat.view`` 播放量），默认仍返回 ``int`` 弹幕数以保持旧契约。
    """
    identifier = valid_bvid(bvid)
    if not identifier:
        raise BiliAPIError(
            "无效的 bvid", endpoint=VIEW_ENDPOINT, format_error=True
        )
    owns_session = session is None
    http = requests.Session() if owns_session else session
    try:
        try:
            response = http.get(
                VIEW_ENDPOINT,
                params={"bvid": identifier},
                headers=dict(BASE_HEADERS),
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.Timeout as error:
            raise BiliAPIError(
                "请求超时", endpoint=VIEW_ENDPOINT
            ) from error
        except requests.RequestException as error:
            raise BiliAPIError(
                "网络连接失败", endpoint=VIEW_ENDPOINT
            ) from error

        status = safe_int(getattr(response, "status_code", 0))
        if status == 412:
            raise BiliAPIError(
                "请求被 B 站拦截（412）",
                code=-412,
                http_status=status,
                endpoint=VIEW_ENDPOINT,
            )
        if status == 429:
            raise BiliAPIError(
                "请求过于频繁",
                code=-509,
                http_status=status,
                endpoint=VIEW_ENDPOINT,
            )
        if not 200 <= status < 300:
            raise BiliAPIError(
                f"B 站接口返回 HTTP {status}",
                http_status=status,
                endpoint=VIEW_ENDPOINT,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=VIEW_ENDPOINT,
                format_error=True,
            ) from error
        if not isinstance(payload, dict):
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=VIEW_ENDPOINT,
                format_error=True,
            )
        raw_code = payload.get("code")
        if raw_code is None:
            raise BiliAPIError(
                "数据格式异常",
                http_status=status,
                endpoint=VIEW_ENDPOINT,
                format_error=True,
            )
        code = safe_int(raw_code)
        if code != 0:
            raise BiliAPIError(
                _message_from(payload),
                code=code,
                http_status=status,
                endpoint=VIEW_ENDPOINT,
            )
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        stat = data.get("stat")
        stat = stat if isinstance(stat, dict) else {}
        danmaku = max(0, safe_int(stat.get("danmaku")))
        if not with_view:
            return danmaku
        raw_view = stat.get("view")
        view = max(0, safe_int(raw_view)) if raw_view is not None else None
        return VideoStat(danmaku=danmaku, view=view, item=WatchLaterItem.from_api(data))
    finally:
        if owns_session:
            try:
                http.close()
            except Exception:
                pass
