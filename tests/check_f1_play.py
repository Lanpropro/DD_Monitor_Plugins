"""Check F1 Play sidebar drops and optionally sample an imported live channel."""
import argparse
from PySide6.QtCore import Qt
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from PySide6.QtWidgets import QApplication
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

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


def drag_card(window, room_id, viewer):
    from PySide6.QtCore import QMimeData, QPoint
    from PySide6.QtGui import QPixmap
    from PySide6.QtTest import QTest
    from ddm.card_drop import DURATION_MS
    from ddm.sidebar_drag import ROOM_MIME

    session = window.sidebar.drag_session
    item = next(item for item in window.sidebar.items() if item.room['room_id'] == room_id)
    mime = QMimeData()
    mime.setData(ROOM_MIME, room_id.encode())
    ghost = QPixmap(32, 32)
    ghost.fill(Qt.transparent)
    position = viewer.picture.mapToGlobal(viewer.picture.rect().center())
    assert session.start(item, mime, ghost, QPoint(), position)
    assert session.finish()
    QTest.qWait(DURATION_MS + 30)


def check_mask(window, page, directory):
    from PySide6.QtGui import QColor, QImage

    skins = Path(directory) / 'utils' / 'skins'
    skins.mkdir(parents=True, exist_ok=True)
    wallpaper = QImage(24, 24, QImage.Format_RGB32)
    wallpaper.fill(QColor('#ff3178'))
    assert wallpaper.save(str(skins / 'f1-mask.png'))
    window.settings.update(skin_background='utils/skins/f1-mask.png', skin_mask=65, skin_mask_enabled=True)
    window.refresh_skin()
    assert ', 0.65)' in page.styleSheet()
    window.settings['skin_mask'] = 83
    window.refresh_skin()
    assert ', 0.83)' in page.styleSheet()
    window.settings['skin_mask_enabled'] = False
    window.refresh_skin()
    assert ', 0.0)' in page.styleSheet()
    window.settings['skin_mask_enabled'] = True
    window.refresh_skin()
    assert ', 0.83)' in page.styleSheet()


def check_workspace(window, entry, page, directory, evidence):
    from PySide6.QtWidgets import QLineEdit

    match = page.match
    assert match.empty_hint.isVisible() and not match.running
    assert match.picture.add_room_button.isHidden()
    assert not any(edit.isVisible() for edit in page.findChildren(QLineEdit))
    if evidence:
        assert page.grab().save(str(evidence / 'empty.png'))
    with (patch.object(type(match), '_start_row', autospec=True) as start_row,
          patch.object(type(match.audio), 'start')):
        drag_card(window, '101', match)
        assert match.running and match.main.currentData() == '101' and not match.empty_hint.isVisible()
        assert start_row.call_count == 1
        drag_card(window, '102', match)
        assert match.main.currentData() == '101' and set(match.rows) == {'101', '102'}
        assert start_row.call_count == 2

        page.console_button.click()
        assert match.settings_panel.isVisible() and match.rows['102'].volume.isVisible()
        page.send_button.click()
        assert match.send_console.isVisible() and set(match.send_console.rows) == {'101', '102'}
        page.overlay_button.click()
        assert match.picture.danmaku_button.isChecked()
        match.picture.danmaku_button.setChecked(False)
        assert not page.overlay_button.isChecked()
        if evidence:
            assert page.grab().save(str(evidence / 'console.png'))
        page.console_button.click()
        page.send_button.click()
        assert match.settings_panel.isHidden() and match.send_console.isHidden() and match.running

        filename = Path(directory) / 'history.json'
        for session, columns in (('Race', 4), ('Practice', 5)):
            filename.write_text(json.dumps(history_data(session)), encoding='utf-8')
            assert page.standings.load_data(filename)
            assert page.standings.table.columnCount() == columns
            match.rows['101'].actual_position = 92
            page.standings.set_anchor(page.picture_position(), 92)
            page.standings.update_picture()
            assert page.standings.table.item(0, 1).text() == 'LEC'
            match.rows['101'].actual_position = 23
            page.standings.update_picture()
            if session == 'Race':
                assert page.standings.table.item(0, 1).text() == 'ANT'
            else:
                assert page.standings.table.rowCount() == 0
        match.rows['102'].main_button.click()
        assert match.main.currentData() == '102' and page.standings.table.rowCount() == 0
        match.rows['101'].main_button.click()
        assert match.main.currentData() == '101'

        page.rank_toggle.click()
        assert page.side.isHidden()
        page.onboard_toggle.click()
        app = QApplication.instance()
        app.processEvents()
        onboard = page.onboard
        assert onboard.empty_hint.isVisible() and not page.promote.isEnabled()
        assert onboard.picture.add_room_button.isHidden()
        drag_card(window, '103', onboard)
        assert onboard.running and not onboard.rows['103'].audible.isChecked()
        assert page.promote.isEnabled() and match.main.currentData() == '101'
        page.promote.click()
        assert match.main.currentData() == '103' and onboard.main.currentData() == '101'
        assert set(match.rows) == {'102', '103'} and set(onboard.rows) == {'101'}
        page.onboard_toggle.click()
        assert not onboard.running and page.onboard is None and page.side.isHidden()
        page.rank_toggle.click()

    page.back_button.click()
    assert not match.running and page.match is None and window.wall.isVisible()
    entry.click()
    assert page.match is not match and not page.match.rows and page.isVisible()


def check_live(window, page, room_id, evidence, seconds):
    from time import monotonic
    from PySide6.QtTest import QTest

    match = page.match
    started = monotonic()
    drag_card(window, room_id, match)
    while page.picture_position() is None and monotonic() - started < 45:
        QTest.qWait(100)
    first = page.picture_position()
    row = match.rows[room_id]
    assert first is not None and not match.canvas.image.isNull(), row.video_text
    ready = monotonic()
    if evidence:
        assert match.canvas.image.save(str(evidence / 'video-frame.png'))
        assert page.grab().save(str(evidence / 'live-workspace.png'))
    samples = []
    while monotonic() - ready < seconds:
        QTest.qWait(250)
        samples.append({'time': round(monotonic() - ready, 2), 'position': page.picture_position(),
                        'waiting': match.canvas.waiting, 'presented': match.canvas.presented})
    result = {'startup_seconds': round(ready - started, 2), 'advanced_seconds': round(page.picture_position() - first, 2),
              'duration': samples[-1]['time'], 'waiting_samples': sum(item['waiting'] for item in samples),
              'resolution': list(row.decoder.video_size), 'fps': row.decoder.fps}
    if evidence:
        (evidence / 'samples.json').write_text(json.dumps({'summary': result, 'samples': samples}), encoding='utf-8')
    print(json.dumps(result), flush=True)
    assert result['advanced_seconds'] > 0
    page.back_button.click()
    assert not match.running and window.wall.isVisible()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--live-source-file', type=Path)
    parser.add_argument('--m3u-plugin', type=Path)
    parser.add_argument('--source-index', type=int, default=0)
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--ui-evidence', type=Path)
    args = parser.parse_args()
    if args.live_source_file and (not args.m3u_plugin or args.seconds < 1):
        parser.error('Live sampling needs --m3u-plugin and a positive duration')

    sys.path.insert(0, str(args.host.resolve()))
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from shutil import copytree
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from ddm import config, images, plugins, theme
    from ddm.app import MainWindow

    evidence = args.ui_evidence
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as directory:
        config.DATA_ROOT = images.REPO = plugins.DEFAULT_PLUGINS_DIR = directory
        config.CONFIG_PATH = str(Path(directory) / 'config.json')
        installer = plugins.PluginManager(plugins_dir=directory, enabled=[])
        assert installer.install_zip(str(ROOT / 'dist/f1_play-0.2.zip')) == 'f1_play'
        assert installer.install_zip(str(ROOT / 'dist/match_sync-1.7.0.zip')) == 'match_sync'
        enabled = ['f1_play', 'match_sync']
        room_id = None
        settings = {}
        if args.live_source_file:
            from hashlib import sha256

            copytree(args.m3u_plugin, Path(directory) / 'm3u_live',
                     ignore=lambda _path, names: {'__pycache__'} & set(names))
            enabled.append('m3u_live')
            address = args.live_source_file.read_text(encoding='utf-8-sig').splitlines()[args.source_index].strip()
            room_id = 'm3u:' + sha256(address.encode()).hexdigest()
            settings['m3u_live'] = {'channels': {room_id: {'name': 'CCTV-5+（验收源）', 'url': address}}}
            rooms = [{'room_id': room_id, 'uname': 'CCTV-5+（验收源）', 'platform': 'm3u'}]
        else:
            rooms = [{'room_id': str(number), 'uname': label}
                     for number, label in ((101, '主画面'), (102, '解说'), (103, '车载'))]
        app = QApplication([])
        app.setStyleSheet(theme.qss())
        state = {'plugins_enabled': enabled, 'plugins': settings,
                 'settings': {'auto_update': False, 'close_behavior': 'exit'}}
        with (patch.object(MainWindow, '_restore_platform_accounts'),
              patch.object(MainWindow, 'refresh_account'),
              patch.object(MainWindow, 'load_room_avatars'),
              patch.object(MainWindow, 'refresh_status'),
              patch.object(MainWindow, '_check_startup_update')):
            window = MainWindow(rooms, [], layout_id='1x1', state=state)
            try:
                assert not window.plugins.skipped, window.plugins.skipped
                window.setAttribute(Qt.WA_ShowWithoutActivating)
                window.resize(1440, 900)
                window.show()
                if app.platformName() == 'offscreen':
                    # Keep both video drop targets inside the 800×800 virtual screen.
                    window.move(-550, 0)
                QTest.qWait(100)
                entry = window.sidebar.workspace_controls.entries['f1_play', 'entry'][1]
                rendered = entry.icon().pixmap(18, 18).toImage()
                icon = QImage(str(Path(directory) / 'f1_play/icon.png'))
                assert not rendered.isNull() and not icon.isNull()
                assert any(rendered.pixelColor(x, y).alpha() > 64
                           for y in range(rendered.height()) for x in range(rendered.width()))
                assert any(icon.pixelColor(x, y).getRgb() == (255, 255, 255, 255)
                           for y in range(icon.height()) for x in range(icon.width()))
                entry.click()
                owner, page, _ = window.plugins._workspace_pages.active
                assert owner == 'f1_play' and page.isVisible() and window.wall.isHidden()
                QTest.qWait(100)
                if args.live_source_file:
                    assert window.plugins.platform_for(room_id).room_info(room_id).uname == 'CCTV-5+（验收源）'
                    check_live(window, page, room_id, evidence, args.seconds)
                else:
                    check_mask(window, page, directory)
                    check_workspace(window, entry, page, directory, evidence)
                    window.plugins.unload()
                    assert window.wall.isVisible()
            finally:
                window.close()
                app.processEvents()
    print('PASS: F1 Play sidebar drop, shared playback workspace and host background mask', flush=True)


if __name__ == '__main__':
    main()
