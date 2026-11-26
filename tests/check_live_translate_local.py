"""Explicit real-model translation smoke test, without user accounts or live audio."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--server', type=Path, required=True)
parser.add_argument('--model', type=Path, required=True)
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/live_translate'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QCoreApplication, QProcess
from PySide6.QtTest import QTest
from local_translation import LocalTranslation
from options import Options
from text_policy import translation_matches
from translation import translate


def main():
    app = QCoreApplication.instance() or QCoreApplication([])
    options = Options(local_server=str(args.server.resolve()), local_model=str(args.model.resolve()),
                      translation_mode='local', timeout=3)
    service = LocalTranslation()
    endpoints, errors = [], []
    service.ready.connect(endpoints.append)
    service.failed.connect(errors.append)
    try:
        service.start(options)
        deadline = time.monotonic() + 65
        while not endpoints and not errors and time.monotonic() < deadline:
            QTest.qWait(25)
        assert endpoints and not errors, 'Local translation runtime did not become ready'
        options = replace(options, endpoint=endpoints[0], model='live-translate')
        cases = [('こんにちは。', ()), ('配信に来てくれてありがとう。', ()),
                 ('その辺全然詳しくない。', ()),
                 ('その辺全然詳しくない。', ('今日はNintendo Switchの話をしています。',))]
        for source, context in cases:
            translated, warning = translate(source, options, service.api_key, context=context)
            # The small model can return the source language. A reported fallback
            # is valid; silently displaying that response as a translation is not.
            if translated:
                assert not warning and translation_matches(translated, source, options.target_language)
            else:
                assert warning, 'Missing translation must have an explicit fallback reason'
            if source == 'こんにちは。':
                assert translated, f'Local model did not complete the greeting smoke: {warning}'
            assert '<think>' not in translated, 'Local translation leaked reasoning'
            print(json.dumps({'source': source, 'context': context, 'translation': translated,
                              'warning': warning}, ensure_ascii=False), flush=True)
        print('PASS: actual local model completed translation or explicit fallback; live accuracy and latency were not measured', flush=True)
    finally:
        service.stop()
        assert service.process.state() == QProcess.NotRunning, 'Local model process was not released'
        app.processEvents()


if __name__ == '__main__':
    main()
