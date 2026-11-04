"""Local archive playlists shared by recording, playback and frozen clip inputs."""
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re


def read_segments(playlist):
    """Read the subset emitted by our FFmpeg HLS muxer, never remote playlists."""
    playlist = Path(playlist)
    duration = None
    init = None
    discontinuity = False
    wall_start = None
    result = []
    for line in playlist.read_text(encoding='utf-8').splitlines():
        if line.startswith('#EXTINF:'):
            duration = float(line.split(':', 1)[1].split(',', 1)[0])
        elif line.startswith('#EXT-X-MAP:'):
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                init = str((playlist.parent / match[1]).resolve())
        elif line == '#EXT-X-DISCONTINUITY':
            discontinuity = True
        elif line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            wall_start = datetime.fromisoformat(line.split(':', 1)[1]).timestamp()
        elif line and not line.startswith('#') and duration is not None:
            part = playlist.parent / line
            if part.is_file() and math.isfinite(duration) and duration > 0 and (not init or Path(init).is_file()):
                result.append({'path': str(part.resolve()), 'duration': duration,
                               'init': init, 'discontinuity': discontinuity,
                               **({'wall_start': wall_start} if wall_start is not None else {})})
            if wall_start is not None:
                wall_start += duration
            duration = None
            discontinuity = False
    return result


def write_playlist(path, segments, *, complete):
    """EVENT retains all published segments; only a completed archive is VOD."""
    path = Path(path)
    version = 7 if any(s.get('init') for s in segments) else 3
    target = max(1, math.ceil(max((s['duration'] for s in segments), default=1)))
    lines = ['#EXTM3U', f'#EXT-X-VERSION:{version}', f'#EXT-X-TARGETDURATION:{target}',
             '#EXT-X-MEDIA-SEQUENCE:0', '#EXT-X-PLAYLIST-TYPE:' + ('VOD' if complete else 'EVENT')]
    previous_init = None
    for segment in segments:
        if segment.get('discontinuity'):
            lines.append('#EXT-X-DISCONTINUITY')
        init = segment.get('init')
        if init and init != previous_init:
            uri = os.path.relpath(init, path.parent).replace('\\', '/')
            lines.append(f'#EXT-X-MAP:URI="{uri}"')
        previous_init = init
        uri = os.path.relpath(segment['path'], path.parent).replace('\\', '/')
        if segment.get('wall_start') is not None:
            stamp = datetime.fromtimestamp(segment['wall_start'], timezone.utc).isoformat(timespec='milliseconds')
            lines.append('#EXT-X-PROGRAM-DATE-TIME:' + stamp)
        lines += [f"#EXTINF:{segment['duration']:.6f},", uri]
    if complete:
        lines.append('#EXT-X-ENDLIST')
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    temporary.replace(path)


def input_options(path):
    return ['-f', 'hls', '-allowed_extensions', 'ALL', '-i', str(path)]


def preview_options(path):
    if Path(path).suffix.lower() != '.m3u8':
        return {}
    return {'demuxer_lavf_o': 'live_start_index=0,allowed_extensions=ALL', 'force_seekable': True}
