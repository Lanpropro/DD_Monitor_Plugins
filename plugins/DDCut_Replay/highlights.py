"""Local chat density analysis; no network or model calls."""
import math
from .chat import ChatReader, align_events

WINDOW = 30
STEP = 5


def media_records(records, segments):
    """Map receive times through HLS program dates, excluding reconnect gaps."""
    return [(row['time'], row.get('text', '')) for row in align_events(records, segments) if row['kind'] == 'danmaku']


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


class HighlightReader(ChatReader):
    """Each review has one reader; workers only read newly appended JSONL rows."""
    def analyze(self, path, segments, duration, percent, keywords, shift=0):
        records = self.read(path)
        events = align_events(records, segments)
        result = detect_peaks([(row['time'] + shift, row.get('text', '')) for row in events if row['kind'] == 'danmaku'], duration, percent, keywords)
        result['events'] = events
        result['video_burned'] = self.video_burned
        return result
