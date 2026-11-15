"""Huya account identity and follows on the owning platform instance."""
import json
import re
from urllib.parse import urlsplit

from .huya_protocol import huya_follow_request


class HuyaAccounts:
    account_login_url = "https://www.huya.com/?evt_fe=login"

    account_cookie_domain = "huya.com"

    follow_login_url = "https://www.huya.com/?evt_fe=login"

    follow_cookie_domain = "huya.com"

    def follow_rooms(self, session, cancelled) -> list:
        if cancelled():
            return []
        account = self.account_info(session, cancelled)
        if cancelled():
            return []
        uid = account["uid"]
        data = huya_follow_request(session, "getAllSubscribeToUidList", uid, uid)
        targets = data.get(1)
        if not isinstance(targets, list) or any(not isinstance(value, int) or value <= 0 for value in targets):
            raise RuntimeError("虎牙关注列表格式异常，请稍后重试")
        rooms = {}
        for target in dict.fromkeys(targets):
            if cancelled():
                return []
            profile = huya_follow_request(session, "getUserProfile", uid, target).get(0, {})
            user, presenter = profile.get(0, {}), profile.get(1, {})
            rid = presenter.get(10) or presenter.get(3)
            if not rid:
                continue  # 普通用户和注销账号没有可导入的直播间。
            if user.get(0) != target:
                raise RuntimeError("虎牙关注主播信息不匹配，请稍后重试")
            canonical = self.normalize("huya:" + str(rid))
            if canonical not in rooms:
                # 账号资料中的最近直播不能代表当前开播状态，复用房间状态查询。
                rooms[canonical] = self.room_info(canonical).as_dict()
                if cancelled():
                    return []
        return list(rooms.values())

    def account_info(self, session, cancelled) -> dict:
        if cancelled():
            return {}
        with session.get("https://l.huya.com/udb_web/udbport2.php",
                params={"m": "HuyaLogin", "do": "checkLogin", "callback": "ddmAccount"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": self.account_login_url},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            match = re.fullmatch(r"\s*ddmAccount\((.*)\)\s*;?\s*", response.text, re.S)
            if match is None:
                raise RuntimeError("虎牙账号确认失败，请在官方页面重新登录后重试")
            data = json.loads(match[1])
        uid = str(data.get("uid") or "")
        if data.get("isLogined") is not True or not uid.isdigit() or int(uid) <= 0:
            raise RuntimeError("虎牙未登录或登录已过期，请在官方页面完成登录")
        face = str(data.get("userLogo") or "")
        if face.startswith("//"):
            face = "https:" + face
        parts = urlsplit(face)
        if (parts.scheme != "https" or parts.username or parts.password or
                not (parts.hostname or "").endswith(".msstatic.com")):
            face = ""
        return {"uid": uid, "uname": data.get("userNick") or data.get("userName") or uid,
                "face": face}
