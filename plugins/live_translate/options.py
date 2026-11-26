"""Validate model files and snapshot one session without loading model weights."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
try:
    from .glossary import validate_terms
except ImportError:
    from glossary import validate_terms


QWEN_FILES = {
    'conv_frontend': 'conv_frontend.onnx',
    'encoder': 'encoder.int8.onnx',
    'decoder': 'decoder.int8.onnx',
}
TOKENIZER_FILES = ('vocab.json', 'merges.txt', 'tokenizer_config.json')
DEFAULT_ASR_DIR = 'qwen3-asr-1.7b-int8'
DEFAULT_TRANSLATION_FILE = 'Qwen3-1.7B-Q8_0.gguf'
PATH_SETTINGS = ('model_dir', 'python', 'local_model', 'local_server', 'music_model_dir')


def bundled_defaults(plugin_dir=None):
    root = Path(plugin_dir or Path(__file__).resolve().parent) / 'vendor'
    python = root / 'python' / 'python.exe'
    models = root / 'models'
    local_server = root / 'llama' / 'b12211' / 'llama-server.exe'
    values = {}
    if python.is_file():
        values['python'] = str(python)
    for folder in (DEFAULT_ASR_DIR, 'qwen3-asr-0.6b-int8'):
        model_dir = models / folder
        if all((model_dir / name).is_file()
               for name in (*QWEN_FILES.values(), 'silero_vad.onnx',
                            *(f'tokenizer/{name}' for name in TOKENIZER_FILES))):
            values['model_dir'] = str(model_dir)
            break
    for filename in (DEFAULT_TRANSLATION_FILE, 'Qwen3-0.6B-Q8_0.gguf'):
        local_model = models / filename
        if local_model.is_file():
            values['local_model'] = str(local_model)
            break
    if local_server.is_file():
        values['local_server'] = str(local_server)
    music = root / 'models' / 'audio-tagging'
    if all((music / name).is_file() for name in ('model.int8.onnx', 'class_labels_indices.csv')):
        values.update(music_detection=True, music_model_dir=str(music))
    if {'python', 'model_dir', 'local_model', 'local_server'} <= values.keys():
        values.update(engine='qwen3_asr', translation_mode='local')
    return values


def with_bundled_defaults(settings, plugin_dir=None):
    merged = dict(settings)
    merged['music_detection'] = True
    if merged.get('engine') == 'reazon':
        merged.update(engine='qwen3_asr', model_dir='')
    for key, value in bundled_defaults(plugin_dir).items():
        current = merged.get(key)
        if isinstance(current, str) and key in PATH_SETTINGS:
            if not current.strip() or not Path(current).exists():
                merged[key] = value
        elif not current and not (key == 'music_detection' and key in merged):
            merged[key] = value
    return merged


@dataclass(frozen=True)
class Options:
    engine: str = 'qwen3_asr'
    model_dir: str = ''
    python: str = ''
    endpoint: str = ''
    model: str = ''
    translation_mode: str = 'local'
    local_model: str = ''
    local_server: str = ''
    source_language: str = '日语'
    target_language: str = '简体中文'
    threads: int = 2
    timeout: float = 1.5
    music_detection: bool = False
    music_model_dir: str = ''
    local_prompt: str = ''
    api_prompt: str = ''
    glossary_language: str = ''
    glossary: tuple = ()

    def files(self):
        if self.engine != 'qwen3_asr':
            raise ValueError('本地识别已改用 Qwen3-ASR，请重新选择对应模型目录')
        root = Path(self.model_dir)
        names = dict(QWEN_FILES)
        names['vad'] = 'silero_vad.onnx'
        result = {key: str(root / name) for key, name in names.items()}
        required = list(result.values())
        result['tokenizer'] = str(root / 'tokenizer')
        required += [str(root / 'tokenizer' / name) for name in TOKENIZER_FILES]
        missing = [str(Path(path).relative_to(root)) for path in required if not Path(path).is_file()]
        if missing:
            raise ValueError('Qwen3-ASR 模型目录不完整，缺少：' + '、'.join(missing))
        return result

    def validate(self):
        validate_terms(self.glossary)
        if len(self.local_prompt) > 400 or len(self.api_prompt) > 2000:
            raise ValueError('本地提示词最多 400 字，API 提示词最多 2000 字')
        self.files()
        if self.music_detection and not all((Path(self.music_model_dir) / name).is_file()
                for name in ('model.int8.onnx', 'class_labels_indices.csv')):
            raise ValueError('音乐检测模型未就绪，请先准备模型或选择完整目录')
        if not 1 <= self.threads <= 8 or not 0.2 <= self.timeout <= 3:
            raise ValueError('Invalid thread count or translation timeout')
        if self.translation_mode not in ('local', 'api', 'original'):
            raise ValueError('请选择本地翻译、API 翻译或仅显示原文')
        if self.translation_mode == 'local':
            if not Path(self.local_server).is_file():
                raise ValueError('本地翻译运行程序未就绪，请先准备本地模型')
            if not Path(self.local_model).is_file():
                raise ValueError('本地翻译模型未就绪，请先准备本地模型')
            with Path(self.local_model).open('rb') as model:
                if model.read(4) != b'GGUF':
                    raise ValueError('请选择有效的 GGUF 本地翻译模型')
        if self.translation_mode == 'api':
            if not self.endpoint.strip() or not self.model.strip():
                raise ValueError('请填写 API 翻译接口地址和模型名称')
            url = urlsplit(self.endpoint)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                raise ValueError('Use an HTTP(S) API base URL without embedded credentials')
            if url.query or url.fragment or not self.model.strip():
                raise ValueError('Use an API base URL and a nonempty translation model')


def audio_arguments(url, headers, *, relayed=False):
    """Preserve the host's headers exactly; do not synthesize a Referer."""
    if urlsplit(url).scheme not in ('https', 'http'):
        raise ValueError('Only HTTP(S) live streams are supported')
    lines = []
    for key, value in headers.items():
        if any(char in str(key) + str(value) for char in '\r\n'):
            raise ValueError('Invalid stream header')
        lines.append(f'{key}: {value}\r\n')
    args = ['-nostdin', '-hide_banner', '-loglevel', 'error', '-rw_timeout',
            '30000000' if relayed else '5000000']
    if relayed:
        # The host relay performs upstream proxy/TLS/retries. Keep loopback direct.
        # HLS downloads arrive in segment-sized bursts. Pace decoded PCM so
        # a fast download cannot overflow the bounded recognition queue.
        args += ['-re', '-http_proxy', '']
        if urlsplit(url).path.endswith('.m3u8'):
            # Relay segment names may end in .ts while carrying fragmented MP4.
            args += ['-extension_picky', '0', '-live_start_index', '-1']
    if lines:
        args += ['-headers', ''.join(lines)]
    return args + ['-i', url, '-map', '0:a:0', '-vn', '-sn', '-dn',
                   '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1']
