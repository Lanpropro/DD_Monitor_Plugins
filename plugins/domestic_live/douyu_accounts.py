"""Douyu account identity and follows on the owning platform instance."""
import json
from urllib.parse import unquote

import requests
from ddm import plugins as api


class DouyuAccounts:
    follow_login_url = "https://www.douyu.com/directory/myFollow?from=normalFollow"

    follow_cookie_domain = "douyu.com"

    account_login_url = "https://passport.douyu.com/index/login?client_id=1"

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
