"""One input, native recording/replay plus a separate source-copy HLS output."""
from pathlib import Path
from ddm.recording import RecordingSession
from .recorder import ReplayRecordingSession


class DualRecordingSession(RecordingSession):
    clip_options = {}

    def __init__(self, tile, settings, recording, folder, video_codec=''):
        super().__init__(tile, settings, recording, folder, video_codec)
        clip_type = type('CompanionHls', (ReplayRecordingSession,), {'clip_options': dict(self.clip_options)})
        destination = Path(self.clip_options.get('directory') or folder).expanduser().resolve()
        self.clip = clip_type(tile, settings, recording, destination, video_codec)
        self.clip.session_id = self.session_id
        self.clip.created_at = self.created_at
        self._completed_clips = []
        self._clip_save_error = ''

    def _sync_clip(self):
        self.clip.process = self.process
        self.clip.recording = self.recording
        self.clip.stopping = self.stopping

    def recording_directories(self):
        return list(dict.fromkeys([self.folder, self.clip.folder])) if self.recording else [self.folder]

    def recording_started(self):
        return True

    def _save_clip(self):
        if self.clip._archive is None or self.clip._saved:
            return
        try:
            self._completed_clips.append(self.clip.save_recording(self.clip.finished_parts()))
            self._clip_save_error = ''
        except OSError as error:
            # The native export must still finish. HLS source files stay intact.
            self._clip_save_error = str(error)

    def segment_options(self, pattern):
        native = super().segment_options(pattern)
        if not self.recording:
            return native
        if self.clip.video_codec != self.video_codec:
            self._save_clip()
            self.clip.video_codec = self.video_codec
            from ddm.recording import segment_extension_for
            self.clip.extension = segment_extension_for(self.clip.settings, self.video_codec)
        self.clip.chunk = self.chunk
        folder = self.clip.parts_directory()
        return native + self.clip.segment_options(folder / ('unused.%05d.' + self.clip.extension))

    def update_review(self):
        self._sync_clip()
        self.clip.update_review()

    def record_danmaku(self, message):
        self._sync_clip()
        self.clip.record_danmaku(message)

    def close_chunk(self):
        self._sync_clip()
        self.clip.close_chunk()
        super().close_chunk()

    def review_info(self):
        self._sync_clip()
        return self.clip.review_info()

    def review_parts(self):
        self._sync_clip()
        return self.clip.finished_parts()

    def snapshot_info(self, parts):
        return self.clip.snapshot_info(parts)

    def save_recording(self, parts):
        self._sync_clip()
        self._save_clip()
        return None

    def additional_recordings(self):
        paths, self._completed_clips = self._completed_clips, []
        return paths, self._clip_save_error


def configured_backends(options):
    options = dict(options)
    return (type('ClipCapture', (ReplayRecordingSession,),
                {'clip_options': options, 'recording_directory': options.get('directory', '')}),
            type('DualCapture', (DualRecordingSession,), {'clip_options': options}))
