"""Room isolation, shared-model ownership and bounded multi-room lifecycle."""
import argparse
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QWidget
from ddm.plugins import StreamSource

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('multi_translate_plugin', ROOT / 'plugins/live_translate/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from multi_translate_plugin.options import Options, with_bundled_defaults
from multi_translate_plugin.local_translation import LocalTranslation
from multi_translate_plugin.session import Session
from multi_translate_plugin import recognizer_pool
from multi_translate_plugin.embedded_recognition import EmbeddedRecognition, _recognition_workers

APP = QApplication.instance() or QApplication([])


class MultiRoomChecks(unittest.TestCase):
    def test_glossaries_reach_only_their_room_and_matching_target_language(self):
        first, second = [source for _tile, source in self.sources]
        rooms = {f'{first.platform}:{first.room_id}': {'简体中文': [['ぺこら', '佩克拉']]},
                 f'{second.platform}:{second.room_id}': {'英语': [['ぺこら', 'Pekora']]}}
        with patch.object(Options, 'validate'), patch.object(LocalTranslation, 'start'), \
                patch.object(Session, '_start_recognition', autospec=True) as recognition:
            self.plugin.group.start(Options(), '', self.sources, 'ffmpeg', glossaries=rooms)
            self.plugin.group.local.ready.emit('http://127.0.0.1:12345/v1')
            options = [call.args[1] for call in recognition.call_args_list]
            self.assertEqual(options[0].glossary, (('ぺこら', '佩克拉'),))
            self.assertEqual(options[1].glossary, ())

    def test_glossary_save_uses_host_setting_without_polluting_global_options(self):
        self.plugin.open()
        editor = self.plugin.panel.glossary
        editor.editor.setPlainText('FF14 = FF14')
        editor.save.click()
        source = self.sources[0][1]
        self.assertEqual(self.settings['glossary_rooms'][f'{source.platform}:{source.room_id}']['简体中文'], [['FF14', 'FF14']])
        self.plugin._save_panel_settings()
        self.assertNotIn('glossary', self.settings['options'])
        self.assertNotIn('FF14', json.dumps(self.settings['options']))

    def test_source_reset_updates_terms_but_keeps_the_local_endpoint_snapshot(self):
        options = Options(endpoint='http://127.0.0.1:12345/v1', model='live-translate',
                          glossary_language='简体中文', glossary=(('old', '旧'),))
        worker = EmbeddedRecognition(options)
        worker.reset_source(glossary=(('new', '新'),))
        self.assertEqual(worker.options.endpoint, options.endpoint)
        self.assertEqual(worker.options.glossary, (('new', '新'),))
        self.assertEqual(options.glossary, (('old', '旧'),))
        self.assertEqual(worker.source_epoch, 1)
        worker.deleteLater()

    def setUp(self):
        self.window = QWidget()
        self.settings, self.writes, self.clears = {}, [], []
        context = SimpleNamespace(window=self.window,
            setting=lambda key, default=None: self.settings.get(key, default),
            set_setting=lambda key, value: self.settings.update({key: value}),
            register_workspace_control=lambda *a, **k: True, log=lambda *a: None,
            set_tile_caption=lambda *a, **k: self.writes.append((a, k)) or True,
            clear_tile_caption=lambda tile: self.clears.append(tile) or True)
        self.plugin = module.LiveTranslate()
        self.plugin.on_load(context)
        self.tiles = [QWidget(self.window), QWidget(self.window)]
        self.sources = [(tile, StreamSource(str(index), 'https://example.invalid/live', uname=f'Room {index}'))
                        for index, tile in enumerate(self.tiles)]
        self.plugin.sources.update(self.sources)

    def tearDown(self):
        self.plugin.on_unload()
        self.window.deleteLater()
        APP.processEvents()

    def start_group(self):
        self.plugin.group.start(Options(), 'private-key-not-saved', self.sources, 'ffmpeg')
        local = self.plugin.group.local
        local.api_key = 'private-loopback-key'
        local.ready.emit('http://127.0.0.1:12345/v1')
        return local

    def test_one_translation_service_and_caption_failure_isolation(self):
        group = self.plugin.group
        with patch.object(Options, 'validate'), patch.object(LocalTranslation, 'start') as start, \
                patch.object(Session, '_start_recognition', autospec=True) as recognition:
            local = self.start_group()
            self.assertEqual(start.call_count, 1)
            self.assertEqual(recognition.call_count, 2)
            self.assertTrue(all(call.args[1].endpoint == 'http://127.0.0.1:12345/v1'
                                and call.args[2] == local.api_key for call in recognition.call_args_list))
            sessions = [group.sessions[tile] for tile in self.tiles]
            self.assertTrue(all(session.local is None for session in sessions))
            sessions[0].caption.emit('original one', '译文一')
            sessions[1].caption.emit('original two', '译文二')
            self.assertEqual([entry[0][0] for entry in self.writes], self.tiles)
            self.assertEqual([entry[0][2] for entry in self.writes], ['译文一', '译文二'])
            second_audio = sessions[1].audio = QProcess(sessions[1])
            sessions[1]._failed(second_audio, 'fixture failure')
            self.assertTrue(group.is_running)
            self.assertIs(group.local, local)
            self.assertIsNotNone(group.session_for(self.tiles[0]))
            self.assertIsNone(group.session_for(self.tiles[1]))
            count = len(self.writes)
            sessions[1].caption.emit('stale', '迟到译文')
            self.assertEqual(len(self.writes), count)
            group.stop_tile(self.tiles[0])
            self.assertIsNone(group.local)
            self.assertFalse(group.is_running)
        self.assertNotIn('private-', json.dumps(self.settings))

    def test_remove_and_stop_while_loading_reject_late_ready(self):
        group = self.plugin.group
        with patch.object(Options, 'validate'), patch.object(LocalTranslation, 'start'), \
                patch.object(Session, '_start_recognition') as recognition:
            group.start(Options(), '', self.sources, 'ffmpeg')
            local = group.local
            group.stop_tile(self.tiles[0])
            local.ready.emit('http://127.0.0.1:12345/v1')
            self.assertEqual(recognition.call_count, 1)
            self.assertIsNone(group.session_for(self.tiles[0]))
            group.stop()
            count = recognition.call_count
            local.ready.emit('http://127.0.0.1:23456/v1')
            self.assertEqual(recognition.call_count, count)

    def test_room_placement_and_quick_changes_remain_independent(self):
        presentation = self.plugin.presentation
        for tile, _source in self.sources:
            presentation.show(tile, '原文', '译文')
        presentation.edited(self.tiles[0], {'x': 10, 'y': 20, 'locked': True})
        presentation.edited(self.tiles[1], {'x': 80, 'y': 90, 'mode': 'translation'})
        self.assertEqual(presentation.style_for(self.tiles[0]).x, 10)
        self.assertEqual(presentation.style_for(self.tiles[1]).x, 80)
        presentation.apply_style(replace(presentation.style, opacity=40))
        self.assertEqual(presentation.style_for(self.tiles[0]).x, 10)
        self.assertEqual(presentation.style_for(self.tiles[1]).mode, 'translation')
        self.assertEqual(presentation.style_for(self.tiles[1]).opacity, 40)
        self.assertEqual(len(self.settings['caption_rooms']), 2)
        action = self.plugin.tile_actions(self.tiles[0])[-1]
        self.assertEqual(action[0], '解锁翻译字幕')
        action[1]()
        self.assertFalse(presentation.style_for(self.tiles[0]).locked)
        self.assertNotIn('原文', json.dumps(self.settings, ensure_ascii=False))

    def test_music_is_automatic_even_for_previously_disabled_settings(self):
        self.assertTrue(with_bundled_defaults({'music_detection': False})['music_detection'])
        self.plugin.open()
        self.assertTrue(self.plugin.panel.options().music_detection)

    def test_caption_master_switch_wins_over_saved_room_preferences_and_late_results(self):
        presentation = self.plugin.presentation
        presentation.style = replace(presentation.style, enabled=False)
        for tile, _source in self.sources:
            presentation.room_styles[presentation._key(tile)] = replace(presentation.style, enabled=True).mapping()
            presentation.show(tile, 'original', '译文')
            presentation.edited(tile, {'x': 20})
            presentation.show(tile, 'late original', '迟到译文')
        self.assertEqual(self.writes, [])
        self.assertTrue(all(tile in self.clears for tile in self.tiles))
        presentation.apply_style(replace(presentation.style, enabled=True))
        self.assertEqual(len(self.writes), 2)

    def test_manual_panel_height_is_saved_separately_from_model_configuration(self):
        self.plugin.open()
        self.plugin.popover.heightChanged.emit(700)
        self.assertEqual(self.settings['panel_height'], 700)
        self.assertNotIn('panel_height', self.settings.get('options', {}))

    def test_recognizer_pool_loads_once_and_releases_last_user(self):
        model = object()
        with patch('multi_translate_plugin.recognition.create_recognizer', return_value=model) as create:
            first = recognizer_pool.acquire(Options(), object())
            second = recognizer_pool.acquire(Options(), object())
            self.assertEqual(create.call_count, 1)
            self.assertIs(first[1], second[1])
            self.assertIs(first[2], second[2])
            recognizer_pool.release(first[0])
            self.assertEqual(len(recognizer_pool._models), 1)
            recognizer_pool.release(second[0])
            self.assertEqual(recognizer_pool._models, {})

    def test_resource_updates_do_not_recount_the_previous_aggregate(self):
        group = self.plugin.group
        with patch.object(Options, 'validate'), patch.object(Session, '_start_recognition'):
            group.start(Options(translation_mode='original'), '', self.sources, 'ffmpeg')
            sessions = [group.sessions[tile] for tile in self.tiles]
            def record(rss):
                return dict(event='resources', child_rss_bytes=rss, pending_pcm_bytes=10,
                            dropped_audio_seconds=0, recognition_embedded=True)
            group._metrics(sessions[0], record(10))
            group._metrics(sessions[1], record(20))
            self.assertEqual(group.samples[-1]['child_rss_bytes'], 30)
            group._metrics(sessions[0], record(11))
            self.assertEqual(group.samples[-1]['child_rss_bytes'], 31)
            group._metrics(sessions[1], record(None))
            self.assertIsNone(group.samples[-1]['child_rss_bytes'])

    def test_group_members_can_run_but_retiring_workers_block_restart(self):
        group = object()
        worker = EmbeddedRecognition(Options(), group=group)
        with patch.object(worker._thread, 'is_alive', return_value=True):
            _recognition_workers.add(worker)
            try:
                self.assertTrue(EmbeddedRecognition.available(group))
                self.assertFalse(EmbeddedRecognition.available())
                self.assertFalse(EmbeddedRecognition.available(object()))
                worker._stop.set()
                self.assertFalse(EmbeddedRecognition.available(group))
            finally:
                _recognition_workers.discard(worker)
        worker.deleteLater()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
