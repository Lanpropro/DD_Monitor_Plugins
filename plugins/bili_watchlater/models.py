"""Data model and pure helpers for the Bilibili watch-later plugin.

This module deliberately has no Qt dependency so it can be tested offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
import time
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

_SPACE_RE = re.compile(r"\s+", re.UNICODE)
_BVID_RE = re.compile(r"BV[0-9A-Za-z]{6,20}")

FILTER_ALL = "all"
FILTER_UNFINISHED = "unfinished"
FILTER_FINISHED = "finished"

SORT_RECENT = "recent"
SORT_OLDEST = "oldest"
SORT_DURATION_DESC = "duration_desc"
SORT_DURATION_ASC = "duration_asc"
SORT_OWNER = "owner"

FILTER_OPTIONS: tuple[tuple[str, str], ...] = (
    (FILTER_ALL, "全部"),
    (FILTER_UNFINISHED, "未看完"),
    (FILTER_FINISHED, "已看完"),
)

SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    (SORT_RECENT, "最近添加"),
    (SORT_OLDEST, "最早添加"),
    (SORT_DURATION_DESC, "时长最长"),
    (SORT_DURATION_ASC, "时长最短"),
    (SORT_OWNER, "UP 主名称"),
)

#: v1.0 弹窗列表 → 画面墙格子的拖拽格式（payload = bvid）。
#: 与宿主 ROOM_MIME / TILE_MIME / DANMAKU_MIME 互不干扰。
VOD_MIME = "application/x-bili-watchlater-video"


def safe_int(value: Any) -> int:
    """Convert API values to int without ever raising on bad data."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else 0
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(text, 10)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                return 0
            return int(number) if math.isfinite(number) else 0
    return 0


def clean_text(value: Any, fallback: str = "") -> str:
    """Normalize whitespace and always return a displayable plain string."""
    if not isinstance(value, str):
        return fallback
    text = _SPACE_RE.sub(" ", value).strip()
    return text or fallback


def valid_bvid(value: Any) -> str:
    """Only accept a conservative BV id; never allow path separators."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if _BVID_RE.fullmatch(text) else ""


def normalize_cover_url(value: Any) -> str:
    """Accept http/https or protocol-relative covers, drop everything else."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text.startswith("//"):
        text = "https:" + text
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return ""


# Row covers are drawn at 168x94 logical px (336x188 at 2x DPR), but the API
# hands us the original image - measured on a real sample: 2560x1440 JPEG,
# 155 KB, ~21 ms to decode.  The CDN can return an already cropped/resized
# copy instead: 336x188, ~7 KB, ~0.4 ms to decode (~50x less CPU, ~22x less
# traffic).  Keep this a single constant so quality is one-line adjustable:
# use "@480w_270h_1c.jpg" for a sharper look, "@224w_126h_1c.webp" for the
# smallest transfer, or "" to restore full-resolution originals.
# v1.0 任务 2：列表封面放大到 224x126（整数 16:9），缓存上限 2x = 448x252，
# 所以 CDN 也按 448x252 取，避免 448 宽的缓存位图由 336 宽放大而来（发虚）。
# 只影响传输体积，解码仍在后台线程按 QImageReader.setScaledSize 缩到缓存尺寸。
COVER_THUMB_SUFFIX = "@448w_252h_1c.jpg"
_BILI_IMAGE_HOSTS = ("hdslb.com", "biliimg.com", "bilicdn1.com", "bilibili.com")


def cover_thumbnail_url(value: Any) -> str:
    """Normalize a cover URL and downgrade it to a display-size CDN thumbnail.

    Idempotent and host-guarded: URLs that already carry an ``@`` processing
    suffix, non-image paths, and hosts outside the Bili image CDN are returned
    untouched.  The suffix is appended to the *path*, before any query string.
    """
    url = normalize_cover_url(value)
    if not url or not COVER_THUMB_SUFFIX or "@" in url:
        return url
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        path = parts.path
    except ValueError:
        return url
    if not any(host == h or host.endswith("." + h) for h in _BILI_IMAGE_HOSTS):
        return url
    if not path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        return url
    return urlunsplit(parts._replace(path=path + COVER_THUMB_SUFFIX))


def format_duration(seconds: int) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    total = max(0, safe_int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_count(value: Any) -> str:
    """v1.0 需求：：弹幕数的中文缩写（9999 / 1.0万 / 1.2亿）。"""
    total = max(0, safe_int(value))
    if total >= 100_000_000:
        return f"{total / 100_000_000:.1f}亿"
    if total >= 10_000:
        return f"{total / 10_000:.1f}万"
    return str(total)


def format_views(value: Any) -> str:
    """v1.0：播放量的中文缩写（``12.3万播放``）；未知（None）返回空串。

    ``toview`` 列表接口不返回播放量，值来自一次 ``video_stat`` 懒加载；
    未知时不显示任何文字（与弹幕数同一套规则）。
    """
    if value is None:
        return ""
    return format_count(value) + "播放"


def list_position(items: Any, bvid: str) -> tuple[int, int] | None:
    """当前视频在稍后再看列表里的位置：``(1-based 序号, 总数)``。

    v1.0 任务 3/8 共用（格子左上角位置徽章 + 全屏徽章）——**只有这一处
    实现**：序号必须和弹窗里看到的顺序一致，两处各写一份迟早会算岔。
    未命中（bvid 为空 / 列表为空 / 列表里没有它）返回 ``None``。
    """
    key = str(bvid or "").strip()
    if not key:
        return None
    try:
        entries = list(items or ())
    except TypeError:
        return None
    total = len(entries)
    if total <= 0:
        return None
    for index, item in enumerate(entries, 1):
        if str(getattr(item, "bvid", "") or "") == key:
            return index, total
    return None


def format_position(position: Any) -> str:
    """``(序号, 总数)`` → ``"5/642"``；无效输入返回空串（徽章据此隐藏）。"""
    if not position:
        return ""
    try:
        index, total = position
        index = int(index)
        total = int(total)
    except (TypeError, ValueError):
        return ""
    if index <= 0 or total <= 0:
        return ""
    return f"{index}/{total}"


def format_added(timestamp: int, now: float | None = None) -> str:
    """Return a short relative time, or an empty string when unknown."""
    value = safe_int(timestamp)
    if value <= 0:
        return ""
    current = time.time() if now is None else float(now)
    delta = current - value
    if delta < 0:
        return time.strftime("%Y-%m-%d", time.localtime(value))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)} 天前"
    local = time.localtime(value)
    if local.tm_year == time.localtime(current).tm_year:
        return time.strftime("%m-%d", local)
    return time.strftime("%Y-%m-%d", local)


@dataclass(slots=True)
class WatchLaterItem:
    """One watch-later entry, already normalized for display."""

    aid: int = 0
    bvid: str = ""
    title: str = "无标题"
    cover_url: str = ""
    duration: int = 0
    progress: int = 0
    add_at: int = 0
    owner_mid: int = 0
    owner_name: str = "未知 UP 主"
    #: v1.0 需求：/17：UP 主头像（画中画/头像位需要 room["face"]）。
    owner_face: str = ""
    videos: int = 1
    cid: int = 0
    state: int = 0
    tname: str = ""
    pubdate: int = 0
    desc: str = ""
    #: v1.0 需求：：弹幕数；None = 尚未获取（toview 列表不返回）。
    danmaku_count: int | None = None
    #: v1.0：播放量；None = 尚未获取（同上，来自同一次 video_stat 请求）。
    view_count: int | None = None

    @property
    def key(self) -> str:
        return self.bvid or (f"av{self.aid}" if self.aid > 0 else "")

    @property
    def parts(self) -> int:
        return max(safe_int(self.videos), 1)

    @property
    def is_multi_part(self) -> bool:
        return self.parts > 1

    @property
    def is_finished(self) -> bool:
        # v1.0：已看 ≥ 80% 即视为已看完（B 站客户端同款口径）。整数比较，
        # 79.9% 仍未完成；duration == 0（时长未知）时永远不算完成。
        return self.duration > 0 and self.progress * 5 >= self.duration * 4

    @property
    def has_progress(self) -> bool:
        return self.duration > 0 and self.progress > 0

    @property
    def progress_text(self) -> str:
        if not self.has_progress:
            return ""
        if self.is_finished:
            return "已看完"
        return f"已看 {format_duration(self.progress)} / {format_duration(self.duration)}"

    @classmethod
    def from_api(cls, raw: Any) -> WatchLaterItem | None:
        if not isinstance(raw, dict):
            return None
        aid = safe_int(raw.get("aid"))
        bvid = valid_bvid(raw.get("bvid"))
        if aid <= 0 and not bvid:
            return None

        duration = max(0, safe_int(raw.get("duration")))
        progress = max(0, safe_int(raw.get("progress")))
        if duration > 0:
            progress = min(progress, duration)

        owner = raw.get("owner")
        owner = owner if isinstance(owner, dict) else {}
        videos = safe_int(raw.get("videos"))
        count = safe_int(raw.get("count"))

        # v1.0 需求：：view 接口带 stat.danmaku 时直接采用（列表接口没有）。
        stat = raw.get("stat")
        stat = stat if isinstance(stat, dict) else {}
        raw_danmaku = stat.get("danmaku")
        danmaku_count = (
            max(0, safe_int(raw_danmaku)) if raw_danmaku is not None else None
        )
        # v1.0：同一次 video_stat 请求顺带返回 stat.view（播放量）。
        raw_view = stat.get("view")
        view_count = (
            max(0, safe_int(raw_view)) if raw_view is not None else None
        )

        return cls(
            aid=aid,
            bvid=bvid,
            title=clean_text(raw.get("title"), "无标题"),
            cover_url=cover_thumbnail_url(raw.get("pic")),
            duration=duration,
            progress=progress,
            add_at=max(0, safe_int(raw.get("add_at"))),
            owner_mid=safe_int(owner.get("mid")),
            owner_name=clean_text(owner.get("name"), "未知 UP 主"),
            owner_face=normalize_cover_url(owner.get("face")),
            videos=max(videos, count, 1),
            cid=max(0, safe_int(raw.get("cid"))),
            state=safe_int(raw.get("state")),
            tname=clean_text(raw.get("tname"), ""),
            pubdate=max(0, safe_int(raw.get("pubdate"))),
            desc=clean_text(raw.get("desc"), ""),
            danmaku_count=danmaku_count,
            view_count=view_count,
        )


@dataclass(slots=True)
class WatchLaterSnapshot:
    """A complete list response plus source information for safe logging."""

    items: list[WatchLaterItem] = field(default_factory=list)
    total: int = 0
    endpoint: str = ""
    http_status: int = 0


def video_url(item: WatchLaterItem) -> str:
    """Build a safe public video URL; add ?t= only for a single unfinished P."""
    if item.bvid:
        base = f"https://www.bilibili.com/video/{item.bvid}"
    elif item.aid > 0:
        base = f"https://www.bilibili.com/video/av{item.aid}"
    else:
        return ""
    if (
        item.parts <= 1
        and item.duration > 0
        and 5 <= item.progress < item.duration - 2
    ):
        base += f"?t={item.progress}"
    return base


def watch_later_web_url() -> str:
    return "https://www.bilibili.com/watchlater/list"


def home_url() -> str:
    return "https://www.bilibili.com/"


def matches_query(item: WatchLaterItem, query: str) -> bool:
    text = (query or "").strip().casefold()
    if not text:
        return True
    haystacks = (item.title, item.owner_name, item.bvid)
    return any(text in value.casefold() for value in haystacks if value)


def filter_items(
    items: Iterable[WatchLaterItem], filter_mode: str = FILTER_ALL
) -> list[WatchLaterItem]:
    source = list(items)
    if filter_mode == FILTER_FINISHED:
        return [item for item in source if item.is_finished]
    if filter_mode == FILTER_UNFINISHED:
        return [item for item in source if not item.is_finished]
    return source


def sort_items(
    items: Iterable[WatchLaterItem], sort_mode: str = SORT_RECENT
) -> list[WatchLaterItem]:
    source = list(items)
    if sort_mode == SORT_OLDEST:
        return sorted(source, key=lambda item: item.add_at)
    if sort_mode == SORT_DURATION_DESC:
        return sorted(source, key=lambda item: item.duration, reverse=True)
    if sort_mode == SORT_DURATION_ASC:
        return sorted(source, key=lambda item: item.duration)
    if sort_mode == SORT_OWNER:
        return sorted(
            source,
            key=lambda item: (item.owner_name.casefold(), -item.add_at),
        )
    return sorted(source, key=lambda item: item.add_at, reverse=True)


def filtered_items(
    items: Sequence[WatchLaterItem],
    query: str = "",
    filter_mode: str = FILTER_ALL,
    sort_mode: str = SORT_RECENT,
) -> list[WatchLaterItem]:
    matched = [item for item in items if matches_query(item, query)]
    return sort_items(filter_items(matched, filter_mode), sort_mode)
