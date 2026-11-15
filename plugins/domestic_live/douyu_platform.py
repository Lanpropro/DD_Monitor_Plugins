"""Douyu platform wiring and room identity; feature state has one owner."""
import re
import threading

import requests
from ddm import plugins as api
from .platform_base import NumericLivePlatform
from .douyu_accounts import DouyuAccounts
from .douyu_playback import DouyuPlayback


class DouyuPlatform(DouyuAccounts, DouyuPlayback, NumericLivePlatform):
    kind = "douyu"
    share_hosts = ("s.douyu.com",)
    label = "斗鱼"
    room_input_hint = "斗鱼直播间链接"
    hosts = ("www.douyu.com", "douyu.com", "m.douyu.com")
    image_hosts = ("douyucdn.cn", "douyu.com")

    def __init__(self):
        super().__init__()
        self._failed_cdns = {}
        self._source_cdns = {}
        self._cdn_lock = threading.Lock()

    def danmaku_client(self, room_id: str, parent=None):
        from .douyu_danmaku import DouyuDanmakuClient
        return DouyuDanmakuClient(room_id, self, parent)

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
