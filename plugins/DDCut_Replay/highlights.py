"""Local chat density analysis; no network or model calls."""
from bisect import bisect_right
import json
import math
from pathlib import Path

WINDOW = 30
STEP = 5


def media_records(records, segments):
    """Map receive times through HLS program dates, excluding reconnect gaps."""
    starts, intervals = [], []
    elapsed = 0
    for segment in segments:
        if segment.get('wall_start') is not None:
            starts.append(segment['wall_start'])
            intervals.append((elapsed, segment['duration']))
        elapsed += segment['duration']
    result = []
    for record in records:
        stamp = record['received_at']
        index = bisect_right(starts, stamp) - 1
        if index >= 0:
            offset, duration = intervals[index]
            delta = stamp - starts[index]
            if 0 <= delta < duration:
                result.append((offset + delta, record['text']))
    return result


def detect_peaks(records, duration, percent=80, keywords=()):
    """30-second windows every 5 seconds; merge expanded above-threshold runs."""
    words = [word.casefold() for word in keywords if word]
    matched = [(stamp, text) for stamp, text in records if 0 <= stamp < duration
               and (not words or any(word in text.casefold() for word in words))]
    result = {'total': len(records), 'matched': len(matched), 'points': [], 'peaks': [], 'threshold': 0}
    if duration < WINDOW or not matched:
        return result
    buckets = [0] * (math.ceil(duration / STEP) + 1)
    for stamp, _text in matched:
        buckets[int(stamp // STEP)] += 1
    width = WINDOW // STEP
    count = sum(buckets[:width])
    points = []
    for index in range(int((duration - WINDOW) // STEP) + 1):
        if index:
            count += buckets[index + width - 1] - buckets[index - 1]
        points.append({'time': index * STEP + WINDOW / 2, 'count': count, 'level': 'normal'})
    maximum = max(point['count'] for point in points)
    threshold = maximum * max(0, min(100, percent)) / 100
    mean = sum(point['count'] for point in points) / len(points)
    variance = sum((point['count'] - mean) ** 2 for point in points) / len(points)
    baseline = min(mean + .5 * math.sqrt(variance), threshold * .8)
    runs = []
    index = 0
    while index < len(points):
        if not points[index]['count'] or points[index]['count'] < threshold:
            index += 1
            continue
        start = end = index
        while end + 1 < len(points) and points[end + 1]['count'] > 0 and points[end + 1]['count'] >= threshold:
            end += 1
        index = end + 1
        while start > 0 and points[start - 1]['count'] > baseline:
            start -= 1
        while end + 1 < len(points) and points[end + 1]['count'] > baseline:
            end += 1
        if runs and start <= runs[-1][1] + 1:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    for start, end in runs:
        for point in points[start:end + 1]:
            point['level'] = 'core' if point['count'] >= threshold else 'extension'
    result.update(points=points, threshold=threshold, peaks=[{
        'start': max(0, points[start]['time'] - STEP / 2),
        'end': min(duration, points[end]['time'] + STEP / 2),
        'count': max(point['count'] for point in points[start:end + 1]),
    } for start, end in runs])
    return result


class HighlightReader:
    """Each review has one reader; workers only read newly appended JSONL rows."""
    def __init__(self):
        self.offset = 0
        self.records = []

    def analyze(self, path, segments, duration, percent, keywords):
        path = Path(path)
        if path.stat().st_size < self.offset:
            self.offset = 0
            self.records.clear()
        with path.open('rb') as handle:
            handle.seek(self.offset)
            while line := handle.readline():
                if not line.endswith(b'\n'):
                    break  # A live writer may not have finished this row.
                self.offset = handle.tell()
                record = json.loads(line)
                self.records.append(record)
        return detect_peaks(media_records(self.records, segments), duration, percent, keywords)
