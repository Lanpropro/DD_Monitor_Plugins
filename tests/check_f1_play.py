"""Open the installed F1 Play workspace and optionally sample a real HLS feed."""
import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--live-source-file', type=Path)
    parser.add_argument('--source-index', type=int, default=0)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--seconds', type=int, default=60)
    args = parser.parse_args()
    if args.live_source_file and (not args.evidence or args.seconds < 1):
        parser.error('Live sampling needs --evidence and a positive sample duration')

    sys.path.insert(0, str(args.host.resolve()))
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'windows' if args.live_source_file else 'offscreen')
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
        app = QApplication([])
        app.setStyleSheet(theme.qss())
        state = {'plugins_enabled': ['f1_play'], 'settings': {'auto_update': False, 'close_behavior': 'exit'}}
        with (patch.object(MainWindow, '_restore_platform_accounts'),
              patch.object(MainWindow, 'refresh_account'),
              patch.object(MainWindow, '_check_startup_update')):
            window = MainWindow([], [], layout_id='1x1', state=state)
            try:
                assert not window.plugins.skipped, window.plugins.skipped
                window.setAttribute(Qt.WA_ShowWithoutActivating)
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
                    check_live(window, page, sources[args.source_index], args.evidence.resolve(), args.seconds)
                else:
                    check_controls(window, entry, page)
            finally:
                window.close()
                app.processEvents()
    print('PASS: installed F1 Play workspace', flush=True)


if __name__ == '__main__':
    main()
