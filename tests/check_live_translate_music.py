"""Music-state policy, bounded audio window and caption display regression."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/live_translate'))
from music_detection import MusicState, MusicDetector, MUSIC_MARKER


class MusicStateTests(unittest.TestCase):
    def test_exact_music_marker(self):
        self.assertEqual(MUSIC_MARKER, '🎵MUSIC🎵')

    def test_sustained_music_enters_after_three_checks_and_speech_exits(self):
        state = MusicState()
        self.assertIsNone(state.update({'Music': .95, 'Speech': .02}))
        self.assertFalse(state.active)
        self.assertIsNone(state.update({'Music': .95, 'Speech': .02}))
        self.assertTrue(state.update({'Music': .95, 'Speech': .02}))
        self.assertTrue(state.active)
        self.assertIsNone(state.update({'Music': .95, 'Speech': .02}))
        self.assertFalse(state.update({'Speech': .95, 'Music': .99}))
        self.assertFalse(state.active)

    def test_vocal_entry_needs_a_recent_anchor_and_current_support(self):
        anchor = {'Female singing': .18, 'Guitar': .18, 'Conversation': .12}
        support = {'Male singing': .10, 'Music': .19, 'Speech': .04}
        ambiguous = {'Guitar': .8, 'Singing': .11}
        state = MusicState()
        self.assertIsNone(state.update(anchor))
        self.assertIsNone(state.update(ambiguous))
        self.assertTrue(state.update(support))
        # The anchor fell out of the most recent three checks.
        self.assertIs(state.update(support), False)
        self.assertIsNone(state.update(support))

    def test_support_alone_never_starts_music(self):
        for scores in ({'Singing': .1799}, {'Mantra': .7, 'Male singing': .0499},
                       {'Male singing': .4, 'Speech': .6}, {'Choir': .1874, 'Conversation': .125},
                       {'A capella': .24, 'Guitar': .25}):
            with self.subTest(scores=scores):
                state = MusicState()
                for _ in range(4):
                    self.assertIsNone(state.update(scores))
                    self.assertFalse(state.active)

    def test_two_vocal_families_can_confirm_one_complete_audio_window(self):
        state = MusicState()
        self.assertIs(state.update({'Mantra': .72, 'Male singing': .06, 'Speech': .04}), True)
        self.assertIs(state.update({'Lullaby': .28, 'Child singing': .25, 'Speech': .09}), None)
        self.assertIs(state.update({}), False)

    def test_active_singing_can_hold_below_entry_score_or_ratio(self):
        state = MusicState()
        for _ in range(3):
            state.update({'Music': .85})
        for scores in ({'Singing': .15, 'Whispering': .14},
                       {'Child singing': .251, 'Conversation': .2499}):
            self.assertIsNone(state.update(scores))
            self.assertTrue(state.active)

    def test_speech_veto_clears_the_vocal_history_at_once(self):
        entry = {'Singing': .27, 'Speech': .15}
        for lost in ({'Singing': .10, 'Conversation': .55},
                     {'Child singing': .17, 'Speech': .9}, {'Whispering': .7}):
            with self.subTest(lost=lost):
                state = MusicState()
                self.assertEqual([state.update(entry) for _ in range(2)], [None, True])
                self.assertIs(state.update(lost), False)
                self.assertEqual([state.update(entry) for _ in range(2)], [None, True])

    def test_both_handoff_branches_still_work_below_another_top_event(self):
        for scores in ({'Music': .85, 'Speech': .079, 'Guitar': .95},
                       {'Choir': .65, 'Speech': .199, 'Guitar': .95}):
            with self.subTest(scores=scores):
                state = MusicState()
                self.assertEqual([state.update(scores) for _ in range(4)], [None, None, True, None])
                self.assertTrue(state.active)

    def test_bgm_with_speech_and_single_positive_never_hide_dialogue(self):
        state = MusicState()
        for _ in range(10):
            self.assertIsNone(state.update({'Music': .99, 'Speech': .98}))
        self.assertIsNone(state.update({'Music': .9, 'Speech': .1}))
        self.assertIsNone(state.update({'Music': .1, 'Speech': .1}))
        self.assertFalse(state.active)

    def test_a_capella_can_enter_but_silence_clears_marker(self):
        state = MusicState()
        self.assertTrue(state.update({'A capella': .8, 'Speech': .01}))
        self.assertIsNone(state.update({'Singing': .8, 'Speech': .01}))
        self.assertFalse(state.update({}))

    def test_child_singing_can_start_amid_speech_and_bridge_limited_weak_windows(self):
        state = MusicState()
        mixed = {'Child singing': .26, 'Speech': .47}
        self.assertIsNone(state.update(mixed))
        self.assertTrue(state.update({'Child singing': .55, 'Speech': .31}))
        for _ in range(4):
            self.assertIsNone(state.update(mixed))
            self.assertTrue(state.active)
        self.assertIs(state.update(mixed), False)
        self.assertIsNone(state.update(mixed))

    def test_fast_release_handles_irregular_chunks_and_reset(self):
        import numpy as np
        from music_detection import FastExit
        guard = FastExit(np)
        for size in (0, 17, 1977, 2765, 320, 16000):
            self.assertFalse(guard.feed(np.zeros(size, dtype=np.float32), True))
        guard.reset()
        self.assertFalse(guard.feed(np.zeros(1599, dtype=np.float32), True))

    def test_overlapping_chant_windows_need_fresh_evidence(self):
        from music_detection import ChantState
        chant = ChantState()
        long = {'Mantra': .80, 'Speech': .04}
        recent = Mock(return_value={'Speech': .90, 'Mantra': .01})
        for _ in range(3):
            self.assertFalse(chant.update_chant(long, recent))
        recent.assert_called_once_with()
        recent.return_value = long
        self.assertTrue(chant.update_chant(long, recent))
        self.assertTrue(chant.update_chant({'Lullaby': .36, 'Speech': .01}, recent))
        self.assertFalse(chant.update_chant({'Speech': .30, 'Mantra': .70}, recent))
        self.assertFalse(chant.update_chant(long, recent))

    def test_spectral_break_alone_cannot_end_singing(self):
        detector = MusicDetector.__new__(MusicDetector)
        detector._scores = Mock(return_value={'Singing': .30, 'Music': .70})
        self.assertFalse(detector._confirm_change([], speech_only=False))
        detector._scores.return_value = {'Speech': .91, 'Music': .04}
        self.assertTrue(detector._confirm_change([], speech_only=False))
        self.assertTrue(detector._confirm_change([], speech_only=True))

    def test_short_speech_veto_only_checks_an_extension_after_confirmed_music(self):
        from music_detection import ExtensionVeto
        guard = ExtensionVeto()
        score = Mock(return_value={'Speech': .9, 'Singing': .02})
        pcm = [0] * guard.WINDOW
        self.assertTrue(guard.apply(3200, True, False, pcm, score))
        self.assertTrue(guard.apply(3200, True, True, pcm, score))
        score.assert_not_called()
        self.assertFalse(guard.apply(3200, True, False, pcm, score))
        score.assert_called_once_with(pcm)
        self.assertTrue(guard.apply(1600, True, True, pcm, score))
        self.assertFalse(guard.apply(1600, False, False, pcm, score))
        self.assertTrue(guard.apply(3200, True, False, pcm, score))
        self.assertEqual(score.call_count, 1)

    def test_audio_window_is_bounded_and_source_reset_discards_music(self):
        # NumPy is a declared runtime dependency; this check runs with that runtime.
        import numpy as np
        events = [SimpleNamespace(name='Music', prob=.99)]
        tagger = SimpleNamespace(create_stream=Mock(side_effect=lambda: SimpleNamespace(accept_waveform=Mock())),
                                 compute=Mock(return_value=events))
        sherpa = SimpleNamespace(AudioTaggingModelConfig=Mock(),
            OfflineZipformerAudioTaggingModelConfig=Mock(), AudioTaggingConfig=Mock(),
            AudioTagging=Mock(return_value=tagger))
        detector = MusicDetector('fixture', sherpa, np)
        frame = np.zeros(16000, dtype=np.float32)
        for _ in range(3):
            self.assertIsNone(detector.feed(frame))
        self.assertIsNone(detector.feed(frame))
        self.assertIsNone(detector.feed(frame))
        self.assertTrue(detector.feed(frame))
        for _ in range(8):
            detector.feed(frame)
        self.assertEqual(len(detector._window), 64000)
        self.assertEqual(tagger.compute.call_count, 11)
        detector.reset()
        self.assertFalse(detector.active)
        self.assertEqual(len(detector._window), 0)

    def test_quiet_speech_and_voice_subclasses_override_game_music(self):
        for voice in ('Speech', 'Whispering', 'Child speech, kid speaking', 'Female speech, woman speaking'):
            state = MusicState()
            for _ in range(8):
                self.assertIsNone(state.update({'Music': .99, voice: .15}))
            self.assertFalse(state.active)
        state = MusicState()
        for _ in range(8):
            self.assertIsNone(state.update({'Music': .7, 'Speech': .01}))
        self.assertFalse(state.active)

    def test_pitch_confirmation_bridges_one_hop_but_never_renews_on_weak_scores(self):
        import numpy as np
        from music_detection import FastExit

        detector = MusicDetector.__new__(MusicDetector)
        detector.np = np
        detector.fast = FastExit(np)
        detector._scores = Mock(return_value={'Speech': .50, 'Singing': .06, 'Music': .03})
        detector.reset()
        frame = np.zeros(1600, dtype=np.float32)
        for _ in range(39):
            self.assertIsNone(detector.feed(frame))
        with patch('music_detection._pitch_support', side_effect=[True, False, False]):
            self.assertIs(detector.feed(frame), True)
            for _ in range(19):
                self.assertIsNone(detector.feed(frame))
                self.assertTrue(detector.active)
            self.assertIs(detector.feed(frame), False)


if __name__ == '__main__':
    unittest.main()
