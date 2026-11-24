"""F1 playback with a native IPTV main picture and the existing commentary mixer."""
from time import monotonic

from PySide6.QtCore import QEvent

from .native_picture import NativePicture
from .viewer import Viewer


class HybridRaceWorkspace(Viewer):
    split_presentation = False

    def __init__(self, context, sources):
        self.native_picture = NativePicture(self)
        super().__init__(context, sources, workspace_crops=True)

    @property
    def native_main(self):
        row = self.rows.get(self.main.currentData())
        return row if row is not None and getattr(row.platform, 'kind', '') == 'm3u' else None

    def eventFilter(self, watched, event):
        if (watched is getattr(self, '_native_clip_timer', None) and event.type() == QEvent.Timer
                and self.native_picture.player is not None):
            return False
        return super().eventFilter(watched, event)

    def _main_changed(self, *_args):
        native = self.native_picture
        previous = native.row
        selected = self.native_main
        if previous is not None and previous is not selected:
            self._stop_row(previous)
        super()._main_changed(*_args)
        self._native_controls(selected)
        if self.running and previous is not None and previous is not selected and previous.room_id in self.rows:
            self._start_row(previous)
        if self.running and selected is not None and native.row is not selected:
            self._stop_row(selected)
            self._start_row(selected)

    def _native_controls(self, selected):
        native = selected is not None
        if native and self.replay.active:
            self.return_live()
        if native and self.automatic.isChecked():
            self.automatic.setChecked(False)
        self.automatic.setEnabled(not native)
        self.automatic.setToolTip('原生主画面使用手动偏移' if native else '自动对齐比赛画面')
        self.picture.recording_button.setEnabled(not native)
        for row in self.rows.values():
            row.clock_button.setEnabled(not native)
            row.floating_button.setEnabled(row is not selected)

    def _start_row(self, row):
        if row is self.native_main:
            self.native_picture.start(row)
            self.sync_picture()
        else:
            super()._start_row(row)

    def _stop_row(self, row):
        if row is self.native_picture.row:
            self.native_picture.release()
        super()._stop_row(row)

    def sync_picture(self, *_args, refresh_metadata=True):
        super().sync_picture(*_args, refresh_metadata=refresh_metadata)
        self.native_picture.sync_controls()

    def _show_buffering(self, active):
        player = self.native_picture.player
        if player is not None:
            active = not player.paused and player.state in ('connecting', 'buffering', 'frozen')
        elif self.native_picture.row is not None:
            active = False
        super()._show_buffering(active)

    def _stream_info_snapshot(self):
        player = self.native_picture.player
        if player is not None:
            row = self.native_picture.row
            return {**player.stream_info(), 'room': f'{row.label()} · {row.room_id}', 'core': 'libmpv'}
        return super()._stream_info_snapshot()

    def _reload_picture(self):
        if self.native_main is not None and self.running:
            self._start_row(self.native_main)
        else:
            super()._reload_picture()

    def _automatic_changed(self, *_args):
        if self.native_main is not None and self.automatic.isChecked():
            self.automatic.setChecked(False)
            return
        return super()._automatic_changed(*_args)

    def _delay_changed(self, *_args):
        if self.native_main is None:
            return super()._delay_changed(*_args)
        row = next((row for row in self.rows.values() if row.delay is self.sender()), None)
        if row is None:
            return
        if row is self.native_main:
            self.native_picture.change_delay(row.delay.value())
        else:
            row.previous_delay = row.delay.value()
            self.show_comparison(row.room_id)
        self.changed()

    def target_shifts(self):
        shifts = super().target_shifts()
        main = self.native_main
        if main is not None and self.running:
            clock = self.audio.clock()
            scene_shift = self.native_picture.scene_time(monotonic()) - clock
            for room_id, row in self.rows.items():
                if room_id == main.room_id:
                    shifts[room_id] = scene_shift
                elif room_id not in self.independent_playback:
                    shifts[room_id] = (scene_shift + self.native_picture.delay
                                       - self.alignment.buffer_seconds - row.delay.value())
        return shifts

    def _render(self, probe):
        native = self.native_picture
        native.observe()
        super()._render(probe)
        if native.row is not None:
            status = native.row.video_text if not native.row.paused else '已暂停'
            text = f'主画面：{native.row.label()} · libmpv · {status} · 手动偏移'
            if self.notice.text() != text:
                self.notice.setText(text)

    def _update_sync_health(self, clock, shifts):
        super()._update_sync_health(clock, shifts)
        if self.native_main is not None and not self.native_picture.advancing(monotonic()):
            for key, row in self.rows.items():
                if key not in self.independent_playback:
                    row.mix_gain = 0.0

    def _record_picture(self):
        if self.native_main is None:
            super()._record_picture()

    def choose_crop(self, row, floating=False, *, current=None):
        if row is self.native_main:
            self.context.notify('原生主画面暂不可裁取')
            return
        return super().choose_crop(row, floating, current=current)
