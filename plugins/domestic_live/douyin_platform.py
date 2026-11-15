"""Douyin platform wiring and room identity; feature state has one owner."""
from pathlib import Path

from .platform_base import NumericLivePlatform
from .douyin_errors import DouyinLookupError, DouyinVerificationRequired  # Legacy entry exports.
from .douyin_accounts import DouyinAccounts
from .douyin_rooms import DouyinRooms
from .douyin_playback import DouyinPlayback


class DouyinPlatform(DouyinAccounts, DouyinRooms, DouyinPlayback, NumericLivePlatform):
    kind = "douyin"
    interaction_user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/130.0.0.0 Safari/537.36")
    share_hosts = ("v.douyin.com", "webcast.amemv.com")
    label = "抖音"
    room_input_hint = "抖音直播间链接"
    profile_browser_init_script = (Path(__file__).parent / "douyin_profile.js").read_text(encoding="utf-8")
    room_browser_init_script = (Path(__file__).parent / "douyin_room.js").read_text(encoding="utf-8")
    hosts = ("live.douyin.com", "douyin.com", "www.douyin.com")
    image_hosts = ("douyinpic.com", "byteimg.com", "ibytedtos.com", "douyincdn.com")

    def __init__(self):
        super().__init__()
        from .douyin_interaction import BroadcastReceipts
        self._chat_delivery = BroadcastReceipts()
        self._anchor_uids = {}
        self._follow_profiles = {}
        self._follow_faces = {}
        self._profile_additions = {}
        self._context = None

    def normalize(self, room_id):
        text = str(room_id or "").strip()
        uid = self._follow_reference_uid(text)
        if uid:
            return next((rid for rid, owner in list(self._anchor_uids.items()) if owner == uid), text)
        return super().normalize(text)

    def room_url(self, room_id):
        canonical = self.normalize(room_id)
        uid = self._follow_reference_uid(canonical)
        if uid:
            sec_uid = self._follow_profiles.get(uid)
            if not sec_uid:
                raise ValueError("账号主页资料尚未读取，请重新读取关注")
            return "https://www.douyin.com/user/" + sec_uid
        return super().room_url(canonical)

    def danmaku_client(self, room_id, parent=None):
        client = super().danmaku_client(room_id, parent)
        canonical = self.normalize(room_id)
        client.message.connect(lambda event: self._chat_delivery.observe(canonical, event))
        return client
