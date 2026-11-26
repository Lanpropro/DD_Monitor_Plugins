"""Audio-boundary, draft/final and stale-caption functional regression."""
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/live_translate'))
from speech_segments import SpeechSegments
from caption_progress import CaptionProgress
from options import Options
from speech_pipeline import SpeechPipeline


class SpeechSegmentTests(unittest.TestCase):
    def test_short_fragment_waits_for_continuation_and_retains_the_gap(self):
        segments = SpeechSegments(np)
        first = np.ones(6400, dtype=np.float32)
        draft, = segments.accept(0, first, finished=True, now=0)
        self.assertFalse(draft.final)
        self.assertEqual(segments.poll(0.9), [])
        final, = segments.accept(9600, np.full(16000, 2, dtype=np.float32), finished=True, now=0.9)
        self.assertTrue(final.final)
        self.assertEqual(final.segment_id, draft.segment_id)
        np.testing.assert_array_equal(final.samples[:6400], first)
        np.testing.assert_array_equal(final.samples[6400:9600], np.zeros(3200))
        np.testing.assert_array_equal(final.samples[9600:], np.full(16000, 2))

    def test_short_fragment_finalizes_within_hold_limit_even_without_new_pcm(self):
        segments = SpeechSegments(np)
        draft, = segments.accept(0, np.ones(6400), finished=True, now=10)
        final, = segments.poll(11)
        self.assertTrue(final.final)
        self.assertEqual(final.segment_id, draft.segment_id)
        self.assertGreater(final.revision, draft.revision)
        self.assertEqual(segments.poll(30), [])

    def test_forced_cut_prefers_a_pause_and_carries_every_boundary_sample_once(self):
        segments = SpeechSegments(np)
        audio = np.ones(96000, dtype=np.float32)
        audio[72000:75200] = 0
        final, = segments.accept(0, audio, finished=False, now=0)
        self.assertTrue(final.final)
        self.assertGreaterEqual(len(final.samples), 72000)
        self.assertLessEqual(len(final.samples), 75200)
        # Repeated VAD snapshots include already committed samples.
        continuation = np.concatenate((audio, np.full(8000, 2, dtype=np.float32)))
        segments.accept(0, continuation, finished=False, now=0.5)
        tail, = segments.poll(1.5)
        np.testing.assert_array_equal(np.concatenate((final.samples, tail.samples)), continuation)
        self.assertEqual(tail.start, len(final.samples))
        self.assertGreater(tail.segment_id, final.segment_id)

    def test_continuous_speech_is_bounded_and_drafts_are_spaced(self):
        segments = SpeechSegments(np)
        jobs = []
        for size in range(3200, 112001, 3200):
            jobs += segments.accept(0, np.ones(size), finished=False, now=size / 16000)
        finals = [job for job in jobs if job.final]
        self.assertEqual(len(finals), 1)
        self.assertLessEqual(len(finals[0].samples), 96000)
        drafts = [job for job in jobs if not job.final and job.segment_id == finals[0].segment_id]
        self.assertLessEqual(len(drafts), 5)
        self.assertLessEqual(len(segments.samples), 96000)

    def test_reset_discards_pending_audio_and_rejects_no_new_source_audio(self):
        segments = SpeechSegments(np)
        segments.accept(5000, np.ones(6400), finished=True, now=0)
        segments.reset()
        self.assertEqual(segments.poll(2), [])
        result, = segments.accept(0, np.full(24000, 2), finished=True, now=3)
        self.assertTrue(result.final)
        self.assertEqual(result.start, 0)
        np.testing.assert_array_equal(result.samples, np.full(24000, 2))

    def test_completed_vad_snapshot_finalizes_an_unchanged_draft(self):
        segments = SpeechSegments(np)
        audio = np.ones(24000)
        draft, = segments.accept(0, audio, finished=False, now=0)
        final, = segments.accept(0, audio, finished=True, now=0.4)
        self.assertFalse(draft.final)
        self.assertTrue(final.final)
        np.testing.assert_array_equal(draft.samples, final.samples)


class SpeechPipelineTests(unittest.TestCase):
    def make_pipeline(self):
        self.translator = SimpleNamespace(run=Mock(return_value=('译文', '')), reset=Mock())
        return SpeechPipeline(Options(), np, Mock(), object(), threading.Lock(), self.translator)

    def test_draft_never_calls_translation_and_final_reuses_identical_asr(self):
        pipeline = self.make_pipeline()
        draft, = pipeline.segments.accept(0, np.ones(6400), finished=True, now=0)
        final, = pipeline.segments.poll(1)
        with patch('recognition.recognize', return_value='原文') as recognize:
            first = pipeline.process(draft, lambda: True)
            self.assertEqual(first['phase'], 'draft')
            self.translator.run.assert_not_called()
            second = pipeline.process(final, lambda: True)
            self.assertEqual(second['phase'], 'final')
            self.translator.run.assert_called_once_with('原文')
            recognize.assert_called_once()

    def test_source_change_during_decode_prevents_translation_and_caption(self):
        pipeline = self.make_pipeline()
        job, = pipeline.segments.accept(0, np.ones(24000), finished=True, now=0)
        active = [True]
        def decode(*_args):
            active[0] = False
            return '旧原文'
        with patch('recognition.recognize', side_effect=decode):
            self.assertIsNone(pipeline.process(job, lambda: active[0]))
        self.translator.run.assert_not_called()
        pipeline.reset()
        self.assertIsNone(pipeline.cached)
        self.translator.reset.assert_called_once()


class CaptionProgressTests(unittest.TestCase):
    def test_draft_retains_last_translation_but_cannot_overwrite_final(self):
        state = CaptionProgress()
        self.assertEqual(state.accept(dict(phase='final', segment_id=1, revision=2,
                                           text='原文一', translated='译文一')), ('原文一', '译文一'))
        self.assertEqual(state.accept(dict(phase='draft', segment_id=2, revision=1,
                                           text='原文二')), ('原文二 …', '译文一'))
        self.assertEqual(state.accept(dict(phase='final', segment_id=2, revision=2,
                                           text='完整原文二', translated='译文二')), ('完整原文二', '译文二'))
        for result in (dict(phase='draft', segment_id=2, revision=3, text='迟到草稿'),
                       dict(phase='draft', segment_id=1, revision=9, text='旧句'),
                       dict(phase='final', segment_id=2, revision=1, text='旧修订')):
            self.assertIsNone(state.accept(result))

    def test_clear_discards_translation_and_sequence_for_new_source(self):
        state = CaptionProgress()
        state.accept(dict(phase='final', segment_id=9, revision=1, text='旧源', translated='旧译文'))
        state.reset()
        self.assertEqual(state.accept(dict(phase='draft', segment_id=1, revision=1, text='新源')), ('新源 …', ''))


if __name__ == '__main__':
    unittest.main()
