"""Shared public HTTP protocol boundaries and response lifecycle; offline fixtures."""
import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
SOURCE = Path(__file__).resolve().parents[1] / 'plugins/global_live'
package = ModuleType('public_protocol_check')
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package


class PublicHTTPTests(unittest.TestCase):
    def http(self):
        return importlib.import_module(package.__name__ + '.public_http')

    def test_http_protocol_imports_without_host_media_or_qt(self):
        code = '''import builtins, importlib.util, sys
original = builtins.__import__
def isolated(name, *args, **kwargs):
    if name.split('.')[0] in {'ddm', 'streamlink', 'PySide6'}:
        raise AssertionError('HTTP helpers must not load host/media/UI: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = isolated
spec = importlib.util.spec_from_file_location('isolated_http', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.image_url('https://i.ytimg.com/fixture', ('ytimg.com',))
'''
        result = subprocess.run([sys.executable, '-c', code, str(SOURCE / 'public_http.py')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_public_live_helpers_are_the_same_functions(self):
        old = importlib.import_module(package.__name__ + '.public_live')
        current = self.http()
        for name in ('public_request', 'official_url', 'image_url', 'thumbnail'):
            self.assertIs(getattr(old, name), getattr(current, name))

    def test_retry_closes_failed_response_and_keeps_success_for_caller(self):
        http = self.http()
        rejected, accepted = Mock(), Mock()
        rejected.raise_for_status.side_effect = http.requests.HTTPError('fixture')
        with patch.object(http.requests, 'get', side_effect=[rejected, accepted]) as request:
            self.assertIs(http.public_request('get', 'https://fixture.invalid/', timeout=(4, 8)), accepted)
        rejected.close.assert_called_once()
        accepted.close.assert_not_called()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs, {'timeout': (4, 8)})


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
