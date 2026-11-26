"""Editable translation templates; output language validation remains separate."""
try:
    from .glossary import matched_terms
except ImportError:
    from glossary import matched_terms

LOCAL_PROMPT = '将{source_language}翻译成{target_language}，只输出译文。/no_think'
API_PROMPT = ('把用户提供的{source_language}原文准确翻译成{target_language}。'
              '只输出{target_language}译文，不要解释、前缀、说话者标签或直播状态提示。'
              '保留原意、否定、疑问和语气，不添加原文没有的内容。'
              '普通词句必须翻译；只有人名、游戏名等专有名词可以保留原文，必要时用引号括起。/no_think')


def local_context(context):
    return tuple(' '.join(text.split())[:80] for text in context[-2:] if text.strip())


def translation_terms(options, source):
    if options.glossary_language != options.target_language:
        return ()
    terms = matched_terms(options.glossary, source)
    if options.translation_mode != 'local':
        return terms
    result, size = [], 0
    for term in terms:
        size += len(' = '.join(term)) + 1
        if len(result) >= 6 or size > 120:
            break
        result.append(term)
    return tuple(result)


def translation_prompt(options, context=(), terms=()):
    custom = options.local_prompt if options.translation_mode == 'local' else options.api_prompt
    template = custom.strip() or (LOCAL_PROMPT if options.translation_mode == 'local' else API_PROMPT)
    prompt = template.replace('{source_language}', options.source_language).replace('{target_language}', options.target_language)
    if custom.strip():
        prompt += f'\n只输出{options.target_language}译文，不输出说明或标签。/no_think'
    if terms:
        prompt += '\n术语（仅匹配原文时采用）：\n' + '\n'.join(' = '.join(term) for term in terms)
    if options.translation_mode == 'local':
        background = local_context(context)
        if background:
            prompt += '\n背景原文（仅理解指代，勿翻译、续写或回抄）：\n' + '\n'.join(background)
            prompt += '\n只翻译当前用户消息。'
    elif context:
        prompt += '\n前文仅用于理解指代，不要重复翻译或输出：\n' + '\n'.join(context[-2:])
    return prompt
