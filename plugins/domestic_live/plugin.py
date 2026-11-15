"""Domestic platform composition and lifecycle; implementations live in feature modules."""
import os
from PySide6.QtWidgets import QApplication
from ddm import plugins as api

__path__ = [os.path.dirname(__file__)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

from .platform_base import LiveQualityPlatform, NumericLivePlatform
from .huya_protocol import huya_string, huya_user, huya_wup_request, huya_follow_request
from .huya_platform import HuyaPlatform
from .douyu_platform import DouyuPlatform
from .douyin_platform import DouyinLookupError, DouyinVerificationRequired, DouyinPlatform
from .platform_logo import PlatformLogoControl
from .playback_diagnostics import PlaybackDiagnostics


class LivePlatformsPlugin(api.Plugin):
    _owned_kinds = ("huya", "douyu", "douyin")
    _diagnostic_reason = staticmethod(PlaybackDiagnostics._diagnostic_reason)
    _player_error_category = staticmethod(PlaybackDiagnostics._player_error_category)

    def _playback_diagnostics(self):
        if not hasattr(self, "_diagnostics"):
            self._diagnostics = PlaybackDiagnostics(self)
        return self._diagnostics

    def _enable_diagnostics(self, context, platform):
        return self._playback_diagnostics()._enable_diagnostics(context, platform)

    def _detach_player_diagnostic(self, tile):
        return self._playback_diagnostics()._detach_player_diagnostic(tile)

    def on_event(self, event, payload):
        if event == api.EVENT_CLOSING and getattr(self, "_douyin_interaction", None) is not None:
            self._douyin_interaction.shutdown()
        if event == api.EVENT_CLOSING and getattr(self, "_huya_interaction", None) is not None:
            self._huya_interaction.shutdown()
        if event == api.EVENT_CLOSING and getattr(self, "_douyu_interaction", None) is not None:
            self._douyu_interaction.shutdown()
        if event == api.EVENT_STREAM_FAILED and self.context is not None and payload.get("source_url"):
            room = str((payload.get("room") or {}).get("room_id") or "")
            if room.split(":", 1)[0] in self._owned_kinds and room.startswith(("huya:", "douyu:")):
                self.context.platform(room.split(":", 1)[0]).report_playback_failure(
                    room, payload["source_url"], payload.get("reason"))
        if event == api.EVENT_CLOSING and self.context is not None:
            platform = self.context.platform("douyin") if "douyin" in self._owned_kinds else None
            if platform is not None:
                # 关闭事件位于主线程；持久化已确认的映射，不在网络线程读取界面或写配置。
                platform.restore_follow_rooms([])
        return self._playback_diagnostics().on_event(event, payload)

    def on_load(self, context: api.PluginContext) -> None:
        huya = None if getattr(context, "platform_replaced", lambda _kind: False)("huya") is True else HuyaPlatform()
        if huya is not None:
            huya._context = context
            context.register_platform(huya)
        douyu = None if getattr(context, "platform_replaced", lambda _kind: False)("douyu") is True else DouyuPlatform()
        if douyu is not None:
            douyu._context = context
            context.register_platform(douyu)
        if callable(getattr(context, "register_tile_control", None)) and hasattr(api, "TileControl"):
            from .douyu_composer import DouyuInteraction, HuyaComposer
            if douyu is not None:
                self._douyu_interaction = DouyuInteraction(context, douyu)
            if huya is not None:
                self._huya_interaction = DouyuInteraction(context, huya, composer_class=HuyaComposer, control_id="huya_interaction")
        douyin = None if getattr(context, "platform_replaced", lambda _kind: False)("douyin") is True else DouyinPlatform()
        if douyin is not None:
            profiles = context.setting("douyin_follow_profiles", {})
            if isinstance(profiles, dict):
                douyin.restore_follow_rooms([{"anchor_uid": uid, "sec_uid": sec_uid} for uid, sec_uid in profiles.items()])
            saved = context.setting("douyin_room_owners", {})
            if isinstance(saved, dict):
                douyin.restore_follow_rooms([{"room_id": rid, "anchor_uid": uid} for rid, uid in saved.items()])
            douyin._context = context
            context.register_platform(douyin)
            if callable(getattr(context, "register_tile_control", None)) and hasattr(api, "TileControl"):
                from .douyu_composer import DouyinComposer
                self._douyin_interaction = DouyuInteraction(context, douyin, composer_class=DouyinComposer, control_id="douyin_interaction")
        self._diagnostic_enabled = True
        self._diagnostic_players = {}
        active = [platform for platform in (huya, douyu, douyin) if platform is not None]
        self._owned_kinds = tuple(platform.kind for platform in active)
        for platform in active:
            self._enable_diagnostics(context, platform)
        context.log("[平台诊断] 已启用；记录失败响应特征与播放器错误类别，不记录正文、凭证或请求地址")
        if active and QApplication.instance() is not None and context.window is not None:
            self._logo_control = PlatformLogoControl(context, (("bilibili",) + self._owned_kinds if len(active) == 3 else self._owned_kinds), "显示国内平台 Logo")

    def on_unload(self):
        if getattr(self, "_douyin_interaction", None) is not None:
            self._douyin_interaction.shutdown()
            self._douyin_interaction = None
        if getattr(self, "_huya_interaction", None) is not None:
            self._huya_interaction.shutdown()
            self._huya_interaction = None
        if getattr(self, "_douyu_interaction", None) is not None:
            self._douyu_interaction.shutdown()
            self._douyu_interaction = None
        for tile in list(getattr(self, "_diagnostic_players", {})):
            self._detach_player_diagnostic(tile)
        self._diagnostic_enabled = False
        if getattr(self, "_logo_control", None) is not None:
            self._logo_control.detach()
            self._logo_control = None


plugin = LivePlatformsPlugin()
