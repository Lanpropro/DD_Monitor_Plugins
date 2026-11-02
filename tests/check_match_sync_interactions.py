"""Selected-recipient interactions and capability feedback, without accounts or requests."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def check(app, host, module, output=None):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QToolTip, QWidget
    from ddm import plugins
    from ddm.plugin_controls import TileControl
    created, opened, sent = {}, [], []

    class Composer(TileControl):
        def __init__(self, context):
            widget = QWidget(context.parent)
            layout = QHBoxLayout(widget)
            widget.edit = QLineEdit()
            send = QPushButton('发送')
            layout.addWidget(widget.edit)
            layout.addWidget(send)
            key = context.room['room_id']
            widget.edit.returnPressed.connect(lambda: sent.append((key, widget.edit.text())))
            if key in ('1', '2'):
                widget.emote_button = QPushButton('emote')
                widget.gift_button = QPushButton('gift')
                layout.addWidget(widget.emote_button)
                layout.addWidget(widget.gift_button)
                widget.open_emoticons = lambda: opened.append((key, 'emote', widget.emote_button))
                widget.open_gifts = lambda: opened.append((key, 'gift', widget.gift_button))
            created.setdefault(key, widget)
            super().__init__(widget, minimum_width=80)

        def focus_input(self):
            return True

    with tempfile.TemporaryDirectory() as directory, \
            patch.object(module.Decoder, 'start', side_effect=AssertionError('No media')), \
            patch.object(module.Chat, 'start', side_effect=AssertionError('No network')):
        host.settings, host.rooms = {}, []
        manager = plugins.PluginManager(window=host, plugins_dir=directory)
        manager.register_tile_control('fixture', 'interactions', Composer)
        viewer = module.Viewer(plugins.PluginContext(manager, 'selected_interactions'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            viewer.resize(1600, 950)
            viewer.show()
            console = viewer.send_console
            assert not hasattr(console, 'interaction_menu')

            def buttons():
                return [button for button in console.findChildren(QPushButton)
                        if button.property('interactionKind')]

            def select(keys):
                console.batch_targets = set(keys)
                console._update_selection()
                app.processEvents()

            assert len(buttons()) == 2 and all(not b.isEnabled() for b in buttons())
            assert all('请先选择主播' in b.toolTip() and not b.isHidden() for b in buttons())
            for key in ('1', '2', '3'):
                viewer.add_room(key, {'alias': '主播' + key})
            select({'1'})
            created['1'].edit.setText('draft one')
            created['2'].edit.setText('draft two')
            assert all(b.isEnabled() and '主播1' in b.toolTip() and '哔哩哔哩' in b.toolTip() for b in buttons())
            native = created['1'].findChild(QWidget, 'SendInteractions')
            for kind in ('emote', 'gift'):
                button = next(b for b in native.findChildren(QPushButton) if b.property('interactionKind') == kind)
                attribute = 'emote_button' if kind == 'emote' else 'gift_button'
                original = getattr(created['1'], attribute)
                button.click()
                assert opened[-1] == ('1', kind, button), 'Open the selected native panel directly'
                assert getattr(created['1'], attribute) is original, 'Restore the native anchor after opening'
            assert not sent and created['1'].edit.text() == 'draft one' and created['2'].edit.text() == 'draft two'
            select({'2'})
            assert all(b.isEnabled() and '主播2' in b.toolTip() for b in buttons())
            button = next(b for b in buttons() if b.property('interactionKind') == 'gift')
            button.click()
            assert opened[-1] == ('2', 'gift', button)
            count = len(opened)
            for keys, reason in (({'1', '2'}, '请选择一位主播'), (set(), '请先选择主播'), ({'3'}, '暂不支持')):
                select(keys)
                assert all(not b.isEnabled() and reason in b.toolTip() and not b.isHidden() for b in buttons())
                for button in buttons():
                    assert button.cursor().shape() == Qt.ArrowCursor
                    button.click()
                console.show_interactions('gift', button)  # Recheck selection even for a stale invocation.
                assert len(opened) == count
            if app.platformName() == 'offscreen':
                tooltip = buttons()[0]
                app.sendEvent(tooltip, QHelpEvent(QEvent.ToolTip, QPoint(5, 5), tooltip.mapToGlobal(QPoint(5, 5))))
                assert QToolTip.text() == tooltip.toolTip(), 'Explain disabled capability on hover'
                QToolTip.hideText()
            select({'1', '2'})
            console.batch_panel.begin('shared plain text')
            for _ in range(20):
                QTest.qWait(20)
                if not console.batch_panel.timer.isActive():
                    break
            assert sent == [('1', 'shared plain text'), ('2', 'shared plain text')], sent
            assert len(opened) == count
            select({'1'})
            if output:
                output = Path(output)
                output.parent.mkdir(parents=True, exist_ok=True)
                viewer.grab().save(str(output))
            viewer.remove_room('1')
            assert all(not b.isEnabled() for b in buttons()), 'Removing the selected source clears its actions'
            return dict(passed=True, selected_native_panel=True, target_tooltips=True,
                        disabled_reasons=True, no_platform_menu=True, plain_multi_send=True,
                        drafts_untouched_by_opening=True, real_messages=0, credentials_read=False)
        finally:
            manager.unload()
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    host = QWidget()
    host.setAttribute(module.Qt.WA_DontShowOnScreen)
    try:
        print(check(app, host, module, args.output))
    finally:
        host.close()
        app.sendPostedEvents(None, 52)
        app.shutdown()


if __name__ == '__main__':
    main()
