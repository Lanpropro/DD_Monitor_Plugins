"""External local inference process. No Qt, host settings, system audio or video."""
import json
import sys
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
    from recognition import create_recognizer, create_vad, recognize
    from translation import TranslationSlot

    recognizer = create_recognizer(options, sherpa_onnx)
    vad = create_vad(options, sherpa_onnx)
    translator = TranslationSlot(options, config.get('api_key', ''))
    emit(event='ready')
    while True:
        frame = read_frame(sys.stdin.buffer)
        if frame is None:
            break
        raw, reset = frame
        if reset:
            vad.reset()
        vad.accept_waveform(np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768)
        while not vad.empty():
            samples = np.array(vad.front.samples, dtype=np.float32)
            start = vad.front.start / 16000
            vad.pop()
            began = time.monotonic()
            text = recognize(recognizer, samples, options.engine)
            asr_ms = (time.monotonic() - began) * 1000
            if not text:
                continue
            # Original text is visible even when translation is slow or unavailable.
            emit(event='original', text=text)
            began = time.monotonic()
            translated, warning = translator.run(text)
            emit(event='caption', text=text, translated=translated, warning=warning,
                 audio_start=start, audio_seconds=len(samples) / 16000,
                 asr_ms=round(asr_ms, 1), translation_ms=round((time.monotonic() - began) * 1000, 1))
    translator.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Keep API keys/stream URLs out of process diagnostics.
        emit(event='error', message=f'本地识别进程失败（{type(error).__name__}），请检查依赖和模型文件')
        sys.exit(1)
