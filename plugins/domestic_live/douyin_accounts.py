"""Douyin account identity and follows on the owning platform instance."""
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests
from ddm import plugins as api
from .douyin_errors import DouyinLookupError


class DouyinAccounts:
    account_login_url = "https://www.douyin.com/user/self"

    account_cookie_domain = "douyin.com"

    follow_login_url = "https://www.douyin.com/user/self"

    follow_cookie_domain = "douyin.com"

    follow_browser_url = ("https://www.douyin.com/aweme/v1/web/user/following/list/",
                          "https://www.douyin.com/aweme/v1/web/user/profile/self/")

    follow_browser_init_script = (Path(__file__).parent / "douyin_follows.js").read_text(encoding="utf-8")

    follow_browser_script = """async (url) => {
        const endpoint = new URL(url);
        if (endpoint.pathname === '/aweme/v1/web/user/following/list/') {
            return await window.__ddmDouyinFollows.read(endpoint.searchParams);
        }
        const deadline = Date.now() + 15000;
        while (Date.now() < deadline) {
            const chunks = window.webpackChunkdouyin_web;
            let require;
            if (chunks) chunks.push([['ddm-follow-' + Date.now()], {}, r => { require = r; }]);
            if (require && require.m) {
                const modules = Object.entries(require.m);
                const common = modules.find(([, fn]) => fn.toString().includes('CHANNEL_PC_WEB:function') &&
                    fn.toString().includes('COMMON_SEARCH_PARAMS:function'));
                const client = modules.find(([, fn]) => fn.toString().includes('skipCheckCode') &&
                    fn.toString().includes('securitySdkInitWeb') && fn.toString().includes('withCredentials'));
                if (common && client) {
                    if (endpoint.origin !== location.origin ||
                        endpoint.pathname !== '/aweme/v1/web/user/profile/self/') throw new Error('Invalid endpoint');
                    const params = {...require(common[0]).COMMON_SEARCH_PARAMS,
                        ...Object.fromEntries(endpoint.searchParams)};
                    // 官网客户端负责设备参数、签名初始化和验证弹窗。
                    return await require(client[0]).U2(endpoint.pathname, params, {timeout: 12000});
                }
            }
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        throw new Error('Official request client not ready');
    }"""

    def restore_follow_rooms(self, rooms):
        for room in rooms:
            rid, uid = str(room.get("room_id") or ""), str(room.get("anchor_uid") or "")
            if self._follow_reference_uid(rid):
                rid = str(room.get("resolved_room_id") or "")
            sec_uid = str(room.get("sec_uid") or "")
            face = self._image_url(room.get("face"))
            if re.fullmatch(r"[1-9][0-9]{0,19}", uid) and face:
                self._follow_faces[uid] = face
            if re.fullmatch(r"[1-9][0-9]{0,19}", uid) and re.fullmatch(r"MS4wLjAB[A-Za-z0-9_-]{10,180}", sec_uid):
                self._follow_profiles[uid] = sec_uid
            if re.fullmatch(r"douyin:[0-9]{1,20}", rid) and re.fullmatch(r"[0-9]{1,20}", uid):
                self._anchor_uids[rid] = uid
        if self._context is not None:
            self._context.set_setting("douyin_room_owners", dict(self._anchor_uids))
            if self._follow_profiles:
                self._context.set_setting("douyin_follow_profiles", dict(self._follow_profiles))

    @staticmethod
    def _follow_reference_uid(value):
        match = re.fullmatch(r"douyin:uid_([1-9][0-9]{0,19})", str(value))
        return match[1] if match else ""

    def follow_account_reference(self, account):
        """稳定账号引用供侧栏保存；它不是主播固定网页房间号。"""
        uid = str(account.get("anchor_uid") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,19}", uid):
            raise ValueError("抖音关注账号身份无效，请重新读取关注")
        rid = "douyin:uid_" + uid
        resolved = self.normalize(rid)
        return dict(account, room_id=rid, platform=self.kind, playback_mode="stream", follow_account_ref=True,
                    resolved_room_id=resolved if resolved != rid else "")

    def follow_accounts(self, session, cancelled):
        """关注身份独立于房间；读取阶段不逐个等待直播资料。"""
        return self.follow_rooms(session, cancelled, accounts_only=True)

    def _follow_avatar(self, uid):
        if uid in self._follow_faces:
            return self._follow_faces[uid]
        if not re.fullmatch(r"[1-9][0-9]{0,19}", str(uid)):
            return ""
        try:
            with requests.get("https://live.douyin.com/webcast/user/",
                    params={"aid": 6383, "app_name": "douyin_web", "target_uid": uid},
                    headers={"User-Agent": self.interaction_user_agent, "Referer": "https://live.douyin.com/"},
                    timeout=(4, 8)) as response:
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict):
                return ""
            user = payload.get("data")
            if (type(payload.get("status_code")) is not int or payload["status_code"] != 0
                    or not isinstance(user, dict) or str(user.get("id_str") or user.get("id")) != uid):
                return ""
            for key in ("avatar_medium", "avatar_thumb", "avatar_large"):
                face = self._image_url(user.get(key))
                if face:
                    self._follow_faces[uid] = face
                    return face
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError):
            pass  # 头像失败不覆盖已确认的直播状态；后续刷新可重试。
        return ""

    def resolve_follow_account(self, account, cancelled):
        uid = str(account.get("anchor_uid") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,19}", uid) or cancelled():
            return dict(account)
        reference = str(account.get("room_id") or "")
        reference_uid = self._follow_reference_uid(reference)
        if reference_uid:
            if reference_uid != uid:
                raise RuntimeError("抖音关注账号身份不匹配")
            canonical = self.normalize(reference)
            resolved = self.resolve_follow_account(dict(account,
                room_id="" if canonical == reference else canonical), cancelled)
            if cancelled():
                return dict(account)
            fixed = resolved.get("room_id") or ""
            return dict(resolved, room_id=reference, follow_account_ref=True, resolved_room_id=fixed,
                        face=resolved.get("face") or self._follow_faces.get(uid) or
                             (self._follow_avatar(uid) if not resolved.get("live") else ""),
                        live=resolved.get("live", False) if fixed else False,
                        live_known=resolved.get("live_known", False) if fixed else True)
        if not account.get("room_id"):
            known = next((rid for rid, owner_uid in self._anchor_uids.items() if owner_uid == uid), "")
            if known:
                account = dict(account, room_id=known)
        try:
            room = self._follow_room(requests, uid, cancelled, canonical=account.get("room_id") or "")
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
            if cancelled():
                return dict(account)
            if not account.get("room_id"):
                raise self._lookup_error("关注主播直播资料", error) from error
            # 已有固定房间号时，不让补充接口失败阻止刷新；仍核对主播身份。
            try:
                info = self._room_page(self.normalize(account["room_id"]), expected_uid=uid)
            except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as page_error:
                raise DouyinLookupError(f"{self._lookup_error('关注主播直播资料', error)}；"
                    f"直播间网页回退也失败：{self._lookup_error('直播间资料', page_error)}") from page_error
            data = info["room"]
            owner = data.get("owner") or info.get("anchor") or {}
            room = {"web_rid": account["room_id"].split(":", 1)[1],
                    "status": data["status"], "title": data.get("title", ""),
                    "face": owner.get("avatar_thumb"), "cover": data.get("cover")}
        if not room or cancelled():
            return dict(account)
        if not room.get("web_rid") and room.get("status") == 4 and not account.get("room_id"):
            return dict(account, live=False, live_known=True)
        canonical = self.normalize("douyin:" + str(room["web_rid"]))
        if account.get("room_id") and account["room_id"] != canonical:
            raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
        self._anchor_uids[canonical] = uid
        result = dict(account)
        result.update(room_id=canonical, live=room.get("status") == 2,
                      live_known=type(room.get("status")) is int,
                      title=room.get("title") or account.get("title") or "",
                      cover_url=self._image_url(room.get("cover")))
        if not result.get("face"):
            result["face"] = self._image_url(room.get("face"))
        return result

    def profile_sec_uid(self, text):
        parts = urlsplit(str(text or "").strip())
        if parts.hostname not in ("douyin.com", "www.douyin.com") or not parts.path.startswith("/user/"):
            return ""
        if (parts.scheme not in ("http", "https") or parts.username or parts.password
                or parts.port not in (None, 80, 443)):
            raise ValueError("请使用抖音官方主播主页链接")
        sec_uid = parts.path.removeprefix("/user/").rstrip("/")
        if not re.fullmatch(r"MS4wLjAB[A-Za-z0-9_-]{10,180}", sec_uid):
            raise ValueError("请粘贴主播的完整个人主页链接，不支持「我的主页」链接")
        return sec_uid

    def profile_room(self, uid, cancelled, *, web_rid=""):
        if not re.fullmatch(r"[0-9]{1,20}", str(uid)) or int(uid) <= 0:
            raise ValueError("抖音主页未返回有效主播身份")
        if cancelled():
            return ""
        if web_rid:
            canonical = self.normalize("douyin:" + str(web_rid))
            self._anchor_uids[canonical] = str(uid)
            return canonical
        room = self._follow_room(requests, str(uid), cancelled)
        if cancelled():
            return ""
        if not room:
            raise RuntimeError("官网暂未返回可用的直播记录，请在主播开播时重试，或使用直播间完整链接")
        canonical = self.normalize("douyin:" + room["web_rid"])
        self._anchor_uids[canonical] = str(uid)
        return canonical

    def profile_room_from_page(self, user, cancelled):
        canonical = self.profile_room(user["uid"], cancelled, web_rid=user.get("web_rid", ""))
        if not canonical or cancelled():
            return ""
        if user.get("web_rid"):
            # 官网已确认主播和固定房间号；添加阶段不再依赖另一条匿名 HTTP 请求。
            status = user.get("status")
            info = api.RoomInfo(room_id=canonical, platform=self.kind,
                uname=user.get("nickname") or f"抖音 · {user['web_rid']}",
                title=user.get("title") or "抖音直播间", live=status == 2,
                face=self._image_url(user.get("avatar")), cover_url=self._image_url(user.get("cover")),
                extra={"playback_mode": "stream", "live_known": type(status) is int,
                       "anchor_uid": str(user["uid"])})
            self._profile_additions[canonical] = (time.monotonic(), info)
        return canonical

    def follow_rooms(self, session, cancelled, *, accounts_only=False) -> list:
        if cancelled():
            return []
        account = self.account_info(session, cancelled)
        params = {"aid": 6383, "device_platform": "webapp", "user_id": account.get("uid"),
                  "count": 20, "source_type": 2, "is_top": 1, "offset": 0,
                  "min_time": 0, "max_time": 0, "gps_access": 0, "address_book_access": 0}
        if account.get("sec_uid"):
            params["sec_user_id"] = account["sec_uid"]
        headers = {"User-Agent": "Mozilla/5.0", "Referer": self.follow_login_url}
        known = {uid: rid for rid, uid in self._anchor_uids.items()}
        rooms, cursors = {}, {(0, 0, 0)}
        while not cancelled():
            try:
                with session.get("https://www.douyin.com/aweme/v1/web/user/following/list/",
                        params=params, headers=headers, timeout=(4, 8)) as response:
                    response.raise_for_status()
                    if not response.content:
                        raise DouyinLookupError("抖音关注接口返回空内容，请在官方页面完成登录或验证后重试")
                    payload = response.json()
            except (requests.RequestException, ValueError) as error:
                raise self._lookup_error("关注列表", error) from error
            if not isinstance(payload, dict):
                raise DouyinLookupError("抖音关注响应格式异常，请稍后重试；持续失败时请反馈插件版本与失败时间")
            if payload.get("status_code") != 0:
                code = payload.get("status_code")
                detail = f"（状态 {code}）" if isinstance(code, int) else "（响应格式异常）"
                raise RuntimeError(f"抖音关注获取失败{detail}，请在官方页面完成登录或验证后重试")
            if not isinstance(payload.get("followings"), list):
                raise RuntimeError("抖音关注响应缺少列表，请稍后重试")
            for user in payload["followings"]:
                if cancelled():
                    return []
                if not isinstance(user, dict):
                    raise DouyinLookupError("抖音关注响应包含无效账号资料，请重新读取")
                room = user.get("room_data") or {}
                if isinstance(room, str):
                    try:
                        room = json.loads(room)
                    except ValueError:
                        room = {}
                if not isinstance(room, dict):
                    room = {}
                uid = str(user.get("uid") or "")
                owner = room.get("owner") or {}
                if not isinstance(owner, dict):
                    owner = {}
                owner_uid = str(owner.get("id_str") or owner.get("id") or "")
                if owner_uid and owner_uid != uid:
                    raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
                rid = user.get("web_rid") or room.get("web_rid") or owner.get("web_rid")
                if rid and not re.fullmatch(r"[0-9]{1,20}", str(rid)):
                    rid = ""
                if rid and self.normalize("douyin:" + str(rid)) in rooms:
                    continue
                # 官网列表已提供固定房间和状态时直接采用，避免逐个查询拖慢读取。
                # 补充接口返回空记录或超时时，不能丢掉官网已经确认的房间号。
                if not accounts_only and uid and (not rid or type(room.get("status")) is not int):
                    try:
                        resolved = self._follow_room(session, uid, cancelled)
                    except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
                        if not rid:
                            raise self._lookup_error("关注主播直播资料", error) from error
                        resolved = {}
                    if cancelled():
                        return []
                    if resolved:
                        if rid and str(resolved["web_rid"]) != str(rid):
                            raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
                        room, rid = resolved, resolved["web_rid"]
                if accounts_only and not rid:
                    rid = known.get(uid, "").removeprefix("douyin:")
                if not rid and not accounts_only:
                    continue
                if accounts_only and not re.fullmatch(r"[1-9][0-9]{0,19}", uid):
                    raise RuntimeError("抖音关注响应缺少有效账号身份，请稍后重试")
                canonical = self.normalize("douyin:" + str(rid)) if rid else ""
                key = uid if accounts_only else canonical
                if key in rooms:
                    continue
                if canonical and re.fullmatch(r"[1-9][0-9]{0,19}", uid):
                    self._anchor_uids[canonical] = uid
                face = next((url for item in [user.get("avatar_medium") or {}, user.get("avatar_thumb") or {}]
                             for raw in item.get("url_list", []) if (url := self._image_url(raw))), "")
                rooms[key] = api.RoomInfo(room_id=canonical, platform=self.kind,
                    uname=user.get("remark_name") or user.get("nickname") or str(rid),
                    title=room.get("title", ""), live=room.get("status") == 2,
                    face=face or self._image_url(room.get("face")), cover_url=self._image_url(room.get("cover")),
                    extra={"playback_mode": "stream", "live_known": type(room.get("status")) is int,
                           "anchor_uid": uid, "sec_uid": str(user.get("sec_uid") or "")}).as_dict()
            if payload.get("has_more") in (False, 0):
                return list(rooms.values())
            if payload.get("has_more") not in (True, 1):
                raise RuntimeError("抖音关注分页信息缺失，请稍后重试")
            cursor = tuple(payload.get(key) for key in ("offset", "min_time", "max_time"))
            if any(value is None for value in cursor) or cursor in cursors:
                raise RuntimeError("抖音关注分页没有前进，请稍后重试")
            cursors.add(cursor)
            params.update(zip(("offset", "min_time", "max_time"), cursor))
        return []

    def _follow_room(self, session, uid, cancelled, *, canonical=""):
        room = self._share_room(session, uid, cancelled, canonical=canonical, identity_only=True)
        if not room:
            return {}
        owner = room["owner"]
        rid = owner["web_rid"]
        return {"web_rid": rid, "status": room["status"], "title": room.get("title", ""),
                "face": owner.get("avatar_thumb"), "cover": room.get("cover")}

    def account_info(self, session, cancelled) -> dict:
        if cancelled():
            return {}
        with session.get("https://www.douyin.com/aweme/v1/web/user/profile/self/",
                params={"aid": 6383, "device_platform": "webapp", "source": "channel_pc_web",
                        "personal_center_strategy": 1},
                headers={"User-Agent": self.interaction_user_agent, "Referer": self.account_login_url},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            payload = response.json()
        data = payload.get("user")
        uid = str(data.get("uid") or "") if isinstance(data, dict) else ""
        if str(payload.get("status_code")) != "0" or not uid.isdigit() or int(uid) <= 0:
            raise RuntimeError("抖音未登录或登录已过期，请在官方页面完成登录")
        avatar = data.get("avatar_thumb") or {}
        face = next((url for value in avatar.get("url_list", []) if (url := self._image_url(value))), "")
        return {"uid": uid, "uname": data.get("nickname") or uid, "face": face,
                "sec_uid": data.get("sec_uid") or ""}
