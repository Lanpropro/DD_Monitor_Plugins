"""twitch platform: extracted platform responsibility."""
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit
import requests
from streamlink.plugins.twitch import Twitch, TwitchAPI
from ddm import plugins as api
from ddm.live_danmaku import USER_AGENT
from .public_live import PublicLivePlatform, official_url, public_request, image_url

class TwitchPlatform(PublicLivePlatform):
    kind = "twitch"
    oauth_provider = "twitch"
    label = "Twitch"
    room_input_hint = "Twitch 频道链接"
    hosts = ("twitch.tv", "www.twitch.tv", "m.twitch.tv", "player.twitch.tv")
    origin = "https://www.twitch.tv/"
    parser = Twitch
    account_login_url = "https://www.twitch.tv/login"
    account_cookie_domain = "twitch.tv"
    follow_login_url = "https://www.twitch.tv/directory/following/channels"
    follow_cookie_domain = "twitch.tv"
    browser_login_url = "https://www.twitch.tv/login"
    browser_cookie_domains = ("twitch.tv",)

    def browser_account_info(self, session, cancelled):
        from . import twitch_accounts as web_accounts
        return web_accounts.twitch_account(session, cancelled)

    def browser_follow_rooms(self, session, cancelled):
        from . import twitch_accounts as web_accounts
        return web_accounts.twitch_follows(session, cancelled)

    def _authorization(self, session):
        token = next((cookie.value for cookie in session.cookies if cookie.name == "auth-token"
                      and (cookie.domain.lstrip(".") == "twitch.tv"
                           or cookie.domain.lstrip(".").endswith(".twitch.tv"))), "")
        if not token:
            raise RuntimeError("Twitch 未登录，请在官方页面完成登录")
        with session.get("https://id.twitch.tv/oauth2/validate",
                headers={"Authorization": "OAuth " + token}, timeout=(4, 8)) as response:
            if response.status_code == 401:
                raise RuntimeError("Twitch 登录已过期，请在官方页面重新登录")
            response.raise_for_status()
            identity = response.json()
        uid = str(identity.get("user_id") or "")
        client = identity.get("client_id")
        expected = getattr(session, "ddm_oauth_client_id", "")
        if expected and client != expected:
            raise RuntimeError("Twitch 授权客户端不匹配，请重新授权")
        if not uid.isdigit() or int(uid) <= 0 or not client or not identity.get("login"):
            raise RuntimeError("Twitch 未返回有效用户账号，请重新登录")
        return identity, token

    def account_info(self, session, cancelled) -> dict:
        if cancelled():
            return {}
        identity, token = self._authorization(session)
        uid, client = str(identity["user_id"]), identity["client_id"]
        if cancelled():
            return {}
        with session.get("https://api.twitch.tv/helix/users", params={"id": uid},
                headers={"Authorization": "Bearer " + token, "Client-ID": client},
                timeout=(4, 8)) as response:
            if response.status_code == 401:
                raise RuntimeError("Twitch 登录已过期，请在官方页面重新登录")
            response.raise_for_status()
            users = response.json().get("data", [])
        if len(users) != 1 or str(users[0].get("id")) != uid:
            raise RuntimeError("Twitch 账号信息不匹配，请重新登录")
        user = users[0]
        return {"uid": uid, "uname": user.get("display_name") or identity["login"],
                "face": image_url(user.get("profile_image_url"), ("jtvnw.net",))}

    def follow_rooms(self, session, cancelled) -> list:
        if cancelled():
            return []
        identity, token = self._authorization(session)
        if "user:read:follows" not in identity.get("scopes", []):
            raise RuntimeError("Twitch 账号已登录，但当前授权缺少 user:read:follows 权限，无法读取关注列表。"
                               "普通网页登录不保证包含此权限，需要另行接入 Twitch OAuth 授权。")
        rooms, seen, cursors = [], set(), set()
        cursor = ""
        while not cancelled():
            params = {"user_id": str(identity["user_id"]), "first": 100}
            if cursor:
                params["after"] = cursor
            with session.get("https://api.twitch.tv/helix/channels/followed", params=params,
                    headers={"Authorization": "Bearer " + token, "Client-ID": identity["client_id"]},
                    timeout=(4, 8)) as response:
                if response.status_code in (401, 403):
                    raise RuntimeError("Twitch 关注读取未获授权，请重新授权 user:read:follows 权限")
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload.get("data"), list):
                raise RuntimeError("Twitch 关注列表返回异常，请稍后重试")
            for item in payload["data"]:
                login = str(item.get("broadcaster_login") or "")
                if not re.fullmatch(r"[A-Za-z0-9_]{1,25}", login):
                    raise RuntimeError("Twitch 关注列表缺少有效频道名，请稍后重试")
                rid = self.normalize("twitch:" + login)
                if rid not in seen:
                    seen.add(rid)
                    rooms.append(api.RoomInfo(room_id=rid, uname=item.get("broadcaster_name") or login,
                                  platform=self.kind, extra={"live_known": False,
                                  "playback_mode": self.playback_mode}).as_dict())
            cursor = payload.get("pagination", {}).get("cursor", "")
            if not cursor:
                return rooms
            if cursor in cursors:
                raise RuntimeError("Twitch 关注分页没有前进，请稍后重试")
            cursors.add(cursor)
        return []

    def normalize(self, room_id):
        text = str(room_id or "").strip()
        raw = text[7:] if text.startswith("twitch:") else text
        if "://" in raw:
            parts = official_url(raw, self.hosts)
            if parts.hostname == "player.twitch.tv":
                query = parse_qs(parts.query)
                channels = query.get("channel", [])
                if len(channels) != 1 or "video" in query:
                    raise ValueError("请使用 Twitch 频道直播链接")
                raw = channels[0]
            else:
                raw = parts.path.strip("/")
        if (not re.fullmatch(r"[A-Za-z0-9_]{1,25}", raw) or
                raw.lower() in ("directory", "videos", "downloads", "settings", "subscriptions", "inventory", "search")):
            raise ValueError("请填写 twitch:频道名，或 Twitch 官方频道链接")
        return "twitch:" + raw.lower()

    def room_url(self, room_id):
        return self.origin + self.normalize(room_id).split(":", 1)[1]

    def room_info(self, room_id):
        canonical = self.normalize(room_id)
        query = """query($login:String!){user(login:$login){id login displayName
            profileImageURL(width:300) lastBroadcast{title}
            stream{id type previewImageURL(width:640,height:360)}}}"""
        try:
            with public_request("post", "https://gql.twitch.tv/gql", timeout=(4, 8),
                    headers={"Client-ID": TwitchAPI.CLIENT_ID, "User-Agent": USER_AGENT},
                    json={"query": query, "variables": {"login": canonical.split(":", 1)[1]}}) as response:
                payload = response.json()
            if payload.get("errors"):
                raise ValueError("Twitch query rejected")
            user = payload["data"]["user"]
            if not isinstance(user, dict) or "stream" not in user:
                raise ValueError("Missing Twitch channel")
            stream = user["stream"] or {}
            return api.RoomInfo(room_id=canonical, uname=user["displayName"],
                title=(user.get("lastBroadcast") or {}).get("title", "Twitch 直播"),
                live=stream.get("type") == "live", platform=self.kind,
                face=image_url(user.get("profileImageURL"), ("jtvnw.net",)),
                cover_url=image_url(stream.get("previewImageURL"), ("jtvnw.net",)),
                extra={"playback_mode": self.playback_mode, "live_known": True})
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            raise RuntimeError("Twitch 频道信息获取失败，请检查频道名或稍后重试") from error
