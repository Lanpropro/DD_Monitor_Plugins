"""File/identity regression only; not performance evidence or generated media."""
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from archive_match_playback import Archive, main, same_process


def record(second, pid=123):
    return json.dumps(dict(pid=pid, time=f'2026-11-03T11:00:{second:02d}+08:00',
                           event='playback')).encode() + b'\n'


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'playback.jsonl'
        self.archive = Archive(123, datetime.fromisoformat('2026-11-03T11:00:01+08:00').timestamp(),
                               datetime.fromisoformat('2026-11-03T11:00:10+08:00').timestamp())
        self.output = BytesIO()

    def test_session_and_time_filter(self):
        self.log.write_bytes(record(0) + record(1) + record(2, 456) + record(10) + record(11))
        self.archive.collect([self.log], self.output)
        self.assertEqual(self.output.getvalue(), record(1) + record(10))

    def test_rotation_seed_and_repeated_reads_do_not_duplicate(self):
        seed = self.log.with_name('seed.jsonl')
        seed.write_bytes(record(1))
        self.log.write_bytes(record(1) + record(2))
        self.archive.collect([seed, self.log], self.output)
        backup = self.log.with_suffix('.jsonl.1')
        self.log.rename(backup)
        self.log.write_bytes(record(3))
        for _ in range(2):
            self.archive.collect([seed, backup, self.log], self.output)
        self.assertEqual(self.output.getvalue(), record(1) + record(2) + record(3))
        self.assertEqual(self.archive.rows, 3)

    def test_incomplete_record_is_collected_after_completion(self):
        self.log.write_bytes(record(1) + record(2)[:-5])
        self.archive.collect([self.log], self.output)
        self.assertEqual(self.archive.rows, 1)
        self.log.write_bytes(record(1) + record(2))
        self.archive.collect([self.log], self.output)
        self.assertEqual(self.output.getvalue(), record(1) + record(2))

    def test_missing_rotated_file_is_revisited(self):
        self.archive.collect([self.log], self.output)
        self.log.write_bytes(record(1))
        self.archive.collect([self.log], self.output)
        self.assertEqual(self.archive.rows, 1)

    def test_pid_reuse_and_stopped_process_are_rejected(self):
        process = SimpleNamespace(is_running=lambda: True, create_time=lambda: 100.)
        self.assertTrue(same_process(process, 100.))
        self.assertFalse(same_process(process, 90.))
        process.is_running = lambda: False
        self.assertFalse(same_process(process, 100.))

    def test_finished_monitor_gets_final_read_without_signalling_client(self):
        session = self.log.parent / 'session'
        session.mkdir()
        (session / 'session.json').write_text(json.dumps(dict(
            started_utc='2026-11-03T11:00:01+08:00')), encoding='utf-8')
        (session / 'finished.json').write_text('{}', encoding='utf-8')
        owner = self.log.parent / 'owner.json'
        owner.write_text(json.dumps(dict(output=str(session), target_pid=123,
            monitor_pid=456, target_create_time=100., monitor_create_time=100.,
            seconds=9)), encoding='utf-8')
        self.log.write_bytes(record(1) + record(2, 456))
        destination = self.log.parent / 'archive'
        process = SimpleNamespace(is_running=lambda: True, create_time=lambda: 100.)
        # No signal/terminate methods: the collector may only read identities.
        with patch.dict(sys.modules, psutil=SimpleNamespace(Process=lambda pid: process)), patch.object(
                sys, 'argv', ['archive', '--owner', str(owner), '--log', str(self.log),
                              '--output', str(destination)]):
            main()
        self.assertEqual((destination / 'playback.jsonl').read_bytes(), record(1))
        finished = json.loads((destination / 'finished.json').read_text(encoding='utf-8'))
        self.assertEqual(finished['reason'], 'monitor_finished')
        self.assertFalse(finished['recording'])


if __name__ == '__main__':
    unittest.main()
