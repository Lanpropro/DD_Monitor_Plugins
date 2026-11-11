"""Explicit local-model smoke test; never a live-stream performance benchmark."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/live_translate'
sys.path.insert(0, str(PLUGIN))
from options import Options
from protocol import AudioGate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--sample', type=Path, required=True, help='An explicitly selected speech sample')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--engine', choices=('reazon', 'qwen3_asr'), default='reazon')
    args = parser.parse_args()
    options = Options(engine=args.engine, model_dir=str(args.model_dir.resolve()), python=sys.executable,
                      translation_mode='original')
    options.validate()
    # Bound this regression fixture to 20 seconds; inference runs in its actual process.
    audio = subprocess.check_output([args.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                                     '-i', str(args.sample), '-t', '20', '-ac', '1', '-ar', '16000',
                                     '-f', 's16le', 'pipe:1'], timeout=30)
    gate = AudioGate()
    frames = b''.join(gate.frame(audio[i:i + 6400], 0) for i in range(0, len(audio), 6400))
    config = json.dumps({'options': asdict(options)}).encode() + b'\n'
    result = subprocess.run([sys.executable, '-u', str(PLUGIN / 'worker.py')],
                            input=config + frames, capture_output=True, timeout=90)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith(b'{')]
    captions = [value for value in events if value.get('event') == 'caption']
    assert result.returncode == 0, 'Local model worker failed'
    assert events and events[0].get('event') == 'ready', 'Local model never became ready'
    assert captions and all(value['text'].strip() for value in captions), 'No local speech captions'
    assert not any(value.get('event') == 'error' for value in events)
    print(f'PASS: real {args.engine} worker produced {len(captions)} captions from the selected sample; '
          'accuracy and live latency were not measured')


if __name__ == '__main__':
    main()
