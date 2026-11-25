"""Own the F1 workspace's native IPTV player and its presentation timestamp."""
from time import monotonic

from ddm.player import decode_media_options


class NativePicture:
    def __init__(self, viewer):
        self.viewer = viewer
        self.player = self.row = None
        self.epoch = 0
        self.reset_clock()

    def reset_clock(self):
        self.origin = self.position = None
        self.sampled_at = 0.0
        self.progress_at = 0.0
        self.delay = 0.0

    def start(self, row):
        self.release()
        self.row = row
        row.video_text = '正在连接'
        row.paused = False
        self._show_surface(True)
        try:
            url, quality, profile, headers = row.platform.play_url(row.room_id, row.quality)
            row.actual_quality = quality
            row.quality_options = row.platform.room_quality_options(row.room_id)
            player = self.viewer.context.create_workspace_player(self.viewer.picture.video, live_rewind=True)
            self.player = player
            player.stateChanged.connect(self.state_changed)
            self.sync_controls()
            settings = self.viewer.context.window.settings
            player.play(url, profile, headers, decode_media_options(settings.get('decode_mode', 'auto')))
        except (RuntimeError, ValueError, OSError):
            self._release_player()
            row.video_text = '原生播放连接失败'
            row.refresh_status()
        self._set_delay(self.delay)

    def _show_surface(self, native):
        viewer = self.viewer
        viewer.canvas.setVisible(not native)
        if viewer.video_danmaku is not None and hasattr(viewer.video_danmaku, 'set_native_video'):
            viewer.video_danmaku.set_native_video(native)
        if native:
            viewer.picture.video.refresh_clip()
        elif viewer._native_clip_timer is not None:
            viewer._native_clip_timer.stop()

    def state_changed(self, state):
        if self.player is None or self.row is None:
            return
        if state == 'connecting':
            self.epoch += 1
            self.reset_clock()
            self.row.actual_position = None
            self._set_delay(0.0)
        self.row.video_text = {'playing': '取流已连接', 'buffering': '正在缓冲', 'frozen': '画面停顿',
                               'error': '原生播放连接失败', 'connecting': '正在连接'}.get(state, state)
        self.row.refresh_status()
        self.viewer._show_buffering(state in ('connecting', 'buffering', 'frozen'))

    def sync_controls(self):
        if self.player is None:
            return
        row = self.row
        self.player.sync_audio(row.volume.value(), not row.audible.isChecked(), row.channel.currentData())
        if self.player.paused != row.paused:
            self.player.set_paused(row.paused)

    def observe(self):
        now = monotonic()
        if self.player is None or now - self.sampled_at < .1:
            return
        self.sampled_at = now
        position, _ = self.player.playback_timing()
        if position is None:
            return
        if self.origin is None:
            self.origin = now - position
        if self.position != position:
            self.progress_at = now
        self.position = self.row.actual_position = position

    def advancing(self, now):
        return (self.player is not None and not self.row.paused and self.player.state == 'playing'
                and self.position is not None and now - self.progress_at < .4)

    def scene_time(self, now):
        if self.origin is None:
            return now
        elapsed = min(.1, now - self.sampled_at) if self.advancing(now) else 0.0
        return self.origin + self.position + elapsed

    def change_delay(self, requested):
        ranges = self.player.cached_seek_ranges() if self.player is not None else ()
        if self.position is not None and monotonic() - self.sampled_at < .1:
            position = self.position
        else:
            position, _ = self.player.playback_timing() if self.player is not None else (None, None)
        if position is not None and requested != self.delay:
            target = position - (requested - self.delay)
            if self.player.seek_cached(target):
                self.delay = requested
                self.row.previous_delay = requested
                self.position = target
                self.sampled_at = monotonic()
                return True
        self._set_delay(self.delay)
        if requested == self.delay:
            return True
        interval = next((span for span in ranges if position is not None and span[0] <= position <= span[1]), None)
        detail = (f'当前可再延后 {position - interval[0]:.1f} 秒、提前 {interval[1] - position:.1f} 秒'
                  if interval else '当前没有可跳转的直播缓存')
        self.viewer.context.notify('主画面偏移超出缓存，' + detail)
        return False

    def _set_delay(self, value):
        if self.row is not None:
            widget = self.row.delay
            previous = widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(previous)
            self.row.previous_delay = value

    def _release_player(self):
        player, self.player = self.player, None
        if player is not None:
            player.stateChanged.disconnect(self.state_changed)
            player.release()

    def release(self):
        if self.row is not None:
            self._release_player()
            self._set_delay(0.0)
            self.row.actual_position = None
            self.row = None
            self.reset_clock()
            self._show_surface(False)
