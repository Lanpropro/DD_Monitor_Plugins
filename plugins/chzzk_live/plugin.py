"""Independent platform entry; shared source is embedded by build_plugins.py."""
import os
__path__ = [os.path.dirname(__file__)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__
from .platform_single import SinglePlatformPlugin
from .chzzk import ChzzkPlatform

class PlatformPlugin(SinglePlatformPlugin):
    platform_factory = ChzzkPlatform

plugin = PlatformPlugin()
