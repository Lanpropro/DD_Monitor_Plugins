"""Per-room caption presentation, placement preferences and legacy fallback."""
from dataclasses import replace
import weakref
from PySide6.QtCore import QObject, Signal
from .caption_style import CaptionStyle
from .music_detection import MUSIC_MARKER
from .panel import Captions
from .glossary import room_key


class CaptionPresenter(QObject):
    stopRequested = Signal(object)

    def __init__(self, context, sources, parent=None):
        super().__init__(parent)
        self.context = context
        self.sources = sources
        self.style = CaptionStyle.from_mapping(context.setting('caption', {}))
        self.room_styles = dict(context.setting('caption_rooms', {}) or {})
        self.values = weakref.WeakKeyDictionary()
        self.windows = weakref.WeakKeyDictionary()
        self.legacy_window = None
        self.last = None
        self.panel = None

    @property
    def fallback(self):
        return self.legacy_window or next(iter(self.windows.values()), None)

    def _key(self, tile):
        source = self.sources.get(tile) if tile is not None else None
        return room_key(source) if source is not None else None

    def style_for(self, tile):
        return CaptionStyle.from_mapping(self.room_styles.get(self._key(tile), self.style.mapping()))

    def show(self, tile, original, translated):
        self.last = (original, translated)
        if tile is not None:
            self.values[tile] = (original, translated)
        self.render(tile)

    def render(self, tile):
        value = self.values.get(tile) if tile is not None else self.last
        if value is None:
            return
        style = self.style_for(tile)
        if not self.style.enabled or not style.enabled:
            self._clear_surface(tile)
            return
        original, translated = value
        displayed = replace(style, mode='original') if original == MUSIC_MARKER else style
        if self.panel is not None:
            if tile is None:
                self.panel.update_caption(original, translated)
            else:
                source = self.sources.get(tile)
                self.panel.update_room_caption(tile, source.uname or source.room_id if source else '字幕',
                                               *displayed.text(original, translated))
        setter = getattr(self.context, 'set_tile_caption', None)
        if tile is not None and callable(setter):
            try:
                try:
                    supported = setter(tile, original, translated, style=displayed.mapping(), visible=True,
                                       on_style_changed=self.edited)
                except TypeError:
                    supported = setter(tile, original, translated, style=displayed.mapping(), visible=True)
                if supported:
                    window = self.windows.get(tile)
                    if window is not None:
                        window.hide()
                    return
            except (AttributeError, RuntimeError, TypeError):
                pass
        window = self.windows.get(tile) if tile is not None else self.legacy_window
        if window is None:
            window = Captions(self.context.window, style=displayed)
            if tile is None:
                self.legacy_window = window
                window.stopRequested.connect(lambda: self.stopRequested.emit(None))
            else:
                reference = weakref.ref(tile)
                self.windows[tile] = window
                window.stopRequested.connect(lambda: self.stopRequested.emit(reference()))
                tile.destroyed.connect(window.deleteLater)
        window.set_style(displayed)
        window.update_caption(original, translated)
        window.show()

    def _clear_surface(self, tile):
        clear = getattr(self.context, 'clear_tile_caption', None)
        if tile is not None and callable(clear):
            try:
                clear(tile)
            except (AttributeError, RuntimeError, TypeError):
                pass
        window = self.windows.get(tile) if tile is not None else self.legacy_window
        if window is not None:
            window.hide()

    def clear(self, tile):
        self._clear_surface(tile)
        if tile is not None:
            self.values.pop(tile, None)
            if self.panel is not None:
                self.panel.remove_room_caption(tile)
        self.last = None

    def clear_all(self):
        for tile in list(self.values):
            self.clear(tile)
        self.clear(None)

    def apply_style(self, style):
        style = CaptionStyle.from_mapping(style)
        previous = self.style.mapping()
        changed = {key: value for key, value in style.mapping().items() if previous[key] != value}
        self.style = style
        self.context.set_setting('caption', style.mapping())
        if self.room_styles and changed:
            self.room_styles = {key: CaptionStyle.from_mapping(dict(value, **changed)).mapping()
                                for key, value in self.room_styles.items()}
            self.context.set_setting('caption_rooms', dict(self.room_styles))
        for tile in list(self.values):
            self.render(tile)
        if self.legacy_window is not None and self.last is not None:
            self.render(None)

    def edited(self, tile, value):
        current = self.values.get(tile) if tile is not None else None
        if current and current[0] == MUSIC_MARKER and value.get('mode') == 'original':
            value = dict(value, mode=self.style_for(tile).mode)
        key = self._key(tile)
        if key is None:
            self.style = CaptionStyle.from_mapping(dict(self.style.mapping(), **value))
            self.context.set_setting('caption', self.style.mapping())
        else:
            style = CaptionStyle.from_mapping(dict(self.style_for(tile).mapping(), **value))
            self.room_styles[key] = style.mapping()
            self.context.set_setting('caption_rooms', dict(self.room_styles))
        self.render(tile)

    def close(self):
        self.clear_all()
        for window in list(self.windows.values()):
            window.deleteLater()
        self.windows.clear()
        if self.legacy_window is not None:
            self.legacy_window.deleteLater()
            self.legacy_window = None
