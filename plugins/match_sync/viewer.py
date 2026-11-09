"""One selectable match picture, independently mixed audio, and labelled chat."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCursor, QImage
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from ddm.widgets import DanmakuToggleButton, RefreshButton, VolumeButton
from ddm import config as config_module, motion, theme
from .engine import Alignment, Match, RATE, SafetyBuffer, LateBuffer, match_scenes, refine_match
from .media import Chat, Decoder, PlatformChat
from .sending import SendConsole, SendingDanmakuPanel
from .panels import SettingsPanel
from .replay import ReplayTimeline
from .floating import FloatingCrop
from .diagnostics import PlaybackProbe
# Compatibility exports: the implementation lives in the owning modules.
from .audio import AudioFeed, AudioPump
from .canvas import Canvas
from .room_widgets import CompactStatus, RoomRow, COLORS, ROW_MIME
from .comparison import ComparisonPanel
from .room_input import parse_room
from .playback_timing import picture_poll_interval, cache_gap
from .alignment_control import Results
from . import viewer_ui, host_integration, console_control, session_control
from . import source_control, chat_control, alignment_control, playback_timing, picture_control, recording_control, replay_control, presentation, floating_control


class Viewer(QDialog):
    def __init__(self, context, sources, initial_room=None):
        super().__init__(context.window)
        self.context = context
        self.sources = sources
        self.rows = {}
        self.dragged_row = None
        self.row_animations = {}
        self.row_preview = []
        self.saved_rooms = {str(item["room_id"]): item for item in context.setting("rooms", [])}
        self.running = False
        self.status_ui_due = 0.0
        self.replay = ReplayTimeline()
        self._replay_live_anchor = 0.0
        self._replay_live_updated = 0.0
        self.overlay_pending = {}
        self.overlay_last = ""
        self.alignment = Alignment()
        self.alignment.buffer_seconds = 6.0
        self.safety_buffer = SafetyBuffer()
        self.reserve_seconds = context.setting('buffer_reserve', 5)
        if self.reserve_seconds not in (2, 5, 8):
            self.reserve_seconds = 5
        self.late_buffer = LateBuffer(self.reserve_seconds)
        self.playback_rate = 1.0
        self.stable_reference_shift = None
        self.skin_visible = False
        self.panel_alpha = 1.0
        self.independent_playback = {}
        self.multi_recorder = None
        self.record_started = None
        self.record_elapsed = ''
        self.independent_shifts = {}
        self.rematch_rooms = set()
        self.rematch_admit = set()
        self.last_health_update = None
        self.analysis_cursor = 0
        self.admitted_rooms = set()
        self.rendered_shifts = {}
        self.relative_rates = {}
        self.shift_reference = None
        self.shift_updated = None
        self.generation = 0
        self.matching = False
        self.analysis_serial = 0
        self.locked_clock = None
        self.sync_waiting = False
        self.offset_repositioning = False
        self.offset_target = None
        self.suppressed = {}
        self.prefer_highest = False
        self.embedded = False
        self.floating_crops = {}
        self.stream_info_dialog = None
        viewer_ui.build_ui(self)
        self.audio = AudioPump(self)
        self.audio.status.connect(self.audio_status.setText)
        self.host_audio_timer = QTimer(self)
        self.host_audio_timer.setInterval(100)
        self.host_audio_timer.timeout.connect(self._suppress_host_audio)
        self.render_timer = QTimer(self)
        self.render_timer.setTimerType(Qt.PreciseTimer)
        self.render_timer.setInterval(33)
        self.render_timer.timeout.connect(self.render)
        self.playback_probe = PlaybackProbe(self, Path(config_module.REPO) / 'logs' / 'match-playback.jsonl')
        self.playback_probe.preview = self.replay_bar.preview.metrics
        self.playback_probe.audio = self.audio
        self.canvas.paint_probe = self.playback_probe
        self.match_timer = QTimer(self)
        self.match_timer.setInterval(500)
        self.match_timer.timeout.connect(self.analyse)
        self.results = Results()
        self.results.matched.connect(self._matched)
        self.results.completed.connect(self._analysis_finished)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self.save)
        if initial_room:
            self.add_room(str(initial_room.get("room_id") or ""),
                          {"alias": initial_room.get("uname") or ""})
        index = self.main.findData(context.setting("main_room", ""))
        if index >= 0:
            self.main.setCurrentIndex(index)
        self._automatic_changed()
        theme.signals().changed.connect(self.refresh_appearance)
        controller = getattr(context.window, "skin_controller", None)
        if controller is not None:
            controller.changed.connect(self.refresh_appearance)
        self.refresh_appearance()

    def refresh_appearance(self, *_args):
        return viewer_ui.refresh_appearance(self, *_args)

    def embed(self, content):
        return host_integration.embed(self, content)

    def _shortcut_action(self, watched, event):
        return host_integration._shortcut_action(self, watched, event)

    def _handle_shortcut(self, action):
        return host_integration._handle_shortcut(self, action)

    def eventFilter(self, watched, event):
        handled = host_integration.eventFilter(self, watched, event)
        return super().eventFilter(watched, event) if handled is None else handled

    def _switch_stage(self, stage):
        return host_integration._switch_stage(self, stage)

    def showEvent(self, event):
        return host_integration.showEvent(self, event, super().showEvent)

    def hideEvent(self, event):
        return host_integration.hideEvent(self, event, super().hideEvent)

    def _sync_sidebar_marks(self):
        return host_integration._sync_sidebar_marks(self)

    def _style_sidebar_item(self, item, active, hold):
        return host_integration._style_sidebar_item(self, item, active, hold)

    def _sync_strip_marks(self, sidebar, enabled):
        return host_integration._sync_strip_marks(self, sidebar, enabled)

    def set_settings_visible(self, visible):
        return console_control.set_settings_visible(self, visible)

    def _layout_restore_control(self):
        return console_control._layout_restore_control(self)

    def set_comparison_visible(self, visible):
        return console_control.set_comparison_visible(self, visible)

    def reorder_room(self, room_id, target_id, after=False):
        return console_control.reorder_room(self, room_id, target_id, after)

    def align_room_controls(self):
        return console_control.align_room_controls(self)

    def queue_console_height(self):
        return console_control.queue_console_height(self)

    def remember_console_height(self, _position, _index):
        return console_control.remember_console_height(self, _position, _index)

    def fit_console_height(self):
        return console_control.fit_console_height(self)

    def begin_row_drag(self, room_id):
        return console_control.begin_row_drag(self, room_id)

    def preview_row_drag(self, point):
        return console_control.preview_row_drag(self, point)

    def finish_row_drag(self, point):
        return console_control.finish_row_drag(self, point)

    def end_row_drag(self):
        return console_control.end_row_drag(self)

    def _queue_rematch(self, row, local=False):
        return alignment_control._queue_rematch(self, row, local)

    def retry_alignment(self, room_id):
        return alignment_control.retry_alignment(self, room_id)

    def submit_alignment_hint(self, room_id, direction):
        return alignment_control.submit_alignment_hint(self, room_id, direction)

    def _delay_changed(self, *_args):
        return alignment_control._delay_changed(self, *_args)

    def show_comparison(self, room_id):
        return console_control.show_comparison(self, room_id)

    def set_chat_visible(self, visible):
        return console_control.set_chat_visible(self, visible)

    def _restore_host_widgets(self):
        return host_integration._restore_host_widgets(self)

    def dragEnterEvent(self, event):
        return host_integration.dragEnterEvent(self, event)

    def dropEvent(self, event):
        return host_integration.dropEvent(self, event)

    def _add_dragged(self, room_id):
        return source_control._add_dragged(self, room_id)

    def changed(self, *_args):
        self.save_timer.start()

    def save(self):
        self.context.set_setting("rooms", [row.preferences() for row in self.rows.values()])
        self.context.set_setting("main_room", self.main.currentData() or "")
        self.context.set_setting("automatic", self.automatic.isChecked())
        self.context.set_setting('buffer_reserve', self.reserve_seconds)
        self.context.set_setting('crop_quality', self.crop_quality.currentData())
        self.context.set_setting('record_format', self.record_format.currentData())
        self.context.set_setting('replay_preview_ratio', self.replay_preview_ratio)

    def _crop_source_policy(self, row):
        return floating_control._crop_source_policy(self, row)

    def _crop_quality_changed(self):
        return floating_control._crop_quality_changed(self)

    def _apply_crop_quality(self):
        return floating_control._apply_crop_quality(self)

    def _close_floating_crop(self, key):
        return floating_control._close_floating_crop(self, key)

    def _buffer_reserve_changed(self):
        return playback_timing._buffer_reserve_changed(self)

    def _open_danmaku_settings(self):
        return viewer_ui._open_danmaku_settings(self)

    def position_removal_notice(self):
        return console_control.position_removal_notice(self)

    def dismiss_removal_notice(self):
        return source_control.dismiss_removal_notice(self)

    def remove_room_with_undo(self, room_id):
        return source_control.remove_room_with_undo(self, room_id)

    def undo_remove_room(self):
        return source_control.undo_remove_room(self)

    def _add_input(self):
        return source_control._add_input(self)

    def add_room(self, text, preferences=None):
        return source_control.add_room(self, text, preferences)

    def import_current(self):
        return source_control.import_current(self)

    def update_main_choices(self):
        return source_control.update_main_choices(self)

    def _main_changed(self, *_args):
        return picture_control._main_changed(self, *_args)

    def select_main(self, room_id):
        return picture_control.select_main(self, room_id)

    def _show_buffering(self, active):
        return picture_control._show_buffering(self, active)

    def _stream_info_snapshot(self):
        return picture_control._stream_info_snapshot(self)

    def _open_stream_info(self):
        return picture_control._open_stream_info(self)

    def _stream_info_closed(self, _result):
        return picture_control._stream_info_closed(self, _result)

    def sync_picture(self, *_args, refresh_metadata=True):
        return picture_control.sync_picture(self, *_args, refresh_metadata=refresh_metadata)

    def replay_histories(self):
        return replay_control.replay_histories(self)

    def open_match_settings(self):
        return picture_control.open_match_settings(self)

    def live_clock(self):
        return replay_control.live_clock(self)

    def _rebuild_replay_chat(self, stamp, live=False):
        return chat_control._rebuild_replay_chat(self, stamp, live)

    def seek_replay(self, stamp):
        return replay_control.seek_replay(self, stamp)

    def return_live(self):
        return replay_control.return_live(self)

    def _picture_setting(self, key, value):
        return picture_control._picture_setting(self, key, value)

    def _quality_changed(self, _room, quality):
        return picture_control._quality_changed(self, _room, quality)

    def _reload_picture(self):
        return picture_control._reload_picture(self)

    def _pause_picture(self):
        return picture_control._pause_picture(self)

    def _fullscreen_picture(self):
        return picture_control._fullscreen_picture(self)

    def _stop_recording(self):
        return recording_control._stop_recording(self)

    def _record_picture(self):
        return recording_control._record_picture(self)

    def _recording_snapshot(self):
        return recording_control._recording_snapshot(self)

    @Slot(str)
    def _recording_started(self, path):
        return recording_control._recording_started(self, path)

    @Slot(str, str)
    def _recording_finished(self, path, error):
        return recording_control._recording_finished(self, path, error)

    def _save_delay_offsets(self, shifts):
        return alignment_control._save_delay_offsets(self, shifts)

    def _automatic_changed(self, *_args):
        return alignment_control._automatic_changed(self, *_args)

    def remove_room(self, room_id):
        return source_control.remove_room(self, room_id)

    def toggle_independent(self, room_id):
        return alignment_control.toggle_independent(self, room_id)

    def target_shifts(self):
        return alignment_control.target_shifts(self)

    def _block_alignment(self, room_id, reason):
        return alignment_control._block_alignment(self, room_id, reason)

    def shifts(self):
        return alignment_control.shifts(self)

    def _smooth_shifts(self, wanted):
        return alignment_control._smooth_shifts(self, wanted)

    def toggle_running(self):
        return session_control.toggle_running(self)

    def _start_row(self, row):
        return source_control._start_row(self, row)

    def _start_chat(self, row):
        return chat_control._start_chat(self, row)

    def _refresh_chat_status(self):
        return chat_control._refresh_chat_status(self)

    def _valid(self, row, worker, chat=False):
        return source_control._valid(self, row, worker, chat)

    def _information(self, row, worker, info):
        return source_control._information(self, row, worker, info)

    def _state(self, row, worker, text, chat):
        return source_control._state(self, row, worker, text, chat)

    def _reset(self, row, worker):
        return source_control._reset(self, row, worker)

    def _message(self, row, worker, event):
        return chat_control._message(self, row, worker, event)

    def _recover_clock(self, clock, shifts):
        return playback_timing._recover_clock(self, clock, shifts)

    def _update_late_buffer(self, now, clock, shifts, ahead, capacity):
        return playback_timing._update_late_buffer(self, now, clock, shifts, ahead, capacity)

    def dismiss_buffer_notice(self):
        return playback_timing.dismiss_buffer_notice(self)

    def _observe_buffer_exhaustion(self, row, exhausted, eligible, now):
        return playback_timing._observe_buffer_exhaustion(self, row, exhausted, eligible, now)

    def _update_sync_health(self, clock, shifts):
        return alignment_control._update_sync_health(self, clock, shifts)

    def confirm_alignment(self, room_id):
        return alignment_control.confirm_alignment(self, room_id)

    def admit_pending(self, room_id, automatic=False):
        return alignment_control.admit_pending(self, room_id, automatic)

    def _render_overlay(self, clock, shifts):
        return chat_control._render_overlay(self, clock, shifts)

    def render(self):
        return presentation.render(self)

    def _render(self, probe):
        return presentation._render(self, probe)

    def _configure_picture_budget(self):
        return presentation._configure_picture_budget(self)

    def analyse(self):
        return alignment_control.analyse(self)

    def _analysis_finished(self, serial, epoch, results):
        return alignment_control._analysis_finished(self, serial, epoch, results)

    def _matched(self, epoch, results):
        return alignment_control._matched(self, epoch, results)

    def floating_surface(self):
        return floating_control.floating_surface(self)

    def reparent_floating_crops(self, parent):
        return floating_control.reparent_floating_crops(self, parent)

    def restore_crop_background(self, floating, exposed):
        return floating_control.restore_crop_background(self, floating, exposed)

    def flush_crop_background(self):
        return floating_control.flush_crop_background(self)

    def show_floating_crop(self, row, crop, frame):
        return floating_control.show_floating_crop(self, row, crop, frame)

    def render_floating_crops(self, clock, shifts):
        return floating_control.render_floating_crops(self, clock, shifts)

    def choose_crop(self, row, floating=False):
        return floating_control.choose_crop(self, row, floating)

    def _suppress_host_audio(self):
        return host_integration._suppress_host_audio(self)

    def _restore_host_audio(self, player, old_tile):
        return host_integration._restore_host_audio(self, player, old_tile)

    def _release_host_audio(self):
        return host_integration._release_host_audio(self)

    @staticmethod
    def _stop_row(row):
        return source_control._stop_row(row)

    def stop(self):
        return session_control.stop(self)

    def closeEvent(self, event):
        return session_control.closeEvent(self, event, super().closeEvent)

    def reject(self):
        return session_control.reject(self, super().reject)
