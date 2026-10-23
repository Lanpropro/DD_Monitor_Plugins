"""Bilibili interaction entry using public host hooks only."""
import os
import weakref
from ddm import plugins as api

__path__ = [os.path.dirname(__file__)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

class BiliLive(api.Plugin):
    def on_load(self, context):
        required = ('register_tile_control', 'credentials', 'update_credentials')
        if not all(callable(getattr(context, name, None)) for name in required) or not hasattr(api, 'TileControl'):
            raise RuntimeError('bili_live 需要支持格子控件扩展与登录快照的 DD Monitor CE 配套构建')
        from .session import BiliSession
        self.context = context
        self.session = BiliSession(context)
        from .gift_controller import GiftCoordinator
        self.coordinator = GiftCoordinator()
        self.controls = weakref.WeakSet()
        context.register_tile_control('interaction', self._make_control, priority=100)

    def _make_control(self, context):
        from .composer import DanmakuComposer
        from .notifications import NotificationReporter
        class BiliControl(api.TileControl):
            def set_room(self, room):
                self.widget.set_room(room.get('room_id') if room.get('platform', 'bilibili') == 'bilibili' else '',room=room)
                context.invalidate_layout()
            def interaction_active(self):
                return self.widget.interaction_active()
            def shutdown(self):
                self.widget.shutdown()
        control = BiliControl(DanmakuComposer(self.session, context.parent, coordinator=self.coordinator, notifier=NotificationReporter(self.context)), minimum_width=90, preferred_width=240)
        self.controls.add(control)
        return control

    def on_event(self, event, payload):
        if event == 'app.closing':
            self._close_controls()
        if hasattr(self, 'session'):
            self.session.on_event(event, payload)

    def _close_controls(self):
        for control in tuple(getattr(self, 'controls', ())):
            try:
                control.shutdown()
            except RuntimeError:
                pass

    def on_unload(self):
        self._close_controls()
        from .workers import wait_for_requests
        wait_for_requests()

plugin = BiliLive()
