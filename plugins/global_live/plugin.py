"""Overseas platform composition; legacy class exports remain available."""
import os
from PySide6.QtWidgets import QApplication
from ddm import plugins as api

__path__ = [os.path.dirname(__file__)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

from .public_live import PublicLivePlatform, public_request, official_url, image_url, thumbnail
from .twitch_platform import TwitchPlatform
from .youtube_platform import LiveYouTube, YouTubePlatform
from .platform_logo import PlatformLogoControl

class GlobalLivePlugin(api.Plugin):
    def on_load(self, context):
        context.register_platform(TwitchPlatform())
        context.register_platform(YouTubePlatform())
        from .chzzk import ChzzkPlatform
        context.register_platform(ChzzkPlatform())
        if QApplication.instance() is not None and context.window is not None:
            self._logo_control = PlatformLogoControl(context, ("twitch", "youtube", "chzzk"), "显示海外平台 Logo")

    def on_unload(self):
        if getattr(self, "_logo_control", None) is not None:
            self._logo_control.detach()
            self._logo_control = None


plugin = GlobalLivePlugin()
