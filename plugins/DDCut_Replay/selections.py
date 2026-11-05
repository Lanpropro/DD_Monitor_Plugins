"""Persistent media ranges; the old active selection remains readable."""
import math


def restore_ranges(entry):
    duration = float(entry['duration'])
    saved = entry.get('ranges')
    if saved is None:
        start, end = entry.get('selection', [0, 0])
        saved = [{'start': start, 'end': end, 'enabled': True}] if end > start else []
    ranges = []
    for item in saved:
        start, end = float(item['start']), float(item['end'])
        if math.isfinite(start) and math.isfinite(end):
            ranges.append({'start': max(0, min(duration, start)), 'end': max(0, min(duration, end)),
                           'enabled': item.get('enabled', True)})
    entry['ranges'] = ranges
    entry['selected_range'] = min(max(0, int(entry.get('selected_range', 0))), len(ranges) - 1)
    return ranges


def enabled_ranges(entry):
    return [dict(item) for item in entry.get('ranges', []) if item.get('enabled', True)]
