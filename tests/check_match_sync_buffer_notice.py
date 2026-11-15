"""Buffer episode classification and console-only warnings; no media or network."""
import argparse
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from ddm import plugins
    with tempfile.TemporaryDirectory() as directory:
        host.rooms, host.settings = [], {}
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        context = plugins.PluginContext(manager, 'buffer_notice_check')
        viewer = module.Viewer(context, {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            viewer.resize(1800, 950)
            viewer.add_room('1', {'alias': '主路'})
            viewer.add_room('2', {'alias': '副路'})
            for widget in viewer.add_controls:
                widget.hide()  # Match the toolbar used when embedded in the host.
            ends = {'1': 100., '2': 200.}
            for key, row in viewer.rows.items():
                row.decoder = SimpleNamespace(history=SimpleNamespace(bounds_at=lambda _target, k=key: (0., ends[k])),
                                              last_frame_received=0., fps=30., stop=lambda: None)
                row.match_confirmed = True
            viewer.running = True
            viewer.alignment.reference = '1'
            viewer.alignment.lags = {'1': 0., '2': 0.}
            viewer.show()
            QTest.qWait(50)
            app.processEvents()
            main, peer = viewer.rows.values()
            with patch.object(context, 'notify', side_effect=AssertionError('No host notifications')), \
                    patch.object(viewer, '_queue_rematch'):
                def health(now, clock):
                    with patch.object(module.time, 'monotonic', return_value=now):
                        viewer._update_sync_health(clock, {'1': 0., '2': 0.})

                def recover(now):
                    health(now, 99.)
                    health(now + .8, 99.)

                # An initial wait is not exhaustion of previously playing media.
                health(1, 99.97)
                health(2, 99.97)
                assert not main.buffer_exhaustions
                recover(3)
                height = viewer.settings_panel.height()
                for now in (10, 20, 30):
                    health(now, 99.97)
                    health(now + .2, 99.97)
                    if now < 30:
                        recover(now + 1)
                        assert main.buffer_warning.isHidden()
                assert len(main.buffer_exhaustions) == 3, 'Count episodes, not render polls'
                assert not main.buffer_warning.isHidden()
                assert not viewer.buffer_notice.isHidden() and viewer.notice.isHidden()
                assert '主路' in viewer.buffer_notice.text()
                assert '为获得更好的观看体验，建议切换主画面' in viewer.buffer_notice.text()
                assert peer.buffer_warning.isHidden() and not peer.buffer_exhaustions
                app.processEvents()
                assert viewer.console_toolbar.isAncestorOf(viewer.buffer_notice)
                assert viewer.console_toolbar.isAncestorOf(viewer.notice)
                assert not viewer.console_footer.isAncestorOf(viewer.buffer_notice)
                assert viewer.buffer_notice.alignment() == Qt.AlignCenter
                center = viewer.console_notices.mapTo(viewer.console_toolbar, viewer.console_notices.rect().center()).x()
                assert abs(center - viewer.console_toolbar.rect().center().x()) <= 1
                assert not viewer.buffer_notice.toolTip() and not main.buffer_warning.toolTip()
                assert not main.buffer_warning.tooltip_at(QPoint(8, 8))
                assert not main.buffer_warning.text(), 'Use the native status icon instead of a text badge'
                assert main.buffer_warning.mapTo(main, QPoint()).x() > main.name_label.mapTo(main, QPoint()).x()
                assert viewer.settings_panel.height() == height, ('A warning must not resize the console', height, viewer.settings_panel.height())
                if output:
                    output = Path(output)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    viewer.grab().save(str(output))
                toolbar_height = viewer.console_toolbar.height()
                notice_text = viewer.buffer_notice.text()
                viewer.buffer_notice.setText('主路：' + '缓冲反复耗尽，请切换主画面；' * 40)
                for width in (1100, 1800):
                    viewer.resize(width, 950)
                    QTest.qWait(30)
                    assert viewer.console_toolbar.height() == toolbar_height, 'Long notices stay on one line'
                    assert viewer.buffer_notice.width() > 0
                    assert viewer.settings_panel.height() == height
                viewer.buffer_notice.setText(notice_text)
                module.theme.apply('light')
                app.processEvents()
                if output:
                    viewer.grab().save(str(output.with_name(output.stem + '-light.png')))
                module.theme.apply('dark')
                viewer.dismiss_buffer_notice()
                assert not viewer.notice.isHidden(), 'Normal status returns in the same header area'
                recover(31)
                health(40, 99.97)
                assert viewer.buffer_notice.isHidden(), 'Repeated incidents within cooldown do not spam'
                # A lengthy single stall remains one episode and keeps its icon.
                health(120, 99.97)
                assert not main.buffer_warning.isHidden()
                recover(121)
                health(170, 99.)
                assert not main.buffer_warning.isHidden()
                health(182, 99.)
                assert main.buffer_warning.isHidden(), 'Clear after a minute of healthy playback'

                # RGB display lag with intact encoded cache must not count.
                peer.decoder.picture_at = lambda _stamp: None
                viewer.compare.blockSignals(True)
                viewer.compare.setChecked(True)
                viewer.compare.blockSignals(False)
                viewer.comparison_panel.shown_rooms = ['2']
                health(190, 99.)
                health(191, 99.)
                assert not peer.buffer_exhaustions
                del peer.decoder.picture_at

                # Incidents in different sources cannot combine to reach three.
                for row in viewer.rows.values():
                    row.clear_buffer_warning()
                health(200, 99.97)
                recover(201)
                ends['2'] = 99.04
                health(210, 99.)
                ends['2'] = 200.
                recover(211)
                health(220, 99.97)
                assert main.buffer_warning.isHidden() and peer.buffer_warning.isHidden()
                recover(221)
                # Widely separated incidents expire rather than accumulating forever.
                health(290, 99.97)
                assert len(main.buffer_exhaustions) == 1 and main.buffer_warning.isHidden()
                main.paused = True
                health(291, 99.97)
                assert not main.buffer_exhaustions
                main.paused = False
                viewer.replay.delay = 1
                health(292, 99.97)
                assert not main.buffer_exhaustions
                viewer.replay.live()
                viewer.stop()
                assert viewer.buffer_notice.isHidden() and not viewer.buffer_notice_timer.isActive()
                assert all(row.buffer_warning.isHidden() and not row.buffer_exhaustions for row in viewer.rows.values())
            return dict(passed=True, console_only=True, three_episodes_in_60_seconds=True,
                        per_source=True, cooldown=True, stable_recovery_clears=True,
                        startup_pause_replay_display_lag_excluded=True, stable_console_height=True,
                        media_playback=False, real_network_requests=0)
        finally:
            viewer.close()
            viewer.deleteLater()
            app.sendPostedEvents(None, 52)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(args.host)]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        print(check(app, QWidget(), module, args.output))
    app.shutdown()


if __name__ == '__main__':
    main()
