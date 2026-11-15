"""Douyu account, follow, room, quality and CDN playback adapter."""
import json
import os
import re
import threading
import time
from urllib.parse import unquote, urlsplit

import requests
from streamlink.plugins.douyu import Douyu
from streamlink.stream.http import HTTPStream
from ddm import plugins as api
from .platform_base import NumericLivePlatform

class DouyuPlatform(NumericLivePlatform):
    kind = "douyu"
    share_hosts = ("s.douyu.com",)
    label = "斗鱼"
    room_input_hint = "斗鱼直播间链接"
    hosts = ("www.douyu.com", "douyu.com", "m.douyu.com")
    image_hosts = ("douyucdn.cn", "douyu.com")
    follow_login_url = "https://www.douyu.com/directory/myFollow?from=normalFollow"
    follow_cookie_domain = "douyu.com"
    account_login_url = "https://passport.douyu.com/index/login?client_id=1"

    def __init__(self):
        super().__init__()
        self._failed_cdns = {}
        self._source_cdns = {}
        self._cdn_lock = threading.Lock()

    def report_playback_failure(self, room_id, url, reason):
        if reason not in ("STALL", "LIVE_EOF", "HTTP_401", "HTTP_403", "HTTP_404", "HTTP_410",
                          "HTTP_502", "HTTP_503", "HTTP_504"):
            return
        room = self.normalize(room_id)
        host = urlsplit(url).hostname or ""
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
            return
        with self._cdn_lock:
            cdns = self._source_cdns.get(room, {}).get(host, ())
            failed = self._failed_cdns.setdefault(room, {})
            failed[host] = time.monotonic()
            for cdn in cdns:
                failed[cdn] = failed[host]
        logger = getattr(self, "_diagnostic_log", None)
        if logger is not None:
            logger(f"[平台诊断] 阶段=source_rejected 房间={room} CDN={host} 原因={reason} 避让秒数=60")

    def room_quality_options(self, room_id: str) -> list[dict]:
        options = super().room_quality_options(room_id)
        # rate=0 的“原画”不一定是最高分辨率；仅调整菜单，不改变取流档位映射。
        return sorted(options, key=lambda item: "2K" in item["desc"].upper(), reverse=True)

    def danmaku_client(self, room_id: str, parent=None):
        from .douyu_danmaku import DouyuDanmakuClient
        return DouyuDanmakuClient(room_id, self, parent)

    def account_info(self, session, cancelled) -> dict:
        if cancelled():
            return {}
        with session.get("https://www.douyu.com/lapi/member/api/getInfo",
                params={"client_type": 0}, headers={"User-Agent": "Mozilla/5.0",
                "Referer": self.follow_login_url}, timeout=(4, 8)) as response:
            response.raise_for_status()
            payload = response.json()
        data = payload.get("msg")
        uid = str(data.get("uid") or "") if isinstance(data, dict) else ""
        if str(payload.get("error")) != "0" or not uid.isdigit() or int(uid) <= 0:
            raise RuntimeError("斗鱼未登录或登录已过期，请在官方页面完成登录")
        # 官方网页 USER_INFO.nickname 与头像缓存同样来自 acf_ Cookie。
        cookies = {cookie.name: unquote(cookie.value) for cookie in session.cookies
                   if cookie.domain.lstrip(".") == "douyu.com"}
        uname = cookies.get("acf_nickname") or uid
        face = cookies.get("acf_avatar", "")
        if face.endswith("_"):
            face += "middle.jpg"
        elif face.endswith("="):
            face += "middle"
        face = self._image_url(face)
        if not face and not cancelled():
            try:
                with session.get(f"https://www.douyu.com/lapi/member/userInfo/getInfo/{uid}",
                        params={"size": "middle", "icon": 1},
                        headers={"User-Agent": "Mozilla/5.0", "Referer": self.follow_login_url},
                        timeout=(4, 8)) as response:
                    response.raise_for_status()
                    avatar = response.json().get("data")
                if isinstance(avatar, str):
                    avatar = json.loads(avatar)
                icon = avatar.get("icon") if isinstance(avatar, dict) else None
                if isinstance(icon, dict) and str(icon.get("code")) == "0":
                    image = icon.get("msg")
                    image = image.get("middle") if isinstance(image, dict) else image
                    if isinstance(image, str):
                        face = self._image_url("https://apic.douyucdn.cn/upload/" + image)
            except (requests.RequestException, ValueError):
                pass  # 头像暂不可用仍保留已确认的账号 ID。
        return {"uid": uid, "uname": uname, "face": face}

    def follow_rooms(self, session, cancelled) -> list:
        result = {}
        for page in range(1, 101):
            if cancelled():
                return []
            with session.get("https://www.douyu.com/wgapi/livenc/liveweb/follow/list",
                    params={"page": page, "sort": 0, "cid1": 0},
                    headers={"User-Agent": "Mozilla/5.0", "Referer": self.follow_login_url},
                    timeout=(4, 8)) as response:
                response.raise_for_status()
                payload = response.json()
            if str(payload.get("error")) != "0":
                raise RuntimeError("斗鱼关注获取失败，请先在页面登录；登录已过期时请重新登录")
            # 官方网页 GSON.parse 就是 JSON.parse，接口 data 为 JSON 字符串。
            data = payload.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise RuntimeError("斗鱼关注数据格式已变化，请稍后重试")
            for room in data["list"]:
                if not isinstance(room, dict) or not room.get("room_id"):
                    continue  # 关注页中的视频/频道推荐不是直播间
                canonical = self.normalize(f"douyu:{room['room_id']}")
                live = str(room.get("show_status")) == "1" and not room.get("videoLoop")
                result[canonical] = api.RoomInfo(room_id=canonical,
                    uname=room.get("nickname") or canonical, title=room.get("room_name") or "",
                    platform=self.kind, live=live, face=self._image_url(room.get("avatar_small")),
                    cover_url=self._image_url(room.get("room_src")),
                    extra={"playback_mode": "stream", "live_known": True}).as_dict()
            try:
                page_count = int(data["pageCount"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("斗鱼关注分页信息缺失，请稍后重试") from error
            if page_count < 0 or page_count > 100:
                raise RuntimeError("斗鱼关注列表页数异常，请稍后重试")
            if page >= page_count:
                return list(result.values())
        raise RuntimeError("斗鱼关注列表未完整读取，请稍后重试")

    def _room_data(self, raw):
        with requests.get(f"https://www.douyu.com/betard/{raw}", headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://www.douyu.com/"},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            return response.json()["room"]

    def room_info(self, room_id: str) -> api.RoomInfo:
        room_id, _share = self._resolve_share_input(room_id)
        canonical = self.normalize(room_id)
        raw = canonical.split(":", 1)[1]
        try:
            try:
                room = self._room_data(raw)
            except ValueError:
                # 靓号的资料接口可能返回提示页，从公开直播页取得实际房间号。
                with requests.get(self.room_url(canonical), headers={
                        "User-Agent": "Mozilla/5.0", "Referer": "https://www.douyu.com/"},
                        timeout=(4, 8)) as response:
                    response.raise_for_status()
                    match = re.search(r"\bwindow\.room_id\s*=\s*([1-9][0-9]{0,19})\s*;", response.text)
                    ids = {match[1]} if match else set(re.findall(
                        r'''(?:\broomI[Dd]|["']roomI[Dd]["'])\s*:\s*([1-9][0-9]{0,19})\b''', response.text))
                if len(ids) != 1 or raw in ids:
                    raise ValueError("Missing actual room ID")
                room = self._room_data(ids.pop())
            if type(room.get("show_status")) is not int or not room.get("room_id"):
                raise ValueError("Missing room status")
            canonical = self.normalize(f"douyu:{room['room_id']}")
            display_id = str(room.get('vipId') or '')
            if not display_id.isdigit() or int(display_id) <= 0:
                display_id = ''
            return api.RoomInfo(room_id=canonical, uname=room.get("nickname") or f"斗鱼 · {raw}",
                title=room.get("room_name") or "斗鱼直播间", platform=self.kind,
                live=room["show_status"] == 1 and not room.get("videoLoop"),
                face=self._image_url(room.get("owner_avatar") or room.get("avatar_small")),
                cover_url=self._image_url(room.get("room_pic") or room.get("coverSrc")),
                extra={"playback_mode": "stream", "live_known": True, "display_room_id": display_id})
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            raise RuntimeError("斗鱼房间信息获取失败，请稍后重试") from error

    def _playback_cookies(self):
        jar = requests.cookies.RequestsCookieJar()
        if os.environ.get("DDM_NO_SAVE") == "1":
            return jar
        try:
            from ddm.account_store import AccountStore
        except ImportError:
            return jar  # Older hosts still support public playback without saved accounts.
        from PySide6.QtCore import QByteArray
        from PySide6.QtNetwork import QNetworkCookie
        context = getattr(self, "_context", None)
        window = context.window if context else None
        session = getattr(window, "_platform_login_sessions", {}).get("douyu")
        cookies = session["cookies"] if session is not None else AccountStore("douyu").load()
        for raw in cookies:
            for cookie in QNetworkCookie.parseCookies(QByteArray(raw.encode("utf-8"))):
                domain = cookie.domain().lstrip(".").lower()
                if domain != "douyu.com" and not domain.endswith(".douyu.com"):
                    continue
                expiry = None if cookie.isSessionCookie() else cookie.expirationDate().toSecsSinceEpoch()
                if expiry is not None and expiry <= time.time():
                    continue
                jar.set(bytes(cookie.name()).decode("utf-8"), bytes(cookie.value()).decode("utf-8"),
                        domain=cookie.domain(), path=cookie.path() or "/", expires=expiry,
                        secure=cookie.isSecure())
        return jar

    def _streams(self, session, room_id, quality=250, *, preview=False):
        session.http.cookies.update(self._playback_cookies())
        info = self.room_info(room_id)
        if not info.live:
            return {}
        parser = Douyu(session, self.room_url(info.room_id))
        raw = self.normalize(info.room_id).split(":", 1)[1]
        data = self._request_source(parser, raw)
        # 网页默认的 P2P 边缘线路可能很快断流，优先使用接口明确给出的普通 CDN。
        cdns = {item.get("cdn") for item in data.get("cdnsWithName", [])}
        cdn = ""
        if (urlsplit(data.get("rtmp_url", "")).hostname or "").endswith(".edgesrv.com"):
            cdn = next((name for name in ("hw-h5", "tct-h5", "ali-h5") if name in cdns), "")
        canonical = self.normalize(room_id)
        with self._cdn_lock:
            failures = {key: stamp for key, stamp in self._failed_cdns.get(canonical, {}).items()
                        if time.monotonic() - stamp < 60}
            self._failed_cdns[canonical] = failures
        host = urlsplit(data.get("rtmp_url", "")).hostname or ""
        if cdn in failures or (not cdn and host in failures):
            candidates = [item.get("cdn") for item in data.get("cdnsWithName", []) if item.get("cdn")]
            if candidates:
                # Use only the current API list; a failed CDN's next hostname is still avoided.
                cdn = min(candidates, key=lambda name: (name in failures, failures.get(name, 0)))
        options = [{"qn": 10000 if item["rate"] == 0 else 2000000 + item["rate"],
                    "desc": item.get("name") or (
                        "原画" if item["rate"] == 0 else f"{item.get('bit', 0)} kbps"),
                    "rate": item["rate"], "bit": item.get("bit", 0)}
                   for item in data.get("multirates", [])
                   if isinstance(item.get("rate"), int) and item["rate"] >= 0]
        if not any(item["rate"] == 0 for item in options):
            options.append({"qn": 10000, "desc": "原画", "rate": 0, "bit": 0})
        self._qualities[room_id] = sorted(options, key=lambda item: (
            item["rate"] == 0, item["bit"]), reverse=True)
        selected = self._select_quality(room_id, quality, preview)
        rate = selected["rate"]
        if cdn or data.get("rate") != rate:
            data = self._request_source(parser, raw, cdn, rate=rate)
            if not data:
                raise RuntimeError("斗鱼所选画质取流失败，请重试或选择其他画质")
        if not data:
            return {}
        actual_rate = data.get("rate", rate)
        if actual_rate != rate:
            try:
                self.account_info(session.http, lambda: False)
            except RuntimeError as error:
                raise getattr(api, "LoginRequiredError", RuntimeError)(
                    "斗鱼登录已失效或未登录，请重新登录后获取所选画质") from error
            raise RuntimeError("斗鱼未返回所选画质，请重试或选择其他线路/画质")
        host = urlsplit(data['rtmp_url']).hostname or ""
        with self._cdn_lock:
            # The API can return another CDN. Keep both the actual CDN and request
            # route so neither bypasses failure avoidance through a new hostname.
            self._source_cdns.setdefault(canonical, {})[host] = tuple(dict.fromkeys(
                name for name in (data.get("rtmp_cdn"), cdn) if name))
        stream = HTTPStream(session, f"{data['rtmp_url']}/{data['rtmp_live']}")
        stream.ddm_quality = 10000 if actual_rate == 0 else 2000000 + actual_rate
        return {"source": stream}

    def _request_source(self, parser, raw, cdn="", *, rate=0):
        # 与固定版本 Streamlink 的公开网页请求一致，请求 AVC 及网页提供的画质/CDN。
        encryption = parser._get_encryption(parser.DID)
        if not encryption:
            return {}
        timestamp, values = encryption
        auth = parser._compute_auth(raw, timestamp, values["key"], values["rand_str"],
                                   values["enc_time"], values["is_special"])
        with parser.session.http.post(parser._URL_PLAY.format(rid=raw), data={
                "enc_data": values["enc_data"], "tt": str(timestamp), "did": parser.DID,
                "auth": auth, "cdn": cdn, "rate": str(rate), "hevc": "0", "fa": "0", "ive": "0"},
                headers={"Content-Type": "application/x-www-form-urlencoded"}) as response:
            response.raise_for_status()
            result = response.json()
        data = result.get("data")
        if result.get("error") != 0 or not isinstance(data, dict):
            return {}
        if urlsplit(data.get("rtmp_url", "")).scheme != "https" or not data.get("rtmp_live"):
            raise ValueError("Invalid public stream")
        return data
