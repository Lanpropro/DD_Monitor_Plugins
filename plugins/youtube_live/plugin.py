"""Independent platform entry; shared source is embedded by build_plugins.py."""
import os
__path__ = [os.path.dirname(__file__)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__
from .platform_single import SinglePlatformPlugin
from .youtube_playback import YouTubePlayback

class PlatformPlugin(SinglePlatformPlugin):
    def on_load(self, context):
        self.platform_factory = lambda: YouTubePlayback(context)
        super().on_load(context)

plugin = PlatformPlugin()
