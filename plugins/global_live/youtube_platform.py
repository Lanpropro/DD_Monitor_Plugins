"""youtube platform: extracted platform responsibility."""
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit
import requests
from streamlink.plugins.youtube import YouTube
from ddm import plugins as api
from ddm.global_danmaku import GlobalDanmakuClient, page_json, renderers
from ddm.live_danmaku import USER_AGENT
from .public_live import PublicLivePlatform, official_url, public_request, image_url, thumbnail

class LiveYouTube(YouTube):
    def _get_res(self, url):
        response = super()._get_res(url)
        player = page_json(response.text, "ytInitialPlayerResponse")
        details = player.get("videoDetails", {})
        broadcast = player.get("microformat", {}).get("playerMicroformatRenderer", {}).get("liveBroadcastDetails", {})
        if details.get("isLiveContent") is False or not (details.get("isLive") or broadcast.get("isLiveNow")):
            raise ValueError("YouTube broadcast is not currently live")
        return response


class YouTubePlatform(PublicLivePlatform):
    kind = "youtube"
    label = "YouTube"
    room_input_hint = "YouTube 视频或直播链接"
    hosts = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
    origin = "https://www.youtube.com/"
    parser = LiveYouTube
    image_hosts = ("ytimg.com", "ggpht.com", "googleusercontent.com")

    oauth_provider = "youtube"
    browser_login_url = "https://www.youtube.com/"
    browser_cookie_domains = ("youtube.com", "google.com")

    def browser_account_info(self, session, cancelled):
        from . import youtube_accounts as web_accounts
        return web_accounts.youtube_account(session, cancelled)

    def browser_follow_rooms(self, session, cancelled):
        from . import youtube_accounts as web_accounts
        return web_accounts.youtube_follows(session, cancelled)

    def account_info(self, session, cancelled):
        if cancelled():
            return {}
        with session.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=(4, 8)) as response:
            if response.status_code == 401:
                raise RuntimeError("Google 登录已过期，请重新授权")
            response.raise_for_status()
            user = response.json()
        uid = str(user.get("sub") or "")
        if not uid or not user.get("name"):
            raise RuntimeError("Google 未返回有效用户身份，请重新授权")
        return {"uid": uid, "uname": user["name"],
                "face": image_url(user.get("picture"), self.image_hosts)}

    def follow_rooms(self, session, cancelled):
        rooms, cursors = {}, set()
        params = {"part": "snippet", "mine": "true", "maxResults": 50}
        while not cancelled():
            with session.get("https://www.googleapis.com/youtube/v3/subscriptions", params=params,
                    timeout=(4, 8)) as response:
                if response.status_code in (401, 403):
                    from .youtube_account_errors import subscription_error
                    raise RuntimeError(subscription_error(response))
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload.get("items"), list):
                raise RuntimeError("YouTube 订阅列表响应异常，请稍后重试")
            for item in payload["items"]:
                if cancelled():
                    return []
                snippet = item.get("snippet") or {}
                channel = snippet.get("resourceId", {}).get("channelId", "")
                if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel):
                    raise RuntimeError("YouTube 订阅缺少有效频道 ID，请稍后重试")
                rid = self.normalize("youtube:" + channel)
                images = snippet.get("thumbnails") or {}
                face = next((url for key in ("high", "medium", "default") if (
                    url := image_url((images.get(key) or {}).get("url"), self.image_hosts))), "")
                rooms[rid] = api.RoomInfo(room_id=rid, platform=self.kind,
                    uname=snippet.get("title") or channel, face=face,
                    extra={"live_known": False, "playback_mode": "stream"}).as_dict()
            cursor = payload.get("nextPageToken")
            if not cursor:
                return list(rooms.values())
            if not isinstance(cursor, str) or cursor in cursors:
                raise RuntimeError("YouTube 订阅分页没有前进，请稍后重试")
            cursors.add(cursor)
            params["pageToken"] = cursor
        return []

    def normalize(self, room_id):
        text = str(room_id or "").strip()
        raw = text[8:] if text.startswith("youtube:") else text
        if "://" in raw:
            parts = official_url(raw, self.hosts)
            path = unquote(parts.path).strip("/").split("/")
            if parts.hostname == "youtu.be" and len(path) == 1:
                raw = path[0]
            elif path == ["watch"]:
                values = parse_qs(parts.query).get("v", [])
                raw = values[0] if len(values) == 1 else ""
            elif len(path) == 2 and path[0] in ("live", "embed", "shorts") and re.fullmatch(r"[A-Za-z0-9_-]{11}", path[1]):
                raw = path[1]
            elif path[0] == "channel" and len(path) in (2, 3) and re.fullmatch(r"UC[A-Za-z0-9_-]{22}", path[1]) and (
                    len(path) == 2 or path[2] in ("live", "streams")):
                raw = path[1]
            elif path[0].startswith("@") and (len(path) == 1 or (len(path) == 2 and path[1] in ("live", "streams"))):
                raw = path[0]
            else:
                raw = ""
        valid = re.fullmatch(r"[A-Za-z0-9_-]{11}|UC[A-Za-z0-9_-]{22}", raw)
        handle = raw.startswith("@") and 2 <= len(raw) <= 101 and not re.search(r"[\s/:?#%\\]", raw)
        if not valid and not handle:
            raise ValueError("请使用 YouTube 直播视频、@频道或 /channel/ 频道链接")
        return "youtube:" + raw

    def room_url(self, room_id):
        raw = self.normalize(room_id).split(":", 1)[1]
        if raw.startswith("@"):
            return self.origin + quote(raw, safe="@") + "/live"
        if raw.startswith("UC") and len(raw) == 24:
            return self.origin + "channel/" + raw + "/live"
        return self.origin + "watch?v=" + raw

    def room_info(self, room_id):
        canonical = self.normalize(room_id)
        try:
            with public_request("get", self.room_url(canonical), params={"hl": "en"},
                    headers={"User-Agent": USER_AGENT}, timeout=(4, 8)) as response:
                page = response.text
            player = page_json(page, "ytInitialPlayerResponse")
            data = page_json(page, "ytInitialData")
            details = player.get("videoDetails", {})
            if not details:
                channel = next(renderers(data, "channelMetadataRenderer"), {})
                if not channel.get("title") or not canonical.split(":", 1)[1].startswith(("@", "UC")):
                    raise ValueError("Missing YouTube live channel")
                return api.RoomInfo(room_id=canonical, uname=channel["title"], platform=self.kind,
                    face=thumbnail(channel.get("avatar", {}), self.image_hosts),
                    extra={"playback_mode": self.playback_mode, "live_known": True})
            broadcast = player.get("microformat", {}).get("playerMicroformatRenderer", {}).get("liveBroadcastDetails", {})
            live = bool(details.get("isLive") or broadcast.get("isLiveNow"))
            if details.get("isLiveContent") is False or not (
                    details.get("isLiveContent") or live or broadcast.get("startTimestamp")):
                raise ValueError("This video is not a live broadcast")
            owner = next((item for item in renderers(data, "videoOwnerRenderer") if
                item.get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId") == details.get("channelId")), {})
            return api.RoomInfo(room_id=canonical, uname=details["author"], title=details["title"],
                live=live, platform=self.kind,
                face=thumbnail(owner.get("thumbnail", {}), self.image_hosts),
                cover_url=thumbnail(details.get("thumbnail", {}), self.image_hosts),
                extra={"playback_mode": self.playback_mode, "live_known": True})
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            raise RuntimeError("YouTube 直播信息获取失败；请使用公开直播或频道链接") from error
