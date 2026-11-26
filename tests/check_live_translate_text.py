"""Language hints, subtitle text policy and bounded translation retry."""
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/live_translate'))
from options import Options
from recognition import recognize
from text_policy import clean_caption, clean_translation, translation_matches
from translation import TranslationSlot, translate


class TextPolicyTests(unittest.TestCase):
    def test_reported_model_introduction_is_removed_without_deleting_spoken_translation(self):
        output = '当然，以下是日语翻译成简体中文的译文： “啊，好像有很多鸟儿的场所呢。”'
        self.assertEqual(clean_translation(output, 'あ、なんか鳥って結構無くしできる場所にいるよね。'),
                         '啊，好像有很多鸟儿的场所呢。')
        self.assertEqual(clean_translation('以下是中文翻译：你好。', 'こんにちは。'), '你好。')
        spoken = '以下是日语翻译成中文的译文：你好。'
        self.assertEqual(clean_translation(spoken, 'これは日本語から中国語への翻訳です。'), spoken)
        self.assertEqual(clean_translation('“你好。”', '「こんにちは。」'), '“你好。”')
        self.assertEqual(clean_translation('我把这句话翻译成中文：你好。', 'こんにちは。'),
                         '我把这句话翻译成中文：你好。')

    def test_only_instruction_labels_are_removed(self):
        self.assertEqual(clean_caption('直播中：翻译：你好。'), '你好。')
        self.assertEqual(clean_caption('Translation: こんにちは。'), 'こんにちは。')
        for label in ('直播中。', '直播中：直播中', '配信中', 'Live:'):
            self.assertEqual(clean_caption(label), '')
        for speech in ('直播中遇到了朋友。', '翻译这个词很有趣。', 'Live music is fun.'):
            self.assertEqual(clean_caption(speech), speech)

    def test_chinese_translation_rejects_japanese_korean_and_english_prose(self):
        for output in ('全部です。', '可哀想すぎる。', '정말 귀여워요.', 'This is very cute.',
                       '这 is very cute。', '你好、かわいいです。'):
            self.assertFalse(translation_matches(output, 'かわいいです。', '简体中文'))

    def test_names_and_games_survive_inside_target_language_text(self):
        source = '今日はホロライブでNintendo Switchを使って「ぺこら」と遊ぶ。'
        terms = tuple((term, term) for term in ('ホロライブ', 'Nintendo Switch', 'ぺこら'))
        self.assertTrue(translation_matches('今天在ホロライブ用Nintendo Switch和「ぺこら」一起玩。', source, '简体中文', terms))
        self.assertTrue(translation_matches('两个人都很可爱，不是吗？', source, '简体中文'))
        self.assertTrue(translation_matches('今日本当にかわいいです。', source, '日语'))
        self.assertTrue(translation_matches('오늘 정말 귀여워요.', source, '韩语'))
        self.assertTrue(translation_matches('They are both cute.', source, '英语'))

    def test_selected_language_reaches_each_recognition_stream_without_deleting_names(self):
        stream = SimpleNamespace(set_option=Mock(), accept_waveform=Mock(),
                                 result=SimpleNamespace(text='原文：今日はNintendo Switchで遊ぶ。'))
        recognizer = SimpleNamespace(create_stream=Mock(return_value=stream), decode_stream=Mock())
        samples = object()
        self.assertEqual(recognize(recognizer, samples, '日语'), '今日はNintendo Switchで遊ぶ。')
        stream.set_option.assert_called_once_with('language', 'Japanese')
        stream.accept_waveform.assert_called_once_with(16000, samples)


class TranslationPolicyTests(unittest.TestCase):
    def test_custom_prompt_expands_languages_keeps_json_braces_and_still_checks_output(self):
        requests = []
        outputs = iter(('全部です。', '全都是这样。'))
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': next(outputs)}}]}).encode())
        text, warning = translate('全部です。', Options(endpoint='http://localhost/v1',
            local_prompt='游戏语气；{source_language} → {target_language}，JSON示例 {"term":"name"}。'), opener=opener)
        self.assertEqual((text, warning), ('全都是这样。', ''))
        prompt = requests[0]['messages'][0]['content']
        self.assertIn('日语 → 简体中文', prompt)
        self.assertIn('{"term":"name"}', prompt)
        self.assertIn('只输出简体中文译文', prompt)
        self.assertEqual(len(requests), 2)

    def test_model_wrapper_is_cleaned_before_translation_reaches_captions(self):
        def opener(*args, **kwargs):
            return io.BytesIO(json.dumps({'choices': [{'message': {'content':
                '当然，以下是日语翻译成简体中文的译文：“你好。”'}}]}).encode())
        self.assertEqual(translate('こんにちは。', Options(endpoint='http://localhost/v1'), opener=opener), ('你好。', ''))

    def test_local_prompt_uses_two_short_background_sentences_and_keeps_current_text_separate(self):
        requests = []
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '那边我完全不了解。'}}]}).encode())
        output, warning = translate('その辺全然詳しくない。', Options(endpoint='http://localhost/v1'),
                                    opener=opener, context=('discarded', '前' * 200, '直近の発言。'))
        self.assertEqual((output, warning), ('那边我完全不了解。', ''))
        prompt = requests[0]['messages'][0]['content']
        self.assertIn('前' * 80 + '\n直近の発言。', prompt)
        self.assertNotIn('前' * 81, prompt)
        self.assertNotIn('discarded', prompt)
        self.assertIn('勿翻译', prompt)
        self.assertEqual(requests[0]['messages'][1], {'role': 'user', 'content': 'その辺全然詳しくない。'})
        self.assertFalse(requests[0]['chat_template_kwargs']['enable_thinking'])

    def test_local_without_context_keeps_the_short_default_prompt(self):
        requests = []
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '你好。'}}]}).encode())
        translate('こんにちは。', Options(endpoint='http://localhost/v1'), opener=opener)
        self.assertEqual(requests[0]['messages'][0]['content'], '将日语翻译成简体中文，只输出译文。/no_think')

    def test_local_background_copy_is_rejected_but_current_repetition_is_allowed(self):
        requests = []
        outputs = iter(('会議終了。那边我不了解。', '那边我不了解。', '会議終了。'))
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': next(outputs)}}]}).encode())
        options = Options(endpoint='http://localhost/v1')
        self.assertEqual(translate('その辺は詳しくない。', options, opener=opener,
                                   context=('会議終了。',)), ('那边我不了解。', ''))
        self.assertEqual(len(requests), 2)
        self.assertEqual(translate('会議終了。', options, opener=opener,
                                   context=('会議終了。',)), ('会議終了。', ''))

    def test_api_keeps_bounded_reference_context_and_name_constraints(self):
        requests = []
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '你好。'}}]}).encode())
        translate('こんにちは。', Options(endpoint='http://localhost/v1', translation_mode='api'),
                  opener=opener, context=('earlier', 'recent', 'latest'))
        prompt = requests[0]['messages'][0]['content']
        self.assertNotIn('earlier', prompt)
        self.assertIn('recent\nlatest', prompt)
        self.assertIn('专有名词', prompt)

    def test_wrong_language_retries_within_original_deadline(self):
        requests = []
        def opener(request, timeout):
            requests.append((json.loads(request.data), timeout))
            content = '全部です。' if len(requests) == 1 else '译文：全都是这样。'
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': content}}]}).encode())
        text, warning = translate('全部です。', Options(endpoint='http://localhost/v1'), opener=opener)
        self.assertEqual((text, warning), ('全都是这样。', ''))
        self.assertEqual(len(requests), 2)
        self.assertLessEqual(requests[1][1], requests[0][1])
        self.assertIn('简体中文', requests[0][0]['messages'][0]['content'])
        self.assertNotIn('livestream speech', requests[0][0]['messages'][0]['content'])

    def test_failed_target_language_is_never_returned_as_translation(self):
        def opener(*args, **kwargs):
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '全部です。'}}]}).encode())
        text, warning = translate('全部です。', Options(endpoint='http://localhost/v1'), opener=opener)
        self.assertEqual(text, '')
        self.assertIn('目标语言', warning)

    def test_label_only_output_is_not_displayed(self):
        def opener(*args, **kwargs):
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '直播中。'}}]}).encode())
        self.assertEqual(translate('係。', Options(endpoint='http://localhost/v1'), opener=opener)[0], '')

    def test_model_echo_of_retry_instruction_is_not_a_translation(self):
        outputs = iter(('全部です。', '必须改写为简体中文，不得返回原文或说明文字。只输出译文。'))
        def opener(*args, **kwargs):
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': next(outputs)}}]}).encode())
        text, warning = translate('全部です。', Options(endpoint='http://localhost/v1'), opener=opener)
        self.assertEqual(text, '')
        self.assertIn('目标语言', warning)

    def test_context_is_bounded_and_reset_on_source_change(self):
        seen = []
        def operation(text, options, key, **kwargs):
            seen.append(kwargs.get('context', ()))
            return '译文', ''
        with patch('translation.translate', side_effect=operation):
            slot = TranslationSlot(Options(endpoint='http://localhost/v1'))
            try:
                for text in ('first', 'second', 'third', 'fourth'):
                    slot.run(text)
                self.assertEqual(seen[-1], ('second', 'third'))
                self.assertEqual(tuple(slot.context), ('third', 'fourth'))
                slot.reset()
                slot.run('new source')
                self.assertEqual(seen[-1], ())
            finally:
                slot.close()

    def test_room_context_is_independent_and_close_discards_it(self):
        seen = []
        def operation(text, options, key, **kwargs):
            seen.append((text, kwargs.get('context', ())))
            return '译文', ''
        with patch('translation.translate', side_effect=operation):
            first = TranslationSlot(Options(endpoint='http://localhost/v1'))
            second = TranslationSlot(Options(endpoint='http://localhost/v1'))
            try:
                first.run('room one')
                second.run('room two')
                first.run('next one')
                second.run('next two')
                self.assertEqual(seen, [('room one', ()), ('room two', ()),
                                        ('next one', ('room one',)), ('next two', ('room two',))])
            finally:
                first.close()
                second.close()
            self.assertEqual(tuple(first.context), ())
            self.assertEqual(tuple(second.context), ())


if __name__ == '__main__':
    unittest.main()
