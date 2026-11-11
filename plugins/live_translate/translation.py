"""Text-only translation through a local or online compatible endpoint."""
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError


class TranslationSlot:
    """One request in flight; deadline expiry never adds another queued request."""
    def __init__(self, options, api_key='', operation=None):
        self.options = options
        self.api_key = api_key
        self.operation = operation or translate
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='text-translation')
        self.future = None

    def run(self, text):
        if self.options.translation_mode == 'original':
            return '', '仅显示识别原文'
        if not self.options.endpoint:
            return '', '未配置翻译接口，仅显示原文'
        if self.future is not None and not self.future.done():
            return '', '上一条翻译请求尚未结束，当前句保留原文'
        self.future = self.pool.submit(self.operation, text, self.options, self.api_key)
        try:
            return self.future.result(timeout=self.options.timeout)
        except TimeoutError:
            return '', '翻译超过等待上限，当前句保留原文'

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


def translate(text, options, api_key='', opener=None):
    if not options.endpoint:
        return '', '未配置翻译接口，仅显示原文'
    languages = {'日语': 'Japanese', '英语': 'English', '韩语': 'Korean',
                 '中文': 'Chinese', '简体中文': 'Simplified Chinese', '繁体中文': 'Traditional Chinese'}
    source = languages.get(options.source_language, options.source_language)
    target = languages.get(options.target_language, options.target_language)
    body = {
        'model': options.model,
        'messages': [
            {'role': 'system', 'content': (
                f'Translate {source} livestream speech into {target}. Return only translation. /no_think')},
            {'role': 'user', 'content': text},
        ],
        'temperature': 0,
        'max_tokens': 256,
        'stream': False,
    }
    headers = {'Content-Type': 'application/json'}
    if options.translation_mode == 'local':
        body['chat_template_kwargs'] = {'enable_thinking': False}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    request = urllib.request.Request(
        options.endpoint.rstrip('/') + '/chat/completions',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'), headers=headers)
    if opener is None:
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({})).open
                  if options.translation_mode == 'local' else urllib.request.urlopen)
    try:
        with opener(request, timeout=options.timeout) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            return '', '翻译响应过大，已保留原文'
        content = json.loads(raw)['choices'][0]['message']['content']
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        if not content or '<think>' in content or len(content) > 2000:
            return '', '翻译未返回有效正文，已保留原文'
        return content, ''
    except urllib.error.HTTPError as error:
        return '', f'翻译接口 HTTP {error.code}，已保留原文'
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        # URLs, response bodies and credentials are deliberately not logged.
        return '', '翻译超时或响应无效，已保留原文'
