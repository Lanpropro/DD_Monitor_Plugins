"""Local HLS recording archives; the host retains FFmpeg/control ownership.

Architecture follows BiliBili ShadowReplay's EVENT -> VOD archive model.
Implementation uses the bundled FFmpeg CLI, libmpv and a loopback archive
reader. Existing EDL archives remain readable through their metadata.
"""
from datetime import datetime
import json
from pathlib import Path

from ddm.recording import RecordingSession, safe_name
from .hls import input_options, read_segments, write_playlist


class ReplayRecordingSession(RecordingSession):
    def __init__(self, tile, settings, recording, folder, video_codec=''):
        # HLS storage follows the source codec. AVC/HEVC use TS; the host's
        # existing AV1 compatibility rule selects MP4, emitted here as fMP4.
        super().__init__(tile, {**settings, 'recording_format': 'ts'}, recording, folder, video_codec)
        self.segment_seconds = 2
        self._archive = None
        self._lists = []
        self._stamp = None
        self._info = None
        self._segments = []
        self._saved = False
        self.review_error = ''

    def parts_directory(self):
        if self._archive is None or self._saved:
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            self._archive = self.folder / 'DDCut_Replay' / f'{safe_name(self.name)}_{stamp}_{self.session_id[:8]}_{self.chunk}'
            self._lists = []
            self._stamp = None
            self._info = None
            self._segments = []
            self._saved = False
        return self._archive

    def segment_options(self, pattern):
        listing = pattern.parent / f'chunk-{self.chunk}.m3u8'
        self._lists.append(listing)
        args = super().segment_options(pattern)
        args = args[:args.index('-f')]
        args += ['-f', 'hls', '-hls_time', str(self.segment_seconds), '-hls_list_size', '0',
                 '-hls_playlist_type', 'event', '-hls_flags', 'temp_file',
                 '-hls_segment_type', 'fmp4' if self.extension == 'mp4' else 'mpegts',
                 '-hls_segment_filename', str(pattern.parent / f'part-{self.chunk}-%05d.{self.extension}')]
        if self.extension == 'mp4':
            args += ['-hls_fmp4_init_filename', str(pattern.parent / f'init-{self.chunk}.mp4')]
        return args + [str(listing)]

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
                chunk = read_segments(listing)
                if chunk and segments:
                    chunk[0]['discontinuity'] = True
                segments.extend(chunk)
            if not segments:
                return
            self._segments = segments
            playlist = self._archive / 'playlist.m3u8'
            write_playlist(playlist, segments, complete=False)
            self._info = {'path': str(playlist), 'title': self.name, 'room': dict(self.room),
                'session_id': self.session_id, 'created_at': self.created_at,
                'source_created_at': self.created_at, 'kind': 'recording', 'index_format': 'hls',
                'duration': sum(s['duration'] for s in segments), 'segments': segments,
                'format': 'fmp4' if self.extension == 'mp4' else 'ts', 'video_codec': self.video_codec}
            self._write_metadata(complete=False)
            self._stamp = stamp
            self.review_error = ''
        except (OSError, ValueError) as error:
            # Raw media survives a failed index write; host disk-space handling
            # is still responsible for stopping and reporting the recording.
            self.review_error = str(error)

    def _write_metadata(self, *, complete):
        path = self._archive / 'recording.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({**self._info, 'recording': self.recording,
            'complete': complete}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    def finished_parts(self):
        self.update_review()
        return [Path(s['path']) for s in self._segments if Path(s['path']).is_file()]

    def close_chunk(self):
        self.update_review()
        self.parts = [Path(s['path']) for s in self._segments if Path(s['path']).is_file()]
        self.active_pattern = None
        if self.process and self.process.stdin:
            self.process.stdin.close()
        self.process = None

    def review_info(self):
        self.update_review()
        if self.review_error:
            raise RuntimeError('录制分段已保留，但回看索引写入失败：' + self.review_error)
        return dict(self._info) if self.recording and self._info else None

    def save_recording(self, parts):
        self.update_review()
        if self.review_error or not self._info:
            raise OSError('录制分段已保留，回看索引保存失败：' + (self.review_error or '尚无完整索引'))
        write_playlist(Path(self._info['path']), self._segments, complete=True)
        self._write_metadata(complete=True)
        self._saved = True
        return Path(self._info['path'])

    def snapshot_info(self, parts):
        selected = set(map(Path, parts))
        segments = []
        # A recent replay can cross a rotation; read each archive's persisted
        # init-map/discontinuity information instead of concatenating fMP4 bytes.
        for folder in dict.fromkeys(part.parent for part in parts):
            metadata = json.loads((folder / 'recording.json').read_text(encoding='utf-8'))
            batch = [s for s in metadata['segments'] if Path(s['path']) in selected]
            if segments and batch:
                batch[0]['discontinuity'] = True
            segments.extend(batch)
        return {'index_format': 'hls', 'segments': segments}

    def export_input(self, parts, playlist):
        segments = self.snapshot_info(parts)['segments']
        write_playlist(playlist, segments, complete=True)
        return input_options(playlist)
