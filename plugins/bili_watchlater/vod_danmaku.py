"""B 站点播弹幕抓取与解析（v1.0 / 用户实测需求 5）。

只拉「本人账号已能看的公开点播」的弹幕 XML（``/x/v1/dm/list.so?oid=cid``），
不做批量抓取、不绕过任何风控：412 / -352 / -401 / -403 / v_voucher 直接
报错给上层，绝不自动重试。Cookie 只作为请求头随快照传入，不写日志、
不写异常、不落盘。

解析产物是 ``[(time, mode, color, text), ...]``（按时间升序），由 GUI 侧
的播放节拍器按 time-pos 喂给宿主原生 ``tile.video_danmaku.add_event``，
渲染/轨道/速度/透明度全部复用宿主弹幕引擎与底栏「弹」开关。

cid 为 0 时先走 ``/x/player/pagelist`` 取第一 P 的 cid（与取流一致，
多 P 不猜分P）。所有网络调用都在工作线程里发生。
"""
from __future__ import annotations

import re
import threading
import zlib
from typing import Any, Callable, Mapping

import requests

from .bili_api import BiliAPIError, BiliCancelled

try:
    import xml.etree.ElementTree as ET
except ImportError:
    # 冻结版宿主（PyInstaller）按宿主自身的引用收集 stdlib；宿主不用
    # xml.etree，故运行时可能缺这个模块。置 None，_parse 走正则兜底。
    ET = None

DANMAKU_ENDPOINT = "https://api.bilibili.com/x/v1/dm/list.so"
PAGELIST_ENDPOINT = "https://api.bilibili.com/x/player/pagelist"
TIMEOUT = 10.0
MAX_COMMENTS = 8000  # 长视频 list.so 全量可能上万；截断保护内存与 XML 解析
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024  # 压缩体下载上限
MAX_XML_BYTES = 32 * 1024 * 1024  # 解压后 XML 上限（防解压炸弹）

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


class DanmakuCancelled(BiliCancelled):
    """调用方在两次请求之间取消了任务。"""


class DanmakuError(BiliAPIError):
    """可读的弹幕失败；message 绝不包含 Cookie/请求正文。"""


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise DanmakuCancelled()


def _cookie_header(cookies: Mapping[str, Any] | None) -> dict[str, str]:
    if not cookies:
        return {}
    pairs = []
    for key, value in dict(cookies).items():
        if value is None:
            continue
        name = str(key)
        if name.lower() in ("sessdata", "bili_jct", "dedeuserid", "buvid3"):
            pairs.append("%s=%s" % (name, value))
    return {"Cookie": "; ".join(pairs)} if pairs else {}


def _inflate(payload: bytes) -> bytes:
    """list.so 的压缩体历史上有三种形态：裸 deflate / gzip / 明文。

    解压输出设硬上限：恶意/损坏的压缩体不能把内存撑爆（解压炸弹）。
    """
    text = payload.lstrip()
    if text.startswith(b"<"):
        if len(payload) > MAX_XML_BYTES:
            raise DanmakuError("弹幕数据过大", endpoint=DANMAKU_ENDPOINT)
        return payload
    for wbits in (-zlib.MAX_WBITS, zlib.MAX_WBITS | 16, zlib.MAX_WBITS):
        try:
            inflater = zlib.decompressobj(wbits)
            out = inflater.decompress(payload, MAX_XML_BYTES + 1)
            if len(out) > MAX_XML_BYTES:
                raise DanmakuError("弹幕数据过大", endpoint=DANMAKU_ENDPOINT)
            if inflater.unconsumed_tail and not inflater.eof:
                # 到达上限但流还没完：视为超限，而不是截断解析。
                raise DanmakuError("弹幕数据过大", endpoint=DANMAKU_ENDPOINT)
            return out
        except zlib.error:
            continue
    raise DanmakuError("弹幕数据解压失败", endpoint=DANMAKU_ENDPOINT)


_D_RE = re.compile(r'<d\s+p="([^"]*)"\s*>(.*?)</d>', re.DOTALL)
_ENTITY_RE = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|amp|lt|gt|quot|apos);")
_NAMED_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def _unescape(text: str) -> str:
    """只展开 XML 预定义实体与数字字符引用；不解析任何外部实体。"""
    def repl(match: re.Match) -> str:
        body = match.group(1)
        if body.startswith("#"):
            try:
                code = int(body[2:], 16) if body[1] in "xX" else int(body[1:])
                return chr(code)
            except (ValueError, OverflowError):
                return match.group(0)
        return _NAMED_ENTITIES.get(body, match.group(0))
    return _ENTITY_RE.sub(repl, text)


def _entries_etree(xml_bytes: bytes) -> list[tuple[str, str]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as error:
        raise DanmakuError(
            "弹幕数据解析失败", endpoint=DANMAKU_ENDPOINT
        ) from error
    return [(node.get("p"), "".join(node.itertext())) for node in root.iter("d")]


def _entries_regex(xml_bytes: bytes) -> list[tuple[str, str]]:
    # 冻结宿主缺 xml.etree 时的兜底：B 站弹幕 XML 是扁平的
    # <d p="time,mode,size,color,...">文本</d>，文本中的特殊字符均已转义，
    # 不会出现裸 < 或嵌套标签，正则足以稳健提取；实体用 _unescape 还原。
    text = xml_bytes.decode("utf-8", "replace")
    return [(m.group(1), _unescape(m.group(2))) for m in _D_RE.finditer(text)]


def _parse(xml_bytes: bytes) -> list[tuple[float, int, str, str]]:
    # B 站弹幕 XML 从不带 DTD/实体声明；出现即视为异常输入直接拒绝，
    # 阻断实体膨胀（billion laughs）与外部实体两类攻击面。
    head = xml_bytes[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise DanmakuError("弹幕数据包含非法声明", endpoint=DANMAKU_ENDPOINT)
    entries = _entries_etree(xml_bytes) if ET is not None else _entries_regex(xml_bytes)
    comments: list[tuple[float, int, str, str]] = []
    for p_attr, raw_text in entries:
        p = str(p_attr or "").split(",")
        if len(p) < 4:
            continue
        text = raw_text.strip()
        if not text:
            continue
        try:
            moment = max(0.0, float(p[0]))
            mode = int(p[1])
            color = int(p[3]) & 0xFFFFFF
        except (TypeError, ValueError):
            continue
        comments.append((moment, mode, "#%06x" % color, text[:300]))
        if len(comments) >= MAX_COMMENTS:
            break
    comments.sort(key=lambda entry: entry[0])
    return comments


def _resolve_cid(
    http: requests.Session,
    bvid: str,
    cancelled: Callable[[], bool] | None,
) -> int:
    _check_cancelled(cancelled)
    try:
        response = http.get(
            PAGELIST_ENDPOINT,
            params={"bvid": bvid},
            headers=dict(BASE_HEADERS),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as error:
        raise DanmakuError(
            "弹幕分P信息请求失败", endpoint=PAGELIST_ENDPOINT
        ) from error
    if response.status_code in (412, 403):
        raise DanmakuError(
            "请求被 B 站拦截（%d）" % response.status_code,
            code=response.status_code,
            http_status=response.status_code,
            endpoint=PAGELIST_ENDPOINT,
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise DanmakuError(
            "弹幕分P信息解析失败", endpoint=PAGELIST_ENDPOINT
        ) from error
    code = int(payload.get("code", -1))
    if code != 0:
        raise DanmakuError(
            "B 站接口返回错误码 %d" % code,
            code=code,
            endpoint=PAGELIST_ENDPOINT,
        )
    pages = payload.get("data") or []
    if not isinstance(pages, list) or not pages:
        raise DanmakuError("视频没有可用的分P信息", endpoint=PAGELIST_ENDPOINT)
    return max(0, int(pages[0].get("cid") or 0))


def fetch_danmaku(
    bvid: str,
    cid: int = 0,
    *,
    cookies: Mapping[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[tuple[float, int, str, str]]:
    """拉取并解析一个点播视频的弹幕（工作线程调用，绝不碰 GUI）。"""
    bvid = str(bvid or "").strip()
    if not bvid:
        raise DanmakuError("缺少视频 BV 号", endpoint=DANMAKU_ENDPOINT)
    http = requests.Session()
    try:
        headers = dict(BASE_HEADERS)
        headers.update(_cookie_header(cookies))
        http.headers.update(headers)
        target_cid = int(cid or 0)
        if target_cid <= 0:
            target_cid = _resolve_cid(http, bvid, cancelled)
        if target_cid <= 0:
            raise DanmakuError("没有可用的弹幕 cid", endpoint=DANMAKU_ENDPOINT)
        _check_cancelled(cancelled)
        _check_cancelled(cancelled)
        try:
            response = http.get(
                DANMAKU_ENDPOINT,
                params={"oid": target_cid},
                timeout=TIMEOUT,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            raise DanmakuError(
                "弹幕请求失败", endpoint=DANMAKU_ENDPOINT
            ) from error
        try:
            if response.status_code in (412, 403):
                # 风控不重试、不绕过：直接交给上层显示可读错误。
                raise DanmakuError(
                    "请求被 B 站拦截（%d）" % response.status_code,
                    code=response.status_code,
                    http_status=response.status_code,
                    endpoint=DANMAKU_ENDPOINT,
                )
            if response.status_code != 200:
                raise DanmakuError(
                    "弹幕服务器响应异常（HTTP %d）" % response.status_code,
                    http_status=response.status_code,
                    endpoint=DANMAKU_ENDPOINT,
                )
            chunks = bytearray()
            for chunk in response.iter_content(64 * 1024):
                _check_cancelled(cancelled)
                chunks.extend(chunk)
                if len(chunks) > MAX_DOWNLOAD_BYTES:
                    raise DanmakuError(
                        "弹幕数据过大", endpoint=DANMAKU_ENDPOINT
                    )
        finally:
            response.close()
        _check_cancelled(cancelled)
        return _parse(_inflate(bytes(chunks)))
    finally:
        http.close()
