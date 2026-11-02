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
    from PySide6.QtCore import QPoint, Qt
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
            assert isinstance(row.audible, module.VolumeButton)
            assert isinstance(row.realign, module.RefreshButton)
            assert isinstance(row.show_chat, module.DanmakuToggleButton)
            assert not hasattr(row, 'rename') and not hasattr(row, 'more_menu')
            color = row.color_choice
            color.view().window().setAttribute(Qt.WA_DontShowOnScreen)
            color.showPopup()
            QTest.qWait(25)
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
            for button in (viewer.automatic, viewer.compare, row.assist):
                with patch.object(button, 'hasFocus', return_value=True):
                    focused = button.grab().toImage()
                    accent = module.theme.qcolor(module.theme.ACCENT)
                    assert all(focused.pixelColor(x, 1) != accent for x in range(focused.width()))
            menu = viewer.alignment_hint_menu('2')
            assert [action.text() for action in menu.actions()] == ['本路比主画面偏快', '本路比主画面偏慢']
            menu.deleteLater()
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
                viewer.submit_alignment_hint('2', -1)
                assert row.alignment_hint == (0., -1, 1) and row.hint_pending
                assert not row.assist.isEnabled()
                viewer.submit_alignment_hint('2', -1)
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
                assert not row.hint_pending and row.assist.isEnabled()
                with patch.object(viewer, 'analyse'):
                    viewer.submit_alignment_hint('2', -1)
                assert row.alignment_hint[2] == 2
                viewer.analyse()
                assert coarse.call_args.kwargs['hint'][2] == 2
                with patch.object(viewer, 'analyse'):
                    viewer.submit_alignment_hint('2', 1)
                assert row.alignment_hint[1:] == (1, 1), 'Changed direction starts new evidence'
                viewer.analyse()
            assert viewer.alignment.lags == before, 'Judgement alone never moves playback'
            if output:
                viewer.grab().save(str(output))
            with patch.object(viewer, '_reload_picture'):
                viewer.main.setCurrentIndex(viewer.main.findData('2'))
            assert all(current.alignment_hint is None and not current.hint_pending for current in viewer.rows.values())
            assert not row.assist.isEnabled()
            return dict(passed=True, directional_ranking=True, no_evidence_no_alignment=True,
                        completed_job_gate=True, repeated_weight=True, stale_job_rejected=True,
                        fixed_height_five_sources=True, horizontal_palette=True,
                        native_host_icons=True, no_focus_border=True, media_playback=False)
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
