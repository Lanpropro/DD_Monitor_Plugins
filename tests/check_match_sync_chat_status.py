"""Check connection indicators and Super Chat expiration in the match viewer."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace


def check_super_chat_expiry(viewer):
    from PySide6.QtCore import QSignalBlocker
    from PySide6.QtTest import QTest
    from ddm.super_chat_model import normalize

    overlay = viewer.video_danmaku
    overlay.set_enabled(True)
    overlay.set_active(True)
    now = int(time.time())
    stamp = time.monotonic()
    shifts = {'1': 0, '2': 0}
    second_chat = viewer.rows['2'].show_chat
    with QSignalBlocker(second_chat):
        second_chat.setChecked(True)
    for room, chat_id, end_time in (('1', 'first', now + 2), ('2', 'second', now + 5)):
        event = normalize({'id': chat_id, 'message': 'fixture', 'price': 30,
                           'start_time': now - 1, 'end_time': end_time})
        viewer.overlay_pending[room] = (stamp, dict(event, color='#ffffff'))
        viewer._render_overlay(stamp, shifts)
    assert {chat['id'] for chat in overlay.super_chats} == {'first', 'second'}
    assert overlay.super_chat_overlay.isVisible()
    assert not viewer.render_timer.isActive(), 'Expiry must run while playback is idle'
    QTest.qWait(max(1, int((now + 3.2 - time.time()) * 1000)))
    assert [chat['id'] for chat in overlay.super_chats] == ['second'], 'Remove only the SC that expired'
    assert overlay.super_chat_overlay.isVisible()
    QTest.qWait(max(1, int((now + 6.2 - time.time()) * 1000)))
    assert not overlay.super_chats and overlay.super_chat_overlay.isHidden(), 'Hide the last expired SC'
    expired = dict(event, id='expired', super_chat_id='expired', end_time=now)
    viewer.overlay_pending['2'] = (stamp, expired)
    viewer._render_overlay(stamp, shifts)
    assert not overlay.super_chats and '2' not in viewer.overlay_pending, 'Skip an already expired SC'


def check(app, host, module):
    from ddm import plugins
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    with tempfile.TemporaryDirectory() as directory:
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'chat_status'), {})
        try:
            for key in ('1', '2'):
                viewer.add_room(key, {'alias': 'fixture ' + key})
                viewer.rows[key].chat = SimpleNamespace(stop=lambda: None)
            first, second = viewer.rows.values()
            viewer.setAttribute(Qt.WA_DontShowOnScreen)
            viewer.show()
            QTest.qWait(80)
            viewer.setFixedSize(viewer.size())
            QTest.qWait(80)
            console_top = viewer.send_console.mapTo(viewer, QPoint()).y()
            viewer.minimize_chat.click()
            QTest.qWait(20)
            assert viewer.panel.isHidden() and viewer.send_console.isVisible()
            assert abs(viewer.send_console.mapTo(viewer, QPoint()).y() - console_top) <= 1, (
                console_top, viewer.send_console.mapTo(viewer, QPoint()).y())
            assert viewer.restore_chat.isVisible()
            viewer.restore_chat.click()
            QTest.qWait(20)
            assert viewer.panel.isVisible() and viewer.send_console.isVisible()
            viewer.running = True
            viewer._state(first, first.chat, '连接中…', True)
            viewer._state(second, second.chat, '连接中…', True)
            icon = viewer.panel.count
            assert icon.connections == 0 and icon.property('state') == 'connecting'
            off = icon.grab().toImage()
            viewer._state(first, first.chat, '已连接', True)
            assert icon.connections == 1 and icon.property('state') == 'connected'
            assert '1/2路' in icon.text() and 'fixture 1' in icon.toolTip()
            one = icon.grab().toImage()
            viewer._state(second, second.chat, '已连接', True)
            assert icon.connections == 2 and '2/2路' in icon.text()
            two = icon.grab().toImage()
            assert off != one and one != two, 'Render distinct disconnected, single and multi-source signals'
            viewer._state(first, object(), '连接已断开', True)
            assert icon.connections == 2, 'Ignore stale worker callbacks'
            viewer._state(first, first.chat, '连接已断开', True)
            assert icon.connections == 1 and icon.grab().toImage() == one
            viewer._state(second, second.chat, '连接失败', True)
            assert icon.connections == 0 and icon.property('state') == 'error'
            assert icon.grab().toImage() == off
            viewer._state(second, second.chat, '弹幕已连接', True)
            assert icon.connections == 1
            second.show_chat.blockSignals(True)
            second.show_chat.setChecked(False)
            second.show_chat.blockSignals(False)
            viewer._start_chat(second)
            assert icon.connections == 0, 'Disabled receivers do not light an arc'
            viewer._state(first, first.chat, '已连接', True)
            assert icon.connections == 1
            viewer.stop()
            assert icon.connections == 0
            check_super_chat_expiry(viewer)
            return {'passed': True, 'levels': [0, 1, 2], 'disconnect_reset': True,
                    'stale_worker_ignored': True, 'disabled_excluded': True,
                    'console_kept_visible': True, 'super_chats_expire_without_playback': True}
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
