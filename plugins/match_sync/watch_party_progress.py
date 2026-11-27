"""Local playback progress while a two-device match is locked (seconds, monotonic clock)."""


class LockedProgress:
    def __init__(self):
        self.clear()

    def clear(self):
        self.anchor = self.previous = self.stable_since = None
        self.recovering = False

    def interrupted(self):
        self.recovering = True
        self.previous = self.anchor = self.stable_since = None

    def recovered(self, wall, position, normal_rate=True):
        if position is None:
            self.interrupted()
            return False
        if self.anchor is None:
            self.anchor = (wall, position)
        start, media = self.anchor
        if not self.recovering and abs((position - media) - (wall - start)) >= .2:
            self.interrupted()
        if self.recovering:
            if not normal_rate:
                self.stable_since = self.previous = None
                return False
            if self.previous is None:
                self.stable_since = wall
            else:
                before, stamp = self.previous
                advance = position - stamp
                if advance <= 0 or abs(advance - (wall - before)) > .08:
                    self.stable_since = wall
            self.previous = (wall, position)
            if wall - self.stable_since >= 1.:
                self.clear()
                return True
        return False


def device_rate_ppm(observation, now):
    """Rate of the block at the last observed device playhead, if available."""
    if observation:
        observed, played, _, _, segments = observation
        if now - observed <= .5:
            for start, end, _, rate in segments:
                if start <= played < end:
                    return round(rate * 1_000_000)
    return None
