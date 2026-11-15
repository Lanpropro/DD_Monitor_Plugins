"""Interaction responsibility imports and legacy Qt class identity; offline only."""
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
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
SOURCE = Path(__file__).resolve().parents[1] / 'plugins/domestic_live'
OWNERS = {
    'interaction_widgets': ('DanmakuEdit', 'SendDetailsDialog', 'style_button'),
    'interaction_gifts': ('GiftButton', 'GiftCard', 'GiftConfirmDialog', 'GiftPopup'),
    'interaction_requests': ('Request',),
    'interaction_composers': ('DouyuComposer', 'HuyaComposer', 'DouyinComposer'),
    'interaction_controls': ('DouyuInteraction',),
}


class InteractionModuleTests(unittest.TestCase):
    def test_responsibilities_import_without_legacy_entry_or_platform_protocols(self):
        for name in OWNERS:
            with self.subTest(module=name):
                tree = ast.parse((SOURCE / (name + '.py')).read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn(node.module, ('plugin', 'douyu_composer'))
                code = '''import importlib, sys, types
sys.path.insert(0,sys.argv[1])
package=types.ModuleType('isolated_interaction')
package.__path__=[sys.argv[2]]
sys.modules[package.__name__]=package
importlib.import_module(package.__name__+'.'+sys.argv[3])
loaded={name.rsplit('.',1)[-1] for name in sys.modules if name.startswith(package.__name__+'.')}
assert not loaded.intersection({'plugin','douyu_composer','douyu_interaction','huya_interaction','douyin_interaction'})
'''
                result = subprocess.run([sys.executable, '-c', code, str(args.host.resolve()), str(SOURCE), name],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_exports_preserve_class_signal_and_helper_identity(self):
        package = ModuleType('legacy_interaction')
        package.__path__ = [str(SOURCE)]
        sys.modules[package.__name__] = package
        old = importlib.import_module(package.__name__ + '.douyu_composer')
        for owner, names in OWNERS.items():
            current = importlib.import_module(package.__name__ + '.' + owner)
            for name in names:
                self.assertIs(getattr(old, name), getattr(current, name))
        request = importlib.import_module(package.__name__ + '.interaction_requests').Request
        self.assertIs(old.Request.result, request.result)
        self.assertIs(old.Request.phase, request.phase)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
