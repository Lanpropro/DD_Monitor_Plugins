"""Room/language term selection, prompt budgets and explicit-name validation."""
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/live_translate'))
from glossary import parse_terms, room_terms, matched_terms
from options import Options
from translation import translate
from text_policy import translation_matches
from prompt_policy import translation_terms


class GlossaryTests(unittest.TestCase):
    def test_room_and_target_language_are_both_required(self):
        rooms = {'youtube:one': {'简体中文': [['ぺこら', '佩克拉']], '英语': [['ぺこら', 'Pekora']]}}
        first = SimpleNamespace(platform='youtube', room_id='one')
        second = SimpleNamespace(platform='youtube', room_id='two')
        self.assertEqual(room_terms(rooms, first, '简体中文'), (('ぺこら', '佩克拉'),))
        self.assertEqual(room_terms(rooms, first, '英语'), (('ぺこら', 'Pekora'),))
        self.assertEqual(room_terms(rooms, second, '简体中文'), ())
        self.assertEqual(room_terms(rooms, first, '韩语'), ())

    def test_editor_format_is_bounded_and_rejects_ambiguous_duplicates(self):
        self.assertEqual(parse_terms('ぺこら = 佩克拉\nNintendo Switch = Nintendo Switch\n'),
                         (('ぺこら', '佩克拉'), ('Nintendo Switch', 'Nintendo Switch')))
        for invalid in ('missing separator', 'a = one\na = two', 'a = ', 'a' * 41 + ' = b',
                        '\n'.join(f'a{i} = b' for i in range(21))):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_terms(invalid)

    def test_only_matching_terms_are_selected_with_latin_word_boundaries(self):
        entries = (('go', '围棋'), ('FF14', '最终幻想14'), ('ぺこら', '佩克拉'))
        self.assertEqual(matched_terms(entries, 'good FF14を遊ぶ'), (('FF14', '最终幻想14'),))
        self.assertEqual(matched_terms(entries, 'GO! ぺこら'), (('go', '围棋'), ('ぺこら', '佩克拉')))

    def test_normal_english_and_quoted_foreign_words_cannot_masquerade_as_names(self):
        for source, output in [('This is good.', 'This 真好。'), ('「very cute」だね。', '这很「very cute」。')]:
            self.assertFalse(translation_matches(output, source, '简体中文'))
        terms = (('Nintendo Switch', 'Nintendo Switch'),)
        self.assertTrue(translation_matches('今天玩Nintendo Switch。', 'Nintendo Switchで遊ぶ。', '简体中文', terms))
        self.assertFalse(translation_matches('今天很Nintendo Switch。', '今日は楽しい。', '简体中文', terms))

    def test_prompt_and_validation_use_only_terms_for_the_selected_language(self):
        requests = []
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '今天玩FF14。'}}]}).encode())
        options = Options(endpoint='http://localhost/v1', glossary_language='简体中文',
                          glossary=(('FF14', 'FF14'), ('Unused', 'Unrelated')))
        self.assertEqual(translate('今日はFF14を遊ぶ。', options, opener=opener), ('今天玩FF14。', ''))
        prompt = requests[0]['messages'][0]['content']
        self.assertIn('FF14 = FF14', prompt)
        self.assertNotIn('Unused', prompt)
        mismatch = Options(endpoint='http://localhost/v1', glossary_language='英语', glossary=options.glossary)
        self.assertEqual(translate('今日はFF14を遊ぶ。', mismatch, opener=opener)[0], '')
        self.assertNotIn('术语', requests[-1]['messages'][0]['content'])

    def test_local_terms_have_a_separate_small_prompt_budget(self):
        entries = tuple((f'Term{i}', '译' * 20) for i in range(20))
        options = Options(glossary_language='简体中文', glossary=entries)
        terms = translation_terms(options, ' '.join(source for source, _target in entries))
        self.assertLessEqual(len(terms), 6)
        self.assertLessEqual(sum(len(' = '.join(term)) + 1 for term in terms), 120)


if __name__ == '__main__':
    unittest.main()
