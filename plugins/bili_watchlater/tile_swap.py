"""Player-preserving swaps for plugin VOD drops, without importing/patching Qt.

Call ``swap_tiles_preserving_players(host, source, target)`` synchronously on
Qt's GUI thread with resolved Tile objects (not indices, rooms or widgets).
Only a room with ``watchlater_bvid`` identifies this plugin's VOD; ``live=False``
alone does not. Pure host/live drops are declined.

The bool means *handled*, NOT *swap succeeded*:
* False: preflight declined, no writes or player/UI mutation calls were made.
* True: mutation began; consume the Drop even if it was rolled back. Never
  follow this with RoomPlacement's stop/rebuild fallback.
The caller owns Drop filtering/acceptance. It should also consume an unsupported
VOD drop on False rather than send that VOD through the destructive host path;
only non-VOD drops should be delegated to the host.

Required: a stable wall.tiles list, visible Tiles with distinct room dicts and
position preferences, a players mapping, the four audio methods on each existing
player, bottom update/layout controls, and wall.relayout(force=True). Optional
UI/overlay/meta hooks are best-effort. Fullscreen/PiP/transitional layouts and
pending slot creation/removal are declined. No playback, resolution, native
window, room identity/live flag, or player mapping is changed here.

Matches the probed RoomPlacement.hot_swap/WallGrid.relayout layout sequence.
Failures restore list order, exact room preference keys, tile preferences and
player audio, then retry layout once. A persistently failing host setter/layout
cannot guarantee physical audio/geometry restoration: errors are logged and the
Drop remains handled. This helper does not run an event loop or support
concurrent/reentrant wall mutation.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging


_LOG = logging.getLogger(__name__)
_POSITION_FIELDS = ("volume", "muted", "audio_channel", "video_danmaku_overrides")
_ROOM_FIELDS = _POSITION_FIELDS[:3]
_AUDIO_METHODS = ("set_volume", "set_muted", "set_audio_channel",
                  "reapply_audio_channel")
_MISSING = object()


def _required(obj, name):
    method = getattr(obj, name, None)
    if not callable(method):
        raise ValueError("missing required capability: " + name)
    return method


def _attempt(method, *args, **kwargs):
    """Recovery/optional calls must not leak an exception into host fallback."""
    try:
        method(*args, **kwargs)
    except Exception:
        _LOG.warning("Tile swap recovery/UI call failed; drop remains handled",
                     exc_info=True)


def _optional(obj, name):
    try:
        method = getattr(obj, name, None)
        if callable(method):
            method()
    except Exception:
        _LOG.warning("Tile swap %s failed; drop remains handled", name,
                     exc_info=True)


@dataclass(frozen=True)
class _TileState:
    tile: object
    room: dict
    preferences: tuple
    room_preferences: tuple
    audio_methods: tuple
    audio_values: tuple
    bottom: object
    layout: object
    updates_enabled: bool
    layout_enabled: bool


def _snapshot(tile, players):
    if not _required(tile, "isVisible")():
        raise ValueError("tile is not visible")
    room = tile.room
    if not isinstance(room, dict):
        raise ValueError("room must be a dict")
    preferences = tuple(getattr(tile, name) for name in _POSITION_FIELDS)
    audio_values = (int(preferences[0]), preferences[1], int(preferences[2]))
    player = players.get(tile)
    audio_methods = (() if player is None else
                     tuple(_required(player, name) for name in _AUDIO_METHODS))
    bottom = tile.bottom
    _required(bottom, "setUpdatesEnabled")
    updates_enabled = _required(bottom, "updatesEnabled")()
    layout = _required(bottom, "layout")()
    _required(layout, "setEnabled")
    _required(layout, "activate")
    layout_enabled = _required(layout, "isEnabled")()
    return _TileState(tile, room, preferences,
                      tuple(room.get(name, _MISSING) for name in _ROOM_FIELDS),
                      audio_methods, audio_values, bottom, layout,
                      updates_enabled, layout_enabled)


def _stable_layout(wall, tiles):
    # These paths bypass ordinary grid placement even with force=True.
    if (getattr(wall, "picture_in_picture_tiles", None) is not None
            or getattr(wall, "fullscreen_tile", None) is not None
            or getattr(wall, "_fullscreen_layout_pending", False)
            or getattr(wall, "_sidebar_animating", False)):
        return False
    capacity = getattr(wall, "_capacity", None)
    if not callable(capacity):
        # The probed ensure_slots can delete an empty Tile (or create a new one).
        return not callable(getattr(wall, "ensure_slots", None))
    count = capacity()
    if count <= 0:
        return True  # ensure_slots is a no-op in this case.
    used = sum(bool(tile.room.get("room_id")) for tile in tiles)
    return len(tiles) == max(count, used)


def _audio(state, values, *, recovering=False):
    for index, method in enumerate(state.audio_methods):
        args = (values[index],) if index < 3 else ()
        if recovering:
            _attempt(method, *args)
        else:
            method(*args)


def _restore(states):
    # Preserve dict identity and even absent/stale persisted preference keys.
    for state in states:
        for name, value in zip(_POSITION_FIELDS, state.preferences):
            _attempt(setattr, state.tile, name, value)
        for name, value in zip(_ROOM_FIELDS, state.room_preferences):
            if value is _MISSING:
                _attempt(state.room.pop, name, None)
            else:
                _attempt(state.room.__setitem__, name, value)
    for state in states:
        _audio(state, state.audio_values, recovering=True)


def _layout_ui(states):
    for state in states:
        if state.audio_methods:
            _optional(state.tile, "sync_audio_ui")
        _optional(state.tile, "raise_overlays")


def swap_tiles_preserving_players(host, source, target) -> bool:
    """Return whether this VOD Drop was handled; see module contract above."""
    try:
        if source is target or source is None or target is None:
            return False
        wall = host.wall
        tiles = wall.tiles
        relayout = _required(wall, "relayout")
        players = host.players
        if not isinstance(tiles, list) or not isinstance(players, Mapping):
            return False
        # Do not use list.index/equality: Tile identity is the player-map key.
        first = [i for i, tile in enumerate(tiles) if tile is source]
        second = [i for i, tile in enumerate(tiles) if tile is target]
        if len(first) != 1 or len(second) != 1:
            return False
        if not (source.room.get("watchlater_bvid")
                or target.room.get("watchlater_bvid")):
            return False
        if source.room is target.room or not _stable_layout(wall, tiles):
            return False
        states = (_snapshot(source, players), _snapshot(target, players))
        original_order = tuple(tiles)
    except Exception:
        # Missing methods, deleted Qt wrappers, malformed preferences, etc.
        return False

    # From this point every outcome is handled, including unsuccessful recovery.
    # The player mapping is only read during preflight, never edited/replaced.
    touched_bars = []
    layout_attempted = False
    try:
        tiles[first[0]], tiles[second[0]] = target, source
        for state, incoming in zip(states, reversed(states)):
            for name, value in zip(_POSITION_FIELDS, incoming.preferences):
                setattr(state.tile, name, value)
        for state, incoming in zip(states, reversed(states)):
            for name, value in zip(_ROOM_FIELDS, incoming.preferences):
                state.room[name] = value
            _audio(state, incoming.audio_values)

        for state in states:
            touched_bars.append(state)  # restore even a partially failed disable
            state.bottom.setUpdatesEnabled(False)
            state.layout.setEnabled(False)
        layout_attempted = True
        if relayout(force=True) is False:
            raise RuntimeError("wall relayout declined the swap")
        _layout_ui(states)
    except Exception:
        _LOG.warning("Tile swap failed; rolling back and consuming the drop",
                     exc_info=True)
        tiles[:] = original_order
        _restore(states)
        if layout_attempted:
            # A layout may have moved one widget before raising. Reposition both
            # from the restored order, still with bottom layouts/updates frozen.
            _attempt(relayout, force=True)
        _layout_ui(states)
    finally:
        for state in touched_bars:
            _attempt(state.layout.setEnabled, state.layout_enabled)
            if state.layout_enabled:
                _attempt(state.layout.activate)
            _attempt(state.bottom.setUpdatesEnabled, state.updates_enabled)

    for state in states:
        _optional(state.tile, "apply_video_danmaku_settings")
        _optional(state.tile, "_refresh_moved_overlays")
    _optional(host, "_refresh_meta")
    return True
