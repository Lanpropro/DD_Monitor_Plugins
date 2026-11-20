"""Build a historical ranking using records known at the selected race time."""
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from math import isfinite


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def duration(value):
    return value if isinstance(value, (float, int)) and isfinite(value) and value > 0 else None


def gap_text(value):
    if isinstance(value, (float, int)) and isfinite(value):
        return f'+{value:.3f}s'
    return value if isinstance(value, str) else '—'


def lap_text(value):
    if value is None:
        return '—'
    minutes, seconds = divmod(round(value * 1000), 60000)
    return f'{minutes}:{seconds / 1000:06.3f}'


class TimedValues:
    def __init__(self, events):
        ordered = sorted(events, key=lambda event: event[0])
        self.times = [event[0] for event in ordered]
        self.values = [event[1] for event in ordered]

    def at(self, elapsed):
        index = bisect_right(self.times, elapsed)
        return self.values[index - 1] if index else None


class Leaderboard:
    def __init__(self, data):
        session = data['session']
        self.race = session['session_type'] == 'Race'
        self.start = timestamp(session['date_start'])
        self.title = f"{session.get('location', '')} · {session['session_name']}".lstrip(' ·')
        self.drivers = {row['driver_number']: row.get('name_acronym') or row['full_name']
                        for row in data['drivers']}
        if not self.drivers:
            raise ValueError('比赛数据缺少车手')
        self.laps = defaultdict(list)
        self.best = {}
        self.active_lap = {}
        lap_starts = defaultdict(dict)
        tyre_events = defaultdict(list)
        for row in data['laps']:
            if not row.get('date_start'):
                continue
            driver = row['driver_number']
            start = timestamp(row['date_start']) - self.start
            number = row['lap_number']
            lap_starts[driver][number] = start
            self.laps[driver].append((start, number))
            value = duration(row.get('lap_duration'))
            if value and not row.get('is_pit_out_lap'):
                self.best.setdefault(driver, []).append((start + value, value))
        for driver, laps in self.laps.items():
            self.active_lap[driver] = TimedValues(laps)
        for driver, laps in self.best.items():
            best = float('inf')
            changes = []
            for end, value in sorted(laps):
                if value < best:
                    best = value
                    changes.append((end, best))
            self.best[driver] = TimedValues(changes)
        for row in data.get('stints', []):
            driver = row['driver_number']
            start = lap_starts[driver].get(row['lap_start'], 0 if row['lap_start'] == 1 else None)
            if start is not None:
                tyre_events[driver].append((start, row.get('compound')))
        self.tyres = {driver: TimedValues(events) for driver, events in tyre_events.items()}
        self.positions = self.timed_field(data.get('position', []), 'position')
        self.gaps = self.timed_field(data.get('intervals', []), 'gap_to_leader')

    def timed_field(self, rows, field):
        events = defaultdict(list)
        for row in rows:
            if row.get('date'):
                events[row['driver_number']].append((timestamp(row['date']) - self.start, row.get(field)))
        return {driver: TimedValues(values) for driver, values in events.items()}

    @staticmethod
    def current(mapping, driver, time):
        values = mapping.get(driver)
        return values.at(time) if values else None

    def at(self, elapsed):
        if elapsed < 0:
            return None, []
        if self.race:
            rank = [(value, driver) for driver in self.drivers
                    if (value := self.current(self.positions, driver, elapsed)) is not None]
        else:
            rank = [(value, driver) for driver in self.drivers
                    if (value := self.current(self.best, driver, elapsed)) is not None]
        rank.sort()
        result = []
        for index, (value, driver) in enumerate(rank, 1):
            tyre = self.current(self.tyres, driver, elapsed) or '—'
            if self.race:
                gap = self.current(self.gaps, driver, elapsed)
                result.append((str(value), self.drivers[driver], '领先' if value == 1 else gap_text(gap), tyre))
            else:
                gap = '领先' if index == 1 else gap_text(value - rank[0][0])
                result.append((str(index), self.drivers[driver], lap_text(value), gap, tyre))
        lap = max((values.at(elapsed) or 0 for values in self.active_lap.values()), default=0)
        return lap if self.race and lap else None, result
