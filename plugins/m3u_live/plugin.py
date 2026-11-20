"""Independent playlist platform entry."""
from pathlib import Path

from ddm import plugins as api


__path__ = [str(Path(__file__).parent)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

from .platform import PlaylistPlatform


class PlaylistPlugin(api.Plugin):
    def on_load(self, context):
        if not hasattr(api, 'RoomList'):
            raise RuntimeError('请更新到支持插件频道列表导入的 DD 监控室 CE')
        context.register_platform(PlaylistPlatform(context))


plugin = PlaylistPlugin()
