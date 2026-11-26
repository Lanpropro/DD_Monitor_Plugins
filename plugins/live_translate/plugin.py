"""DD Monitor CE entry points and multi-room translation coordination."""
from dataclasses import replace
import os
import weakref
import json

from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QToolButton
from ddm.plugins import Plugin

__path__ = [os.path.dirname(__file__)]
__package__ = __name__

from .caption_style import CaptionStyle
from .panel import Panel
from .secret_store import protect, unprotect
from .session import Session
from .session_group import SessionGroup
from .caption_presenter import CaptionPresenter
from .anchored_panel import AnchoredPanel
from .session_settings import panel_settings, save_options
from .setup_dialog import SetupCoordinator
from .glossary import room_terms


class LiveTranslate(Plugin):
    name = '实时翻译'
    description = '本地识别直播音轨，显示原文与翻译字幕'
    version = '0.1'

    def __init__(self):
        super().__init__()
        self.sources = weakref.WeakKeyDictionary()
        self.session = self.panel = self.group = self.presentation = None
        self.popover = None
        self.setup = None

    def on_load(self, context):
        self.context = context
        self.presentation = CaptionPresenter(context, self.sources, context.window)
        if context.window is None:
            return
        self.session = Session(context.window)
        self.group = SessionGroup(self.session, context.window)
        self.group.caption.connect(self._caption_for_tile)
        self.group.status.connect(self._status)
        self.group.metrics.connect(self._metrics)
        self.group.running.connect(self._running)
        self.group.stopping.connect(self._session_stopping)
        self.presentation.stopRequested.connect(self._stop_tile)
        entry = self.entry = QToolButton(context.window)
        self.action = QAction(self.name, context.window)
        self.action.setCheckable(True)
        self.action.setIcon(QIcon(os.path.join(os.path.dirname(__file__), 'translate.svg')))
        entry.setDefaultAction(self.action)
        entry.setToolTip('为所选直播间开启本地识别与翻译字幕')
        self.action.triggered.connect(self.toggle_panel)
        self.action.toggled.connect(self._entry_changed)
        context.register_workspace_control('translate', entry, priority=30)
        self.setup = SetupCoordinator(context, self._models_prepared, self._show_models)
        QTimer.singleShot(0, self.setup, self.setup.offer)

    def toggle_panel(self, checked=None):
        if checked is False or checked is None and self.popover is not None and self.popover.isVisible():
            self._save_api_key()
            if self.popover is not None:
                self.popover.hide()
        else:
            self.open()

    def open(self, selected=None):
        if self.panel is None:
            settings = panel_settings(self.context)
            saved_key = unprotect(self.context.setting('api_key_dpapi', ''))
            self.panel = Panel(settings, self.context.window, caption=self.caption_style,
                               api_key=saved_key,
                               caption_preview_factory=getattr(self.context, 'create_caption_preview', None),
                               room_presentation=getattr(self.context, 'room_presentation', None),
                               glossary_rooms=self.context.setting('glossary_rooms', {}))
            self.panel.glossary.changed.connect(lambda values: self.context.set_setting('glossary_rooms', values))
            self.presentation.panel = self.panel
            self.popover = AnchoredPanel(self.panel, self.context.window, self.entry,
                                         height=self.context.setting('panel_height', self.panel.height()))
            self.popover.heightChanged.connect(lambda value: self.context.set_setting('panel_height', value))
            self.panel.hideRequested.connect(self.popover.hide)
            self.popover.hidden.connect(self._panel_hidden)
            self.panel.startRequested.connect(self.start)
            self.panel.stopRequested.connect(self.stop)
            self.panel.captionStyleChanged.connect(self._caption_style_changed)
            self.panel.apiKeyChanged.connect(self._api_key_changed)
            self.panel.settingsChanged.connect(self._save_panel_settings)
            self.panel.setupRequested.connect(self.setup.open)
            self.panel.export.clicked.connect(lambda: self.panel.export_metrics(list(self.group.samples)))
        self.panel.refresh_sources(self.sources, selected)
        self.panel.show()
        self.popover.show()
        self.action.setChecked(True)

    def _entry_changed(self, checked):
        if not checked and self.popover is not None:
            self.popover.hide()

    def _panel_hidden(self):
        self._save_api_key()
        self._save_panel_settings()
        self.action.setChecked(False)

    def tile_actions(self, tile):
        if self.session is None:
            return []
        actions = [('实时翻译…', lambda: self.open(tile))]
        if tile in self.presentation.values:
            actions.append(('停止此直播间传译', lambda: self._stop_tile(tile)))
            locked = self.presentation.style_for(tile).locked
            actions.append(('解锁翻译字幕' if locked else '锁定翻译字幕',
                            lambda: self.presentation.edited(tile, {'locked': not locked})))
        return actions

    def start(self):
        if self.setup is not None and self.setup.dialog is not None and self.setup.dialog.job is not None:
            self._status('模型正在准备，请等待完成或取消后再开始')
            return
        selected = [(tile, self.sources[tile]) for tile in self.panel.sources.selected_tiles() if tile in self.sources]
        if not selected:
            self._status('请选择至少一个正在播放的直播间')
            return
        try:
            options = self.panel.options()
            self.presentation.apply_style(self.panel.caption_options())
            self._save_api_key()
            self.presentation.clear_all()
            ffmpeg = self.context.recording.options()['ffmpeg']
            self.group.start(options, self.panel.key.text(), selected, ffmpeg,
                             glossaries=self.context.setting('glossary_rooms', {}))
            save_options(self.context, options)
        except (ValueError, RuntimeError, OSError) as error:
            self._status(str(error))

    def stop(self):
        released = self.group.stop() if self.group is not None else True
        if self.presentation is not None:
            self.presentation.clear_all()
        self._status('已停止；本地识别、翻译与取流进程已释放' if released else
                     '已停止取流；识别线程正在退出，请稍后再开始')

    def _session_stopping(self, tile=None):
        self.presentation.clear(tile)

    def _stop_tile(self, tile):
        if tile is None:
            self.stop()
        elif tile in self.group.sessions:
            self.group.stop_tile(tile)
        elif self.session.tile and self.session.tile() is tile:
            self.session.stop()

    def on_event(self, event, payload):
        tile = payload.get('tile')
        if tile is None:
            return
        if event == 'stream.resolved':
            source = payload.get('source')
            if source is not None:
                snapshot = replace(source, headers=dict(source.headers))
                self.sources[tile] = snapshot
                session = self.group.session_for(tile) if self.group else None
                if session is None and self.session and self.session.tile and self.session.tile() is tile:
                    session = self.session
                if session is not None:
                    try:
                        terms = room_terms(self.context.setting('glossary_rooms', {}), snapshot,
                                           session._options.target_language)
                        if session.switch_source(snapshot, glossary=terms):
                            self._status('直播源已切换，正在接续传译')
                    except (ValueError, RuntimeError, OSError) as error:
                        self._status(str(error))
                elif self.group is not None:
                    self.group.switch_source(tile, snapshot)
        elif event in ('tile.stopped', 'tile.removed'):
            self.sources.pop(tile, None)
            self._stop_tile(tile)
            self.presentation.clear(tile)
        if self.panel is not None:
            self.panel.refresh_sources(self.sources)

    def _caption(self, original, translated):
        self.presentation.show(self._active_tile(), original, translated)

    def _caption_for_tile(self, tile, original, translated):
        self.presentation.show(tile, original, translated)

    def _active_tile(self):
        if self.session is None or self.session.tile is None:
            return None
        return self.session.tile()

    def _apply_caption(self):
        self.presentation.render(self._active_tile())

    def _clear_caption(self):
        self.presentation.clear(self._active_tile())

    def _caption_style_changed(self, style):
        self.presentation.apply_style(style)

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

    def _caption_edited(self, tile, value):
        self.presentation.edited(tile, value)

    @property
    def caption_style(self):
        return self.presentation.style

    @caption_style.setter
    def caption_style(self, value):
        self.presentation.style = CaptionStyle.from_mapping(value)

    @property
    def captions(self):
        return self.presentation.fallback if self.presentation is not None else None

    @property
    def _last_caption(self):
        return self.presentation.last if self.presentation is not None else None

    def _api_key_changed(self, _key):
        self._save_api_key()

    def _save_panel_settings(self):
        if self.panel is not None:
            save_options(self.context, self.panel.options())

    def _show_models(self):
        self.open()
        self.panel.navigation.setValue('models')

    def _models_prepared(self, values):
        saved = dict(self.context.setting('options', {}))
        values = dict(values, translation_mode=self.panel.translation_mode.currentData() if self.panel is not None
                      else saved.get('translation_mode', 'local'))
        self.context.set_setting('options', dict(saved, **values))
        if self.panel is not None:
            self.panel.apply_prepared_models(values)
        self._show_models()

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
        self._save_panel_settings()
        if self.setup is not None:
            self.setup.close()
        self.stop()
        if self.popover is not None:
            self.popover.hide()
        if self.presentation is not None:
            self.presentation.close()
        for widget in (self.popover, self.group, self.session, self.presentation, self.setup):
            if widget is not None:
                widget.deleteLater()
        self.panel = self.session = self.group = self.presentation = None
        self.popover = None
        self.setup = None
        if hasattr(self, 'action'):
            self.action.deleteLater()
        self.sources.clear()


plugin = LiveTranslate()
