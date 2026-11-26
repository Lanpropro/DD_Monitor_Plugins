"""One local CPU recognizer and one bounded VAD per active session."""
try:
    from .text_policy import LANGUAGES, clean_caption
    from .speech_segments import MAX_SPEECH_SECONDS
except ImportError:
    from text_policy import LANGUAGES, clean_caption
    from speech_segments import MAX_SPEECH_SECONDS


def create_recognizer(options, sherpa):
    files = options.files()
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
    config.silero_vad.min_silence_duration = 0.35
    config.silero_vad.min_speech_duration = 0.15
    config.silero_vad.max_speech_duration = MAX_SPEECH_SECONDS
    config.sample_rate = 16000
    config.num_threads = 1
    return sherpa.VoiceActivityDetector(config, buffer_size_in_seconds=10)


def recognize(recognizer, samples, source_language):
    stream = recognizer.create_stream()
    stream.set_option('language', LANGUAGES.get(source_language, source_language))
    stream.accept_waveform(16000, samples)
    recognizer.decode_stream(stream)
    return clean_caption(stream.result.text)
