"""Bounded suspicious-result review, cancellation and genuine speech retention."""
import io
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/live_translate'))
from options import Options
from quality_policy import recognition_issues
from speech_pipeline import SpeechPipeline
from translation import translate
from caption_progress import CaptionProgress


class RecognitionReviewTests(unittest.TestCase):
    def test_draft_decode_is_skipped_when_new_pcm_is_waiting(self):
        pipeline = self.pipeline()
        job = pipeline.segments.accept(0, np.ones(24000), finished=False, now=0)[0]
        with patch('recognition.recognize') as decode:
            self.assertIsNone(pipeline.process(job, lambda: True, can_review=lambda: False))
        decode.assert_not_called()
        pipeline.translator.run.assert_not_called()

    def pipeline(self):
        translator = SimpleNamespace(run=Mock(return_value=('译文', '')), reset=Mock(), review={})
        return SpeechPipeline(Options(), np, Mock(), object(), threading.Lock(), translator)

    def job(self, pipeline):
        return pipeline.segments.accept(0, np.ones(64000), finished=True, now=0)[0]

    def test_long_audio_with_short_text_gets_only_one_padded_review(self):
        pipeline = self.pipeline()
        with patch('recognition.recognize', side_effect=['あ', '今日はみんなと遊びます。']) as decode:
            result = pipeline.process(self.job(pipeline), lambda: True)
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(len(decode.call_args_list[1].args[1]), 67200)
        self.assertEqual(result['text'], '今日はみんなと遊びます。')
        self.assertEqual(result['asr_review_attempts'], 1)
        self.assertIn('long_audio_short_text', result['quality_reasons'])
        pipeline.translator.run.assert_called_once_with('今日はみんなと遊びます。')

    def test_backlog_and_busy_shared_model_skip_review(self):
        for backlog in (True, False):
            pipeline = self.pipeline()
            # Hold the real lock only when a nonblocking review tries to take it.
            class Lock:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def acquire(self, blocking=False): return False
            if not backlog:
                pipeline.decode_lock = Lock()
            with patch('recognition.recognize', return_value='あ') as decode:
                result = pipeline.process(self.job(pipeline), lambda: True, can_review=lambda: not backlog)
            decode.assert_called_once()
            self.assertEqual(result['asr_review_attempts'], 0)
            self.assertEqual(result['asr_review_skipped'], 'backlog' if backlog else 'shared_model_busy')

    def test_elapsed_budget_prevents_an_extra_decode(self):
        pipeline = self.pipeline()
        clock = [0]
        def decode(*args):
            clock[0] = 1.1
            return 'あ'
        with patch('speech_pipeline.time.monotonic', side_effect=lambda: clock[0]), \
                patch('recognition.recognize', side_effect=decode) as recognise:
            result = pipeline.process(self.job(pipeline), lambda: True)
        recognise.assert_called_once()
        self.assertEqual(result['asr_review_skipped'], 'deadline')
        self.assertEqual(result['asr_review_attempts'], 0)

    def test_same_long_sentence_three_times_is_a_signal_without_a_phrase_blacklist(self):
        sentence = '今日はみんなと一緒に遊びます。'
        self.assertIn('repeated_segment', recognition_issues(sentence, 4, (sentence, sentence)))
        self.assertEqual(recognition_issues(sentence, 4, ('違う内容。', sentence)), [])

    def test_unchanged_suspicious_speech_is_preserved_instead_of_blacklisted(self):
        self.assertEqual(recognition_issues('谢谢观看。', 4, ()), [])
        pipeline = self.pipeline()
        repeated = 'ありがとうありがとうありがとう'
        with patch('recognition.recognize', return_value=repeated) as decode:
            result = pipeline.process(self.job(pipeline), lambda: True)
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(result['text'], repeated)
        self.assertEqual(result['asr_review_attempts'], 1)

    def test_empty_result_is_reported_without_blank_caption_and_history_resets(self):
        pipeline = self.pipeline()
        with patch('recognition.recognize', return_value='') as decode:
            result = pipeline.process(self.job(pipeline), lambda: True)
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(result['event'], 'quality_review')
        self.assertIn('empty_recognition', result['quality_reasons'])
        pipeline.translator.run.assert_not_called()
        pipeline.previous.append('旧内容')
        pipeline.reset()
        self.assertEqual(tuple(pipeline.previous), ())
        progress = CaptionProgress()
        progress.accept(dict(phase='final', segment_id=1, revision=1, text='第一句', translated='译文一'))
        self.assertEqual(progress.accept(result), ())
        self.assertEqual(progress.translated, '译文一')
        self.assertIsNone(progress.accept(dict(phase='draft', segment_id=result['segment_id'], revision=9, text='迟到')))

    def test_review_that_finishes_after_source_change_cannot_translate(self):
        pipeline = self.pipeline()
        active = [True]
        def decode(_recognizer, samples, _language):
            if len(samples) > 64000:
                active[0] = False
            return 'あ'
        with patch('recognition.recognize', side_effect=decode):
            self.assertIsNone(pipeline.process(self.job(pipeline), lambda: active[0]))
        pipeline.translator.run.assert_not_called()


class TranslationReviewTests(unittest.TestCase):
    def test_empty_wrong_language_and_new_repetition_are_bounded_and_recorded(self):
        for first, reason in [('', 'empty_translation'), ('こんにちは。', 'wrong_language'),
                              ('很可爱很可爱很可爱', 'repeated_translation'),
                              ('只翻译当前句一次，不重复前文或续写。', 'instruction_echo')]:
            requests = []
            def opener(request, timeout):
                requests.append(timeout)
                output = first if len(requests) == 1 else '很可爱。'
                return io.BytesIO(json.dumps({'choices': [{'message': {'content': output}}]}).encode())
            review = {}
            self.assertEqual(translate('かわいい。', Options(endpoint='http://localhost/v1'),
                                       opener=opener, diagnostics=review), ('很可爱。', ''))
            self.assertEqual(review, dict(attempts=2, reasons=[reason]))
            self.assertLessEqual(requests[1], requests[0])

    def test_source_change_after_first_response_prevents_retry(self):
        active, calls, review = [True], [], {}
        def opener(request, timeout):
            calls.append(request)
            active[0] = False
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': 'こんにちは。'}}]}).encode())
        translate('こんにちは。', Options(endpoint='http://localhost/v1'), opener=opener,
                  current=lambda: active[0], diagnostics=review)
        self.assertEqual(len(calls), 1)
        self.assertEqual(review['attempts'], 1)

    def test_repeated_real_speech_is_not_deleted(self):
        def opener(*args, **kwargs):
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '谢谢谢谢谢谢。'}}]}).encode())
        self.assertEqual(translate('ありがとうありがとうありがとう。', Options(endpoint='http://localhost/v1'),
                                   opener=opener), ('谢谢谢谢谢谢。', ''))

    def test_deadline_prevents_retry_after_a_slow_first_response(self):
        clock, calls, review = [0], [], {}
        def opener(request, timeout):
            calls.append(request)
            clock[0] = 2
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': ''}}]}).encode())
        with patch('translation.time.monotonic', side_effect=lambda: clock[0]):
            self.assertEqual(translate('こんにちは。', Options(endpoint='http://localhost/v1'), opener=opener,
                                       diagnostics=review)[0], '')
        self.assertEqual(len(calls), 1)
        self.assertEqual(review['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
