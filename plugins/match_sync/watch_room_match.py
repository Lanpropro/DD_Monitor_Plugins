"""Match time-stamped displayed-frame fingerprints. Positive lag means the guest is behind."""
from collections import deque
from dataclasses import dataclass, field
from math import ceil
from statistics import median


def picture_delta(a, b, now):
    if len(a) < 20 or len(b) < 20 or min(a[-1][0], b[-1][0]) < now - 2200:
        return None
    pairs = {}
    for side, (recent, history) in enumerate(((a, b), (b, a))):
        for stamp, signature in reversed(recent):
            if stamp < now - 7500:
                break
            compared = [((signature ^ sig).bit_count(), at) for at, sig in history]
            distance, other = min(compared)
            if distance > 54 or any(d < distance + 12 and abs(at - other) > 450 for d, at in compared):
                continue
            pair = (other, stamp) if side else (stamp, other)
            pairs[pair] = distance
    if len(pairs) < 10:
        return None
    lag = median(b_at - a_at for a_at, b_at in pairs)
    good = sorted((x, y) for x, y in pairs if abs(y - x - lag) <= 120)
    if len(good) < max(10, .75 * len(pairs)) or good[-1][0] - good[0][0] < 2400:
        return None
    if max(good[-1]) < now - 2000:
        return None
    return round(median(y - x for x, y in good))


def consistent(first, second):
    differences = [a - b for a, b in zip(first, second)]
    return max(differences) - min(differences) <= 140


@dataclass
class LiveMatch:
    active: tuple = (0, 1)
    frames: list = field(default_factory=lambda: [deque(maxlen=480) for _ in range(3)])
    rtt: list = field(default_factory=lambda: [0] * 3)
    errors: deque = field(default_factory=lambda: deque(maxlen=60))
    last_check: int = 0
    cutoff: int = 0
    last_lag: int | None = None
    missing_since: int | None = None
    candidates: deque = field(default_factory=lambda: deque(maxlen=3))
    verification: tuple | None = None
    locked: bool = False
    interval: int = 1600
    basis: list = field(default_factory=lambda: ["screen"] * 3)

    def reset(self, at):
        self.verification = None
        self.locked = False
        self.cutoff = at
        for buffer in self.frames:
            buffer.clear()
        self.candidates.clear()
        self.last_lag = self.missing_since = None
        self.interval = 1600
        self.errors.clear()

    def observe(self, side, data, now):
        if self.locked:
            return None
        points, rtt = data.get("points"), data.get("rtt")
        basis = data.get("basis", "screen")
        if (data.keys() - {"type", "points", "rtt", "basis"} or type(rtt) is not int or not 0 <= rtt <= 2000
                or basis not in ("screen", "clock") or not isinstance(points, list) or len(points) > 16):
            raise ValueError("invalid")
        parsed = []
        for item in points:
            if (not isinstance(item, list) or len(item) != 2 or type(item[0]) is not int
                    or abs(item[0] - now) > 8000 or not isinstance(item[1], str) or len(item[1]) != 128
                    or any(c not in '0123456789abcdef' for c in item[1])):
                raise ValueError("invalid")
            parsed.append((item[0], int(item[1], 16)))
        self.rtt[side] = rtt
        if self.basis[side] != basis:
            self.candidates.clear()
            self.errors.clear()
            self.frames[side].clear()
            self.interval = 1600
            self.verification = None
            self.basis[side] = basis
        buffer = self.frames[side]
        for stamp, signature in parsed:
            if stamp >= self.cutoff and (not buffer or stamp >= buffer[-1][0] + 30):
                buffer.append((stamp, signature))
        if now < max(self.cutoff, self.last_check + self.interval):
            return None
        self.last_check = now
        reference, *others = self.active
        offsets = [0]
        for other in others:
            delta = picture_delta(self.frames[reference], self.frames[other], now)
            if delta is None:
                return self.decide(None, now)
            offsets.append(delta)
        return self.decide(tuple(offsets), now)

    def decide(self, offsets, now):
        # An offset is when this seat displayed the same picture, relative to the reference.
        rtts = [self.rtt[i] for i in self.active]
        result = dict(type="measurement", lag=None, rtt=max(rtts), samples=0,
                      clock=any(self.basis[i] == "clock" for i in self.active))
        self.interval = 1600
        if offsets is None:
            self.candidates.clear()
            if self.missing_since is None:
                self.missing_since = now
            if self.last_lag is not None:
                if now - self.missing_since < 12000:
                    result["held"] = self.last_lag
                else:
                    self.reset(now)
            return result
        spread = max(offsets) - min(offsets)
        lag = offsets[1] if len(offsets) == 2 else spread
        result.update(lag=lag, spread=spread, leading=self.active[offsets.index(min(offsets))])
        self.missing_since = None
        self.last_lag = lag
        self.errors.append(abs(lag))
        result.update(samples=len(self.errors), p95=sorted(self.errors)[ceil(.95 * len(self.errors)) - 1])
        tolerance = max(350, sum(rtts) / len(rtts) + 70)
        if spread <= tolerance:
            self.candidates.clear()
            if self.verification is not None and consistent(offsets, self.verification):
                self.locked = True
                result["locked"] = True
            else:
                self.verification = offsets
                self.cutoff = now + 1
                result["verifying"] = True
            # Require independent pictures for verification, and retain none while locked.
            for buffer in self.frames:
                buffer.clear()
            return result
        self.verification = None
        if any(not consistent(value, offsets) for value in self.candidates):
            self.candidates.clear()
        self.candidates.append(offsets)
        if len(self.candidates) == 3:
            times = [median(values) for values in zip(*self.candidates)]
            delays = {str(i): max(times) - value for j, (i, value) in enumerate(zip(self.active, times))
                      if all(max(sample) - sample[j] > tolerance for sample in self.candidates)}
            if not delays:
                return result
            result.update(delays=delays, at=now + 1500)
            if len(times) == 2:
                side, delay = next(iter(delays.items()))
                result.update(adjust=delay, side=int(side))
            self.reset(result["at"] + 800)
        return result
