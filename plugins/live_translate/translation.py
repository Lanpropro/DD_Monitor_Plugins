"""Text-only translation through a local or online compatible endpoint."""
import json
import re
import time
from collections import deque
from functools import partial
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError
try:
    from .text_policy import clean_translation, repeats_context, translation_matches
    from .prompt_policy import local_context, translation_prompt, translation_terms
    from .quality_policy import repetitive
except ImportError:
    from text_policy import clean_translation, repeats_context, translation_matches
    from prompt_policy import local_context, translation_prompt, translation_terms
    from quality_policy import repetitive


class TranslationSlot:
    """One request in flight; deadline expiry never adds another queued request."""
    def __init__(self, options, api_key='', operation=None):
        self.options = options
        self.api_key = api_key
        self.operation = operation
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='text-translation')
        self.future = None
        self.context = deque(maxlen=2)
        self.current = None
        self.review = {}

    def reset(self):
        self.context.clear()
        self.review = {}

    def run(self, text):
        self.review = {}
        if self.options.translation_mode == 'original':
            return '', '仅显示识别原文'
        if not self.options.endpoint:
            return '', '未配置翻译接口，仅显示原文'
        if self.future is not None and not self.future.done():
            return '', '上一条翻译请求尚未结束，当前句保留原文'
        details = {}
        operation = self.operation or partial(translate, context=tuple(self.context), current=self.current,
                                               diagnostics=details)
        self.future = self.pool.submit(operation, text, self.options, self.api_key)
        self.context.append(text[:128])
        try:
            return self.future.result(timeout=self.options.timeout)
        except TimeoutError:
            return '', '翻译超过等待上限，当前句保留原文'
        finally:
            self.review = dict(attempts=details.get('attempts', 0), reasons=tuple(details.get('reasons', ())))

    def close(self):
        self.reset()
        self.pool.shutdown(wait=False, cancel_futures=True)


def translate(text, options, api_key='', opener=None, context=(), *, current=None, diagnostics=None):
    review = diagnostics if diagnostics is not None else {}
    review.update(attempts=0, reasons=[])
    if not options.endpoint:
        return '', '未配置翻译接口，仅显示原文'
    terms = translation_terms(options, text)
    prompt = translation_prompt(options, context, terms)
    body = {
        'model': options.model,
        'messages': [
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': text},
        ],
        'temperature': 0,
        'max_tokens': 256,
        'stream': False,
    }
    retry_instruction = f'必须改写为{options.target_language}，不得返回原文或说明文字。只输出译文。'
    retry_rule = retry_instruction + '只翻译当前句一次，不重复前文或续写。'
    headers = {'Content-Type': 'application/json'}
    if options.translation_mode == 'local':
        body['chat_template_kwargs'] = {'enable_thinking': False}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    if opener is None:
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({})).open
                  if options.translation_mode == 'local' else urllib.request.urlopen)
    try:
        deadline = time.monotonic() + options.timeout
        for _ in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or current is not None and not current():
                break
            request = urllib.request.Request(
                options.endpoint.rstrip('/') + '/chat/completions',
                data=json.dumps(body, ensure_ascii=False).encode('utf-8'), headers=headers)
            review['attempts'] += 1
            with opener(request, timeout=remaining) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                return '', '翻译响应过大，已保留原文'
            content = json.loads(raw)['choices'][0]['message']['content']
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if '<think>' in content or len(content) > 2000:
                return '', '翻译未返回有效正文，已保留原文'
            content = clean_translation(content, text)
            instruction_echo = content in (prompt, prompt.removesuffix('/no_think'), retry_instruction, retry_rule,
                                           '只翻译当前句一次，不重复前文或续写。')
            context_echo = (options.translation_mode == 'local'
                            and repeats_context(content, text, local_context(context)))
            reason = ('empty_translation' if not content else
                      'instruction_echo' if instruction_echo else
                      'context_echo' if context_echo else
                      'wrong_language' if not translation_matches(content, text, options.target_language, terms) else
                      'repeated_translation' if repetitive(content) and not repetitive(text) else '')
            if not reason:
                return content, ''
            review['reasons'].append(reason)
            body['messages'][0]['content'] += '\n' + retry_rule
        return '', '译文未满足目标语言或正文要求，当前句仅显示原文'
    except urllib.error.HTTPError as error:
        return '', f'翻译接口 HTTP {error.code}，已保留原文'
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        # URLs, response bodies and credentials are deliberately not logged.
        return '', '翻译超时或响应无效，已保留原文'
