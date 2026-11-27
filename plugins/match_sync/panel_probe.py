"""Exercise visible controls during a local diagnostic session and time the opened panel."""
from time import perf_counter, time
from pathlib import Path
import cProfile
from collections import Counter
from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog
from shiboken6 import isValid


class PanelProbe(QObject):
    panels = {'pip': 'QDialog', 'pip_volume': 'QDialog', 'region': 'QDialog',
              'clock': 'ClockDialog', 'plugin_settings': 'MatchSettingsDialog',
              'settings': 'SettingsDialog', 'add_room': 'RoomAddPanel', 'notifications': 'NotificationCenter',
              'layout': 'LayoutPicker', 'wall_settings': 'WallQuickSettingsMenu', 'host_pip': 'PictureInPictureDialog',
              'manage': 'AnimatedMenu', 'monitor': 'StreamerMonitorMenu', 'functions': 'QFrame'}

    def __init__(self, bridge):
        super().__init__(bridge)
        self.bridge = bridge
        self.active = None
        self.panel = None
        self.result = {}
        self.profiler = None

    def probe_folders(self, bulk):
        sidebar = getattr(self.bridge.viewer.window(), 'sidebar', None)
        if (sidebar is None or type(bulk) is not bool or sidebar.filter_text or sidebar._folder_drag_previous
                or sidebar.select_mode or sidebar._sort_selected_ids or sidebar._sort_anchor):
            raise ValueError('Follow folders unavailable')
        folders = sidebar.folders
        initial = {folder['id']: folder['collapsed'] for folder in folders}
        previous = sidebar._bulk_fold_previous
        previous = None if previous is None else previous.copy()
        drag_folds = sidebar._folders_folded_by_drag.copy()
        search_expands = sidebar._search_expands_folders
        selected = [sidebar.navigation.folder_toggle_button] if bulk else [
            button for button in sidebar._folder_buttons.values() if button.isVisible() and button.isEnabled()]
        if not selected:
            raise ValueError('Folder control unavailable')
        timings = []
        try:
            for button in selected:
                timings.append(self.bridge.buttons.click(button))
                if not bulk:
                    timings.append(self.bridge.buttons.click(button))
        finally:
            changed = any(folder['collapsed'] != initial[folder['id']] for folder in folders)
            for folder in folders:
                folder['collapsed'] = initial[folder['id']]
            sidebar._bulk_fold_previous = previous
            sidebar._folders_folded_by_drag.update(drag_folds)
            sidebar._search_expands_folders = search_expands
            sidebar._refresh_folders(notify=changed)
        return dict(timings=timings, restored=all(folder['collapsed'] == initial[folder['id']] for folder in folders))

    def button(self, action, source):
        viewer = self.bridge.viewer
        if action == 'functions':
            return viewer.window().sidebar.workspace_controls.launcher
        if action in ('manage', 'monitor'):
            nav = viewer.window().sidebar.navigation
            return nav.manage_button if action == 'manage' else nav.monitor_button
        if action == 'plugin_settings':
            return viewer.match_settings_button
        if action in ('pip', 'pip_volume', 'region', 'clock'):
            if type(source) is not int or not 0 <= source < len(viewer.rows):
                raise ValueError('Invalid source')
            row = tuple(viewer.rows.values())[source]
            if action == 'region':
                for button in row.findChildren(QAbstractButton):
                    if button.accessibleName() == '选择对照区域':
                        return button
                raise ValueError('Region button unavailable')
            return row.clock_button if action == 'clock' else row.floating_button
        for button in viewer.window().findChildren(QAbstractButton):
            if not button.isVisible() or not button.isEnabled():
                continue
            kind = type(button).__name__
            if ((action == 'add_room' and kind == 'AddRoomButton')
                    or (action == 'notifications' and kind == 'NotificationButton')
                    or (kind == 'NavigationButton' and action in ('settings', 'layout', 'wall_settings', 'host_pip')
                        and button.accessibleName() == {'settings': '设置', 'layout': '布局预设',
                                                        'wall_settings': '画面墙全局设置', 'host_pip': '画中画'}[action])):
                return button
        raise ValueError('Control unavailable')

    def start(self, action, source, profile=False):
        if self.active is not None or action not in self.panels:
            raise ValueError('Invalid or overlapping probe')
        button = self.button(action, source)
        if not button.isVisible() or not button.isEnabled():
            raise ValueError('Control unavailable')
        self.active = action
        self.panel = None
        self.result = dict(action=action, source=source)
        def click():
            if self.active != action:
                return
            self.started = perf_counter()
            self.result['started_at'] = time()
            if profile:
                self.event_counts = Counter()
                QApplication.instance().installEventFilter(self)
                self.profiler = cProfile.Profile()
                self.profiler.enable()
            if action == 'add_room':
                from PySide6.QtCore import Qt
                QTest.mouseClick(button, Qt.LeftButton)
            else:
                self.bridge.buttons.click(button)
            self.result['click_return_ms'] = round((perf_counter() - self.started) * 1000, 3)
        QTimer.singleShot(0, self, click)
        return dict(queued=True)

    def shown(self, obj):
        if self.panel is not None:
            return
        if self.active in ('pip', 'pip_volume', 'region'):
            if not isinstance(obj, QDialog):
                return
        elif type(obj).__name__ != self.panels.get(self.active):
            return
        viewer = self.bridge.viewer
        if self.active == 'functions' and obj is not viewer.window().sidebar.workspace_controls.popup:
            return
        owner = obj.parentWidget()
        while owner is not None and owner is not viewer.window():
            owner = owner.parentWidget()
        if owner is None:
            return False
        if self.active in ('pip', 'pip_volume', 'region') and obj.parentWidget() is not viewer:
            return False
        self.panel = obj
        if self.profiler is None:
            obj.installEventFilter(self)
        self.result['panel_show_ms'] = round((perf_counter() - self.started) * 1000, 3)

    def eventFilter(self, obj, event):
        kind = event.type()
        if self.profiler is not None:
            self.event_counts[kind] += 1
        if kind == QEvent.Paint and self.panel is obj:
            self.result.setdefault('panel_first_paint_ms', round((perf_counter() - self.started) * 1000, 3))
            self.stop_profile()
        return False

    def stop_profile(self):
        profile = self.profiler
        if profile is None:
            return
        profile.disable()
        self.profiler = None
        QApplication.instance().removeEventFilter(self)
        self.result['qt_events'] = {kind.name: n for kind, n in self.event_counts.most_common(12)}
        self.result['window_blocked'] = self.event_counts[QEvent.WindowBlocked]
        rows = []
        for entry in sorted(profile.getstats(), key=lambda e: e.inlinetime, reverse=True)[:12]:
            code = entry.code
            name = code if isinstance(code, str) else f'{Path(code.co_filename).name}:{code.co_firstlineno} {code.co_name}'
            rows.append(dict(function=name, calls=entry.callcount, self_ms=round(entry.inlinetime * 1000, 3)))
        self.result['profile'] = rows

    def finish(self):
        self.stop_profile()
        panel, self.panel = self.panel, None
        action, self.active = self.active, None
        if panel is not None and isValid(panel) and panel.isVisible():
            panel.removeEventFilter(self)
            if action == 'pip_volume':
                from PySide6.QtCore import Qt
                from .floating_control import CropSelections
                viewer = self.bridge.viewer
                row = tuple(viewer.rows.values())[self.result['source']]
                before = self.bridge.dispatch({'command': 'crops'})
                volume = row.volume.value()
                try:
                    if (row.crop_editor is panel and not panel.isModal() and QApplication.activeModalWidget() is None
                            and row.volume.isVisible() and row.volume.isEnabled()):
                        picker = panel.findChild(CropSelections)
                        picker.add_button.click()
                        picker.use_full_frame()
                        QTest.keyClick(row.volume, Qt.Key_Left if volume else Qt.Key_Right)
                        self.result.update(volume_changed=row.audio_controls[2] != volume,
                                           draft_only=before == self.bridge.dispatch({'command': 'crops'}))
                finally:
                    row.volume.setValue(volume)
                    panel.reject()
                self.result.update(volume_restored=row.audio_controls[2] == volume,
                                   cancel_preserved=before == self.bridge.dispatch({'command': 'crops'}))
            elif isinstance(panel, QDialog):
                panel.reject()
            else:
                panel.close()
        return dict(self.result)
