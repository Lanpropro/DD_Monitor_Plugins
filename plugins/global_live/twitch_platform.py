"""twitch platform: extracted platform responsibility."""
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit
import requests
from streamlink.plugins.twitch import Twitch, TwitchAPI
from ddm import plugins as api
from ddm.live_danmaku import USER_AGENT
from .public_live import PublicLivePlatform, official_url, public_request, image_url
from .twitch_accounts import TwitchAccounts


class TwitchPlatform(TwitchAccounts, PublicLivePlatform):
    kind = "twitch"
    label = "Twitch"
    room_input_hint = "Twitch 频道链接"
    hosts = ("twitch.tv", "www.twitch.tv", "m.twitch.tv", "player.twitch.tv")
    origin = "https://www.twitch.tv/"
    parser = Twitch

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
