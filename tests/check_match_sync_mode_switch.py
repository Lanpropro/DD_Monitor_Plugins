"""Mode routing and sparse diagnostics with empty widgets; no media or accounts."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch


def check(app, _host, module):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QToolButton, QVBoxLayout, QWidget
    from ddm import plugins
    diagnostic = import_module(module.__package__ + '.diagnostics')
    calls, switches = [], []
    host = QWidget()
    host.setAttribute(Qt.WA_DontShowOnScreen)
    host.resize(900, 500)
    host.settings, host.rooms, host.players = {}, [], {}
    def set_playback(active):
        if not switches or switches[-1] != active:
            switches.append(active)
    host.set_match_sync_playback = set_playback
    host.sidebar = sidebar = QWidget(host)
    sidebar.side, sidebar.collapsed = 'left', False
    sidebar.tool_row = QWidget(sidebar)
    row = QHBoxLayout(sidebar.tool_row)
    sidebar.layout_button = QToolButton(sidebar.tool_row)
    sidebar.layout_button.setText('layout')
    row.addWidget(sidebar.layout_button)
    QVBoxLayout(sidebar).addWidget(sidebar.tool_row)
    sidebar._bar_row = QWidget(sidebar)
    sidebar._bar_row_box = QHBoxLayout(sidebar._bar_row)
    sidebar.toggle_button = QToolButton(sidebar._bar_row)
    sidebar._bar_row_box.addWidget(sidebar.toggle_button)
    sidebar.layout().addWidget(sidebar._bar_row)
    host._content = QWidget(host)
    host.wall = QWidget(host._content)
    QVBoxLayout(host._content).addWidget(host.wall)
    box = QHBoxLayout(host)
    box.addWidget(sidebar)
    box.addWidget(host._content, 1)
    host.show()
    app.processEvents()

    class EmptyViewer(QDialog):
        def __init__(self, context, _sources, _room):
            super().__init__(context.window)
            assert plugin.switch_probe.button_painted, 'Constructed before visible button feedback'
            calls.append('construct')
            self.rows, self.running = {}, False
        def embed(self, content):
            self.setParent(content, Qt.Widget)
            self.setGeometry(content.rect())
        def showEvent(self, event):
            calls.append('show')
            super().showEvent(event)
        def closeEvent(self, event):
            calls.append('close')
            super().closeEvent(event)

    with tempfile.TemporaryDirectory(prefix='ddm-mode-switch-') as directory:
        with patch.object(module.config_module, 'REPO', directory):
            manager = plugins.PluginManager(window=host, plugins_dir=str(Path(directory) / 'plugins'))
            context = plugins.PluginContext(manager, 'mode_switch_check')
            plugin = module.MatchSyncPlugin()
            plugin.on_load(context)
            probe = plugin.switch_probe
            try:
                app.processEvents()
                with patch.object(module, 'Viewer', EmptyViewer):
                    plugin.button.setChecked(True)
                    assert plugin.viewer is None and plugin.entry._mode
                    QTest.qWait(70)
                    assert calls == ['construct', 'show'], calls
                    plugin.button.setChecked(False)
                    assert 'close' not in calls
                    QTest.qWait(70)
                    assert calls[-1] == 'close'
                    before = len(calls)
                    for active in (True, False, True):
                        plugin.button.setChecked(active)
                    QTest.qWait(70)
                    assert calls[before:] == ['show'], calls
                    assert plugin.button.isChecked() and plugin.entry._mode
                    plugin.button.setChecked(False)
                    QTest.qWait(200)
                    # Portrait reparenting and the system animation-off path.
                    sidebar.side = 'top'
                    with patch.object(module.motion, 'enabled', return_value=False):
                        plugin.entry._place()
                        assert plugin.entry.parentWidget() is sidebar._bar_row
                        plugin.button.setChecked(True)
                        QTest.qWait(70)
                        assert plugin.entry.parentWidget() is sidebar.tool_row
                        plugin.button.setChecked(False)
                        QTest.qWait(70)
                        assert plugin.entry.parentWidget() is sidebar._bar_row
                    # A hidden entry must not wait indefinitely for a paint.
                    sidebar.side, sidebar.collapsed = 'left', True
                    plugin.entry._place()
                    before = len(calls)
                    plugin.button.setChecked(True)
                    QTest.qWait(70)
                    assert calls[before:] == ['show']
                    plugin.button.setChecked(False)
                    QTest.qWait(70)
                    # Unload cancels queued work before the viewer can reopen.
                    plugin.button.setChecked(True)
                    plugin.on_unload()
                    before = len(calls)
                    QTest.qWait(40)
                    assert len(calls) == before
                probe.thread.join(2)
                records = [json.loads(line) for line in probe.path.read_text(encoding='utf-8').splitlines()]
                first = [r['phase'] for r in records if r['switch'] == 1]
                assert first.index('button_paint_completed') < first.index('stage_begin'), first
                assert first.count('target_paint_event') == 1
                assert any(r['phase'] == 'superseded' for r in records)
                # Real Viewer lifecycle hooks, with zero sources and no playback.
                plugin = module.MatchSyncPlugin()
                sidebar.collapsed = False
                plugin.on_load(context)
                real_probe = plugin.switch_probe
                plugin.button.setChecked(True)
                QTest.qWait(100)
                assert plugin.viewer is not None and not plugin.viewer.running
                plugin.button.setChecked(False)
                QTest.qWait(100)
                plugin.on_unload()
                real_probe.thread.join(2)
                stages = {r.get('stage') for r in map(json.loads, real_probe.path.read_text(encoding='utf-8').splitlines())}
                assert {'suspend_host_playback', 'show_controls', 'stop_audio', 'stop_sources',
                        'restore_host_widgets', 'resume_host_playback'} <= stages, stages
                assert switches == [True, False], switches
                # Verify writer location and slow-stage stack routing, not performance.
                writer_threads = []
                original = diagnostic.RotatingFileHandler
                def handler(*args, **kwargs):
                    writer_threads.append(threading.get_ident())
                    return original(*args, **kwargs)
                slow = diagnostic.SwitchProbe(host, Path(directory) / 'slow.jsonl')
                with patch.object(diagnostic, 'RotatingFileHandler', handler):
                    slow.begin(True)
                    with slow.span('controlled_wait'):
                        threading.Event().wait(.3)
                    slow.finish(True)
                    slow.close()
                    slow.thread.join(2)
                raw = slow.path.read_text(encoding='utf-8')
                assert any(r['phase'] == 'slow_stage' and r['stage'] == 'controlled_wait'
                           for r in map(json.loads, raw.splitlines()))
                assert writer_threads and all(t != threading.get_ident() for t in writer_threads)
                assert slow.events.maxlen == 256 and not slow.thread.is_alive()
                with patch.dict(os.environ, DDM_MATCH_DIAGNOSTICS='0'):
                    disabled = diagnostic.SwitchProbe(host, Path(directory) / 'disabled.jsonl')
                    disabled.begin(True)
                    with disabled.span('disabled'):
                        pass
                    disabled.close()
                    assert disabled.thread is None and not disabled.path.exists()
                return dict(passed=True, media_playback=False, feedback_before_construction=True,
                            latest_request_only=True, portrait_and_animation_off=True,
                            hidden_entry_fallback=True, unload_cancels_pending=True,
                            real_viewer_hooks=True, background_writer=True, bounded_sampling=True)
            finally:
                plugin.on_unload()
                host.close()
                app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    from PySide6.QtWidgets import QApplication
    module = import_module('plugins.match_sync.plugin')
    app = QApplication([])
    try:
        print(check(app, None, module))
    finally:
        app.shutdown()


if __name__ == '__main__':
    main()
