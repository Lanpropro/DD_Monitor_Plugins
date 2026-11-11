"""One local CPU recognizer and one bounded VAD per active session."""
import numpy as np


def create_recognizer(options, sherpa):
    files = options.files()
    if options.engine == 'reazon':
        return sherpa.OfflineRecognizer.from_transducer(
            **{key: files[key] for key in ('encoder', 'decoder', 'joiner', 'tokens')},
            num_threads=options.threads, provider='cpu')
    factory = getattr(sherpa.OfflineRecognizer, 'from_qwen3_asr', None)
    if factory is None:
        raise ValueError('Installed sherpa-onnx does not support Qwen3-ASR')
    return factory(
        **{key: files[key] for key in ('conv_frontend', 'encoder', 'decoder', 'tokenizer')},
        num_threads=options.threads, provider='cpu', max_total_len=512, max_new_tokens=128)


def create_vad(options, sherpa):
    config = sherpa.VadModelConfig()
    config.silero_vad.model = options.files()['vad']
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = 0.25
    config.silero_vad.min_speech_duration = 0.15
    config.silero_vad.max_speech_duration = 1.8
    config.sample_rate = 16000
    config.num_threads = 1
    return sherpa.VoiceActivityDetector(config, buffer_size_in_seconds=5)


def recognize(recognizer, samples, engine):
    # ReazonSpeech's published integration pads the beginning to avoid lost words.
    if engine == 'reazon':
        samples = np.pad(samples, (4800, 4800))
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    recognizer.decode_stream(stream)
    return stream.result.text.strip()
