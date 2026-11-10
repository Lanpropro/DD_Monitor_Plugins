"""Keep resize and status work out of continuous playback updates."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextCursor, QTextDocument
    from PySide6.QtTest import QTest
    from ddm import plugins
    sending = import_module(module.__package__ + '.sending')
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(sending.SendingDanmakuPanel, '_ensure_emoticon', lambda *_args: None):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'ui_work'), {})
        panel = sending.SendingDanmakuPanel(host)
        try:
            panel.resize(600, 400)
            panel.show()
            for index in range(100):
                panel.add_event({'text': str(index), 'uname': 'fixture', 'source_room': '1',
                                 '_replay_divider': index in (20, 80)})
            QTest.qWait(20)
            text = panel.body.toPlainText()
            blocks = panel.body.document().blockCount()
            with patch.object(panel, '_render_all', side_effect=AssertionError('Resize rebuilt chat')):
                for width in (400, 750, 320, 600):
                    panel.resize(width, 400)
                    QTest.qWait(20)
                    assert panel.body.toPlainText() == text
                    assert panel.body.document().blockCount() == blocks
                    for index in (20, 80):
                        cursor = QTextCursor(panel.body.document().findBlockByNumber(index))
                        cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
                        image = cursor.charFormat().toImageFormat()
                        expected = max(1, panel.body.viewport().width() - 24)
                        assert image.isValid() and image.width() == expected, (width, image.width(), expected)
                        resource = panel.body.document().resource(QTextDocument.ImageResource, QUrl(image.name()))
                        assert resource.width() == expected and resource.height() == 80
            assert [entry['source_room'] for entry in panel._blocks] == ['1'] * 100
            with patch.object(module.Decoder, 'start', lambda _self: None), \
                    patch.object(module.Chat, 'start', lambda _self: None), \
                    patch.object(module.AudioPump, 'start', lambda _self: None):
                viewer.add_room('1')
                viewer.toggle_running()
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            viewer.audio.timer.stop()
            row = viewer.rows['1']
            row.decoder.fps = 60
            calls = []
            row.platform = SimpleNamespace(kind='huya', room_quality_options=lambda _key: calls.append(_key) or [])
            viewer.status_ui_due = 0
            base = time.monotonic()
            with patch.object(viewer, 'render_floating_crops', wraps=viewer.render_floating_crops) as frames, \
                    patch.object(viewer, 'sync_picture', wraps=viewer.sync_picture) as metadata, \
                    patch.object(row.delay, 'setValue', wraps=row.delay.setValue) as offset_updates:
                for index in range(125):
                    with patch.object(module.time, 'monotonic', return_value=base + index * .008):
                        viewer.render()
                assert frames.call_count == 125, 'Do not throttle the playback path'
                assert sum(call.kwargs.get('refresh_metadata', True) for call in metadata.call_args_list) == 4
                assert offset_updates.call_count == 0, 'Unchanged offsets must not request spinbox repaints'
            assert not calls, 'Continuous playback must not query a platform provider'
            assert viewer.render_timer.interval() == 8
            assert {'metadata_sidebar', 'metadata_picture'} <= dict(viewer.playback_probe.stages).keys()
            # Explicit source information and room changes bypass periodic UI throttling.
            row.title = 'new title'
            viewer.sync_picture()
            assert viewer.picture.room['title'] == 'new title'
            assert not calls
            row.volume.setValue(37)
            row.audible.setChecked(False)
            before_controls = len(calls)
            viewer.sync_picture(refresh_metadata=False)
            assert viewer.picture.volume == 37 and viewer.picture.muted
            assert len(calls) == before_controls, 'Playback controls must not require a metadata refresh'

            # Display precision/range must match Qt without repeatedly setting
            # sub-centisecond changes, signed zero or values beyond the range.
            from PySide6.QtCore import QLocale
            from PySide6.QtWidgets import QDoubleSpinBox
            with patch.object(module.Decoder, 'start', lambda _self: None), \
                    patch.object(module.Chat, 'start', lambda _self: None):
                viewer.add_room('2')
            peer = viewer.rows['2']
            expected = QDoubleSpinBox()
            expected.setRange(peer.delay.minimum(), peer.delay.maximum())
            expected.setDecimals(peer.delay.decimals())
            values = (1.231, 1.234, 1.235, 1.236, 1.239, 70., 80., -70., -80., -.004, -.003, -.005, .005, 0.)
            emitted_offsets = []
            peer.delay.valueChanged.connect(emitted_offsets.append)
            for locale in (QLocale.c(), QLocale('de_DE')):
                peer.delay.setLocale(locale)
                expected.setLocale(locale)
                for value in values:
                    expected.setValue(value)
                    previous = peer.delay.value()
                    wanted = {'1': 0., '2': -value}
                    with patch.object(viewer, '_smooth_shifts', return_value=wanted), \
                            patch.object(peer.delay, 'setValue', wraps=peer.delay.setValue) as updated:
                        for _ in range(3):
                            viewer.render()
                        assert peer.delay.value() == expected.value(), (locale.name(), value)
                        assert peer.previous_delay == expected.value()
                        assert updated.call_count == int(previous != expected.value()), (value, updated.call_count)
            assert not emitted_offsets, 'Automatic display updates must not become user alignment changes'
            peer.delay.lineEdit().setText('1,')
            peer.delay.lineEdit().setModified(True)
            editing_text = peer.delay.lineEdit().text()
            with patch.object(peer.delay, 'setValue', side_effect=AssertionError('Overwrote an offset being edited')):
                viewer.render()
            assert peer.delay.lineEdit().text() == editing_text and peer.delay.lineEdit().isModified()
            peer.delay.lineEdit().setModified(False)
            viewer.automatic.setChecked(False)
            with patch.object(peer.delay, 'setValue', side_effect=AssertionError('Overwrote a manual offset')):
                viewer.render()
            expected.deleteLater()
            viewer.remove_room('2')
            viewer.automatic.setChecked(True)

            # Live and replay overlays use source colors without duplicating source names in the text.
            emitted = []
            overlay = SimpleNamespace(enabled=True, add_event=lambda event: emitted.append(event) or True)
            row.alias.setText('主播一')
            incoming = {'kind': 'danmaku', 'uname': '观众', 'text': '测试[表情]',
                        'emoticons': {'[表情]': 'https://example.invalid/emote.png'}, 'self_sent': True}
            row.pending.append((base - .1, dict(incoming)))
            with patch.object(viewer, 'video_danmaku', overlay), \
                    patch.object(viewer, 'live_clock', return_value=base), \
                    patch.object(viewer, '_smooth_shifts', return_value={'1': 0}), \
                    patch.object(viewer, '_recover_clock', side_effect=lambda clock, _shifts: clock), \
                    patch.object(viewer, '_update_sync_health'), \
                    patch.object(module.time, 'monotonic', return_value=base):
                viewer.render()
                assert emitted[-1]['text'] == incoming['text']
                assert emitted[-1]['color'] == row.color
                assert emitted[-1]['emoticons'] == incoming['emoticons'] and emitted[-1]['self_sent']
                assert emitted[-1]['source_label'] == '主播一' and emitted[-1]['source_room'] == '1'
                assert '【主播一】 观众' in viewer.panel.body.toPlainText(), 'Keep source labels in the chat list'
                recorded = dict(viewer.replay.messages[-1][1])
                assert recorded['text'] == incoming['text'], 'Keep original replay text'
                viewer.replay.messages.clear()
                viewer.replay.record_message(base - 1.05, recorded)
                viewer.replay.delay = 1
                viewer.replay.cursor = base - 1.1
                with patch.object(viewer.replay, 'valid', return_value=True), \
                        patch.object(viewer.replay_bar, 'refresh'):
                    viewer.render()
                assert len(emitted) == 2 and emitted[-1]['text'] == incoming['text'], 'Replay follows the same display rule'
                assert viewer.replay.messages[0][1] == recorded, 'Rendering must not mutate recorded messages'
                viewer.replay.live()
                for color in ('', None):
                    plain = dict(recorded, color=color)
                    viewer.overlay_pending['1'] = (base, plain)
                    viewer._render_overlay(base, {'1': 0})
                    assert emitted[-1]['text'] == '【主播一】 ' + incoming['text']
                    assert plain['text'] == incoming['text'], 'Fallback must not change the chat/replay event'
            return {'passed': True, 'resize_full_rebuilds': 0, 'messages_preserved': 100,
                    'playback_updates': 125, 'metadata_refreshes': 4, 'platform_polls': 0, 'immediate_controls': True,
                    'colored_overlay_without_source_prefix': True, 'uncolored_prefix_preserved': True,
                    'replay_overlay_consistent': True, 'sidebar_source_preserved': True,
                    'unchanged_offset_sets': 0, 'qt_rounding_range_locale_preserved': True,
                    'editing_and_manual_offsets_preserved': True}
        finally:
            panel.close()
            viewer.close()
            panel.deleteLater()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
