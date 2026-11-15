"""Clock readings and second transitions; independent of players and widgets."""
from dataclasses import dataclass
import math
import re


def parse_clock(text):
    text = text.strip().replace('•.', ':').replace('·.', ':')
    compact = re.fullmatch(r'\d{3,5}', text)
    if compact:
        minutes, seconds = int(text[:-2]), int(text[-2:])
        return minutes * 60 + seconds if seconds < 60 else None
    matches = re.findall(r'(?<!\d)(\d{1,3})\s*[:：]\s*([0-5]\d)(?!\d)', text)
    values = {int(minutes) * 60 + int(seconds) for minutes, seconds in matches}
    return next(iter(values)) if len(values) == 1 else None


def format_clock(seconds):
    return f'{int(seconds) // 60:02d}:{int(seconds) % 60:02d}'


def valid_region(region):
    return (len(region) == 4 and all(math.isfinite(v) for v in region)
            and min(region[:2]) >= 0 and min(region[2:]) > 0
            and region[0] + region[2] <= 1.000001 and region[1] + region[3] <= 1.000001)


def suggest_transition(times, readings, direction):
    """Require observed neighbors around a consistent one-second transition."""
    if len(times) != len(readings) or not readings or readings[0] is None:
        return None
    first = readings[0]
    observed = [value for value in readings if value is not None]
    if any(direction * (b - a) < 0 or direction * (b - a) > 1
           for a, b in zip(observed, observed[1:])):
        return None
    for index in range(1, len(times) - 1):
        if (readings[index - 1] == first and readings[index] == first + direction
                and readings[index + 1] == first + direction
                and 0 < times[index] - times[0] <= 1.1
                and 0 < times[index] - times[index - 1] <= .2
                and 0 < times[index + 1] - times[index] <= .2):
            return index
    return None


@dataclass(frozen=True)
class ClockReading:
    first_second: int
    times: tuple
    transition: int
    anchor: float

    def at_anchor(self, direction):
        if direction not in (-1, 1) or not 0 < self.transition < len(self.times):
            raise ValueError('请选择第一次数值变化后的画面')
        if not all(math.isfinite(value) for value in (*self.times, self.anchor)):
            raise ValueError('画面时间无效，请重新采样')
        if self.first_second < 0 or direction == -1 and self.first_second == 0:
            raise ValueError('倒计时已归零，请在同一回合重新采样')
        before, after = self.times[self.transition - 1:self.transition + 1]
        if not 0 < after - before <= .2 or not 0 < after - self.times[0] <= 1.1:
            raise ValueError('翻秒位置需在首帧后约一秒内，且前后画面必须连续')
        boundary = (before + after) / 2
        value = self.first_second + direction * (1 + self.anchor - boundary)
        if value < 0:
            raise ValueError('倒计时已结束，请重新采样同一回合')
        return value, (after - before) / 2


def calculate_offsets(readings, shifts, reference, direction):
    if reference not in readings or len(readings) < 2:
        raise ValueError('至少需要主画面和一路副画面的计时器')
    values = {key: reading.at_anchor(direction) for key, reading in readings.items()}
    baseline = values[reference][0]
    desired = dict(shifts)
    uncertainty = {}
    for key, (value, error) in values.items():
        correction = direction * (value - baseline)
        if abs(correction) > 60:
            raise ValueError('时间差超过60秒，请检查是否同一局、同一回合和同一计时器')
        desired[key] = shifts[key] - correction
        uncertainty[key] = error + values[reference][1]
    return desired, uncertainty
