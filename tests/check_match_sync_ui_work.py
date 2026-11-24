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


def check_main_canvas_paint(app, canvas_type):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    for immediate in (True, False):
        canvas = canvas_type(immediate_frame_paint=immediate)
        try:
            canvas.setAttribute(Qt.WA_ShowWithoutActivating)
            canvas.resize(480, 270)
            canvas.show()
            app.processEvents()
            image = QImage(320, 180, QImage.Format_RGB32)
            image.fill(Qt.blue)
            with patch.object(canvas, '_paint_frame', wraps=canvas._paint_frame) as paint:
                canvas.set_frame((1, image))
                assert paint.call_count == int(immediate)
                canvas.set_frame((1, image))
                assert paint.call_count == int(immediate)
                app.processEvents()
                assert paint.call_count == 1
                canvas.hide()
                canvas.set_frame((2, image))
                assert paint.call_count == 1
                canvas.show()
                app.processEvents()
                assert paint.call_count == 2 and canvas.frame_key == 2
        finally:
            canvas.close()


def check(app, host, module):
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QPixmap, QTextCursor, QTextDocument
    from PySide6.QtTest import QTest
    from ddm import plugins
    sending = import_module(module.__package__ + '.sending')
    canvas = import_module(module.__package__ + '.canvas')
    check_main_canvas_paint(app, canvas.Canvas)
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(sending.SendingDanmakuPanel, '_ensure_emoticon', lambda *_args: None):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'ui_work'), {})
        panel = sending.SendingDanmakuPanel(host)
        try:
            assert viewer.canvas.immediate_frame_paint
            viewer.video_danmaku._start_timer()
            assert viewer.video_danmaku.timer.interval() == 16
            viewer.video_danmaku._stop_timer()
            panel.resize(600, 400)
            panel.show()
            panel.add_event({'text': '0', 'uname': 'fixture', 'source_room': '1'})
            url = 'https://example.invalid/batch-emoticon.png'
            with patch.object(panel.body, 'append', wraps=panel.body.append) as append, \
                    patch.object(panel, '_restore_scroll_state', wraps=panel._restore_scroll_state) as scroll:
                panel.add_events({'text': str(index) + ('[face]' if index == 42 else ''),
                                  'uname': 'fixture', 'source_room': '1',
                                  'emoticons': {'[face]': url} if index == 42 else {},
                                  '_replay_divider': index in (20, 80)} for index in range(1, 100))
                assert append.call_count == scroll.call_count == 1
            QTest.qWait(20)
            document = panel.body.document()
            original = document.findBlockByNumber(41)
            old_text = original.text()
            with patch.object(panel, '_render_all', side_effect=AssertionError('Emoticon rebuilt all chat')), \
                    patch.object(panel, '_block_html', wraps=panel._block_html) as format_block:
                pixmap = QPixmap(18, 10)
                pixmap.fill(Qt.cyan)
                panel._on_emoticon_loaded(url, pixmap)
                QTest.qWait(40)
                assert [call.args[0]['text'] for call in format_block.call_args_list] == ['42[face]']
                assert document.blockCount() == 100 and original.isValid() and original.text() == old_text
                assert '[face]' not in document.findBlockByNumber(42).text()
                assert '\ufffc' in document.findBlockByNumber(42).text()
                assert not document.resource(QTextDocument.ImageResource, QUrl(url)).isNull()
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
                viewer.playback_probe.path = Path(directory) / 'playback.jsonl'
                viewer.add_room('1')
                viewer.toggle_running()
            # Drive the real timers. A fresh picture callback must not run the
            # crop and chat work inline, even when both are active.
            order = []
            with patch.object(viewer.canvas, 'show_at', side_effect=lambda *_: order.append('picture')), \
                    patch.object(viewer, 'render_floating_crops', side_effect=lambda *_: order.append('crop')), \
                    patch.object(viewer, '_render_overlay', side_effect=lambda *_: order.append('chat')):
                QTest.qWait(90)
            assert order[0] == 'picture' and 'crop' in order and 'chat' in order, order
            assert len(viewer.playback_probe.auxiliary_updates) > 0
            stats = viewer.playback_probe.snapshot()
            assert 'picture_timer_late' in stats['gui_pipeline']['stages']
            assert 'chat' in stats['auxiliary_stages_ms']
            assert 'chat' not in stats['stages_ms'] and 'floating' not in stats['stages_ms']
            viewer.render_timer.stop()
            viewer.auxiliary_timer.stop()
            assert viewer._presentation_update is not None
            viewer.generation += 1
            with patch.object(viewer, 'sync_picture') as stale_update:
                viewer._auxiliary_tick()
                stale_update.assert_not_called()
            viewer.match_timer.stop()
            viewer.audio.timer.stop()
            row = viewer.rows['1']
            row.decoder.fps = 60
            calls = []
            row.platform = SimpleNamespace(kind='huya', room_quality_options=lambda _key: calls.append(_key) or [])
            viewer.status_ui_due = 0
            base = time.monotonic()
            with patch.object(viewer, 'render_floating_crops', wraps=viewer.render_floating_crops) as frames, \
                    patch.object(viewer, 'sync_picture', wraps=viewer.sync_picture) as metadata:
                for index in range(125):
                    with patch.object(module.time, 'monotonic', return_value=base + index * .008):
                        viewer.render()
                assert frames.call_count == 125, 'Do not throttle the playback path'
                assert sum(call.kwargs.get('refresh_metadata', True) for call in metadata.call_args_list) == 4
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
            viewer.stop()
            assert viewer._presentation_update is None and viewer._picture_due is None
            assert not viewer.render_timer.isActive() and not viewer.auxiliary_timer.isActive()
            return {'passed': True, 'resize_full_rebuilds': 0, 'messages_preserved': 100,
                    'picture_timer_isolated': True, 'separate_picture_and_auxiliary_metrics': True,
                    'playback_updates': 125, 'metadata_refreshes': 4, 'platform_polls': 0, 'immediate_controls': True,
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
