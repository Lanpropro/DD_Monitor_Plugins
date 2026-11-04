"""Plugin recording backend: durable segments and an immediately readable index.

The host still owns capture controls, source renewal, FFmpeg and shutdown. No Qt
or plugin-context reference is retained by a session, so unloading the UI does
not interrupt a recording already in progress.
"""
import csv
from datetime import datetime
import json
import math
from pathlib import Path

from ddm.recording import RecordingSession, safe_name


class ReplayRecordingSession(RecordingSession):
    def __init__(self, tile, settings, recording, folder, video_codec=''):
        super().__init__(tile, settings, recording, folder, video_codec)
        self.segment_seconds = 2
        self._archive = None
        self._lists = []
        self._stamp = None
        self._info = None
        self._saved = False
        self.review_error = ''

    def parts_directory(self):
        if self._archive is None or self._saved:
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            self._archive = self.folder / 'DDCut_Replay' / f'{safe_name(self.name)}_{stamp}_{self.session_id[:8]}_{self.chunk}'
            self._lists = []
            self._stamp = None
            self._info = None
            self._saved = False
        return self._archive

    def segment_options(self, pattern):
        listing = pattern.with_suffix('.csv')
        self._lists.append(listing)
        args = super().segment_options(pattern)
        # CSV rows are emitted only after a segment is closed. The final row is
        # also available after FFmpeg exits, including a short last segment.
        return args[:-1] + ['-segment_list', str(listing), '-segment_list_type', 'csv',
                           '-segment_list_size', '0', '-flush_packets', '1', args[-1]]

    def update_review(self):
        if not self._archive or self._saved:
            return
        try:
            stamp = (self.recording, tuple((p.stat().st_size, p.stat().st_mtime_ns)
                     if p.exists() else (0, 0) for p in self._lists))
            if stamp == self._stamp:
                return
            segments = []
            for listing in self._lists:
                if not listing.exists():
                    continue
                with listing.open(encoding='utf-8', newline='') as handle:
                    for row in csv.reader(handle):
                        if len(row) != 3:
                            continue
                        try:
                            duration = float(row[2]) - float(row[1])
                        except ValueError:
                            continue
                        part = listing.parent / Path(row[0]).name
                        if part.is_file() and math.isfinite(duration) and duration > 0:
                            segments.append((part, duration))
            if not segments:
                return
            # mpv's built-in EDL presents separate local files as one virtual
            # timeline. This also works for AV1/MP4/MKV, without transcoding.
            lines = ['# mpv EDL v0', '!no_chapters']
            for part, duration in segments:
                name = part.name
                lines.append(f'%{len(name.encode("utf-8"))}%{name},0,{duration:.6f}')
            playlist = self._archive / 'review.edl'
            text = '\n'.join(lines) + '\n'
            self._atomic_text(playlist, text)
            self._info = {'path': str(playlist), 'title': self.name, 'room': dict(self.room),
                'session_id': self.session_id, 'created_at': self.created_at,
                'source_created_at': self.created_at, 'kind': 'recording',
                'duration': sum(duration for _, duration in segments),
                'segments': [{'path': str(part.resolve()), 'duration': duration} for part, duration in segments],
                'format': self.extension, 'video_codec': self.video_codec}
            self._atomic_text(self._archive / 'recording.json', json.dumps(
                {**self._info, 'recording': self.recording}, ensure_ascii=False))
            self._stamp = stamp
            self.review_error = ''
        except OSError as error:
            # Keep every media file even if an index cannot be written (e.g. disk
            # full). Host disk-space handling remains responsible for stopping.
            self.review_error = str(error)

    @staticmethod
    def _atomic_text(path, text):
        partial = path.with_suffix(path.suffix + '.tmp')
        partial.write_text(text, encoding='utf-8', newline='\n')
        partial.replace(path)

    def review_info(self):
        self.update_review()
        if self.review_error:
            raise RuntimeError('录制分段已保留，但回看索引写入失败：' + self.review_error)
        return dict(self._info) if self.recording and self._info else None

    def save_recording(self, parts):
        self.update_review()
        if self.review_error or not self._info:
            raise OSError('录制分段已保留，回看索引保存失败：' + (self.review_error or '尚无完整索引'))
        self._atomic_text(self._archive / 'recording.json', json.dumps(
            {**self._info, 'recording': True, 'complete': True}, ensure_ascii=False))
        self._saved = True
        return Path(self._info['path'])
