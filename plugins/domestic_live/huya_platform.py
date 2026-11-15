"""Huya platform wiring and room identity; feature state has one owner."""
import json
import re
import threading
from urllib.parse import urlsplit

import requests
from ddm import plugins as api
from .platform_base import LiveQualityPlatform
from .huya_accounts import HuyaAccounts
from .huya_playback import HuyaPlayback


class HuyaPlatform(HuyaAccounts, HuyaPlayback, LiveQualityPlatform):
    kind = "huya"
    share_hosts = ("hy.fan",)
    hosts = ("www.huya.com", "huya.com", "m.huya.com")
    label = "虎牙"
    room_input_hint = "虎牙直播间链接"
    playback_mode = "stream"

    def __init__(self):
        super().__init__()
        self._failed_cdns = {}
        self._cdn_lock = threading.Lock()

    def matches(self, room_id: str) -> bool:
        text = str(room_id or "").strip()
        if text.startswith("huya:"):
            return True
        try:
            parts = urlsplit(text)
            return parts.scheme in ("http", "https") and parts.hostname in (*self.hosts, *self.share_hosts)
        except ValueError:
            return False

    def normalize(self, room_id: str) -> str:
        text = str(room_id or "").strip()
        if share := self._share_url(text):
            return share
        raw = text[5:] if text.startswith("huya:") else text
        if "://" in raw:
            parts = urlsplit(raw)
            if (parts.scheme not in ("http", "https") or
                    parts.hostname not in self.hosts or
                    parts.username or parts.password or parts.port not in (None, 80, 443)):
                raise ValueError("请使用虎牙官方直播间链接")
            raw = parts.path.strip("/")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", raw):
            raise ValueError("请填写 huya:房间号，或虎牙官方直播间链接")
        return f"huya:{raw}"

    def room_info(self, room_id: str) -> api.RoomInfo:
        room_id, _share = self._resolve_share_input(room_id)
        canonical = self.normalize(room_id)
        raw = canonical.split(":", 1)[1]
        try:
            with requests.get(self.room_url(canonical), headers={
                    "User-Agent": "Mozilla/5.0", "Referer": "https://www.huya.com/"},
                    timeout=(4, 8)) as response:
                response.raise_for_status()
                page = response.text
            def page_data(name):
                match = re.search(r"\b" + name + r"\s*=\s*", page)
                if match is None:
                    raise ValueError("Missing room data")
                data, _end = json.JSONDecoder().raw_decode(page[match.end():].lstrip())
                if not isinstance(data, dict):
                    raise ValueError("Invalid room data")
                return data

            room = page_data("TT_ROOM_DATA")
            profile = page_data("TT_PROFILE_INFO")
            if not isinstance(room.get("isOn"), bool):
                raise ValueError("Missing live status")

            def image_url(value):
                url = str(value or "")
                if url.startswith("//"):
                    url = "https:" + url
                parts = urlsplit(url)
                host = parts.hostname or ""
                if (parts.scheme in ("http", "https") and not parts.username and
                        not parts.password and (host == "msstatic.com" or host.endswith(".msstatic.com"))):
                    return url
                return ""

            return api.RoomInfo(
                room_id=canonical, uname=profile.get("nick") or f"虎牙 · {raw}",
                title=room.get("introduction") or "虎牙直播间", live=room["isOn"], platform=self.kind,
                face=image_url(profile.get("avatar")), cover_url=image_url(room.get("screenshot")),
                extra={"playback_mode": self.playback_mode, "live_known": True},
            )
        except (requests.RequestException, ValueError) as error:
            raise RuntimeError("虎牙房间信息获取失败，请稍后重试") from error

    def rooms_status(self, room_ids: list) -> dict:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        result = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self.room_info, rid): rid for rid in room_ids}
            for future in as_completed(futures):
                try:
                    result[futures[future]] = future.result().as_dict()
                except Exception:  # noqa: BLE001
                    continue                    # 请求失败保留旧状态，不误报下播
        if room_ids and not result:
            raise RuntimeError("虎牙状态获取失败")
        return result

    def room_url(self, room_id: str) -> str:
        canonical = self.normalize(room_id)
        return f"https://www.huya.com/{canonical.split(':', 1)[1]}"
