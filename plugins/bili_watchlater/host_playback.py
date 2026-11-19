"""Host player/Tile adaptation; no binding, request or Qt lifetime ownership."""


class HostPlaybackAdapter:
    def __init__(self, context, log):
        self.context = context
        self.log = log

    def window(self):
        if self.context() is None:
            return None
        try:
            return getattr(self.context(), "window", None)
        except Exception:
            return None

    def tiles(self):
        window = self.window()
        wall = getattr(window, "wall", None)
        tiles = getattr(wall, "tiles", None)
        try:
            return list(tiles) if tiles is not None else []
        except TypeError:
            return []

    def playback_tiles(self):
        """Chat cells keep backing Tiles, but are not video destinations."""
        tiles = self.tiles()
        wall = getattr(self.window(), 'wall', None)
        panes = getattr(wall, 'pane_danmaku', None)
        if panes is None:
            return tiles
        chat_cells = panes.bindings()
        return [tile for cell, tile in panes.cell_tiles().items()
                if cell not in chat_cells and tile in tiles]

    def player(self, tile):
        window = self.window()
        players = getattr(window, "players", None)
        if players is None:
            return None

        try:
            return players.get(tile)
        except Exception:
            return None

    def supports_playback(self):
        return callable(getattr(self.window(), '_play_on', None))

    def stop(self, tile) -> None:
        """能力探测：停掉这一格的宿主播放器（释放解码/音频/网络/重连）。"""
        window = self.window()
        stop = getattr(window, "_stop_tile", None)
        if not callable(stop):
            return
        try:
            stop(tile)
        except Exception as error:
            self.log("host tile stop failed type=" + type(error).__name__)

    def start(self, tile) -> None:
        """能力探测：恢复这一格的直播。宿主自己检查 room_id/live/关窗。"""
        room = getattr(tile, "room", None)
        if not isinstance(room, dict) or not str(room.get("room_id") or ""):
            return
        window = self.window()
        start = getattr(window, "start_tile", None)
        if not callable(start):
            return
        try:
            start(tile)
        except Exception as error:
            self.log("host tile start failed type=" + type(error).__name__)

    def play(self, tile, url: str, headers: dict, *, start_paused=False) -> bool:
        """v1.0 核心：把点播直链注入宿主自己的格子播放器。

        ``_play_on``（app.py）会创建/复用 ``window.players[tile]``、
        接好宿主状态槽、写 tile.stream_*、发 stream.resolved —— 底栏按钮、
        占位符、暂停帧、快捷键、拖拽秒切因此全部原生生效。
        """
        window = self.window()
        play_on = getattr(window, "_play_on", None)
        if not callable(play_on):
            return False
        muted = bool(getattr(tile, 'muted', False))
        if start_paused:
            tile.muted = True  # Suppress audio until the host player has accepted pause.
        try:
            play_on(tile, url, profile="web", headers=headers)
            if start_paused:
                tile.set_paused(True)
                player = self.player(tile)
                if player is not None:
                    player.set_paused(True)
        except Exception as error:
            self.log("host play_on failed type=" + type(error).__name__)
            return False
        finally:
            if start_paused:
                tile.muted = muted
                player = self.player(tile)
                if player is not None:
                    player.set_muted(muted or bool(getattr(window, '_match_sync_active', False)))
        return True
