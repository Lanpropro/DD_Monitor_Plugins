"""Local HLS recording archives; the host retains FFmpeg/control ownership.

Architecture follows BiliBili ShadowReplay's EVENT -> VOD archive model.
Implementation uses the bundled FFmpeg CLI, libmpv and a loopback archive
reader. Existing EDL archives remain readable through their metadata.
"""
from datetime import datetime
import time
import json
import hashlib
from pathlib import Path
from PySide6.QtWidgets import QApplication

from ddm.recording import RecordingSession, safe_name
from ddm import images
from ddm.room_artwork import room_platform
from ddm.follow_folders import PLATFORM_NAMES
from .hls import input_options, read_segments, write_playlist
from ddm.danmaku_content import content_runs


class ReplayRecordingSession(RecordingSession):
    supports_replay = False
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
        self._artwork = {}
        self._danmaku_pending = []
        self.danmaku_error = ''
        self._emoticon_loaders = {}

    def record_danmaku(self, message):
        kind = message.get('kind') or 'danmaku'
        if (not self._archive or self._saved or self.stopping or not self.process
                or self.process.poll() is not None or kind not in ('danmaku', 'super_chat', 'super_chat_delete')):
            return
        row = {key: message[key] for key in ('text', 'uname', 'color', 'emoticon', 'emoticons',
               'id', 'ids', 'price', 'face', 'start_time', 'end_time') if key in message}
        row.update(kind=kind, received_at=time.time(), text=str(message.get('text') or message.get('message') or ''))
        if kind == 'super_chat':
            row['duration'] = (max(0, float(message['end_time']) - row['received_at'])
                               if message.get('end_time') else max(0, float(message.get('time', 60))))
        paths = {}
        for _text, url in content_runs(message):
            if url:
                folder = self._archive / 'emoticons'
                folder.mkdir(exist_ok=True)
                target = folder / (hashlib.sha256(url.encode()).hexdigest() + '.png')
                paths[url] = str(target)
                if target.is_file():
                    continue
                cached = images.load_cached_emoticon(url)
                if cached is not None:
                    cached.save(str(target))
                elif str(target) not in self._emoticon_loaders and len(self._emoticon_loaders) < 16:
                    loader = images.AvatarLoader({str(target): url}, QApplication.instance(), subdir='emoticons')
                    self._emoticon_loaders[str(target)] = loader
                    loader.loaded.connect(lambda name, picture: picture.save(name) if not picture.isNull() else None)
                    loader.finished.connect(lambda name=str(target): self._emoticon_loaders.pop(name, None))
                    loader.finished.connect(loader.deleteLater)
                    QApplication.instance().aboutToQuit.connect(loader.wait)
                    loader.start()
        if paths:
            row['image_paths'] = paths
        if row['text'] or kind == 'super_chat_delete' or message.get('emoticon'):
            self._danmaku_pending.append(row)

    def _flush_danmaku(self):
        if not self._danmaku_pending:
            return
        try:
            with (self._archive / 'danmaku.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in self._danmaku_pending))
            self._danmaku_pending.clear()
            self.danmaku_error = ''
        except OSError as error:
            self.danmaku_error = str(error)

    def recording_message(self, event, path=None):
        return {'start': f'{self.name} 已开始录制切片素材',
                'resume': f'{self.name} 直播源已更新，切片素材继续录制',
                'saved': f'已保存切片素材：{path}'}[event]

    def parts_directory(self):
        if self._archive is None or self._saved:
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            platform = room_platform(self.room)
            parts = [PLATFORM_NAMES.get(platform, platform or '直播'), self.name[:24],
                     str(self.room.get('title') or '未命名直播')[:32], self.room_id[:24],
                     stamp, self.session_id[:8], str(self.chunk)]
            self._playlist_name = '_'.join(safe_name(part) for part in parts[:4]) + '_' + stamp + '.m3u8'
            self._archive = self.folder / 'DDCut_Replay' / f'{safe_name(self.name)[:24]}_{stamp}_{self.session_id[:8]}_{self.chunk}'
            self._archive.mkdir(parents=True, exist_ok=True)
            (self._archive / 'danmaku.jsonl').touch()
            self._archive_started = time.time()
            cover = images.load_cached_image(self.room.get('cover_url', ''), 'covers') or images.load_room_cover(self.room_id)
            avatar = images.load_cached_avatar(self.room.get('face', '')) or images.load_room_avatar(self.room_id)
            self._artwork = {}
            for key, picture in [('cover_path', cover), ('avatar_path', avatar)]:
                if picture is not None:
                    target = self._archive / (key + '.png')
                    if picture.save(str(target)):
                        self._artwork[key] = str(target)
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
                 '-hls_playlist_type', 'event', '-hls_flags', 'temp_file+program_date_time',
                 '-hls_segment_type', 'fmp4' if self.extension == 'mp4' else 'mpegts',
                 '-hls_segment_filename', str(pattern.parent / f'part-{self.chunk}-%05d.{self.extension}')]
        if self.extension == 'mp4':
            args += ['-hls_fmp4_init_filename', str(pattern.parent / f'init-{self.chunk}.mp4')]
        return args + [str(listing)]

    def update_review(self):
        if not self._archive or self._saved:
            return
        self._flush_danmaku()
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
            playlist = self._archive / self._playlist_name
            write_playlist(playlist, segments, complete=False)
            self._info = {'path': str(playlist), 'title': self.room.get('title') or self.name, 'room': dict(self.room), **self._artwork,
                'session_id': self.session_id, 'created_at': self._archive_started,
                'source_created_at': self.created_at, 'kind': 'recording', 'index_format': 'hls',
                'duration': sum(s['duration'] for s in segments), 'segments': segments,
                'danmaku_path': str(self._archive / 'danmaku.jsonl'),
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
        if complete:
            self._info['ended_at'] = time.time()
        temporary.write_text(json.dumps({**self._info, 'recording': self.recording,
            'complete': complete, 'danmaku_error': self.danmaku_error}, ensure_ascii=False), encoding='utf-8')
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
        return {**self._info, 'danmaku_error': self.danmaku_error} if self.recording and self._info else None

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
