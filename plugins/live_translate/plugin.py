"""DD Monitor CE entry points; one active stream, local recognition by demand."""
from dataclasses import asdict, replace
import os
import weakref
import json

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QToolButton
from ddm.plugins import Plugin

__path__ = [os.path.dirname(__file__)]
__package__ = __name__

from .caption_style import CaptionStyle
from .options import with_bundled_defaults
from .panel import Captions, Panel
from .secret_store import protect, unprotect
from .session import Session


class LiveTranslate(Plugin):
    name = '实时翻译'
    description = '本地识别直播音轨，显示原文与翻译字幕'
    version = '0.1'

    def __init__(self):
        super().__init__()
        self.sources = weakref.WeakKeyDictionary()
        self.session = self.panel = self.captions = None
        self.caption_style = CaptionStyle()
        self._last_caption = None

    def on_load(self, context):
        self.context = context
        self.caption_style = CaptionStyle.from_mapping(context.setting('caption', {}))
        if context.window is None:
            return
        self.session = Session(context.window)
        self.session.caption.connect(self._caption)
        self.session.status.connect(self._status)
        self.session.metrics.connect(self._metrics)
        self.session.running.connect(self._running)
        entry = QToolButton(context.window)
        entry.setText(self.name)
        entry.setIcon(QIcon(os.path.join(os.path.dirname(__file__), 'translate.svg')))
        entry.setToolTip('为所选直播间开启本地识别与翻译字幕')
        entry.clicked.connect(self.toggle_panel)
        context.register_workspace_control('translate', entry, priority=30)

    def toggle_panel(self):
        if self.panel is not None and self.panel.isVisible():
            self._save_api_key()
            self.panel.hide()
        else:
            self.open()

    def open(self, selected=None):
        if self.panel is None:
            settings = with_bundled_defaults(self.context.setting('options', {}))
            saved_key = unprotect(self.context.setting('api_key_dpapi', ''))
            self.panel = Panel(settings, self.context.window, caption=self.caption_style,
                               api_key=saved_key)
            self.panel.startRequested.connect(self.start)
            self.panel.stopRequested.connect(self.stop)
            self.panel.captionStyleChanged.connect(self._caption_style_changed)
            self.panel.apiKeyChanged.connect(self._api_key_changed)
            self.panel.export.clicked.connect(lambda: self.panel.export_metrics(list(self.session.samples)))
        self.panel.refresh_sources(self.sources, selected)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def tile_actions(self, tile):
        if self.session is None:
            return []
        return [('实时翻译…', lambda: self.open(tile))]

    def start(self):
        reference = self.panel.sources.currentData()
        tile = reference() if reference else None
        source = self.sources.get(tile) if tile is not None else None
        if source is None:
            self._status('请先播放直播间；如果插件加载时已在播放，请重新播放一次以获取流信息')
            return
        try:
            options = self.panel.options()
            self.caption_style = self.panel.caption_options()
            self.context.set_setting('caption', self.caption_style.mapping())
            self._save_api_key()
            self._clear_caption()
            self._last_caption = None
            ffmpeg = self.context.recording.options()['ffmpeg']
            self.session.start(options, self.panel.key.text(), source, tile, ffmpeg)
            self.context.set_setting('options', asdict(options))
        except (ValueError, RuntimeError, OSError) as error:
            self._status(str(error))

    def stop(self):
        self._clear_caption()
        self._last_caption = None
        if self.session is not None:
            self.session.stop()
        if self.captions is not None:
            self.captions.hide()
        self._status('已停止；本地识别、翻译与取流进程已释放')

    def on_event(self, event, payload):
        tile = payload.get('tile')
        if tile is None:
            return
        if event == 'stream.resolved':
            source = payload.get('source')
            if source is not None:
                snapshot = replace(source, headers=dict(source.headers))
                self.sources[tile] = snapshot
                if self.session and self.session.tile and self.session.tile() is tile:
                    try:
                        if self.session.switch_source(snapshot):
                            self._status('直播源已切换，正在接续传译')
                    except (ValueError, RuntimeError, OSError) as error:
                        self._status(str(error))
        elif event in ('tile.stopped', 'tile.removed'):
            self.sources.pop(tile, None)
            if self.session and self.session.tile and self.session.tile() is tile:
                self.stop()
        if self.panel is not None:
            self.panel.refresh_sources(self.sources)

    def _caption(self, original, translated):
        self._last_caption = (original, translated)
        if self.panel is not None:
            self.panel.update_caption(original, translated)
        self._apply_caption()

    def _active_tile(self):
        if self.session is None or self.session.tile is None:
            return None
        return self.session.tile()

    def _apply_caption(self):
        if self._last_caption is None:
            return
        if not self.caption_style.enabled:
            self._clear_caption()
            return
        original, translated = self._last_caption
        tile = self._active_tile()
        setter = getattr(self.context, 'set_tile_caption', None)
        if tile is not None and callable(setter):
            try:
                if setter(tile, original, translated,
                          style=self.caption_style.mapping(), visible=True):
                    if self.captions is not None:
                        self.captions.hide()
                    return
            except (AttributeError, RuntimeError, TypeError):
                pass
        if self.captions is None:
            self.captions = Captions(self.context.window, style=self.caption_style)
            self.captions.stopRequested.connect(self.stop)
        self.captions.update_caption(original, translated)
        self.captions.show()

    def _clear_caption(self):
        tile = self._active_tile()
        clear = getattr(self.context, 'clear_tile_caption', None)
        if tile is not None and callable(clear):
            try:
                clear(tile)
            except (AttributeError, RuntimeError, TypeError):
                pass
        if self.captions is not None:
            self.captions.hide()

    def _caption_style_changed(self, style):
        self.caption_style = CaptionStyle.from_mapping(style)
        self.context.set_setting('caption', self.caption_style.mapping())
        if self.captions is not None:
            self.captions.set_style(self.caption_style)
        self._apply_caption()

    def _save_api_key(self):
        if self.panel is None:
            return
        key = self.panel.key.text().strip()
        if not key:
            self.context.set_setting('api_key_dpapi', '')
            return
        token = protect(key)
        if token:
            self.context.set_setting('api_key_dpapi', token)
        elif self.panel is not None:
            self.panel.status.setText('API Key 无法加密保存，本次仅在内存中使用')

    def _api_key_changed(self, _key):
        self._save_api_key()

    def _status(self, message):
        if self.panel is not None:
            self.panel.status.setText(message)

    def _metrics(self, value):
        if value['event'] in ('audio_transport', 'audio_start', 'audio_first_pcm', 'audio_exit'):
            self.context.log('[音轨诊断] ' + json.dumps(value, ensure_ascii=True))
        if self.panel is not None:
            self.panel.update_metrics(value)

    def _running(self, active):
        if self.panel is not None:
            self.panel.set_running(active)

    def on_unload(self):
        self.stop()
        for widget in (self.captions, self.panel, self.session):
            if widget is not None:
                widget.deleteLater()
        self.captions = self.panel = self.session = None
        self.sources.clear()


plugin = LiveTranslate()
