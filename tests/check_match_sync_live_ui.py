"""Unchanged live UI is inert; late artwork and sidebar rebuilds still update."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check(app, _host, module):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QFrame, QWidget
    from ddm import plugins
    from ddm.continuous_corners import install, source_stylesheet

    install(app)
    host = QWidget()
    host.settings, host.rooms = {}, []
    host.sidebar = sidebar = QWidget(host)
    sidebar.side, sidebar.collapsed = 'left', False
    artwork = [None]
    def card(key):
        item = QFrame(sidebar)
        item.setObjectName('NavItem')
        item.room = {'room_id': key}
        item.thumb = SimpleNamespace(face_pixmap=lambda: artwork[0])
        item.ensurePolished()
        item.setStyleSheet('#NavItem {border-radius: 8px; background: #123456;}')
        return item
    sidebar._items = [card('1'), card('2')]
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'live_ui'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            viewer.add_room('1')
            row = viewer.rows['1']
            row.decoder = module.Decoder('1')  # Never start capture or audio.
            viewer.embedded = True
            viewer.show()
            viewer.running = True
            viewer.sidebar_marks_state = None
            now = [100.]
            with patch.object(module.time, 'monotonic', side_effect=lambda: now[0]):
                viewer._sync_sidebar_marks()
                assert sidebar._items[0].property('matchSync')
                assert source_stylesheet(sidebar._items[0].styleSheet()) != sidebar._items[0].styleSheet(), \
                    ('Exercise the real host QSS conversion', sidebar._items[0].styleSheet())
                with patch.object(sidebar._items[0], 'setStyleSheet', wraps=sidebar._items[0].setStyleSheet) as style, \
                        patch.object(sidebar._items[1], 'setStyleSheet', wraps=sidebar._items[1].setStyleSheet) as other_style, \
                        patch.object(row.name_label, 'setText') as name, \
                        patch.object(row.avatar, 'setText') as placeholder:
                    for _ in range(5):
                        now[0] += .3
                        viewer._sync_sidebar_marks()
                    assert style.call_count == other_style.call_count == 0, (
                        'Unchanged styles were reapplied', style.call_count, other_style.call_count)
                    assert name.call_count == placeholder.call_count == 0
                    sidebar._items[1].setProperty('onWall', True)
                    now[0] += .3
                    viewer._sync_sidebar_marks()
                    assert 'border: none' in source_stylesheet(sidebar._items[1].styleSheet())
                    assert style.call_count == 0 and other_style.call_count >= 1
                    # Same-sized rebuilds must not reuse the old cards' state.
                    sidebar._items[:] = [card('1'), card('2')]
                    now[0] += .3
                    viewer._sync_sidebar_marks()
                    assert sidebar._items[0].property('matchSync')
                    sidebar._items[0].room = {'room_id': '3'}
                    now[0] += .3
                    viewer._sync_sidebar_marks()
                    assert not sidebar._items[0].property('matchSync')
                    sidebar._items[0].room = {'room_id': '1'}
                    now[0] += .3
                    viewer._sync_sidebar_marks()
                # Periodic artwork polling remains even when marks do not change.
                pixmap = QPixmap(24, 24)
                pixmap.fill(QColor('red'))
                artwork[0] = pixmap
                now[0] += .3
                viewer._sync_sidebar_marks()
                assert row.avatar_key == pixmap.cacheKey()
                row.alias.setText('updated')
                assert row.name_label.text() == 'updated'
                viewer._restore_host_widgets()
                assert not sidebar._items[0].property('matchSync')
                now[0] += .3
                viewer._sync_sidebar_marks()
                assert sidebar._items[0].property('matchSync'), 'Re-entry must restore marks'
                row.decoder = None
                viewer._sync_sidebar_marks()
                assert not sidebar._items[0].property('matchSync'), 'Stopped sources lose marks'

            status = row.status
            parts, hints = [('buffer', '5.0s')], ['buffer detail']
            status.set_status(parts, 'detail', hints)
            regions = status.hit_regions = [('preserved until repaint', 'buffer detail')]
            with patch.object(status, 'setText', wraps=status.setText) as text, \
                    patch.object(status, 'update', wraps=status.update) as update:
                status.set_status(list(parts), 'detail', list(hints))
                assert text.call_count == update.call_count == 0
                assert status.hit_regions is regions, 'No repaint means existing hit regions remain valid'
                status.set_status(parts, 'changed detail', hints)
                assert status.accessibleDescription() == 'changed detail'
                status.set_status(parts, 'changed detail', ['new hint'])
                assert status.hints == ['new hint']
                status.set_status([('buffer', '4.0s')], 'changed detail')
                assert status.text() == '4.0s' and status.hints == ['4.0s']
                assert text.call_count == update.call_count == 3
            return dict(passed=True, unchanged_sidebar_style_updates=0,
                        unchanged_status_updates=0, artwork_polling=True,
                        same_size_rebuild=True, reentry=True, status_hints=True)
        finally:
            viewer.close()
            viewer.deleteLater()
            host.close()
            host.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
