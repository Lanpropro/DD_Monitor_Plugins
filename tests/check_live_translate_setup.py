"""First-use consent, verified downloads, cancellation and API preferences."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ.update(QT_QPA_PLATFORM='offscreen', DDM_NO_SAVE='1')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_translate_check', ROOT / 'plugins/live_translate/plugin.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from setup_translate_check.options import Options, QWEN_FILES, TOKENIZER_FILES, with_bundled_defaults
from setup_translate_check import prepare_runtime as prepare, runtime_setup, setup_dialog
from setup_translate_check.session_settings import panel_settings, save_options
APP = QApplication.instance() or QApplication([])


def prepared_fixture(root):
    values = asdict(Options(python=str(root / 'runtime'), model_dir=str(root / 'asr'),
                    music_detection=True, music_model_dir=str(root / 'music'),
                    local_model=str(root / 'model.gguf'), local_server=str(root / 'server.exe')))
    for package in ('numpy', 'sherpa_onnx'):
        (root / 'runtime/Lib/site-packages' / package).mkdir(parents=True)
    for name in (*QWEN_FILES.values(), 'silero_vad.onnx', *(f'tokenizer/{name}' for name in TOKENIZER_FILES)):
        path = root / 'asr' / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
    for name in ('model.int8.onnx', 'class_labels_indices.csv'):
        path = root / 'music' / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
    Path(values['local_model']).write_bytes(b'GGUFfixture')
    Path(values['local_server']).write_bytes(b'fixture')
    return values


class SetupChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.window = QWidget(); self.window.show()
        self.settings = {}
        self.context = SimpleNamespace(window=self.window,
            setting=lambda key, default=None: self.settings.get(key, default),
            set_setting=lambda key, value: self.settings.update({key: value}))

    def tearDown(self):
        self.window.close(); self.window.deleteLater()
        APP.processEvents()
        self.temporary.cleanup()

    def test_first_use_offers_choices_without_downloading_and_later_does_not_repeat(self):
        coordinator = setup_dialog.SetupCoordinator(self.context, Mock(), Mock())
        with patch.object(setup_dialog, 'install_models') as install, patch.object(setup_dialog.SetupJob, 'start') as start:
            coordinator.offer()
            self.assertTrue(coordinator.dialog.isVisible())
            self.assertTrue(self.settings['setup_offered'])
            coordinator.dialog.later.click()
            coordinator.offer()
            self.assertFalse(coordinator.dialog.isVisible())
            install.assert_not_called(); start.assert_not_called()
            coordinator.open()
            self.assertTrue(coordinator.dialog.isVisible())
        coordinator.close(); coordinator.deleteLater()

    def test_existing_models_skip_automatic_offer(self):
        self.settings['options'] = prepared_fixture(self.root)
        coordinator = setup_dialog.SetupCoordinator(self.context, Mock(), Mock())
        coordinator.offer()
        self.assertIsNone(coordinator.dialog)
        coordinator.close(); coordinator.deleteLater()

    def test_offer_waits_for_window_show_when_plugin_loads_before_the_main_window(self):
        self.window.hide()
        coordinator = setup_dialog.SetupCoordinator(self.context, Mock(), Mock())
        coordinator.offer()
        self.assertIsNone(coordinator.dialog)
        self.window.show(); APP.processEvents()
        self.assertTrue(coordinator.dialog.isVisible())
        coordinator.close(); coordinator.deleteLater()

    def test_model_files_without_runtime_libraries_still_offer_preparation(self):
        self.settings['options'] = prepared_fixture(self.root)
        (self.root / 'runtime/Lib/site-packages/sherpa_onnx').rmdir()
        coordinator = setup_dialog.SetupCoordinator(self.context, Mock(), Mock())
        coordinator.offer()
        self.assertIsNotNone(coordinator.dialog)
        coordinator.close(); coordinator.deleteLater()

    def test_preparing_models_preserves_the_users_api_mode(self):
        self.settings['options'] = dict(translation_mode='api', endpoint='https://example.invalid/v1', model='saved-api')
        plugin = module.LiveTranslate()
        plugin.context = self.context
        plugin._show_models = Mock()
        values = prepared_fixture(self.root)
        prepared = {name: values[name] for name in ('python', 'model_dir', 'music_model_dir',
                    'music_detection', 'engine', 'local_model', 'local_server', 'translation_mode')}
        plugin._models_prepared(prepared)
        self.assertEqual(self.settings['options']['translation_mode'], 'api')
        self.assertEqual(self.settings['options']['model'], 'saved-api')

    def test_download_starts_only_after_click_and_closing_requests_cancellation(self):
        dialog = setup_dialog.SetupDialog(str(self.root), self.window)
        fake = SimpleNamespace(progress=Mock(), failed=Mock(), completed=Mock(), finished=Mock(), start=Mock(), cancel=Mock())
        with patch.object(setup_dialog, 'SetupJob', return_value=fake) as factory:
            dialog.show(); APP.processEvents()
            factory.assert_not_called()
            dialog.download.click()
            fake.start.assert_called_once()
            self.assertFalse(dialog.directory.isEnabled())
            dialog.close()
            fake.cancel.assert_called_once()
        dialog.job = None
        dialog.deleteLater()

    def test_job_keeps_ownership_until_cancellation_finishes_and_never_applies_cancelled_result(self):
        entered, release = threading.Event(), threading.Event()
        values = prepared_fixture(self.root)
        def operation(directory, progress, cancel):
            entered.set(); release.wait(2)
            return values
        job = setup_dialog.SetupJob(str(self.root), operation)
        results = []; job.completed.connect(results.append)
        job.start(); self.assertTrue(entered.wait(1))
        job.cancel(); self.assertIn(job, setup_dialog._jobs)
        release.set()
        deadline = time.monotonic()+2
        while job in setup_dialog._jobs and time.monotonic()<deadline:
            QTest.qWait(10)
        self.assertNotIn(job, setup_dialog._jobs)
        self.assertEqual(results, [])

    def test_download_checks_digest_reports_progress_and_cancel_preserves_existing_file(self):
        destination = self.root / 'model.bin'
        body = b'new artifact'
        def response():
            result = io.BytesIO(body); result.headers = {'Content-Length': str(len(body))}
            return result
        progress = []
        with patch.object(prepare.urllib.request, 'urlopen', side_effect=lambda *a, **k: response()):
            prepare.download('https://example.invalid', destination, hashlib.sha256(body).hexdigest(),
                             progress=lambda done, total: progress.append((done, total)))
            self.assertEqual(destination.read_bytes(), body)
            self.assertEqual(progress[-1], (len(body), len(body)))
            with self.assertRaises(ValueError):
                prepare.download('https://example.invalid', destination, 'wrong')
            cancel = threading.Event(); cancel.set()
            with self.assertRaises(InterruptedError):
                prepare.download('https://example.invalid', destination, cancel=cancel)
            cancel.clear()
            with self.assertRaises(InterruptedError):
                prepare.download('https://example.invalid', destination, cancel=cancel,
                                 progress=lambda done, total: cancel.set())
        self.assertEqual(destination.read_bytes(), body)
        self.assertFalse(destination.with_name('model.bin.part').exists())

    def test_first_setup_pins_both_1_7b_models_and_reuses_completed_files(self):
        fetches = {}

        def fetch(url, target, digest):
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
            fetches[target] = url
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'GGUFfixture' if target.suffix == '.gguf' else b'fixture')

        server = self.root / 'translation' / prepare.LLAMA_TAG / 'llama-server.exe'
        server.parent.mkdir(parents=True)
        server.touch()
        asr = prepare.prepare_recognition(self.root, fetch=fetch)
        translation = prepare.prepare_translation(self.root, fetch=fetch)
        model = Path(translation['local_model'])
        self.assertEqual(asr.name, 'qwen3-asr-1.7b-int8')
        self.assertEqual(model.name, 'Qwen3-1.7B-Q8_0.gguf')
        self.assertIn('/Qwen/Qwen3-1.7B-GGUF/resolve/', fetches[model])
        self.assertIn('/sherpa-onnx-qwen3-asr-1.7B-int8/resolve/', fetches[asr / 'decoder.int8.onnx'])
        Options(model_dir=str(asr), **translation).validate()
        (asr / 'encoder.int8.onnx').unlink()
        fetches.clear()
        prepare.prepare_recognition(self.root, fetch=fetch)
        prepare.prepare_translation(self.root, fetch=fetch)
        self.assertEqual(set(fetches), {asr / 'encoder.int8.onnx'})

    def test_ranged_weights_are_assembled_and_verified_before_installing(self):
        body = b'GGUF012345'
        digest = hashlib.sha256(body).hexdigest()
        destination = self.root / 'local.gguf'
        failures = []

        class InterruptedRange(io.BytesIO):
            def read1(self, size):
                if self.tell():
                    raise TimeoutError('interrupted range')
                return self.read(1)

        def response(request, timeout):
            start, end = map(int, request.get_header('Range').removeprefix('bytes=').split('-'))
            data = body[start:end+1]
            if failures == [4, 5, 'corrupt'] and start == 4:
                data = b'X' * len(data)
            if start == 4 and not failures:
                failures.append(start)
                stream = InterruptedRange(data)
            else:
                if start == 5:
                    failures.append(start)
                stream = io.BytesIO(data)
            stream.headers = {'Content-Range': f'bytes {start}-{end}/{len(body)}'}
            return stream

        with patch.object(prepare, 'LARGE_FILES', {digest: len(body)}), \
                patch.object(prepare, 'RANGE_SIZE', 4), \
                patch.object(prepare.urllib.request, 'urlopen', side_effect=response):
            progress = []
            prepare.download('https://example.invalid/model', destination, digest,
                             progress=lambda done, total: progress.append((done, total)))
            self.assertEqual(failures, [4, 5])
            self.assertEqual(progress[-1], (len(body), len(body)))
            self.assertEqual(destination.read_bytes(), body)
            failures.append('corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                prepare.download('https://example.invalid/model', destination, digest)
            self.assertEqual(destination.read_bytes(), body)
            self.assertFalse(destination.with_suffix('.gguf.part').exists())

    def test_upgrade_preserves_existing_model_paths_even_with_1_7b_available(self):
        models = self.root / 'vendor/models'
        for size in ('1.7', '0.6'):
            asr = models / f'qwen3-asr-{size}b-int8'
            for name in (*QWEN_FILES.values(), 'silero_vad.onnx',
                         *(f'tokenizer/{name}' for name in TOKENIZER_FILES)):
                target = asr / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            (models / f'Qwen3-{size}B-Q8_0.gguf').write_bytes(b'GGUFfixture')
        self.assertEqual(Path(with_bundled_defaults({}, self.root)['model_dir']).name, 'qwen3-asr-1.7b-int8')
        self.assertEqual(Path(with_bundled_defaults({}, self.root)['local_model']).name, 'Qwen3-1.7B-Q8_0.gguf')
        previous = dict(model_dir=str(models / 'qwen3-asr-0.6b-int8'),
                        local_model=str(models / 'Qwen3-0.6B-Q8_0.gguf'))
        effective = with_bundled_defaults(previous, self.root)
        self.assertEqual({key: effective[key] for key in previous}, previous)

    def test_wheel_extraction_rejects_escape_and_marks_only_completed_install(self):
        archive = self.root / 'fixture.whl'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('../escaped.py', 'bad')
        with self.assertRaises(ValueError):
            runtime_setup.extract_wheel(archive, self.root / 'libraries', threading.Event())
        self.assertFalse((self.root / 'escaped.py').exists())
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('package/__init__.py', '# fixture')
        raw = archive.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
        def fetch(url, target, expected):
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        with patch.object(runtime_setup, 'WHEELS', (('fixture.whl', 'fixture', digest),)):
            runtime = runtime_setup.prepare_libraries(self.root, fetch, threading.Event())
            self.assertTrue((runtime / f'Lib/site-packages/.ddm-{digest}.ready').is_file())
            runtime_setup.prepare_libraries(self.root, Mock(side_effect=AssertionError('must reuse')), threading.Event())


class APISettingsChecks(unittest.TestCase):
    def test_missing_api_stays_empty_and_legacy_local_default_is_removed(self):
        context = SimpleNamespace(setting=lambda key, default=None: {'model': 'qwen3:0.6b'} if key == 'options' else default)
        self.assertEqual(Options().model, '')
        self.assertEqual(panel_settings(context)['model'], '')

    def test_last_api_profile_survives_local_mode_and_contains_no_key(self):
        settings = {}
        context = SimpleNamespace(setting=lambda key, default=None: settings.get(key, default),
                                  set_setting=lambda key, value: settings.update({key: value}))
        save_options(context, Options(translation_mode='api', endpoint='https://example.invalid/v1', model='my-api-model'))
        save_options(context, Options(translation_mode='local', model='local-alias'))
        restored = panel_settings(context)
        self.assertEqual(restored['model'], 'my-api-model')
        self.assertEqual(restored['endpoint'], 'https://example.invalid/v1')
        self.assertNotIn('key', json.dumps(settings['api_profile']).lower())

    def test_legacy_actual_api_model_is_preserved(self):
        saved = dict(translation_mode='api', endpoint='https://example.invalid/v1', model='qwen3:0.6b')
        context = SimpleNamespace(setting=lambda key, default=None: saved if key == 'options' else default)
        self.assertEqual(panel_settings(context)['model'], 'qwen3:0.6b')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
