"""Host control registration, request retirement and plugin shutdown ownership."""
import weakref
from PySide6.QtCore import Qt
from ddm import plugins as api
from .interaction_composers import DouyuComposer


class DouyuInteraction:
    def __init__(self, context, platform, *, composer_class=DouyuComposer, control_id="douyu_interaction"):
        self.context, self.platform = context, platform
        self.composer_class = composer_class
        self.controls = weakref.WeakSet()
        self.requests = set()
        self.sending = False
        context.register_tile_control(control_id, self.make_control, priority=100)

    def make_control(self, context):
        class Control(api.TileControl):
            def set_room(self, room):
                self.widget.set_room(room)
                context.invalidate_layout()

            def interaction_active(self):
                return self.widget.interaction_active()

            def focus_input(self):
                if not self.widget.isVisible() or not self.widget.isEnabled():
                    return False
                self.widget.edit.setFocus(Qt.ShortcutFocusReason)
                return True

            def shutdown(self):
                self.widget.shutdown()

        widget = self.composer_class(self, context.parent)
        self.controls.add(widget)
        return Control(widget, minimum_width=90, preferred_width=240)

    def retire(self, request):
        request.wait()
        self.requests.discard(request)
        request.deleteLater()

    def shutdown(self):
        for widget in tuple(self.controls):
            widget.shutdown()
        for request in tuple(self.requests):
            request.cancelled.set()
        for request in tuple(self.requests):
            request.wait()
        self.requests.clear()
