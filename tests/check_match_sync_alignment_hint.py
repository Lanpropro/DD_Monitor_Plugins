"""Directional ranking and console interaction regressions; no media or network."""
import argparse
from importlib import import_module
import os
from pathlib import Path
import random
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch


def check_ranking(engine):
    rng = random.Random(17)
    samples = [engine.Sample(100 + i * .05, rng.getrandbits(512), 20, rng.randbytes(256)) for i in range(81)]
    duplicate = [engine.Sample(s.time + lag, s.signature, s.texture, s.feature)
                 for lag in (-4., 4.) for s in samples]
    ambiguous = engine.Match(None, 0, 'Repeated descriptors', candidates=(-4., 4.))
    assert engine.refine_match(ambiguous, samples, duplicate).lag is None
    for direction in (-1, 1):
        hint = (0., direction, 1)
        coarse = engine.match_scenes(samples, duplicate, hint=hint)
        refined = engine.refine_match(ambiguous, samples, duplicate, hint=hint)
        assert abs(coarse.lag - direction * 4) < .1, coarse
        assert abs(refined.lag - direction * 4) < .05 and refined.refined, refined
        assert refined.confidence <= 1
    unrelated = [engine.Sample(s.time, rng.getrandbits(512), 20, rng.randbytes(256)) for s in samples]
    assert engine.refine_match(ambiguous, samples, unrelated, hint=(0., -1, 4)).lag is None
    return samples


def check(app, host, module, output=None):
    from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
    from PySide6.QtGui import QEnterEvent, QMouseEvent, QWheelEvent
    from PySide6.QtTest import QTest
    from ddm import plugins
    engine = import_module(module.__package__ + '.engine')
    samples = check_ranking(engine)
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(module.Decoder, 'start', side_effect=AssertionError('No media')), \
            patch.object(module.Chat, 'start', side_effect=AssertionError('No network')):
        host.settings, host.rooms = {}, []
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'alignment_hint'), {})
        viewer.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            viewer.resize(1800, 950)
            viewer.show()
            for _ in range(20):
                QTest.qWait(10)
                if viewer.settings_panel.height() == viewer.settings_height and not viewer.console_size_timer.isActive():
                    break
            initial = viewer.settings_panel.height()
            assert initial == viewer.settings_height
            for key in ('1', '2', '3', '4', '5'):
                viewer.add_room(key, {'alias': 'Streamer ' + key})
                QTest.qWait(15)
                assert viewer.settings_panel.height() == initial, (key, initial, viewer.settings_panel.height())
            assert viewer.controls.verticalScrollBar().maximum() > 0
            bar = viewer.controls.verticalScrollBar()
            bar.setValue(bar.maximum())
            assert bar.value() > 0
            bar.setValue(0)
            viewer.body_split.setSizes([600, 0, 310])
            viewer.body_split.splitterMoved.emit(0, 2)
            manual_height = viewer.settings_height
            QTest.qWait(20)
            assert viewer.settings_panel.height() == manual_height
            viewer.add_room('6', {'alias': 'Streamer 6'})
            QTest.qWait(20)
            assert viewer.settings_panel.height() == manual_height, 'Keep explicit splitter adjustments too'
            row = viewer.rows['2']
            assert row.assist.x() > row.offset_box.geometry().right()
            assert row.hint_fast.x() > row.assist.geometry().right()
            assert row.hint_slow.x() > row.hint_fast.geometry().right()
            assert row.hint_fast.isHidden() and row.hint_slow.isHidden()
            assert row.independent.parentWidget() is row.audible.parentWidget()
            assert row.independent.parentWidget() is not row.floating_button.parentWidget()
            assert isinstance(row.audible, module.VolumeButton)
            assert isinstance(row.realign, module.RefreshButton)
            assert isinstance(row.show_chat, module.DanmakuToggleButton)
            channel_faces = []
            for value in (3, 4, 0):
                row.channel.setCurrentIndex(row.channel.findData(value))
                channel_faces.append(row.channel.grab().toImage())
                assert row.audible.audio_channel == value
            assert all(a != b for i, a in enumerate(channel_faces) for b in channel_faces[i + 1:])
            assert '点击' in row.channel.toolTip()
            assert row.channel.left.isChecked() and row.channel.right.isChecked()
            QTest.mouseClick(row.channel.left, Qt.LeftButton)
            assert row.channel.currentData() == 4 and not row.channel.left.isChecked()
            assert app.activePopupWidget() is None
            inactive = row.channel.left
            inactive.setAttribute(Qt.WA_UnderMouse, True)
            app.sendEvent(inactive, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
            app.sendEvent(inactive, QMouseEvent(QEvent.MouseMove, QPointF(5, 5), QPointF(5, 5),
                                              Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            face = inactive.grab().toImage()
            scale = face.devicePixelRatio()
            background = face.pixelColor(round(6 * scale), round(6 * scale))
            assert background.blue() - background.red() <= 10, 'An inactive hovered channel must not look blue/on'
            inactive.setAttribute(Qt.WA_UnderMouse, False)
            app.sendEvent(inactive, QEvent(QEvent.Leave))
            face = inactive.grab().toImage()
            background = face.pixelColor(round(6 * scale), round(6 * scale))
            assert background.blue() - background.red() <= 10, 'Click focus must not leave an inactive channel blue'
            row.channel.right.click()
            assert row.channel.currentData() == 4 and row.channel.right.isChecked(), 'Retain an output side'
            row.channel.left.click()
            assert row.channel.currentData() == 0
            wheel = QWheelEvent(QPointF(10, 10), QPointF(row.channel.mapToGlobal(QPoint(10, 10))),
                                QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
            app.sendEvent(row.channel, wheel)
            assert row.channel.currentData() == 0, 'Scrolling the console must not change the channel'
            row.channel.right.click()
            assert row.channel.currentData() == 3
            row.channel.right.click()
            assert row.channel.currentData() == 0
            assert not hasattr(row, 'rename') and not hasattr(row, 'more_menu')
            color = row.color_choice
            popup = color.view().window()
            popup.setAttribute(Qt.WA_DontShowOnScreen)
            popup_events = []
            class PopupProbe(QObject):
                def eventFilter(self, widget, event):
                    if event.type() in (QEvent.Show, QEvent.Paint, QEvent.Resize) and widget.isVisible():
                        popup_events.append((event.type().name, widget.width(), widget.height()))
                    return False
            popup_probe = PopupProbe(popup)
            popup.installEventFilter(popup_probe)
            QTest.mouseClick(color, Qt.LeftButton)
            QTest.qWait(25)
            assert popup.isVisible(), 'The first click must leave the palette open'
            assert popup_events and all(height <= 40 for _, _, height in popup_events), popup_events
            QTest.qWait(app.doubleClickInterval())  # Respect the native combo's opening-release guard.
            rects = [color.view().visualRect(color.model().index(i, 0)) for i in range(color.count())]
            assert len({rect.y() for rect in rects}) == 1, rects
            assert all(a.right() < b.left() for a, b in zip(rects, rects[1:])), rects
            assert color.view().horizontalScrollBar().maximum() == 0
            if output:
                output = Path(output)
                output.parent.mkdir(parents=True, exist_ok=True)
                color.view().window().grab().save(str(output.with_name('horizontal-colors.png')))
            QTest.mouseClick(color.view().viewport(), Qt.LeftButton, pos=rects[-1].center())
            assert row.preferences()['color'] == module.COLORS[-1]
            color.hidePopup()
            for button in (viewer.automatic, viewer.compare, row.assist, row.hint_fast, row.hint_slow):
                with patch.object(button, 'hasFocus', return_value=True):
                    focused = button.grab().toImage()
                    accent = module.theme.qcolor(module.theme.ACCENT)
                    assert all(focused.pixelColor(x, 1) != accent for x in range(focused.width()))
            panels = import_module(module.__package__ + '.panels')
            for button in (viewer.compare, row.floating_button, row.color_choice):
                with patch.object(button, 'underMouse', return_value=False), \
                        patch.object(panels.QApplication, 'widgetAt', return_value=None):
                    with patch.object(button, 'hasFocus', return_value=False):
                        normal = button.grab().toImage()
                    with patch.object(button, 'hasFocus', return_value=True):
                        assert button.grab().toImage() == normal, 'Focus alone must not leave a blue selection background'
            assert not hasattr(viewer, 'alignment_hint_menu')
            assert row.hint_fast.text() == '偏快' and row.hint_slow.text() == '偏慢'
            # Numeric history descriptors exercise the real job dispatch, without decoding.
            for current in viewer.rows.values():
                current.decoder = SimpleNamespace(
                    history=SimpleNamespace(origin=100., lock=threading.RLock(), details=samples,
                                            snapshots=lambda: samples, bounds=lambda: (90., 120.),
                                            bounds_at=lambda _stamp: (90., 120.)),
                    stop=lambda: None)
            viewer.running = True
            viewer.render_timer.stop()
            viewer.match_timer.stop()
            viewer.results.matched.disconnect(viewer._matched)
            received = []
            def result(epoch, matches):
                viewer.matching = False
                received.append((epoch, matches))
                for current in viewer.rows.values():
                    current.refresh_status()
            viewer.results.matched.connect(result)
            positions = {key: -3. for key in viewer.rows}
            viewer.canvas.frame_key = 100.
            viewer.alignment.lags['2'] = 0.
            before = dict(viewer.alignment.lags)
            with patch.object(viewer, 'live_clock', return_value=103.), \
                    patch.object(viewer, 'shifts', return_value=positions), patch.object(viewer, 'analyse'):
                row.refresh_status()
                fixed_height = viewer.settings_panel.height()
                remove_position = row.remove_button.mapTo(row, QPoint())
                row.assist.click()
                app.processEvents()
                assert not row.hint_fast.isHidden() and not row.hint_slow.isHidden()
                assert row.remove_button.mapTo(row, QPoint()) == remove_position
                assert viewer.settings_panel.height() == fixed_height
                if output:
                    viewer.settings_panel.grab().save(str(output.with_name(output.stem + '-expanded.png')))
                row.assist.click()
                assert row.hint_fast.isHidden() and row.hint_slow.isHidden() and not row.hint_pending
                row.assist.click()
                row.hint_fast.click()
                assert row.alignment_hint == (0., -1, 1) and row.hint_pending
                assert not row.hint_fast.isEnabled() and not row.hint_slow.isEnabled()
                assert row.hint_fast.isHidden() and row.hint_slow.isHidden()
                assert not row.assist.isEnabled() and not row.assist.isChecked()
                if output:
                    viewer.settings_panel.grab().save(str(output.with_name(output.stem + '-cooldown.png')))
                row.assist.click()
                assert row.hint_fast.isHidden() and row.hint_slow.isHidden()
                row.hint_slow.click()
                assert row.alignment_hint[2] == 1, 'Do not count clicks during a pending scan'
                viewer._analysis_finished(viewer.analysis_serial - 1, viewer.generation, {'2': engine.Match(-4, 1, 'stale')})
                assert row.hint_pending, 'An older job cannot unlock the button'
                viewer._analysis_finished(viewer.analysis_serial, viewer.generation, {'2': engine.Match(None, 0, 'collecting')})
                assert row.hint_pending, 'Insufficient history did not complete a full scan'
                row.hint_serial = viewer.analysis_serial
                viewer._analysis_finished(viewer.analysis_serial, viewer.generation, {'2': engine.Match(None, 0, '匹配异常：fixture')})
                assert row.hint_pending, 'A failed job cannot unlock the button'
            class InlineJob:
                def __init__(self, target, **_kwargs):
                    self.target = target
                def start(self):
                    self.target()
            refined = engine.Match(-4., .95, 'verified descriptors', refined=True)
            with patch.object(module.threading, 'Thread', InlineJob), \
                    patch.object(module, 'match_scenes', return_value=refined) as coarse, \
                    patch.object(module, 'refine_match', return_value=refined), \
                    patch.object(viewer, 'live_clock', return_value=103.), \
                    patch.object(viewer, 'shifts', return_value=positions):
                viewer.analyse()
                assert received and coarse.call_args.kwargs['hint'] == (0., -1, 1)
                assert not row.hint_pending and row.hint_fast.isEnabled() and row.hint_slow.isEnabled()
                assert row.assist.isEnabled() and row.hint_fast.isHidden() and row.hint_slow.isHidden()
                with patch.object(viewer, 'analyse'):
                    row.assist.click()
                    row.hint_fast.click()
                assert row.alignment_hint[2] == 2
                viewer.analyse()
                assert coarse.call_args.kwargs['hint'][2] == 2
                with patch.object(viewer, 'analyse'):
                    row.assist.click()
                    row.hint_slow.click()
                assert row.alignment_hint[1:] == (1, 1), 'Changed direction starts new evidence'
                viewer.analyse()
            assert viewer.alignment.lags == before, 'Judgement alone never moves playback'
            if output:
                viewer.grab().save(str(output))
            with patch.object(viewer, '_reload_picture'):
                viewer.main.setCurrentIndex(viewer.main.findData('2'))
            assert all(current.alignment_hint is None and not current.hint_pending for current in viewer.rows.values())
            assert not row.hint_fast.isEnabled() and not row.hint_slow.isEnabled()
            return dict(passed=True, directional_ranking=True, no_evidence_no_alignment=True,
                        completed_job_gate=True, repeated_weight=True, stale_job_rejected=True,
                        fixed_height_five_sources=True, horizontal_palette=True,
                        native_host_icons=True, no_focus_border=True, first_popup_sized=True,
                        expandable_hint_buttons=True, completion_cooldown=True, stable_hint_expansion=True,
                        direct_channels=True, independent_sound_group=True, channel_faces=True,
                        state_only_channel_blue=True, no_focus_tint=True,
                        media_playback=False)
        finally:
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
