"""youtube platform: extracted platform responsibility."""
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit
import requests
from streamlink.plugins.youtube import YouTube
from ddm import plugins as api
from ddm.global_danmaku import GlobalDanmakuClient, page_json, renderers
from ddm.live_danmaku import USER_AGENT
from .public_live import PublicLivePlatform
from .public_http import official_url, public_request, thumbnail

class LiveYouTube(YouTube):
    def _get_res(self, url):
        response = super()._get_res(url)
        player = page_json(response.text, "ytInitialPlayerResponse")
        details = player.get("videoDetails", {})
        broadcast = player.get("microformat", {}).get("playerMicroformatRenderer", {}).get("liveBroadcastDetails", {})
        if details.get("isLiveContent") is False or not (details.get("isLive") or broadcast.get("isLiveNow")):
            raise ValueError("YouTube broadcast is not currently live")
        return response


from .youtube_accounts import YouTubeAccounts


class YouTubePlatform(YouTubeAccounts, PublicLivePlatform):
    kind = "youtube"
    label = "YouTube"
    room_input_hint = "YouTube 视频或直播链接"
    hosts = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
    origin = "https://www.youtube.com/"
    parser = LiveYouTube
    image_hosts = ("ytimg.com", "ggpht.com", "googleusercontent.com")

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
