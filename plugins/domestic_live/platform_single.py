"""One platform owns its interaction controls, diagnostics and lifecycle."""
from PySide6.QtWidgets import QApplication
from ddm import plugins as api
from .platform_logo import PlatformLogoControl
from .playback_diagnostics import PlaybackDiagnostics

class SinglePlatformPlugin(api.Plugin):
    platform_factory = None

    def on_load(self, context):
        if not callable(getattr(context, 'platform_replaced', None)):
            raise RuntimeError('独立平台插件需要支持平台迁移的0.7.3开发版')
        self.platform = self.platform_factory()
        self._owned_kinds = (self.platform.kind,)
        if self.platform.kind == 'douyin':
            profiles = context.setting('douyin_follow_profiles', {})
            if isinstance(profiles, dict):
                self.platform.restore_follow_rooms([{'anchor_uid': uid, 'sec_uid': sec} for uid, sec in profiles.items()])
            owners = context.setting('douyin_room_owners', {})
            if isinstance(owners, dict):
                self.platform.restore_follow_rooms([{'room_id': rid, 'anchor_uid': uid} for rid, uid in owners.items()])
        self.platform._context = context
        context.register_platform(self.platform)
        self._diagnostic_enabled = True
        self._diagnostic_players = {}
        self._diagnostics = PlaybackDiagnostics(self)
        self._diagnostics._enable_diagnostics(context, self.platform)
        if callable(getattr(context, 'register_tile_control', None)) and hasattr(api, 'TileControl'):
            from .douyu_composer import DouyuInteraction, DouyuComposer, HuyaComposer, DouyinComposer
            composer = {'huya': HuyaComposer, 'douyu': DouyuComposer, 'douyin': DouyinComposer}[self.platform.kind]
            self._interaction = DouyuInteraction(context, self.platform, composer_class=composer,
                                                  control_id=self.platform.kind + '_interaction')
        if QApplication.instance() is not None and context.window is not None:
            self._logo_control = PlatformLogoControl(context, self._owned_kinds, '显示' + self.platform.label + ' Logo')

    def on_event(self, event, payload):
        if event == api.EVENT_CLOSING:
            if getattr(self, '_interaction', None) is not None:
                self._interaction.shutdown()
            if self.platform.kind == 'douyin':
                self.platform.restore_follow_rooms([])
        if event == api.EVENT_STREAM_FAILED and payload.get('source_url'):
            room = str((payload.get('room') or {}).get('room_id') or '')
            if room.startswith(self.platform.kind + ':') and self.platform.kind in ('huya', 'douyu'):
                self.platform.report_playback_failure(room, payload['source_url'], payload.get('reason'))
        self._diagnostics.on_event(event, payload)

    def on_unload(self):
        if getattr(self, '_interaction', None) is not None:
            self._interaction.shutdown()
            self._interaction = None
        for tile in list(getattr(self, '_diagnostic_players', {})):
            self._diagnostics._detach_player_diagnostic(tile)
        self._diagnostic_enabled = False
        if getattr(self, '_logo_control', None) is not None:
            self._logo_control.detach()
            self._logo_control = None
