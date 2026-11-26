"""Conservative caption-label cleanup and translation language checks."""
import re
import unicodedata
try:
    from .glossary import matched_terms
except ImportError:
    from glossary import matched_terms

LANGUAGES = {'日语': 'Japanese', '英语': 'English', '韩语': 'Korean',
             '中文': 'Chinese', '简体中文': 'Simplified Chinese', '繁体中文': 'Traditional Chinese'}
_LABEL = re.compile(r'^(?:直播中|正在直播|原文|译文|翻译|配信中|翻訳|翻訳文|번역|'
                    r'translation|translated text|original|livestream|live)\s*[:：]\s*', re.I)
_STATUS = re.compile(r'^(?:(?:直播中|正在直播|配信中|livestream|live)[\s:：。.!！…]*)+$', re.I)
_KANA = re.compile(r'[\u3040-\u30ff]')
_HANGUL = re.compile(r'[\uac00-\ud7af]')
_HAN = re.compile(r'[\u3400-\u9fff]')
_LATIN = re.compile(r'[A-Za-z]')
_TRANSLATION_INTRO = re.compile(
    r'^(?:(?:当然|好的|好|可以|没问题)[，,。!！\s]*)?'
    r'(?=以下是|下面是|这是|将|把|中文|简体中文|繁体中文|日语|英语|韩语|译文如下|翻译如下)'
    r'(?:以下是|下面是|这是)?(?:将|把)?[^\n:：]{0,50}'
    r'(?:翻译(?:成|为)[^\n:：]{0,20}(?:的)?(?:译文|翻译|结果)?|'
    r'(?:中文|简体中文|繁体中文|日语|英语|韩语)(?:译文|翻译)|译文如下|翻译如下)'
    r'\s*[:：]\s*', re.I)


def clean_translation(text, source=''):
    """Remove model presentation wrappers, without changing spoken quotations."""
    text = clean_caption(text)
    # These words can themselves be spoken; preserve an explicitly discussed
    # translation instead of stripping its introductory phrase.
    if not re.search(r'翻译|翻訳|訳文|translation|translate', source, re.I):
        stripped = _TRANSLATION_INTRO.sub('', text, count=1).strip()
        if stripped != text:
            text = stripped
            for left, right in (('“', '”'), ('「', '」'), ('"', '"')):
                if text.startswith(left) and text.endswith(right):
                    text = text[1:-1].strip()
                    break
    return clean_caption(text)


def clean_caption(text):
    text = str(text or '').strip()
    while True:
        cleaned = _LABEL.sub('', text, count=1).strip()
        if cleaned == text:
            break
        text = cleaned
    return '' if _STATUS.fullmatch(text) else text


def _letters(text):
    return ''.join(char for char in text if unicodedata.category(char).startswith(('L', 'N')))


def repeats_context(text, source, context):
    output, current = _letters(text), _letters(source)
    return any(len(previous) >= 4 and previous not in current and previous in output
               for previous in map(_letters, context))


def translation_matches(text, source, target_language, terms=()):
    """Reject clear wrong-language prose, retaining source terms conservatively.

    Script checks cannot identify every proper noun or distinguish Chinese from
    Japanese kanji-only strings; they are a guard, not semantic verification.
    """
    remainder = text
    for _original, target in matched_terms(terms, source):
        remainder = remainder.replace(target, '')
    letters = _letters(remainder)
    if not letters:
        return bool(matched_terms(terms, source)) and bool(_letters(text))
    if target_language in ('中文', '简体中文', '繁体中文'):
        return bool(_HAN.search(remainder)) and not (_KANA.search(remainder) or _HANGUL.search(remainder) or _LATIN.search(remainder))
    if target_language in ('日语', 'Japanese'):
        return bool(_KANA.search(remainder) or _HAN.search(remainder)) and not _HANGUL.search(remainder)
    if target_language in ('韩语', 'Korean'):
        return bool(_HANGUL.search(remainder)) and not _KANA.search(remainder)
    if target_language in ('英语', 'English'):
        return bool(_LATIN.search(remainder)) and not (_KANA.search(remainder) or _HANGUL.search(remainder) or _HAN.search(remainder))
    return True
