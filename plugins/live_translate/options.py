"""Validate model files and snapshot one session without loading model weights."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


REAZON_FILES = {
    'encoder': 'encoder-epoch-35-avg-1.int8.onnx',
    'decoder': 'decoder-epoch-35-avg-1.onnx',
    'joiner': 'joiner-epoch-35-avg-1.int8.onnx',
    'tokens': 'tokens.txt',
}
QWEN_FILES = {
    'conv_frontend': 'conv_frontend.onnx',
    'encoder': 'encoder.int8.onnx',
    'decoder': 'decoder.int8.onnx',
}
PATH_SETTINGS = ('model_dir', 'python', 'local_model', 'local_server')


def bundled_defaults(plugin_dir=None):
    root = Path(plugin_dir or Path(__file__).resolve().parent) / 'vendor'
    python = root / 'python' / 'python.exe'
    model_dir = root / 'models' / 'reazon-ja-en'
    local_model = root / 'models' / 'Qwen3-0.6B-Q8_0.gguf'
    local_server = root / 'llama' / 'b12211' / 'llama-server.exe'
    values = {}
    if python.is_file():
        values['python'] = str(python)
    if model_dir.is_dir() and all(
            (model_dir / name).is_file()
            for name in (*REAZON_FILES.values(), 'silero_vad.onnx')):
        values['model_dir'] = str(model_dir)
    if local_model.is_file():
        values['local_model'] = str(local_model)
    if local_server.is_file():
        values['local_server'] = str(local_server)
    if {'python', 'model_dir', 'local_model', 'local_server'} <= values.keys():
        values.update(engine='reazon', translation_mode='local')
    return values


def with_bundled_defaults(settings, plugin_dir=None):
    merged = dict(settings)
    for key, value in bundled_defaults(plugin_dir).items():
        current = merged.get(key)
        if isinstance(current, str) and key in PATH_SETTINGS:
            if not current.strip() or not Path(current).exists():
                merged[key] = value
        elif not current:
            merged[key] = value
    return merged


@dataclass(frozen=True)
class Options:
    engine: str = 'reazon'
    model_dir: str = ''
    python: str = ''
    endpoint: str = ''
    model: str = 'qwen3:0.6b'
    translation_mode: str = 'local'
    local_model: str = ''
    local_server: str = ''
    source_language: str = '日语'
    target_language: str = '简体中文'
    threads: int = 2
    timeout: float = 1.5

    def files(self):
        if self.engine not in ('reazon', 'qwen3_asr'):
            raise ValueError('Unsupported recognition engine')
        root = Path(self.model_dir)
        names = dict(REAZON_FILES if self.engine == 'reazon' else QWEN_FILES)
        names['vad'] = 'silero_vad.onnx'
        result = {key: str(root / name) for key, name in names.items()}
        required = list(result.values())
        if self.engine == 'qwen3_asr':
            result['tokenizer'] = str(root / 'tokenizer')
            required += [str(root / 'tokenizer' / name)
                         for name in ('vocab.json', 'merges.txt', 'tokenizer_config.json')]
        if any(not Path(path).is_file() for path in required):
            raise ValueError('Model directory is incomplete; see the plugin README')
        return result

    def validate(self):
        self.files()
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
        if self.engine == 'reazon' and self.source_language not in ('日语', '英语'):
            raise ValueError('ReazonSpeech supports Japanese and English only')


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
        args += ['-http_proxy', '']
    if lines:
        args += ['-headers', ''.join(lines)]
    return args + ['-i', url, '-map', '0:a:0', '-vn', '-sn', '-dn',
                   '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1']
