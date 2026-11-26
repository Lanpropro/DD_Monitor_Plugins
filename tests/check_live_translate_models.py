"""Bounded local model discovery without Qt, downloads or inference."""
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest

package = types.ModuleType('catalog_check')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'plugins/live_translate')]
sys.modules[package.__name__] = package
catalog = importlib.import_module('catalog_check.model_catalog')
options = importlib.import_module('catalog_check.options')


class LocalModelsTests(unittest.TestCase):
    def test_translation_candidates_use_selected_folder_and_bundle_without_recursive_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local, bundled = root / 'selected', root / 'bundled'
            local.mkdir(); bundled.mkdir()
            first, second = local / 'first.gguf', local / 'second.gguf'
            for path in (first, second, bundled / 'bundled.gguf', root / 'outside.gguf'):
                path.write_bytes(b'GGUFfixture')
            (local / 'invalid.gguf').write_bytes(b'not a model')
            (local / 'nested').mkdir()
            (local / 'nested/deep.gguf').write_bytes(b'GGUFfixture')
            found = catalog.discover_models(str(first), bundled=bundled)
            self.assertEqual(set(found), {str(first), str(second), str(bundled / 'bundled.gguf')})

    def test_asr_discovery_requires_the_complete_supported_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            complete, incomplete = root / 'qwen', root / 'incomplete'
            incomplete.mkdir()
            for name in (*options.QWEN_FILES.values(), 'silero_vad.onnx',
                         *(f'tokenizer/{name}' for name in options.TOKENIZER_FILES)):
                path = complete / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            (incomplete / 'weights.safetensors').write_bytes(b'unsupported')
            found = catalog.discover_models(str(complete), directory=True, bundled=root / 'absent')
            self.assertEqual(found, [str(complete)])

    def test_empty_and_missing_configuration_never_searches_other_user_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'other.gguf').write_bytes(b'GGUFfixture')
            self.assertEqual(catalog.discover_models('', bundled=root / 'absent'), [])
            self.assertFalse(catalog.model_ready(root / 'absent.gguf'))


if __name__ == '__main__':
    unittest.main()
