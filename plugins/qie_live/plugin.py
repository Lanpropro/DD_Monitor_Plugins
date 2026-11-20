"""Independent Qie TV platform entry."""
from pathlib import Path

from ddm import plugins as api


# The host loads the entry file as a module, with no package by default.
__path__ = [str(Path(__file__).parent)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

from .platform import QiePlatform


class QiePlugin(api.Plugin):
    def on_load(self, context):
        context.register_platform(QiePlatform(context))


plugin = QiePlugin()
