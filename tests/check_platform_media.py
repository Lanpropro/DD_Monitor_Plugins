"""Media/query dependencies and old parser exports; no live media or account reads."""
import argparse
import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'plugins/domestic_live'


class MediaTests(unittest.TestCase):
    def test_media_and_lookup_import_without_entry_ui_or_other_platforms(self):
        for name in ('douyu_playback', 'huya_playback', 'douyin_playback', 'douyin_rooms', 'douyin_room_data'):
            with self.subTest(module=name):
                tree = ast.parse((SOURCE / (name + '.py')).read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn(node.module, ('plugin', 'platform_logo', 'douyu_composer'))
                        self.assertFalse((node.module or '').endswith('_platform'))
                        self.assertFalse((node.module or '').startswith('PySide6.QtWidgets'))
                code = '''import importlib, sys, types
sys.path.insert(0, sys.argv[1])
package=types.ModuleType('isolated_media')
package.__path__=[sys.argv[2]]
sys.modules[package.__name__]=package
importlib.import_module(package.__name__+'.'+sys.argv[3])
loaded={name.rsplit('.',1)[-1] for name in sys.modules if name.startswith(package.__name__+'.')}
assert not loaded.intersection({'plugin','platform_logo','douyu_composer'})
kind=sys.argv[3].split('_',1)[0]
# Shared diagnostic exception types do not load another platform adapter or lifecycle.
assert not any(name.startswith(other+'_') and not name.endswith('_errors')
    for name in loaded for other in ('douyu','huya','douyin') if other != kind)
'''
                result = subprocess.run([sys.executable, '-c', code, str(args.host.resolve()), str(SOURCE), name],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_douyin_old_static_helpers_keep_parser_function_identity(self):
        package = ModuleType('media_compatibility')
        package.__path__ = [str(SOURCE)]
        sys.modules[package.__name__] = package
        adapter = importlib.import_module(package.__name__ + '.douyin_platform').DouyinPlatform
        parsers = importlib.import_module(package.__name__ + '.douyin_room_data')
        for name in ('share_identity', 'sdk_stream_data', 'share_info', 'page_info'):
            self.assertIs(getattr(adapter, '_' + name), getattr(parsers, name))
        self.assertEqual(adapter._sdk_stream_data('', 'inline-data'), 'inline-data')
        self.assertEqual(adapter._sdk_stream_data('', '$abcdef'), '')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
