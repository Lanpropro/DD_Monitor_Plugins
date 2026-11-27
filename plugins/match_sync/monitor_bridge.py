"""Same-user named pipe for the opt-in Windows match diagnostics."""
import math
import os
import sys
from time import monotonic

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QTimer
from .local_control import LocalControlServer
from .button_timings import ButtonTimings
from .interaction_probe import InteractionProbe
from .panel_probe import PanelProbe


class MonitorBridge(QObject):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.buttons = ButtonTimings(viewer)
        self.panels = PanelProbe(self)
        self.interactions = InteractionProbe(self)
        self.match_hold_until = 0.
        self.original_switch_interval = None
        self.restore_switch_timer = QTimer(self)
        self.restore_switch_timer.setSingleShot(True)
        self.restore_switch_timer.timeout.connect(self.restore_switch_interval)
        self.server = LocalControlServer(self, self.dispatch)
        viewer.installEventFilter(self)
        if viewer.isVisible():
            self.listen()

    def listen(self):
        if not self.server.isListening():
            self.server.listen(f'ddm-match-monitor-{os.getpid()}')

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Timer and watched is getattr(self.viewer, 'match_timer', None):
            return monotonic() < self.match_hold_until
        if event.type() == QEvent.Show:
            self.listen()
        elif event.type() == QEvent.Hide:
            self.restore_switch_interval()
            self.server.close()
        return False

    def restore_switch_interval(self):
        self.restore_switch_timer.stop()
        if self.original_switch_interval is not None:
            sys.setswitchinterval(self.original_switch_interval)
            self.original_switch_interval = None

    def dispatch(self, request):
        v = self.viewer
        presenter = v.d3d11_picture
        button = v.picture.danmaku_button
        if set(request) == {'command', 'action', 'source', 'slot'} and request['command'] == 'probe_interaction':
            return self.interactions.start(request['action'], request['source'], request['slot'])
        if request == {'command': 'interaction_result'}:
            return dict(self.interactions.result)
        if set(request) == {'command', 'paused'} and request['command'] == 'pause_picture':
            row = v.rows.get(v.main.currentData())
            paused = request['paused']
            if row is None or type(paused) is not bool:
                raise ValueError('No main source')
            before = row.paused
            timing = None
            if before != paused:
                if not v.picture.pause_button.isVisible():
                    v.picture.set_controls_visible(True)
                timing = self.buttons.click(v.picture.pause_button)
            return dict(before=before, paused=row.paused, timing=timing)
        if set(request) == {'command', 'bulk'} and request['command'] == 'probe_folders':
            return self.panels.probe_folders(request['bulk'])
        if set(request) == {'command', 'all', 'collapsed'} and request['command'] == 'follow_folder':
            sidebar = getattr(v.window(), 'sidebar', None)
            if (sidebar is None or type(request['all']) is not bool or type(request['collapsed']) is not bool
                    or sidebar.filter_text or sidebar._folder_drag_previous or sidebar._bulk_fold_previous):
                raise ValueError('Folder state unavailable')
            # A single visible folder makes the individual and bulk controls directly comparable.
            if len(sidebar.folders) != 1:
                raise ValueError('Single folder required')
            from PySide6.QtWidgets import QToolButton
            folder, = sidebar.folders
            target = sidebar.navigation.folder_toggle_button if request['all'] else next(
                (b for b in sidebar.findChildren(QToolButton) if getattr(b, 'folder_id', None) == folder['id']), None)
            if target is None:
                raise ValueError('Folder control unavailable')
            before = folder['collapsed']
            timing = self.buttons.click(target) if before != request['collapsed'] else None
            return dict(before=before, collapsed=folder['collapsed'], timing=timing)
        if set(request) == {'command', 'seconds'} and request['command'] == 'short_switch':
            seconds = request['seconds']
            if type(seconds) is not int or not 0 <= seconds <= 150:
                raise ValueError('Invalid trial duration')
            self.restore_switch_interval()
            if seconds:
                self.original_switch_interval = sys.getswitchinterval()
                sys.setswitchinterval(.001)
                self.restore_switch_timer.start(seconds * 1000)
            return dict(interval_ms=round(sys.getswitchinterval() * 1000, 3))
        if set(request) == {'command', 'live_only'} and request['command'] == 'follow_filter':
            sidebar = getattr(v.window(), 'sidebar', None)
            live_only = request['live_only']
            if sidebar is None or type(live_only) is not bool or sidebar.plugin_pages.current is not None:
                raise ValueError('Follow list unavailable')
            before = sidebar.live_only
            target = sidebar.navigation.live_button if live_only else sidebar.navigation.all_button
            timing = self.buttons.click(target) if before != live_only else None
            return dict(before=before, live_only=sidebar.live_only, timing=timing)
        if set(request) == {'command', 'enabled'} and request['command'] == 'multiselect':
            sidebar = getattr(v.window(), 'sidebar', None)
            enabled = request['enabled']
            if sidebar is None or type(enabled) is not bool or sidebar.filter_text or sidebar.selected_sort_ids():
                raise ValueError('Sidebar unavailable or selection in use')
            timing = None
            if sidebar.select_mode != enabled:
                timing = self.buttons.click(sidebar.batch_button if enabled else sidebar.cancel_button)
            return dict(enabled=sidebar.select_mode, timing=timing)
        if set(request) == {'command', 'collapsed'} and request['command'] == 'sidebar':
            sidebar = getattr(v.window(), 'sidebar', None)
            collapsed = request['collapsed']
            if sidebar is None or type(collapsed) is not bool:
                raise ValueError('Sidebar unavailable')
            before = sidebar.collapsed
            timing = self.buttons.click(sidebar.toggle_button) if before != collapsed else None
            return dict(before=before, collapsed=sidebar.collapsed, timing=timing)
        if set(request) == {'command', 'seconds'} and request['command'] == 'hold_search':
            seconds = request['seconds']
            if type(seconds) is not int or not 0 <= seconds <= 150:
                raise ValueError('Invalid hold duration')
            self.match_hold_until = monotonic() + seconds
            if seconds:
                v.match_timer.installEventFilter(self)
            else:
                v.match_timer.removeEventFilter(self)
            return dict(automatic=v.alignment.automatic, busy=v.matching, searches=v.analysis_cursor,
                        held=bool(seconds))
        if (set(request) == {'command', 'enabled'} and request['command'] == 'raster_paint'
                and type(request['enabled']) is bool and not presenter.requested):
            v.canvas.immediate_frame_paint = request['enabled']
            return dict(immediate=v.canvas.immediate_frame_paint)
        if set(request) == {'command', 'panel', 'opened'} and request['command'] == 'console':
            opened = request['opened']
            if type(opened) is not bool:
                raise ValueError('Invalid console state')
            panel, minimize, restore = {
                'playback': (v.settings_panel, v.minimize_settings, v.restore_settings),
                'comments': (v.panel, v.minimize_chat, v.restore_chat),
                'sender': (v.send_console, v.send_console.minimize, v.send_console.restore),
            }[request['panel']]
            before = panel.isVisible()
            timing = None
            if before != opened:
                target = restore if opened else minimize
                if opened and target in (v.restore_settings, v.restore_chat) and not target.isVisible():
                    v.picture.set_controls_visible(True)
                timing = self.buttons.click(target)
            return dict(before=before, opened=panel.isVisible(), timing=timing)
        if set(request) == {'command', 'source', 'control', 'checked'} and request['command'] == 'row_toggle':
            index, checked = request['source'], request['checked']
            if type(index) is not int or not 0 <= index < len(v.rows) or type(checked) is not bool:
                raise ValueError('Invalid row toggle')
            row = tuple(v.rows.values())[index]
            toggle = {'audio': row.audible, 'left': row.channel.left, 'right': row.channel.right,
                      'chat': row.show_chat, 'assist': row.assist}[request['control']]
            before = toggle.isChecked()
            timing = self.buttons.click(toggle) if before != checked else None
            return dict(before=before, checked=toggle.isChecked(), timing=timing)
        if set(request) == {'command', 'source', 'slot', 'rect', 'placement'} and request['command'] == 'crop':
            source, slot = request['source'], request['slot']
            rect, placement = request['rect'], request['placement']
            if (type(source) is not int or not 0 <= source < len(v.rows)
                    or type(slot) is not int or slot < 0
                    or not isinstance(rect, list) or len(rect) != 4
                    or not all(type(n) in (float, int) and math.isfinite(n) and 0 <= n <= 1 for n in rect)
                    or min(rect[2:]) <= 0 or rect[0] + rect[2] > 1 or rect[1] + rect[3] > 1
                    or not isinstance(placement, list) or len(placement) != 3
                    or not all(type(n) is int and abs(n) < 65536 for n in placement) or placement[2] <= 0):
                raise ValueError('Invalid crop')
            row = tuple(v.rows.values())[source]
            item = v.floating_crops.get(row.room_id, {}).get(slot)
            if item is None or list(item.crop.getRect()) != rect:
                item = v.show_floating_crop(row, rect, None, slot=slot)
            pos = item.parentWidget().mapFromGlobal(QPoint(*placement[:2]))
            item.fit_geometry(pos.x(), pos.y(), placement[2])
            return {'source': source, 'slot': slot, 'rect': item.crop.getRect(), 'width': item.width()}
        if (set(request) == {'command', 'enabled'} and request['command'] == 'precise_wait'
                and type(request['enabled']) is bool):
            worker = presenter.worker
            if worker is None:
                raise ValueError('D3D11 worker unavailable')
            worker.precise_wait = request['enabled']
            return dict(precise_wait=worker.precise_wait)
        if set(request) == {'command', 'source'} and request['command'] == 'confirm_mix':
            index = request['source']
            if type(index) is not int or not 0 <= index < len(v.rows):
                raise ValueError('Invalid source')
            row = tuple(v.rows.values())[index]
            if row.room_id == v.alignment.reference or not row.match_confirmed:
                raise ValueError('No matched secondary')
            if row.awaiting_confirmation and v.alignment.automatic:
                self.buttons.click(row.join)
            return dict(admitted=row.user_confirmed, awaiting=row.awaiting_confirmation)
        if request == {'command': 'crops'}:
            crops = []
            for source, key in enumerate(v.rows):
                for slot, item in v.floating_crops.get(key, {}).items():
                    pos = item.mapToGlobal(QPoint())
                    crops.append(dict(source=source, slot=slot, rect=item.crop.getRect(),
                                      placement=[pos.x(), pos.y(), item.width()]))
            return {'crops': crops}
        if set(request) == {'command', 'action', 'slot'} and request['command'] == 'sound_calibration':
            bar = v.sound_calibration
            action, slot = request['action'], request['slot']
            if type(slot) is not int or not 0 <= slot < len(v.rows):
                raise ValueError('Source unavailable')
            key = tuple(v.rows)[slot]
            if action == 'start':
                bar.start()
            elif action in ('apply', 'undo'):
                if not bar.change(key, undo=action == 'undo'):
                    raise ValueError('Calibration action unavailable')
            elif action != 'state':
                raise ValueError('Unknown calibration action')
            index = bar.keys.index(key) if key in bar.keys and key != v.main.currentData() else None
            return dict(phase=bar.phase, estimate_ms=bar.estimate(key),
                        goal_ms=v.audio.trim_goal(key), settled=v.audio.trims_settled(),
                        text=bar.status_text, can_undo=key in bar.previous,
                        measured_ms=[relative[index] for at, _, relative in bar.evidence.windows
                                     if index is not None and monotonic() - at <= 60 and index in relative])
        if request in ({'command': 'start_mix_lag'}, {'command': 'mix_lag'}):
            from .mix_lag_probe import MixLagProbe
            audio = v.audio
            with audio.lock:
                probe = audio.mix_lag_probe
                if probe is not None and monotonic() > probe.deadline:
                    probe.cancel()
                if request['command'] == 'start_mix_lag' and (probe is None or probe.result['state'] not in ('collecting', 'analyzing')):
                    rows = tuple(v.rows.values())
                    if (len(rows) != 3 or v.replay.active or v.independent_playback or not v.running
                            or any(r.decoder is None or r.sync_state != '已同步' for r in rows)):
                        raise ValueError('Three synchronized live sources required')
                    probe = MixLagProbe([r.decoder.history for r in rows])
                    audio.mix_lag_probe = probe
                    def expire(sample=probe):
                        with audio.lock:
                            sample.cancel()
                    QTimer.singleShot(20500, self, expire)
                return probe.result if probe is not None else {'state': 'idle'}
        if request == {'command': 'sources'}:
            return {'sources': [dict(slot=i, main=key == v.main.currentData(),
                independent=key in v.independent_playback, confirmed=row.match_confirmed,
                awaiting=row.awaiting_confirmation, admitted=row.user_confirmed,
                audible=row.audible.isChecked(), sync=row.sync_state, actual_quality=row.actual_quality, choices=[opt['qn'] for opt in row.quality_options])
                for i, (key, row) in enumerate(v.rows.items())]}
        if set(request) == {'command', 'quality'} and request['command'] == 'main_quality':
            quality = request['quality']
            row = v.rows.get(v.main.currentData())
            if row is None or type(quality) is not int or not any(
                    opt['qn'] == quality and not opt.get('restricted') for opt in row.quality_options):
                raise ValueError('Quality unavailable')
            changed = row.actual_quality != quality
            if changed:
                v._quality_changed(None, quality)
            return dict(requested=quality, changed=changed)
        if set(request) == {'command', 'source', 'enabled'} and request['command'] == 'independent':
            source, enabled = request['source'], request['enabled']
            if type(enabled) is not bool or type(source) is not int or not 0 <= source < len(v.rows):
                raise ValueError('Invalid source')
            key = tuple(v.rows)[source]
            if (key in v.independent_playback) != enabled:
                v.toggle_independent(key)
            return dict(independent=key in v.independent_playback)
        if set(request) == {'command', 'action', 'source'} and request['command'] == 'probe_panel':
            return self.panels.start(request['action'], request['source'])
        if set(request) == {'command', 'action', 'source'} and request['command'] == 'profile_panel':
            return self.panels.start(request['action'], request['source'], profile=True)
        if request == {'command': 'panel_result'}:
            return dict(self.panels.result)
        if request == {'command': 'finish_panel'}:
            return self.panels.finish()
        if request == {'command': 'inventory'}:
            return {'buttons': self.buttons.inventory()}
        if request == {'command': 'buttons'}:
            return {'recent': list(self.buttons.rows)[-16:]}
        if (set(request) == {'command', 'opened'} and request['command'] == 'comparison'
                and type(request['opened']) is bool):
            if v.compare.isChecked() == request['opened']:
                return {'changed': False, 'opened': v.compare.isChecked()}
            timing = self.buttons.click(v.compare)
            return {'changed': True, 'opened': v.compare.isChecked(), 'timing': timing}
        if request == {'command': 'state'}:
            pass
        elif set(request) == {'command', 'backend'} and request['command'] == 'presenter':
            backend = request['backend']
            if backend not in ('raster', 'd3d11') or not presenter.available:
                raise ValueError('Unavailable presenter')
            presenter.set_requested(backend == 'd3d11')
        elif (set(request) == {'command', 'enabled'} and request['command'] == 'scrolling'
              and type(request['enabled']) is bool):
            button.setChecked(request['enabled'])
        else:
            raise ValueError('Unknown diagnostic command')
        canvas = v.canvas
        rect = QRect(canvas.mapToGlobal(QPoint()), canvas.size())
        screen = canvas.screen()
        visible = rect.intersected(screen.geometry())
        layer = v.video_danmaku._quick_layer
        return dict(available=True, pid=os.getpid(), automatic=v.alignment.automatic,
                    switch_interval_ms=round(sys.getswitchinterval() * 1000, 3),
                    search_held=monotonic()<self.match_hold_until, searches=v.analysis_cursor, matching=v.matching,
                    requested='d3d11' if presenter.requested else 'raster',
                    actual=presenter.diagnostics()['backend'], scrolling=button.isChecked(),
                    quick=layer is not None, quick_window_id=int(layer.window.winId()) if layer else None,
                    canvas_logical=rect.getRect(), screen_logical=screen.geometry().getRect(),
                    intersection_logical=visible.getRect(), dpr=canvas.devicePixelRatioF(),
                    screen_hz=screen.refreshRate(), running=v.running)
