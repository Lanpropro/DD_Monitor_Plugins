"""Synthetic common game sound vs. independent speech/noise and repeated tone."""
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_match_sync_audio import estimate_lag


def main():
    random = np.random.default_rng(20261025)
    rate = 4000
    common = random.normal(size=rate * 25)
    delay = round(3.27 * rate)
    other = np.r_[np.zeros(delay), common[:-delay]]
    for gain in (1, .7):
        result = estimate_lag(common, gain * other + random.normal(size=len(common)) * .5, rate)
        assert result['lag'] is not None and abs(result['lag'] - 3.27) <= 1 / rate, result
    reversed_result = estimate_lag(other, common, rate)
    assert abs(reversed_result['lag'] + 3.27) < 1 / rate
    assert estimate_lag(common, random.normal(size=len(common)), rate)['lag'] is None
    assert estimate_lag(common, other * .03 + random.normal(size=len(common)), rate)['lag'] is None
    assert estimate_lag(np.zeros_like(common), np.zeros_like(common), rate)['lag'] is None
    tone = np.sin(2 * np.pi * 440 * np.arange(len(common)) / rate)
    assert estimate_lag(tone, tone, rate)['lag'] is None, 'Periodic audio cannot identify a unique offset'
    assert estimate_lag(common[:rate], other[:rate], rate)['lag'] is None
    print('PASS: positive/negative shared-audio delay, gain/noise, unrelated audio, buried audio, silence and periodic ambiguity')


if __name__ == '__main__':
    main()
