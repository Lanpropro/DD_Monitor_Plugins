"""Offline feasibility probe only; never changes the plugin's playback offsets."""
import argparse
import json
from pathlib import Path
import wave

import numpy as np


def estimate_lag(main, secondary, rate, max_lag=30, minimum_seconds=8):
    """Positive lag means common audio appears later in the secondary WAV."""
    main = np.asarray(main, dtype=np.float64)
    secondary = np.asarray(secondary, dtype=np.float64)
    minimum = round(minimum_seconds * rate)
    if min(len(main), len(secondary)) < minimum:
        return {'lag': None, 'reason': 'insufficient audio', 'score': 0.0}
    main = main - main.mean()
    secondary = secondary - secondary.mean()
    size = 1 << (len(main) + len(secondary) - 2).bit_length()
    correlation = np.fft.irfft(np.fft.rfft(secondary, size) * np.fft.rfft(main[::-1], size), size)
    radius = round(max_lag * rate)
    lags = np.arange(max(-radius, minimum - len(main)), min(radius, len(secondary) - minimum) + 1)
    a = np.maximum(0, -lags)
    b = np.maximum(0, lags)
    count = np.minimum(len(main) - a, len(secondary) - b)
    main_energy = np.r_[0, np.cumsum(main * main)]
    secondary_energy = np.r_[0, np.cumsum(secondary * secondary)]
    denominator = np.sqrt((main_energy[a + count] - main_energy[a]) *
                          (secondary_energy[b + count] - secondary_energy[b]))
    scores = np.divide(correlation[len(main) - 1 + lags], denominator,
                       out=np.zeros(len(lags)), where=denominator > 1e-10)
    best = int(np.argmax(scores))
    score = float(scores[best])
    distant = abs(lags - lags[best]) > rate * .25
    runner_up = float(scores[distant].max()) if distant.any() else 0.0
    accepted = score >= .4 and score - runner_up >= .12
    return {'lag': float(lags[best] / rate) if accepted else None,
            'reason': 'shared audio candidate; requires independent verification' if accepted else 'weak or ambiguous common audio',
            'score': score, 'runner_up': runner_up}


def read_pcm(path):
    with wave.open(str(path), 'rb') as source:
        if source.getsampwidth() != 2:
            raise ValueError('Probe requires 16-bit PCM WAV input')
        rate = source.getframerate()
        data = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2')
        data = data.reshape(-1, source.getnchannels()).mean(axis=1)
    # Average before downsampling; this small probe compares the low audio band.
    factor = max(1, rate // 4000)
    data = data[:len(data) // factor * factor].reshape(-1, factor).mean(axis=1)
    return data, rate / factor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', type=Path, required=True)
    parser.add_argument('--secondary', type=Path, required=True)
    parser.add_argument('--max-lag', type=float, default=30)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not np.isfinite(args.max_lag) or args.max_lag < 0:
        parser.error('--max-lag must be a finite nonnegative number')
    first, rate = read_pcm(args.main)
    second, other_rate = read_pcm(args.secondary)
    if rate != other_rate:
        parser.error('WAV sample rates must match')
    result = estimate_lag(first, second, rate, args.max_lag)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
