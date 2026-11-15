"""CHZZK public channel metadata and live HLS; no account or rewind session."""
import re

import requests
from streamlink.plugins.chzzk import Chzzk
from streamlink.stream.hls import HLSStream

from ddm import plugins as api
from ddm.live_danmaku import USER_AGENT
from .public_live import PublicLivePlatform
from .public_http import image_url, official_url, public_request


class LiveChzzk(Chzzk):
    def _get_live(self, channel_id):
        result, data = self._api.get_live_detail(channel_id)
        if result != "success":
            raise RuntimeError("CHZZK 直播信息获取失败")
        if data is None or data[1] != "OPEN":
            raise RuntimeError("CHZZK 频道尚未开播")
        media, _, self.id, self.author, self.category, self.title, adult, _ = data
        if media is None:
            if adult:
                raise api.LoginRequiredError("CHZZK 此直播需要登录及年龄验证，请在官网观看")
            raise RuntimeError("CHZZK 未提供公开直播流")
        # The host consumes URLs, not Streamlink readers. Rewind streams require
        # reader-specific segment signing; use the live HLS even with time machine enabled.
        for media_id, protocol, path in media:
            if media_id == "HLS" and protocol == "HLS":
                yield from HLSStream.parse_variant_playlist(self.session, path).items()
                return


class ChzzkPlatform(PublicLivePlatform):
    kind = "chzzk"
    label = "CHZZK"
    room_input_hint = "CHZZK 频道或直播链接"
    hosts = ("chzzk.naver.com",)
    origin = "https://chzzk.naver.com/"
    parser = LiveChzzk
    image_hosts = ("pstatic.net", "naver.net", "livecloud-thumb.akamaized.net")

    def normalize(self, room_id):
        text = str(room_id or "").strip()
        raw = text[6:] if text.startswith("chzzk:") else text
        if "://" in raw:
            parts = official_url(raw, self.hosts)
            match = re.fullmatch(r"/(?:live/)?([a-fA-F0-9]{32})/?", parts.path)
            raw = match[1] if match else ""
        if not re.fullmatch(r"[a-fA-F0-9]{32}", raw):
            raise ValueError("请填写 CHZZK 官方频道／直播链接，或 chzzk:32位频道ID")
        return "chzzk:" + raw.lower()

    def room_url(self, room_id):
        return self.origin + "live/" + self.normalize(room_id).split(":", 1)[1]

    def _content(self, path):
        with public_request("get", "https://api.chzzk.naver.com/service/" + path,
                headers={"User-Agent": USER_AGENT, "Referer": self.origin}, timeout=(4, 8)) as response:
            payload = response.json()
        if payload.get("code") != 200:
            raise ValueError("CHZZK API rejected the request")
        return payload.get("content")

    def room_info(self, room_id):
        canonical = self.normalize(room_id)
        channel_id = canonical.split(":", 1)[1]
        try:
            channel = self._content("v1/channels/" + channel_id)
            if not isinstance(channel, dict) or channel.get("channelId") != channel_id:
                raise ValueError("Missing CHZZK channel")
            detail = self._content("v2/channels/" + channel_id + "/live-detail")
            if detail is not None and (not isinstance(detail, dict) or
                    detail.get("status") not in ("OPEN", "CLOSE")):
                raise ValueError("Invalid CHZZK live status")
            detail = detail or {}
            cover = str(detail.get("liveImageUrl") or detail.get("defaultThumbnailImageUrl") or "")
            return api.RoomInfo(room_id=canonical, uname=channel.get("channelName") or channel_id,
                title=detail.get("liveTitle") or "CHZZK 直播", live=detail.get("status") == "OPEN",
                viewers=str(detail.get("concurrentUserCount") or ""), platform=self.kind,
                face=image_url(channel.get("channelImageUrl"), self.image_hosts),
                cover_url=image_url(cover.replace("{type}", "480"), self.image_hosts),
                extra={"playback_mode": self.playback_mode, "live_known": True})
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
            raise RuntimeError("CHZZK 频道信息获取失败，请检查频道链接或稍后重试") from error

    def danmaku_client(self, room_id, parent=None):
        return None
