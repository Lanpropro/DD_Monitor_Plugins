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
            row.platform = SimpleNamespace(kind='bilibili', room_quality_options=lambda _key: calls.append(_key) or [])
            viewer.status_ui_due = 0
            base = time.monotonic()
            with patch.object(viewer, 'render_floating_crops', wraps=viewer.render_floating_crops) as frames:
                for index in range(125):
                    with patch.object(module.time, 'monotonic', return_value=base + index * .008):
                        viewer.render()
                assert frames.call_count == 125, 'Do not throttle the playback path'
            assert len(calls) == 4, len(calls)
            assert viewer.render_timer.interval() == 8
            assert {'metadata_sidebar', 'metadata_picture'} <= dict(viewer.playback_probe.stages).keys()
            # Explicit source information and room changes bypass periodic UI throttling.
            row.title = 'new title'
            viewer.sync_picture()
            assert viewer.picture.room['title'] == 'new title'
            assert len(calls) == 5
            row.volume.setValue(37)
            row.audible.setChecked(False)
            before_controls = len(calls)
            viewer.sync_picture(refresh_metadata=False)
            assert viewer.picture.volume == 37 and viewer.picture.muted
            assert len(calls) == before_controls, 'Playback controls must not require a metadata refresh'

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
                    'playback_updates': 125, 'metadata_polls': 4, 'immediate_controls': True,
                    'colored_overlay_without_source_prefix': True, 'uncolored_prefix_preserved': True,
                    'replay_overlay_consistent': True, 'sidebar_source_preserved': True}
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
