"""One public platform, independently enabled and released."""
from PySide6.QtWidgets import QApplication
from ddm import plugins as api
from .platform_logo import PlatformLogoControl

class SinglePlatformPlugin(api.Plugin):
    platform_factory = None

    def on_load(self, context):
        if not callable(getattr(context, 'platform_replaced', None)):
            raise RuntimeError('独立平台插件需要支持平台迁移的0.7.3开发版')
        platform = self.platform_factory()
        context.register_platform(platform)
        if QApplication.instance() is not None and context.window is not None:
            self._logo_control = PlatformLogoControl(context, (platform.kind,), '显示' + platform.label + ' Logo')

    def on_unload(self):
        if getattr(self, '_logo_control', None) is not None:
            self._logo_control.detach()
            self._logo_control = None
