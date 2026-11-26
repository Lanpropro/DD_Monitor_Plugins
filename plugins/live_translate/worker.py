"""External local inference process. No Qt, host settings, system audio or video."""
import json
import sys
import threading
import time

from options import Options
from protocol import read_frame


def emit(**value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    config_line = sys.stdin.buffer.readline(16385)
    if len(config_line) > 16384:
        raise ValueError('Session configuration is too large')
    config = json.loads(config_line)
    options = Options(**config['options'])
    options.validate()
    import numpy as np
    import sherpa_onnx
    from recognition import create_recognizer, create_vad
    from translation import TranslationSlot
    from speech_pipeline import SpeechPipeline
    from music_detection import MusicDetector, MUSIC_MARKER

    recognizer = create_recognizer(options, sherpa_onnx)
    vad = create_vad(options, sherpa_onnx)
    translator = TranslationSlot(options, config.get('api_key', ''))
    pipeline = SpeechPipeline(options, np, vad, recognizer, threading.Lock(), translator)
    detector = MusicDetector(options.music_model_dir, sherpa_onnx, np) if options.music_detection else None
    emit(event='ready')
    while True:
        frame = read_frame(sys.stdin.buffer)
        if frame is None:
            break
        raw, reset = frame
        if reset:
            pipeline.reset()
            if detector is not None:
                detector.reset()
                emit(event='original', text='')
        waveform = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768
        changed = detector.feed(waveform) if detector is not None else None
        if changed is not None:
            pipeline.reset()
            emit(event='original', text=MUSIC_MARKER if changed else '')
        if detector is not None and detector.active:
            continue
        for job in pipeline.feed(waveform):
            result = pipeline.process(job, lambda: True)
            if result is not None:
                emit(**result)
    for job in pipeline.segments.poll(time.monotonic() + 1):
        result = pipeline.process(job, lambda: True)
        if result is not None:
            emit(**result)
    translator.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Keep API keys/stream URLs out of process diagnostics.
        emit(event='error', message=f'本地识别进程失败（{type(error).__name__}），请检查依赖和模型文件')
        sys.exit(1)
