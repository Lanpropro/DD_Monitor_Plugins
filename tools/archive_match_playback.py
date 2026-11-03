"""Preserve a running client's diagnostic log across rotation; never probe or stop it."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time


def same_process(process, created):
    return process.is_running() and abs(process.create_time() - created) < .01


class Archive:
    def __init__(self, pid, start, end):
        self.pid, self.start, self.end = pid, start, end
        self.seen = set()
        self.files = {}
        self.rows = 0
        self.last_time = None

    def collect(self, paths, output):
        for path in paths:
            try:
                stat = path.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
                if self.files.get(path) == signature:
                    continue
                data = path.read_bytes()
            except FileNotFoundError:
                continue  # Rotation may rename a file between stat and read.
            self.files[path] = signature
            for raw in data.splitlines(keepends=True):
                if not raw.endswith(b'\n'):
                    continue  # Revisit an incomplete record when the file grows.
                key = hashlib.sha256(raw).digest()
                if key in self.seen:
                    continue
                row = json.loads(raw)
                if row.get('pid') != self.pid:
                    continue
                stamp = datetime.fromisoformat(row['time']).timestamp()
                if not self.start <= stamp <= self.end:
                    continue
                output.write(raw)
                self.seen.add(key)
                self.rows += 1
                self.last_time = max(self.last_time or row['time'], row['time'])
        output.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=Path)
    args = parser.parse_args()
    import psutil
    owner = json.loads(args.owner.read_text(encoding='utf-8'))
    session = Path(owner['output'])
    metadata = json.loads((session / 'session.json').read_text(encoding='utf-8'))
    target = psutil.Process(owner['target_pid'])
    monitor = psutil.Process(owner['monitor_pid'])
    if not (same_process(target, owner['target_create_time'])
            and same_process(monitor, owner['monitor_create_time'])):
        raise RuntimeError('Session process identity does not match ownership record')
    start = datetime.fromisoformat(metadata['started_utc']).timestamp()
    deadline = start + owner['seconds']
    archive = Archive(owner['target_pid'], start, deadline)
    args.output.mkdir(exist_ok=False)
    (args.output / 'owner.json').write_text(json.dumps(owner, indent=2), encoding='utf-8')
    scans, max_scan_ms = 0, 0.
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    reason = 'deadline'
    with (args.output / 'playback.jsonl').open('wb') as output:
        while True:
            before = time.perf_counter()
            paths = ([args.seed] if args.seed else []) + [
                args.log.with_name(args.log.name + '.2'),
                args.log.with_name(args.log.name + '.1'), args.log]
            archive.collect(paths, output)
            scans += 1
            max_scan_ms = max(max_scan_ms, (time.perf_counter() - before) * 1000)
            state = dict(pid=archive.pid, rows=archive.rows, last_time=archive.last_time,
                scans=scans, max_scan_ms=round(max_scan_ms, 3),
                cpu_s=round(time.process_time() - cpu_start, 4),
                wall_s=round(time.perf_counter() - wall_start, 3),
                updated_utc=datetime.now(timezone.utc).isoformat())
            (args.output / 'live.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
            if (session / 'finished.json').exists():
                reason = 'monitor_finished'
                break
            if time.time() >= deadline:
                break
            try:
                if not same_process(target, owner['target_create_time']):
                    reason = 'target_changed'
                    break
                if not same_process(monitor, owner['monitor_create_time']):
                    reason = 'monitor_changed'
                    break
            except psutil.NoSuchProcess:
                reason = 'session_process_exited'
                break
            time.sleep(2)
    state.update(reason=reason, recording=False)
    (args.output / 'finished.json').write_text(json.dumps(state, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
