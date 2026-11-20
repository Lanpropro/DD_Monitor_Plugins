"""Open the installed F1 Play workspace and optionally sample a real HLS feed."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

FIRST = 'https://example.invalid/race.m3u8'
SECOND = 'https://example.invalid/onboard.m3u8'


def history_data(session_type='Race'):
    start = '2026-03-15T07:00:00+00:00'
    return {
        'session': {'location': 'Shanghai', 'session_type': session_type,
                    'session_name': session_type, 'date_start': start},
        'drivers': [{'driver_number': 12, 'name_acronym': 'ANT'},
                    {'driver_number': 16, 'name_acronym': 'LEC'}],
        'laps': [
            {'driver_number': 12, 'lap_number': 1, 'date_start': start, 'lap_duration': 90.0},
            {'driver_number': 16, 'lap_number': 1, 'date_start': start, 'lap_duration': 89.0},
            {'driver_number': 12, 'lap_number': 2, 'date_start': '2026-03-15T07:01:30+00:00',
             'lap_duration': 86.0},
        ],
        'position': [
            {'driver_number': number, 'position': position, 'date': date}
            for number, position, date in (
                (12, 1, start), (16, 2, start), (16, 1, '2026-03-15T07:01:28+00:00'),
                (12, 2, '2026-03-15T07:01:28+00:00'))
        ],
        'intervals': [{'driver_number': 16, 'gap_to_leader': 1.2, 'date': start}],
        'stints': [{'driver_number': 12, 'lap_start': 1, 'compound': 'MEDIUM'},
                   {'driver_number': 12, 'lap_start': 2, 'compound': 'HARD'}],
    }


def check_ranking(page, directory, evidence=None):
    from ddm.player import TilePlayer

    filename = Path(directory) / 'history.json'
    filename.write_text(json.dumps(history_data()), encoding='utf-8')
    assert page.standings.load_data(filename)
    assert page.standings.table.rowCount() == 0
    with patch.object(TilePlayer, 'play'), patch.object(TilePlayer, 'playback_timing') as timing:
        page.source.setText(FIRST)
        page.play_button.click()
        timing.return_value = (92, 180)
        page.standings.set_anchor(92, 92)
        page.update_timeline()
        assert page.standings.table.item(0, 1).text() == 'LEC'
        assert page.standings.table.item(1, 3).text() == 'H'
        assert page.seek_bar.isVisible()
        with patch.object(TilePlayer, 'seek_to', return_value=True) as seek:
            page.seek_bar.setValue(230)
            seek.assert_called_once_with(23)
        timing.return_value = (23, 180)
        page.update_timeline()
        assert page.standings.table.item(0, 1).text() == 'ANT'
        assert page.standings.table.item(0, 3).text() == 'M'
        if evidence:
            assert page.grab().save(str(evidence / 'default.png'))
        filename.write_text(json.dumps(history_data('Practice')), encoding='utf-8')
        assert page.standings.load_data(filename)
        assert page.standings.table.columnCount() == 5
        page.standings.set_anchor(23, 88)
        assert page.standings.table.rowCount() == 0
        timing.return_value = (25, 180)
        page.update_timeline()
        assert page.standings.table.item(0, 1).text() == 'LEC'
        assert page.standings.table.item(0, 2).text() == '1:29.000'
    page.stop()


def check_optional_views(window, page, evidence=None):
    from ddm.player import TilePlayer

    assert page.rank_toggle.isChecked() and not page.onboard_toggle.isChecked()
    page.rank_toggle.click()
    assert page.side.isHidden()
    page.rank_toggle.click()
    with patch.object(TilePlayer, 'play') as play:
        page.source.setText(FIRST)
        page.play_button.click()
        page.onboard_toggle.click()
        page.onboard.source.setText(SECOND)
        page.onboard.play()
        assert page.onboard.player.muted
        page.onboard.promote.click()
        assert page.address == SECOND and page.onboard.address == FIRST
        assert play.call_count == 4
        previous_onboard = page.onboard.player
        page.onboard_toggle.click()
        assert previous_onboard._released and page.standings.isVisible()

    installed = next(plugin for plugin in window.plugins.plugins if plugin.context.name == 'match_sync')
    module = import_module('.race_workspace', type(installed).__module__)
    former_player = page.player
    with patch.object(type(page.context), 'create_plugin_widget', return_value=None):
        page.console_button.click()
        assert page.player is former_player and '比赛二路' in page.status.text()
    with patch.object(module.RaceViewer, 'start'):
        page.send_button.click()
        viewer = page.match
        assert viewer is not None and page.screen.currentWidget() is viewer and former_player._released
        assert viewer.settings_panel.isHidden() and viewer.send_console.isVisible()
        page.send_button.click()
        assert viewer.send_console.isHidden()
        page.console_button.click()
        assert viewer.settings_panel.isVisible() and viewer.send_console.isHidden()
        with patch.object(module.QInputDialog, 'getText', return_value=('123', True)):
            viewer.add_controls[1].click()
        assert '123' in viewer.rows
        assert viewer.rows['123'].show_chat.isChecked()
        assert viewer.send_console.checked_rooms() == ['123']
        assert viewer.send_console.rooms.count() == 1
        page.overlay_button.click()
        assert viewer.picture.danmaku_button.isChecked()
        viewer.picture.danmaku_button.setChecked(False)
        assert not page.overlay_button.isChecked()
        viewer.send_console.set_collapsed(False)
        assert viewer.send_console.isVisible()
        if evidence:
            assert page.grab().save(str(evidence / 'console.png'))
        page.console_button.click()
        assert viewer.settings_panel.isHidden() and viewer.send_console.isVisible()
        page.back_button.click()
        assert page.match is None and not viewer.running and window.wall.isVisible()


def check_controls(window, entry, page):
    from ddm.player import TilePlayer

    page.source.setText('file:///tmp/sample.m3u8')
    page.play_button.click()
    assert page.player is None and 'HTTP' in page.status.text()

    source = 'https://example.invalid/live.m3u8?token=%41%2Fb'
    page.source.setText(source)
    with patch.object(TilePlayer, 'play') as play:
        page.play_button.click()
        play.assert_called_once_with(source, profile='m3u_live')
        player = page.player
        assert player.muted and page.video.isVisible()
        player.stateChanged.emit('buffering')
        assert '缓冲' in page.status.text()
        page.mute_button.click()
        assert not player.muted
        page.stop_button.click()
        assert page.player is None and player._released

        page.play_button.click()
        player = page.player
        page.back_button.click()
        assert player._released and page.isHidden() and window.wall.isVisible()
        entry.click()
        assert page.isVisible() and page.player is None

        page.play_button.click()
        player = page.player
        window.plugins.unload()
        assert player._released and window.wall.isVisible()


def check_live(window, page, source, output, seconds):
    from PySide6.QtTest import QTest

    output.mkdir(parents=True, exist_ok=True)
    page.source.setText(source)
    started = monotonic()
    page.play_button.click()
    player = page.player
    while player and monotonic() - started < 30:
        if player.state == 'playing' and player._prop('vo-configured') and player._prop('width'):
            break
        QTest.qWait(200)
    else:
        raise RuntimeError(f'No native video within 30 seconds, state={getattr(player, "state", None)}')
    assert page.video.isVisible() and player._bound_hwnd
    startup = monotonic() - started
    page.source.setText('CCTV-5+（验收源）')
    QTest.qWait(150)
    window.grab().save(str(output / 'workspace.png'))
    begin = last_progress = monotonic()
    initial_position = latest_position = player._prop('time-pos')
    longest_gap = 0.0
    samples = []
    while monotonic() - begin < seconds:
        QTest.qWait(250)
        now = monotonic()
        position = player._prop('time-pos')
        if position is not None and position > latest_position:
            longest_gap = max(longest_gap, now - last_progress)
            last_progress, latest_position = now, position
        samples.append({'seconds': round(now - begin, 2), 'position': position,
                        'state': player.state, 'rebuffering': bool(player._prop('paused-for-cache')),
                        'decoder_drops': player._prop('decoder-frame-drop-count'),
                        'display_drops': player._prop('frame-drop-count')})

    summary = {'startup_seconds': round(startup, 2), 'duration': samples[-1]['seconds'],
               'position_advanced': round(latest_position - initial_position, 2),
               'longest_progress_gap': round(max(longest_gap, now - last_progress), 2),
               'rebuffer_samples': sum(sample['rebuffering'] for sample in samples),
               'states': sorted({sample['state'] for sample in samples}),
               'resolution': [player._prop('width'), player._prop('height')],
               'fps': player._prop('container-fps'), 'codec': player._prop('video-format'),
               'audio_codec': player._prop('audio-codec-name'), 'format': player._prop('file-format'),
               'decoder_drops': samples[-1]['decoder_drops'], 'display_drops': samples[-1]['display_drops']}
    player.player.screenshot_to_file(str(output / 'video-frame.png'), includes='video')
    (output / 'samples.json').write_text(json.dumps({'summary': summary, 'samples': samples}), encoding='utf-8')
    print(json.dumps(summary), flush=True)
    assert summary['format'] == 'hls' and summary['position_advanced'] > 0
    page.back_button.click()
    assert player._released and window.wall.isVisible()


def check_match_live(window, page, source, output, seconds):
    from PySide6.QtTest import QTest

    page.source.setText(source)
    page.console_button.click()
    viewer = page.match
    assert viewer is not None and viewer.running
    start = monotonic()
    while viewer.picture_position() is None and monotonic() - start < 45:
        QTest.qWait(200)
    first = viewer.picture_position()
    assert first is not None, viewer.rows[viewer.feed.room_id].video_text
    decoder = viewer.rows[viewer.feed.room_id].decoder
    page.console_button.click()
    page.send_button.click()
    assert viewer.settings_panel.isHidden() and viewer.send_console.isVisible()
    assert viewer.send_console.rooms.count() == 0
    page.send_button.click()
    QTest.qWait(seconds * 1000)
    last = viewer.picture_position()
    assert viewer.running and decoder is viewer.rows[viewer.feed.room_id].decoder
    assert last is not None and last > first
    output.mkdir(parents=True, exist_ok=True)
    page.source.setText('HLS 实播验收源')
    page.console_button.click()
    QTest.qWait(200)
    assert page.grab().save(str(output / 'match-live.png'))
    print(f'PASS: real HLS frames advanced {last - first:.2f}s with console collapsed', flush=True)
    page.back_button.click()
    assert not viewer.running and window.wall.isVisible()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--live-source-file', type=Path)
    parser.add_argument('--source-index', type=int, default=0)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--ui-evidence', type=Path)
    parser.add_argument('--via-match', action='store_true')
    args = parser.parse_args()
    if args.live_source_file and (not args.evidence or args.seconds < 1):
        parser.error('Live sampling needs --evidence and a positive sample duration')
    if args.via_match and not args.live_source_file:
        parser.error('--via-match requires --live-source-file')

    sys.path.insert(0, str(args.host.resolve()))
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'windows' if args.live_source_file or args.ui_evidence else 'offscreen')
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from ddm import config, images, plugins, theme
    from ddm.app import MainWindow

    with TemporaryDirectory() as directory:
        config.DATA_ROOT = images.REPO = plugins.DEFAULT_PLUGINS_DIR = directory
        config.CONFIG_PATH = str(Path(directory) / 'config.json')
        installer = plugins.PluginManager(plugins_dir=directory, enabled=[])
        assert installer.install_zip(str(ROOT / 'dist/f1_play-0.1.zip')) == 'f1_play'
        enabled = ['f1_play']
        if not args.live_source_file or args.via_match:
            assert installer.install_zip(str(ROOT / 'dist/match_sync-1.7.0.zip')) == 'match_sync'
            enabled.append('match_sync')
        app = QApplication([])
        app.setStyleSheet(theme.qss())
        state = {'plugins_enabled': enabled, 'settings': {'auto_update': False, 'close_behavior': 'exit'}}
        with (patch.object(MainWindow, '_restore_platform_accounts'),
              patch.object(MainWindow, 'refresh_account'),
              patch.object(MainWindow, '_check_startup_update')):
            window = MainWindow([], [], layout_id='1x1', state=state)
            try:
                assert not window.plugins.skipped, window.plugins.skipped
                window.setAttribute(Qt.WA_ShowWithoutActivating)
                if args.ui_evidence or args.via_match:
                    window.resize(1250, 780)
                else:
                    window.resize(1000, 600)
                window.show()
                QTest.qWait(100)
                assert window.wall.isVisible()
                entry = window.sidebar.workspace_controls.entries['f1_play', 'entry'][1]
                entry.click()
                owner, page, _ = window.plugins._workspace_pages.active
                assert owner == 'f1_play' and page.isVisible() and window.wall.isHidden()
                if args.live_source_file:
                    sources = args.live_source_file.read_text(encoding='utf-8-sig').splitlines()
                    check = check_match_live if args.via_match else check_live
                    check(window, page, sources[args.source_index], args.evidence.resolve(), args.seconds)
                else:
                    if args.ui_evidence:
                        args.ui_evidence.mkdir(parents=True, exist_ok=True)
                    check_ranking(page, directory, args.ui_evidence)
                    check_optional_views(window, page, args.ui_evidence)
                    entry.click()
                    check_controls(window, entry, page)
            finally:
                window.close()
                app.processEvents()
    print('PASS: installed F1 Play workspace', flush=True)


if __name__ == '__main__':
    main()
