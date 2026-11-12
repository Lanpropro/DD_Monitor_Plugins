"""Comparison console appearance and retained controls; no media or accounts."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFrame
    from ddm import plugins
    panels = import_module(module.__package__ + '.panels')
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'comparison_controls'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            for key in ('1', '2', '3'):
                viewer.add_room(key, {'alias': 'Streamer ' + key})
            for widget in viewer.add_controls:
                widget.hide()
            viewer.resize(1800, 950)
            viewer.show()
            viewer.compare.setChecked(True)
            QTest.qWait(30)
            panel = viewer.comparison_panel
            assert isinstance(panel, panels.SettingsPanel)
            assert not hasattr(panel, 'clock_button'), 'Timer entry belongs beside each alignment assist'
            assert isinstance(panel.zoom, panels.ConsoleModeToggle)
            assert isinstance(panel.close_button, panels.ConsoleIconButton)
            assert panel.close_button.focusPolicy() == Qt.StrongFocus
            assert panel.toolbar.layout().contentsMargins() == viewer.console_toolbar.layout().contentsMargins()
            assert panel.cards_layout.spacing() == 12 and panel.shown_rooms == ('1', '2', '3')
            assert panel.target.isHidden()
            for mode in ('dark', 'light'):
                module.theme.apply(mode)
                for index, (card, name, canvas, state) in enumerate(panel.cards.values()):
                    assert isinstance(card, QFrame) and card.objectName() == 'MatchSyncCompareCard'
                    assert card.parentWidget() is panel.cards_layout.parentWidget()
                    image = QImage(640, 360, QImage.Format_RGB32)
                    image.fill(('#265776', '#464477', '#225d52')[index])
                    canvas.set_frame((100., image))
                    app.processEvents()
                    assert canvas.image_rect.width() <= canvas.width() + 1
                    assert canvas.image_rect.height() <= canvas.height() + 1
                    assert canvas.image_rect.x() >= -1 and canvas.image_rect.right() <= canvas.width() + 1
                    assert canvas.image_rect.y() >= -1 and canvas.image_rect.bottom() <= canvas.height() + 1
                    assert abs(canvas.image_rect.width() / canvas.image_rect.height() - 16 / 9) < .02
                    if canvas.width() / canvas.height() >= 16 / 9:
                        assert abs(canvas.image_rect.top()) <= 1 and abs(canvas.image_rect.bottom() - canvas.height()) <= 1
                    else:
                        assert abs(canvas.image_rect.left()) <= 1 and abs(canvas.image_rect.right() - canvas.width()) <= 1
                    assert name.fontMetrics().horizontalAdvance(name.text()) < card.width()
                    assert 'font-size: 12px' in state.styleSheet()
                app.processEvents()
                if output:
                    output = Path(output)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    panel.grab().save(str(output.with_name(output.stem + '-' + mode + '.png')))
            panel.zoom.setChecked(False)
            panel.render(100., viewer.shifts())
            assert all(not canvas.zoom_crop for _card, _name, canvas, _state in panel.cards.values())
            for key in ('4', '5'):
                viewer.add_room(key, {'alias': 'Streamer ' + key})
            panel.render(100., viewer.shifts())
            panel.target.setCurrentIndex(panel.target.findData('5'))
            panel.render(100., viewer.shifts())
            assert panel.shown_rooms == ('1', '5') and not panel.target.isHidden()
            with patch.object(panel, 'open_clock') as opened:
                viewer.rows['2'].clock_button.click()
                opened.assert_called_once()
            panel.close_button.click()
            assert panel.isHidden() and not viewer.compare.isChecked()
            module.theme.apply('dark')
            return dict(passed=True, shared_console_material=True, timer_header_removed=True,
                        row_timer_retained=True, native_controls=True, dark_light=True,
                        zoom_and_source_selection=True, close_preserves_session=True,
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
