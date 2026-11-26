"""Host settings components, small-window scrolling and retained UI behavior."""
import argparse
from dataclasses import asdict
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', required=True, type=Path)
parser.add_argument('--render-dir', type=Path)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ.update(QT_QPA_PLATFORM='offscreen', DDM_NO_SAVE='1')
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QGroupBox, QToolButton, QWidget
from PySide6.QtTest import QTest
from ddm import theme
from ddm.ui import ActionButton, SelectControl, SettingsSurface, StepperControl, ToggleSwitch
from ddm.caption_preview import CaptionPlacementPreview

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('translate_ui_check', ROOT / 'plugins/live_translate/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Options = sys.modules[spec.name + '.options'].Options
APP = QApplication.instance() or QApplication([])
theme.danmaku_font_family()


class PanelLayoutTests(unittest.TestCase):
    def test_glossary_editor_keeps_room_and_target_language_profiles_separate(self):
        from ddm.plugins import StreamSource
        panel = self.panel
        tiles = [QWidget(), QWidget()]
        sources = {tile: StreamSource(str(i), 'https://example.invalid/live', platform='youtube', uname=f'房间{i}')
                   for i, tile in enumerate(tiles)}
        try:
            panel.refresh_sources(sources)
            editor = panel.glossary
            changes = []
            editor.changed.connect(changes.append)
            editor.editor.setPlainText('ぺこら = 佩克拉')
            editor.save.click()
            self.assertEqual(changes[-1]['youtube:0']['简体中文'], [['ぺこら', '佩克拉']])
            panel.target_language.setText('英语')
            self.assertEqual(editor.editor.toPlainText(), '')
            editor.editor.setPlainText('ぺこら = Pekora')
            editor.save.click()
            editor.room.setCurrentIndex(1)
            self.assertEqual(editor.editor.toPlainText(), '')
            panel.target_language.setText('简体中文')
            editor.room.setCurrentIndex(0)
            self.assertEqual(editor.editor.toPlainText(), 'ぺこら = 佩克拉')
            # An ordinary stream refresh must not erase an unfinished edit.
            editor.editor.setPlainText('unfinished')
            panel.refresh_sources(sources)
            self.assertEqual(editor.editor.toPlainText(), 'unfinished')
            editor.save.click()
            self.assertEqual(len(changes), 2)
            panel.glossary_details.setChecked(True)
            panel.resize(320, 420)
            panel.show()
            APP.processEvents()
            panel.scroll.ensureWidgetVisible(editor.save)
            APP.processEvents()
            self.assertLessEqual(editor.width(), panel.scroll.viewport().width())
            self.assertLessEqual(editor.room.width(), editor.width())
            self.assertTrue(editor.save.isVisible())
            panel.set_running(True)
            self.assertFalse(editor.isEnabled())
        finally:
            for tile in tiles:
                tile.deleteLater()

    def test_focused_selector_ignores_wheel_without_changing_selection(self):
        panel = self.panel
        panel.resize(440, 420)
        panel.show()
        APP.processEvents()
        scroll = panel.page_scrolls['overview']
        bar = scroll.verticalScrollBar()
        bar.setValue(0)
        combo = panel.translation_mode
        combo.setFocus()
        previous = combo.currentIndex()
        changes = []
        combo.currentIndexChanged.connect(changes.append)
        event = QWheelEvent(QPointF(combo.rect().center()),
                            QPointF(combo.mapToGlobal(combo.rect().center())),
                            QPoint(), QPoint(0, -120), Qt.NoButton,
                            Qt.NoModifier, Qt.NoScrollPhase, False)
        APP.sendEvent(combo, event)
        APP.processEvents()
        self.assertEqual(combo.currentIndex(), previous)
        self.assertEqual(changes, [])
        self.assertFalse(event.isAccepted())
        QTest.keyClick(combo, Qt.Key_Down)
        self.assertNotEqual(combo.currentIndex(), previous)

    def test_models_page_has_named_translation_choice_and_api_defaults_to_empty(self):
        panel = self.panel
        panel.navigation.setValue('models')
        panel.show()
        APP.processEvents()
        self.assertTrue(panel.translation_model_choice.isVisible())
        self.assertFalse(panel.local_model.isVisible())
        self.assertEqual(panel.translation_model_choice.currentData(), panel.local_model.text())
        self.assertTrue(panel.translation_model_choice.currentText().startswith('Qwen3 0.6B'))
        self.assertEqual(panel.model.text(), '')
        panel.translation_mode.setCurrentIndex(panel.translation_mode.findData('api'))
        self.assertEqual(panel.home_api_model.text(), '')
        self.assertTrue(panel.home_api_model.placeholderText())

    def test_custom_prompt_can_be_restored_and_prepared_models_fill_both_selectors(self):
        panel = self.panel
        panel.local_prompt.setPlainText('自定义 {source_language} 到 {target_language}')
        self.assertIn('自定义', panel.options().local_prompt)
        panel.prompt_resets[0].click()
        self.assertEqual(panel.options().local_prompt, '')
        values = dict(python='C:/prepared/runtime', model_dir='C:/prepared/new-asr',
                      local_model='C:/prepared/second.gguf', music_model_dir='C:/prepared/audio',
                      local_server='C:/prepared/server.exe')
        panel.apply_prepared_models(values)
        self.assertEqual(panel.translation_model_choice.currentData(), values['local_model'])
        self.assertEqual(panel.home_translation.currentData(), values['local_model'])
        panel.set_running(True)
        self.assertFalse(panel.local_prompt.isEnabled())
        self.assertFalse(panel.translation_model_choice.isEnabled())

    def test_overview_model_choices_update_advanced_paths_and_cancel_preserves_values(self):
        panel = self.panel
        panel.show()
        APP.processEvents()
        self.assertTrue(panel.home_recognition.isVisible())
        self.assertTrue(panel.home_translation.isVisible())
        picker_module = sys.modules[spec.name + '.model_picker']
        with patch.object(picker_module.QFileDialog, 'getOpenFileName', return_value=('C:/models/other.gguf', '')):
            panel.home_translation.activated.emit(panel.home_translation.count() - 1)
        self.assertEqual(panel.options().local_model, 'C:/models/other.gguf')
        panel.local_model.setText('C:/models/advanced.gguf')
        self.assertEqual(panel.home_translation.currentData(), 'C:/models/advanced.gguf')
        with patch.object(picker_module.QFileDialog, 'getExistingDirectory', return_value=''):
            panel.home_recognition.activated.emit(panel.home_recognition.count() - 1)
        self.assertEqual(panel.options().model_dir, self.settings['model_dir'])
        self.assertEqual(panel.home_recognition.currentIndex(), 0)
        panel.translation_mode.setCurrentIndex(panel.translation_mode.findData('api'))
        self.assertTrue(panel.home_api_model.isVisible())
        self.assertFalse(panel.home_translation.isVisible())
        panel.home_api_model.setText('chosen-api-model')
        self.assertEqual(panel.options().model, 'chosen-api-model')
        panel.set_running(True)
        for control in (panel.home_recognition, panel.home_translation, panel.home_api_model, panel.local_model):
            self.assertFalse(control.isEnabled())

    def test_disclosure_paints_never_expose_intermediate_header_positions(self):
        panel = self.panel
        panel.show()
        panel.navigation.setValue('models')
        APP.processEvents()
        for button in (panel.recognition_details, panel.translation_details):
            panel.scroll.verticalScrollBar().setValue(0)
            APP.processEvents()
            expected = button.mapTo(panel.scroll.widget(), QPoint()).y()
            positions = []
            class PaintTrace(QObject):
                def eventFilter(self, obj, event):
                    if event.type() == QEvent.Paint:
                        positions.append(button.mapTo(panel.scroll.widget(), QPoint()).y())
                    return False
            trace = PaintTrace()
            button.installEventFilter(trace)
            for _ in range(4):
                QTest.mouseClick(button, Qt.LeftButton)
                for _ in range(3):
                    APP.processEvents()
            self.assertTrue(positions)
            self.assertEqual(set(positions), {expected})
            button.removeEventFilter(trace)

    def test_local_translation_lists_existing_siblings_and_selects_without_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder) / 'first.gguf', Path(folder) / 'second.gguf'
            first.write_bytes(b'GGUFfixture'); second.write_bytes(b'GGUFfixture')
            panel = self.panel
            panel.local_model.setText(str(first))
            picker = panel.home_translation
            self.assertEqual(picker.count(), 3)
            panel.local_model.setText(second.as_posix())
            self.assertEqual(Path(picker.currentData()), second)
            panel.local_model.setText(str(first))
            picker.activated.emit(picker.findData(str(second)))
            self.assertEqual(panel.options().local_model, str(second))
            self.assertEqual(picker.currentData(), str(second))
            self.assertEqual(panel.local_server.text(), self.settings['local_server'])

    def test_action_buttons_keep_rounded_corners_when_focused_and_pressed(self):
        panel = self.panel
        panel.navigation.setValue('models')
        panel.recognition_details.setChecked(True)
        panel.show()
        APP.processEvents()
        pickers = [button for button in panel.findChildren(ActionButton) if button.property('pathPicker')]
        for mode in ('dark', 'light'):
            theme.apply(mode)
            for button in (panel.start, panel.hide_button, pickers[0]):
                button.clearFocus()
                APP.processEvents()
                idle = button.grab().toImage()
                button.setFocus(Qt.TabFocusReason)
                QTest.mousePress(button, Qt.LeftButton)
                held = button.grab().toImage()
                for x, y in ((0, 0), (1, 1), (2, 2)):
                    scale = held.devicePixelRatio()
                    self.assertEqual(held.pixelColor(round(x*scale), round(y*scale)),
                                     idle.pixelColor(round(x*scale), round(y*scale)))
                # Release away from the action to avoid opening a file dialog.
                QTest.mouseRelease(button, Qt.LeftButton, pos=QPoint(-10, -10))

    def setUp(self):
        self.previous_theme = theme.mode()
        self.settings = asdict(Options(python='C:/prepared/runtime/Scripts/python.exe',
                                      model_dir='C:/prepared/qwen3-asr',
                                      local_model='C:/prepared/Qwen3-0.6B-Q8_0.gguf',
                                      local_server='C:/prepared/llama-server.exe', music_detection=True))
        self.panel = module.Panel(self.settings, caption_preview_factory=CaptionPlacementPreview)

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        APP.processEvents()
        theme.apply(self.previous_theme)

    def test_public_host_controls_replace_groupboxes_and_default_editors(self):
        self.assertIsInstance(self.panel.start, ActionButton)
        self.assertTrue(callable(self.panel.sources.selected_tiles))
        self.assertIsInstance(self.panel.caption_enabled, ToggleSwitch)
        self.assertTrue(self.panel.options().music_detection)
        self.assertIsInstance(self.panel.threads.parentWidget(), StepperControl)
        self.assertGreaterEqual(len(self.panel.findChildren(SettingsSurface)), 5)
        self.assertEqual(self.panel.findChildren(QGroupBox), [])
        self.assertEqual(self.panel.start.objectName(), 'PrimaryButton')

    def test_pages_and_footer_fit_compact_sizes_in_both_themes(self):
        for mode in ('dark', 'light'):
            theme.apply(mode)
            for width, height in ((320, 360), (360, 480), (440, 560), (600, 680)):
                self.panel.resize(width, height)
                self.panel.show()
                for page in ('overview', 'captions', 'models'):
                    with self.subTest(mode=mode, size=(width, height), page=page):
                        self.panel.navigation.setValue(page)
                        APP.processEvents()
                        self.assertEqual(self.panel.size().width(), width)
                        self.assertEqual(self.panel.size().height(), height)
                        for button in (self.panel.start, self.panel.stop):
                            point = button.mapTo(self.panel, QPoint())
                            self.assertTrue(self.panel.rect().contains(point))
                            self.assertTrue(self.panel.rect().contains(point + QPoint(button.width()-1, button.height()-1)))
                        self.assertLessEqual(self.panel.scroll.widget().width(), self.panel.scroll.viewport().width())
                self.panel.navigation.setValue('overview')
                self.panel.scroll.verticalScrollBar().setValue(self.panel.scroll.verticalScrollBar().maximum())
                APP.processEvents()
                point = self.panel.preview.mapTo(self.panel.scroll.viewport(), QPoint())
                self.assertLess(point.y(), self.panel.scroll.viewport().height())
                self.assertGreater(point.y() + self.panel.preview.height(), 0)

    def test_disclosures_keep_values_signals_and_dont_start_or_stop_sessions(self):
        panel = self.panel
        panel.show()
        APP.processEvents()
        events, captions = [], []
        panel.startRequested.connect(lambda: events.append('start'))
        panel.stopRequested.connect(lambda: events.append('stop'))
        panel.captionStyleChanged.connect(captions.append)
        panel.navigation.setValue('models')
        self.assertFalse(panel.python.isVisible())
        panel.recognition_details.click()
        APP.processEvents()
        self.assertTrue(panel.python.isVisible())
        panel.navigation.setValue('captions')
        panel.caption_details.click()
        panel.caption_lines.setValue(3)
        panel.caption_font_size.parentWidget().plus.click()
        self.assertEqual(captions[-1].lines, 3)
        self.assertEqual(captions[-1].font_size, 25)
        panel.recognition_details.click()
        self.assertEqual(asdict(panel.options()), self.settings)
        self.assertEqual(events, [])
        panel.start.click()
        panel.set_running(True)
        panel.stop.click()
        self.assertEqual(events, ['start', 'stop'])

    def test_api_mode_and_realtime_theme_changes_keep_user_input(self):
        panel = self.panel
        panel.show()
        panel.translation_mode.setCurrentIndex(panel.translation_mode.findData('api'))
        panel.navigation.setValue('models')
        panel.endpoint.setText('http://127.0.0.1:11434/v1')
        panel.key.setText('fixture-key')
        for mode in ('light', 'dark'):
            theme.apply(mode)
            APP.processEvents()
            self.assertTrue(panel.api_group.isVisible())
            self.assertFalse(panel.local_group.isVisible())
            self.assertEqual(panel.options().endpoint, 'http://127.0.0.1:11434/v1')
            self.assertEqual(panel.key.text(), 'fixture-key')

    def test_language_route_reflows_and_icons_keep_accessible_names(self):
        panel = self.panel
        panel.show()
        for width, stacked in ((440, False), (320, True), (440, False)):
            panel.resize(width, 560)
            APP.processEvents()
            route = panel.language_route
            self.assertEqual(route.arrow.text(), '↓' if stacked else '→')
            for control in (panel.source_language, panel.target_language):
                self.assertTrue(route.rect().contains(control.mapTo(route, QPoint())))
                self.assertTrue(route.rect().contains(control.mapTo(route, control.rect().bottomRight())))
                self.assertGreaterEqual(control.height(), 28)
            source_rect = panel.source_language.geometry().translated(panel.source_language.parentWidget().mapTo(route, QPoint()))
            target_rect = panel.target_language.geometry().translated(panel.target_language.parentWidget().mapTo(route, QPoint()))
            self.assertFalse(source_rect.intersects(target_rect))
            self.assertLessEqual(route.mapTo(panel, route.rect().bottomLeft()).y(),
                                 panel.translation_mode.mapTo(panel, QPoint()).y())
            self.assertEqual(panel.options().source_language, '日语')
            self.assertEqual(panel.options().target_language, '简体中文')
        self.assertEqual(panel.stop.text(), '')
        self.assertEqual(panel.stop.accessibleName(), '停止传译')
        self.assertFalse(panel.stop.icon().isNull())
        pickers = [button for button in panel.findChildren(ActionButton) if button.property('pathPicker')]
        self.assertEqual(len(pickers), 5)
        self.assertTrue(all(button.toolTip() and button.accessibleName() and not button.icon().isNull()
                            for button in pickers))

    def test_missing_translation_has_no_fake_placeholder_and_metrics_preserve_warning(self):
        panel = self.panel
        panel.show()
        panel.update_caption('こんにちは。', '')
        self.assertFalse(panel.preview.translated.isVisible())
        self.assertEqual(panel.preview.original.text(), 'こんにちは。')
        panel.update_caption('こんにちは。', '你好。')
        self.assertTrue(panel.preview.translated.isVisible())
        panel.status.setText('译文未满足目标语言要求')
        panel.update_metrics({'event': 'caption', 'asr_ms': 230, 'translation_ms': 440})
        self.assertEqual(panel.status.text(), '译文未满足目标语言要求')
        self.assertIn('230 ms', panel.processing.text())
        self.assertIn('440 ms', panel.processing.text())

    def test_room_cards_support_multi_selection_and_keep_other_choices_on_refresh(self):
        from ddm.plugins import StreamSource
        panel = self.panel
        tiles = [QWidget(), QWidget(), QWidget()]
        sources = {tile: StreamSource(str(index), 'https://example.com/live', uname=f'房间 {index}')
                   for index, tile in enumerate(tiles)}
        panel.refresh_sources(sources, tiles[0])
        panel.show()
        APP.processEvents()
        QTest.mouseClick(panel.sources.buttons[tiles[1]], Qt.LeftButton)
        self.assertEqual(set(panel.sources.selected_tiles()), set(tiles[:2]))
        panel.refresh_sources(sources)
        self.assertEqual(set(panel.sources.selected_tiles()), set(tiles[:2]))
        panel.refresh_sources({tiles[1]: sources[tiles[1]], tiles[2]: sources[tiles[2]]})
        self.assertEqual(panel.sources.selected_tiles(), [tiles[1]])
        panel.update_room_caption(tiles[1], '房间一', '原文一', '译文一')
        panel.update_room_caption(tiles[2], '房间二', '原文二', '译文二')
        self.assertEqual(len(panel._room_previews), 2)
        self.assertEqual(panel._room_previews[tiles[1]].translated.text(), '译文一')
        self.assertEqual(panel._room_previews[tiles[2]].translated.text(), '译文二')
        for tile in tiles:
            tile.deleteLater()

    def test_model_popups_and_disclosures_keep_panel_and_viewport_width(self):
        panel = self.panel
        panel.show()
        panel.navigation.setValue('models')
        APP.processEvents()
        geometry = panel.geometry()
        width = panel.scroll.viewport().width()
        for index in range(3):
            panel.engine.showPopup()
            APP.processEvents()
            self.assertEqual(panel.geometry(), geometry)
            self.assertEqual(panel.scroll.viewport().width(), width)
            panel.engine.hidePopup()
            panel.translation_details.click()
            APP.processEvents()
            self.assertEqual(panel.geometry(), geometry)
            self.assertEqual(panel.scroll.viewport().width(), width)

    def test_interactive_preview_updates_parameters_and_parameters_update_preview(self):
        panel = self.panel
        panel.navigation.setValue('captions')
        panel.show()
        APP.processEvents()
        events = []
        panel.captionStyleChanged.connect(events.append)
        panel.style_preview.caption._interaction.apply(x=15, y=30, width=60, height=30)
        self.assertEqual((panel.caption_x.value(), panel.caption_y.value(), panel.caption_width.value()), (15, 30, 60))
        self.assertEqual(len(events), 1)
        panel.caption_opacity.setValue(40)
        self.assertEqual(panel.style_preview.caption._style.opacity, 40)
        self.assertTrue(panel.options().music_detection)
        before = panel.style_preview.mapTo(panel, QPoint())
        panel.scroll.verticalScrollBar().setValue(panel.scroll.verticalScrollBar().maximum())
        APP.processEvents()
        self.assertEqual(panel.style_preview.mapTo(panel, QPoint()), before)
        self.assertTrue(panel.style_preview.isVisible())


class AnchoredSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.host.resize(1200, 850)
        self.anchor = QToolButton(self.host)
        self.anchor.setGeometry(12, 170, 42, 42)
        self.panel = module.Panel(asdict(Options()))
        self.popover = module.AnchoredPanel(self.panel, self.host, self.anchor)
        self.panel.hideRequested.connect(self.popover.hide)
        self.host.show()
        self.panel.show()
        self.popover.show()
        APP.processEvents()

    def tearDown(self):
        self.popover.hide()
        self.host.close()
        self.host.deleteLater()
        APP.processEvents()

    def test_surface_tracks_rail_and_stays_inside_small_and_portrait_hosts(self):
        self.assertFalse(self.popover.isWindow())
        self.assertFalse(self.panel.isWindow())
        self.assertEqual(self.popover.x(), self.anchor.x() + self.anchor.width() + 10)
        for size in ((320, 360), (360, 480), (480, 480), (720, 900), (1200, 850)):
            self.host.resize(*size)
            APP.processEvents()
            self.assertTrue(self.host.rect().contains(self.popover.geometry()))
            self.assertLessEqual(self.popover.width(), 440)
            self.assertTrue(self.panel.rect().contains(self.panel.start.mapTo(self.panel, QPoint())))
        self.anchor.move(70, 42)
        APP.processEvents()
        self.assertEqual(self.popover.x(), self.anchor.x() + self.anchor.width() + 10)

    def test_top_and_bottom_grips_change_height_and_retain_it_after_reopen_and_host_resize(self):
        popover = self.popover
        changes = []
        popover.heightChanged.connect(changes.append)
        before = popover.geometry()
        grip = popover.bottom_grip
        point = grip.rect().center()
        QTest.mousePress(grip, Qt.LeftButton, pos=point)
        QTest.mouseMove(grip, point + QPoint(0, 60))
        QTest.mouseRelease(grip, Qt.LeftButton, pos=point)
        self.assertEqual(popover.height(), before.height() + 60)
        self.assertEqual(popover.y(), before.y())
        bottom = popover.geometry().bottom()
        grip = popover.top_grip
        point = grip.rect().center()
        QTest.mousePress(grip, Qt.LeftButton, pos=point)
        QTest.mouseMove(grip, point - QPoint(0, 40))
        QTest.mouseRelease(grip, Qt.LeftButton, pos=point)
        self.assertEqual(popover.geometry().bottom(), bottom)
        expected = popover.height()
        self.assertEqual(changes, [before.height() + 60, expected])
        popover.hide(); popover.show()
        APP.processEvents()
        self.assertEqual(popover.height(), expected)
        self.host.resize(320, 360)
        APP.processEvents()
        self.assertTrue(self.host.rect().contains(popover.geometry()))
        self.host.resize(1200, 850)
        APP.processEvents()
        self.assertEqual(popover.height(), expected)

    def test_inside_click_keeps_surface_and_outside_click_dismisses(self):
        QTest.mouseClick(self.panel.preview, Qt.LeftButton)
        self.assertTrue(self.popover.isVisible())
        QTest.mouseClick(self.host, Qt.LeftButton, pos=QPoint(1100, 780))
        self.assertFalse(self.popover.isVisible())

    def test_escape_and_hide_button_keep_settings_and_emit_no_session_stop(self):
        stopped = []
        self.panel.stopRequested.connect(lambda: stopped.append(True))
        self.panel.target_language.setText('繁体中文')
        self.popover.setFocus()
        QTest.keyClick(self.popover, Qt.Key_Escape)
        APP.processEvents()
        self.assertFalse(self.popover.isVisible())
        self.popover.show()
        self.assertEqual(self.panel.options().target_language, '繁体中文')
        self.panel.hide_button.click()
        self.assertFalse(self.popover.isVisible())
        self.assertEqual(stopped, [])

    def test_combo_popup_selection_does_not_dismiss_translation_surface(self):
        self.panel.source_language.showPopup()
        APP.processEvents()
        view = self.panel.source_language.view()
        rect = view.visualRect(view.model().index(1, 0))
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=rect.center())
        APP.processEvents()
        self.assertTrue(self.popover.isVisible())
        self.assertEqual(self.panel.source_language.currentText(), '英语')


def render():
    from PySide6.QtWidgets import QWidget, QToolButton
    args.render_dir.mkdir(parents=True, exist_ok=True)
    host = QWidget()
    host.resize(1100, 800)
    anchor = QToolButton(host)
    anchor.setGeometry(12, 176, 42, 42)
    anchor.setText('译')
    from ddm.plugins import StreamSource
    panel = module.Panel(asdict(Options()), caption_preview_factory=CaptionPlacementPreview)
    tiles = [QWidget(host), QWidget(host)]
    sources = {tiles[0]: StreamSource('fixture-a', 'https://example.invalid/a', uname='聊天直播'),
               tiles[1]: StreamSource('fixture-b', 'https://example.invalid/b', uname='游戏直播')}
    panel.refresh_sources(sources, tiles[0])
    panel.sources.buttons[tiles[1]].setChecked(True)
    popover = module.AnchoredPanel(panel, host, anchor)
    panel.hideRequested.connect(popover.hide)
    panel.update_caption('今日はみんなとゲームを楽しみます。', '今天和大家一起享受游戏。')
    panel.status.setText('选择直播间后开始')
    for mode in ('dark', 'light'):
        theme.apply(mode)
        host.show()
        panel.show()
        popover.show()
        panel.navigation.setValue('overview')
        APP.processEvents()
        assert popover.grab().save(str(args.render_dir / f'panel-{mode}.png'))
        panel.glossary_details.setChecked(True)
        panel.glossary.editor.setPlainText('ぺこら = 佩克拉\nMinecraft = 我的世界')
        panel.scroll.ensureWidgetVisible(panel.glossary.save)
        APP.processEvents()
        assert popover.grab().save(str(args.render_dir / f'panel-{mode}-glossary.png'))
        panel.glossary_details.setChecked(False)
        panel.scroll.verticalScrollBar().setValue(0)
        panel.navigation.setValue('captions')
        panel.caption_details.setChecked(True)
        APP.processEvents()
        panel.style_preview.caption._interaction.toolbar.reveal()
        APP.processEvents()
        assert popover.grab().save(str(args.render_dir / f'panel-{mode}-captions.png'))
        panel.navigation.setValue('models')
        panel.recognition_details.setChecked(True)
        APP.processEvents()
        assert popover.grab().save(str(args.render_dir / f'panel-{mode}-models.png'))
        panel.navigation.setValue('overview')
        host.resize(320, 420)
        APP.processEvents()
        assert popover.grab().save(str(args.render_dir / f'panel-{mode}-compact.png'))
        host.resize(1100, 800)
        popover.hide()
    host.close()
    host.deleteLater()
    APP.processEvents()


if __name__ == '__main__':
    result = unittest.main(argv=[sys.argv[0], *remaining], exit=False)
    if result.result.wasSuccessful() and args.render_dir:
        render()
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
