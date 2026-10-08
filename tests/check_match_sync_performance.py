"""Run the host's native media regressions against the release plugin source."""
import argparse
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    checks = ('selfcheck_match_sync_multistream.py', 'selfcheck_match_sync_comparison.py')
    parser.add_argument('--check', choices=checks)
    args = parser.parse_args()
    host = args.host.resolve()
    if not args.check:
        for name in checks:
            subprocess.run([sys.executable, __file__, '--host', str(host), '--check', name], check=True)
        return
    sys.path.insert(0, str(host))
    os.chdir(host)
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['DDM_NO_SAVE'] = '1'
    import plugins_user
    package = types.ModuleType('plugins_user._match_sync')
    package.__path__ = [str(ROOT / 'plugins/match_sync')]
    sys.modules[package.__name__] = package
    plugins_user._match_sync = package
    runpy.run_path(str(host / 'dev' / args.check), run_name='__main__')


if __name__ == '__main__':
    main()
