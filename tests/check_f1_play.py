"""Check F1 Play sidebar drops and optionally sample an imported live channel."""
import argparse
from copy import deepcopy
from PySide6.QtCore import QEvent, Qt
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from PySide6.QtWidgets import QApplication, QWidget
from unittest.mock import Mock, patch


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
    from math import isclose
    from PySide6.QtGui import QColor, QImage

    skins = Path(directory) / 'utils' / 'skins'
    skins.mkdir(parents=True, exist_ok=True)
    wallpaper = QImage(24, 24, QImage.Format_RGB32)
    wallpaper.fill(QColor('#ff3178'))
    assert wallpaper.save(str(skins / 'f1-mask.png'))
    window.settings.update(skin_background='utils/skins/f1-mask.png', skin_mask=65, skin_mask_enabled=True)
    window.refresh_skin()
    assert page.skin_visible and isclose(page.panel_alpha, .72 + .28 * .65)
    window.settings['skin_mask'] = 83
    window.refresh_skin()
    assert isclose(page.panel_alpha, .72 + .28 * .83)
    window.settings['skin_mask_enabled'] = False
    window.refresh_skin()
    assert isclose(page.panel_alpha, .72)
    window.settings['skin_mask_enabled'] = True
    window.refresh_skin()
    assert isclose(page.panel_alpha, .72 + .28 * .83)


def check_entry_icon(window, directory, evidence):
    from PySide6.QtGui import QImage

    key = ('f1_play', 'entry')
    controls = window.sidebar.workspace_controls
    entry = controls.entries[key][1]
    assert entry.property('dockMotion') == 'slide-in-left'
    launch = controls._rows[key][1]
    for button, size in ((entry, 18), (launch, 20)):
        assert button.iconSize().width() == size
        for scale in (1, 2):
            image = button.icon().pixmap(button.iconSize(), float(scale)).toImage()
            ink = [(x, y) for y in range(image.height()) for x in range(image.width())
                   if image.pixelColor(x, y).alpha() >= 160]
            xs, ys = zip(*ink)
            assert max(xs) - min(xs) + 1 >= 14 * scale
            assert max(ys) - min(ys) + 1 >= 7 * scale

    catalog_icon = QImage(str(Path(directory) / 'f1_play/icon.png'))
    assert not catalog_icon.isNull()
    assert any(catalog_icon.pixelColor(x, y).getRgb() == (255, 255, 255, 255)
               for y in range(catalog_icon.height()) for x in range(catalog_icon.width()))
    if evidence:
        assert entry.grab().save(str(evidence / 'rail-icon.png'))
    return entry


def layout_signature(page):
    return (
        type(page), page.geometry(), page.picture_split.sizes(), page.body_split.sizes(),
        page.picture.isVisible(), page.settings_panel.isVisible(), page.chat_pane.isVisible(),
        page.send_console.isVisible(), tuple(widget.isHidden() for widget in page.add_controls),
    )


def check_floating_over_standings(window, match, tools, evidence):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QImage, QWheelEvent
    from PySide6.QtTest import QTest

    frame = QImage(320, 180, QImage.Format_RGB32)
    frame.fill(Qt.red)
    floating = match.show_floating_crop(match.rows['102'], (0, 0, 1, 1), (1, frame))
    tools.onboard_toggle.click()
    QTest.qWait(50)
    assert tools.onboard.isVisible()
    parent = floating.parentWidget()
    target = parent.mapFromGlobal(tools.standings.mapToGlobal(QPoint()))
    floating.fit_geometry(target.x() + 12, target.y() + 42, 180)
    # The host raises the active plugin page independently of its siblings.
    assert tools.context.show_workspace(match, manages_content=True)
    QTest.qWait(50)
    for point in (floating.rect().center(), floating.adjust_rect().center(),
                  floating.lock_rect().center(), floating.close_rect().center()):
        assert window.childAt(window.mapFromGlobal(floating.mapToGlobal(point))) is floating
        if QApplication.platformName() == 'windows':
            from ctypes import POINTER, WinDLL, byref
            from ctypes.wintypes import BOOL, HWND, POINT, UINT

            user32 = WinDLL('user32')
            user32.ChildWindowFromPointEx.argtypes = HWND, POINT, UINT
            user32.ChildWindowFromPointEx.restype = HWND
            user32.ScreenToClient.argtypes = HWND, POINTER(POINT)
            user32.ScreenToClient.restype = BOOL
            user32.ClientToScreen.argtypes = HWND, POINTER(POINT)
            user32.ClientToScreen.restype = BOOL
            window_point = floating.mapTo(window, point)
            pixel = window.devicePixelRatioF()
            hwnd = int(window.winId())
            screen = POINT(round(window_point.x() * pixel), round(window_point.y() * pixel))
            assert user32.ClientToScreen(hwnd, byref(screen))
            while True:
                local = POINT(screen.x, screen.y)
                assert user32.ScreenToClient(hwnd, byref(local))
                child = user32.ChildWindowFromPointEx(hwnd, local, 7)
                if not child or child == hwnd:
                    break
                hwnd = child
            assert hwnd == int(floating.effectiveWinId()), (hwnd, floating.effectiveWinId())

    if evidence:
        assert match.grab().save(str(evidence / 'floating-over-standings.png'))
    start = floating.mapTo(window, floating.rect().center())
    x = floating.x()
    QTest.mousePress(window.windowHandle(), Qt.LeftButton, pos=start)
    assert floating.drag is not None
    QTest.mouseMove(window.windowHandle(), start - QPoint(22, 0))
    QTest.mouseRelease(window.windowHandle(), Qt.LeftButton, pos=start - QPoint(22, 0))
    assert floating.x() < x and floating.drag is None
    width = floating.width()
    center = floating.rect().center()
    wheel = QWheelEvent(QPointF(center), QPointF(floating.mapToGlobal(center)), QPoint(), QPoint(0, 120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(floating, wheel)
    assert wheel.isAccepted() and floating.width() > width
    QTest.mouseClick(floating, Qt.LeftButton, pos=floating.lock_rect().center())
    assert floating.lock_mode == 1
    QTest.mouseClick(floating, Qt.LeftButton, pos=floating.close_rect().center())
    assert '102' not in match.floating_crops
    tools.onboard_toggle.click()


def check_workspace(window, entry, match, directory, evidence, suspended_tile):
    from PySide6.QtTest import QTest

    assert not match.running and match.settings_panel.isVisible() and match.chat_pane.isVisible()
    assert match.settings_panel.findChild(QWidget, 'MatchSyncFooter') is None
    original_settings = deepcopy(window.plugins.plugin_settings.get('match_sync', {}))
    with (patch.object(type(match), '_start_row', autospec=True) as start_row,
          patch.object(type(match.audio), 'start')):
        drag_card(window, '101', match)
        assert match.running and match.main.currentData() == '101'
        assert start_row.call_count == 1
        drag_card(window, '102', match)
        assert match.main.currentData() == '101' and set(match.rows) == {'101', '102'}
        assert start_row.call_count == 2
        assert match.rows['102'].volume.isVisible()
        assert match.send_console.isVisible() and set(match.send_console.rows) == {'101', '102'}

        match.picture.danmaku_button.setChecked(True)
        assert match.picture.danmaku_button.isChecked()
        match.minimize_settings.click()
        assert match.settings_panel.isHidden() and match.running
        match.restore_settings.click()
        assert match.settings_panel.isVisible()
        match.minimize_chat.click()
        assert match.panel.isHidden() and match.running
        match.restore_chat.click()
        assert match.panel.isVisible()
        if evidence:
            assert match.grab().save(str(evidence / 'f1-with-sources.png'))

        plugin = next(plugin for plugin in window.plugins.plugins if plugin.context.name == 'f1_play')
        tools = plugin.tools
        assert tools.isHidden() and not tools.isWindow()
        assert tools.parentWidget() is match.chat_pane
        assert not any(action.text() == '赛事工具' for action in entry.actions())
        tools.toggle.click()
        QTest.qWait(100)
        assert tools.isVisible() and match.running
        check_floating_over_standings(window, match, tools, evidence)
        if evidence:
            assert match.grab().save(str(evidence / 'f1-inline-tools.png'))
        filename = Path(directory) / 'history.json'
        for session, columns in (('Race', 4), ('Practice', 5)):
            filename.write_text(json.dumps(history_data(session)), encoding='utf-8')
            assert tools.standings.load_data(filename)
            assert tools.standings.table.columnCount() == columns
            match.rows['101'].actual_position = 92
            tools.standings.set_anchor(tools.picture_position(), 92)
            assert tools.standings.table.item(0, 1).text() == 'LEC'
            match.rows['101'].actual_position = 23
            tools.standings.update_picture()
            if session == 'Race':
                assert tools.standings.table.item(0, 1).text() == 'ANT'
            else:
                assert tools.standings.table.rowCount() == 0
        match.rows['102'].main_button.click()
        tools.update_timeline()
        assert match.main.currentData() == '102' and tools.standings.table.rowCount() == 0
        match.rows['101'].main_button.click()
        assert match.main.currentData() == '101'

        tools.rank_toggle.click()
        assert tools.standings.isHidden()
        tools.onboard_toggle.click()
        QTest.qWait(100)
        onboard = tools.onboard
        assert onboard.empty_hint.isVisible() and not tools.promote.isEnabled()
        drag_card(window, '103', onboard)
        assert onboard.running and not onboard.rows['103'].audible.isChecked()
        assert tools.promote.isEnabled() and match.main.currentData() == '101'
        tools.promote.click()
        assert match.main.currentData() == '103' and onboard.main.currentData() == '101'
        assert set(match.rows) == {'102', '103'} and set(onboard.rows) == {'101'}

        tools.toggle.click()
        assert tools.isHidden() and not onboard.running and match.running
        tools.toggle.click()
        assert tools.isVisible() and not tools.onboard_toggle.isChecked()
        with patch.object(window.stream_playback, 'start_tile') as start:
            QTest.mouseClick(entry, Qt.LeftButton)
            assert not window._match_sync_active
            assert start.call_count == 1 and start.call_args.args == (suspended_tile,)
        assert not match.running and tools.isHidden() and window.wall.isVisible()
        assert 'rooms' in window.plugins.plugin_settings['f1_play']
        assert window.plugins.plugin_settings.get('match_sync', {}) == original_settings
        suspended_tile.set_room(None)
        entry.click()
        assert match.running and match.isVisible()
        match.close()


def check_live(window, match, room_id, evidence, seconds):
    from time import monotonic
    from PySide6.QtTest import QTest

    started = monotonic()
    drag_card(window, room_id, match)
    row = match.rows[room_id]
    while row.actual_position is None and monotonic() - started < 45:
        QTest.qWait(100)
    first = row.actual_position
    assert first is not None and not match.canvas.image.isNull(), row.video_text
    ready = monotonic()
    if evidence:
        assert match.canvas.image.save(str(evidence / 'video-frame.png'))
        assert match.grab().save(str(evidence / 'live-workspace.png'))
    samples = []
    while monotonic() - ready < seconds:
        QTest.qWait(250)
        samples.append({'time': round(monotonic() - ready, 2), 'position': row.actual_position,
                        'waiting': match.canvas.waiting, 'presented': match.canvas.presented})
    result = {'startup_seconds': round(ready - started, 2), 'advanced_seconds': round(row.actual_position - first, 2),
              'duration': samples[-1]['time'], 'waiting_samples': sum(item['waiting'] for item in samples),
              'resolution': list(row.decoder.video_size), 'fps': row.decoder.fps}
    if evidence:
        (evidence / 'samples.json').write_text(json.dumps({'summary': result, 'samples': samples}), encoding='utf-8')
    print(json.dumps(result), flush=True)
    assert result['advanced_seconds'] > 0
    match.close()
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
                entry = check_entry_icon(window, directory, evidence)
                if not args.live_source_file:
                    provider = next(plugin for plugin in window.plugins.plugins
                                    if plugin.context.name == 'match_sync')
                    provider.open_viewer({})
                    QTest.qWait(100)
                    assert provider.viewer.settings_panel.findChild(QWidget, 'MatchSyncFooter') is None
                    reference = layout_signature(provider.viewer)
                    if evidence:
                        assert provider.viewer.grab().save(str(evidence / 'match-sync.png'))
                    provider.viewer.close()
                    provider.viewer.deleteLater()
                    provider.viewer = None
                    QTest.qWait(100)
                    suspended_tile = window.wall.tiles[0]
                    suspended_tile.set_room({'room_id': '101', 'uname': '主画面', 'live': True})
                    player = Mock()
                    window.players[suspended_tile] = player
                entry.click()
                owner, page, _ = window.plugins._workspace_pages.active
                assert owner == 'f1_play' and page.isVisible() and window.wall.isHidden()
                QTest.qWait(100)
                if args.live_source_file:
                    assert window.plugins.platform_for(room_id).room_info(room_id).uname == 'CCTV-5+（验收源）'
                    check_live(window, page, room_id, evidence, args.seconds)
                else:
                    assert layout_signature(page) == reference
                    if evidence:
                        assert page.grab().save(str(evidence / 'f1-play.png'))
                    assert window._match_sync_active and suspended_tile not in window.players
                    player.release.assert_called_once()
                    check_mask(window, page, directory)
                    check_workspace(window, entry, page, directory, evidence, suspended_tile)
                    window.plugins.unload()
                    assert window.wall.isVisible()
            finally:
                window.close()
                window.deleteLater()
                app.sendPostedEvents(None, QEvent.DeferredDelete)
    print('PASS: F1 Play sidebar drop, wall suspension, background mask and floating crop input', flush=True)


if __name__ == '__main__':
    main()
