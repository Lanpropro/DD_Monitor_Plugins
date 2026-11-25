"""Read only numeric, allowlisted fields from the bounded diagnostic logs."""
from datetime import datetime
import json
from statistics import median
import time


def epoch(row):
    return datetime.fromisoformat(row['time']).timestamp()


def records(path, tail=False):
    try:
        with path.open('rb') as stream:
            if tail and stream.seek(0, 2) > 256 * 1024:
                stream.seek(-256 * 1024, 2)
                stream.readline()
            else:
                stream.seek(0)
            for line in stream:
                try:
                    yield json.loads(line)
                except (UnicodeError, ValueError):
                    continue
    except FileNotFoundError:
        pass


def latest(logs, pid=None):
    for suffix in ('', '.1'):
        found = [r for r in records(logs / ('match-playback.jsonl' + suffix), True)
                 if r.get('event') == 'playback' and r.get('picture') and r.get('surface', {}).get('main_presenter')
                 and (pid is None or r['pid'] == pid)]
        if found and -1 < time.time() - epoch(found[-1]) < 5:
            return found[-1]
    raise ValueError('No recent live diagnostic playback row')


def scene(row):
    s, pic = row['surface'], row['picture']
    return dict(pid=row['pid'], time=row['time'], backend=s['main_presenter']['backend'],
                canvas=s['canvas_size'], inputs=len(row['source_pipelines']),
                audio=row['audio_reads'].get('tracks_min', len(row['audio_reads']['sources'])),
                enhanced=sum(bool(c['enhanced']) for c in s['floating']),
                crops=len(s['floating']), fps=pic['source_fps'], main_slot=s['main_slot'],
                platform=s['source_kind'], screen_hz=s.get('screen_hz'), exposed=s['exposed'],
                replay=s['replay'], waiting=pic['waiting'])


def read_window(logs, stem, start, end, predicate):
    selected = {}
    for suffix in ('.2', '.1', ''):
        for r in records(logs / (stem + '.jsonl' + suffix)):
            if predicate(r) and start <= epoch(r) <= end:
                selected[r['time']] = r
    return [selected[t] for t in sorted(selected)]


def summarize(logs, start, end, initial, window):
    pid = initial['pid']
    playback = read_window(logs, 'match-playback', start, end,
                           lambda r: r.get('pid') == pid and r.get('event') == 'playback')
    quick = read_window(logs, 'danmaku-motion', start, end,
                        lambda r: r.get('stage') in ('quick-cadence', 'quick-window-state')
                        and r.get('window_id') == window
                        and r.get('session', '').split('-')[0] == str(pid)) if window else []
    keys = ('backend', 'canvas', 'inputs', 'audio', 'enhanced', 'crops', 'fps', 'main_slot', 'platform', 'screen_hz')
    valid = []
    waiting_buckets = 0
    for r in playback:
        if not r.get('picture') or not r.get('surface', {}).get('main_presenter'):
            continue
        s = scene(r)
        if s['exposed'] and not s['replay'] and all(s[k] == initial[k] for k in keys):
            if s['waiting']:
                waiting_buckets += 1
            elif epoch(r) >= start + 1:
                valid.append(r)
    # Each Quick bucket is counted once, alongside a playback bucket from the same scene.
    valid_times = {int(epoch(r)) for r in valid}
    quick = [r for r in quick if r.get('comments', 0) and int(epoch(r)) in valid_times]
    swaps = [r for r in quick if r['stage'] == 'quick-cadence']
    def mid(values):
        return round(median(values), 3) if values else None
    rates = []
    for a, b in zip(valid, valid[1:]):
        delta = epoch(b) - epoch(a)
        x, y = (r['surface']['main_presenter'] for r in (a, b))
        if (0 < delta < 1.6 and x.get('pid') == y.get('pid') and
                'presented' in x and 'presented' in y and y['presented'] >= x['presented']):
            rates.append((y['presented'] - x['presented']) / delta)
    displays = [source['display']['stages'] for r in valid for source in r['source_pipelines']
                if source['slot'] == initial['main_slot']]
    stages = {stage: max((display[stage]['max_ms'] for display in displays if stage in display), default=None)
              for stage in ('display_video_decode', 'display_bgra_convert', 'display_image_copy')}
    stages.update({stage: max((r['gui_pipeline']['stages'][stage]['max_ms'] for r in valid
                               if stage in r['gui_pipeline']['stages']), default=None)
                   for stage in ('picture_timer_late', 'picture_poll_gap')})
    gaps = {}
    for r in valid:
        presenter = r['surface']['main_presenter']
        for gap in presenter.get('publish_gaps', []):
            if gap['ended_at'] - gap['gap_ms'] / 1000 < start or gap['ended_at'] > end:
                continue
            gaps[presenter['pid'], gap['sequence']] = {key: gap[key] for key in
                ('sequence', 'ended_at', 'gap_ms', 'poll_interval_max_ms', 'lookup_max_ms',
                 'target_age_max_ms', 'publish_ms', 'duplicates', 'missing', 'wait_max_ms') if key in gap}
    return dict(scene=initial, elapsed_s=round(end-start, 2), playback_buckets=len(playback),
                waiting_buckets=waiting_buckets,
                first_playback_offset_s=round(epoch(playback[0])-start, 2) if playback else None,
                last_playback_offset_s=round(epoch(playback[-1])-start, 2) if playback else None,
                matched_buckets=len(valid), quick_window_id=window, quick_buckets=len(quick),
                decoded_gap_p95_ms_median=mid([r['picture']['decoded_cadence']['gap_p95_ms'] for r in valid
                                               if r['picture'].get('decoded_cadence', {}).get('gap_p95_ms') is not None]),
                decoded_per_bucket=mid([r['picture']['decoded_cadence']['distinct'] for r in valid
                                        if 'decoded_cadence' in r['picture']]),
                raster_paints_per_bucket=mid([r['painted_cadence']['distinct'] for r in valid])
                if initial['backend'] == 'raster' else None, d3d_presented_per_s=mid(rates),
                quick_swaps_per_bucket=mid([r['swaps'] for r in swaps]),
                quick_gap_p95_ms_median=mid([r['swap_gap_ms']['p95'] for r in swaps if r['gap_count']]),
                quick_gap_count=sum(r['gap_count'] for r in swaps) if swaps else None,
                quick_gaps_over_50_ms=sum(r['over_50_ms'] for r in swaps) if swaps else None,
                quick_gaps_over_100_ms=sum(r['over_100_ms'] for r in swaps) if swaps else None,
                comments_median=mid([r['comments'] for r in quick]),
                stage_max_ms=stages,
                observed_publish_gaps=list(gaps.values()) if any('publish_gaps' in r['surface']['main_presenter']
                                                                 for r in valid) else None)
