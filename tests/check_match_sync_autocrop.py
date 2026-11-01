"""Checks the webcam auto-crop detector, its tracker and the viewer wiring."""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.match_sync import autocrop  # noqa: E402

CORNERS = {
    "top-left": (0.02, 0.03, 0.26, 0.42),
    "top-right": (0.70, 0.05, 0.27, 0.40),
    "bottom-left": (0.03, 0.55, 0.24, 0.40),
    "bottom-right": (0.68, 0.52, 0.28, 0.42),
    "middle-right": (0.66, 0.30, 0.30, 0.34),
}


def scene(rect=None, seed=7, size=(320, 180)):
    """A noisy "game" frame with an optional bordered webcam window."""
    width, height = size
    rng = np.random.default_rng(seed)
    image = rng.integers(30, 90, (height, width)).astype(np.float32)
    for _ in range(40):
        x = rng.integers(0, width - 20)
        y = rng.integers(0, height - 12)
        image[y:y + rng.integers(4, 12), x:x + rng.integers(10, 40)] = rng.integers(90, 220)
    if rect is not None:
        x, y, ratio_w, ratio_h = rect
        left, top = int(x * width), int(y * height)
        right, bottom = left + int(ratio_w * width), top + int(ratio_h * height)
        image[top:bottom, left:right] = 150 + rng.normal(0, 3, (bottom - top, right - left))
        centre_x, centre_y = (left + right) // 2, (top + bottom) // 2
        image[centre_y - 8:centre_y + 12, centre_x - 6:centre_x + 6] = 90
        image[top:top + 2, left:right] = 240
        image[bottom - 2:bottom, left:right] = 240
        image[top:bottom, left:left + 2] = 240
        image[top:bottom, right - 2:right] = 240
    return np.clip(image, 0, 255).astype(np.uint8)


class DetectTests(unittest.TestCase):
    def test_finds_the_window_in_every_position(self):
        for name, wanted in CORNERS.items():
            with self.subTest(corner=name):
                found, score = autocrop.detect(scene(wanted), faces=False)
                self.assertIsNotNone(found, msg=f"{name} not detected")
                self.assertGreaterEqual(autocrop._iou(found, wanted), 0.85)
                self.assertGreater(score, autocrop.SCORE_FLOOR)

    def test_ignores_frames_without_a_window(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                found, score = autocrop.detect(scene(None, seed=seed), faces=False)
                self.assertIsNone(found)
                self.assertLess(score, autocrop.SCORE_FLOOR)

    def test_small_or_flat_frames_are_ignored(self):
        self.assertEqual(autocrop.detect(np.zeros((8, 8), dtype=np.uint8)), (None, 0.0))
        self.assertEqual(autocrop.detect(np.full((180, 320), 40, dtype=np.uint8), faces=False),
                         (None, 0.0))

    def test_face_fallback_when_no_border_is_found(self):
        original = autocrop._face_boxes
        autocrop._face_boxes = lambda gray: [(246, 40, 22, 26)]
        try:
            found, score = autocrop.detect(np.zeros((180, 320), dtype=np.uint8), faces=True)
        finally:
            autocrop._face_boxes = original
        self.assertIsNotNone(found)
        self.assertGreater(score, 0.0)
        left, top, width, height = found
        self.assertTrue(left <= 246 / 320 <= left + width)
        self.assertTrue(top <= 40 / 180 <= top + height)
        self.assertAlmostEqual(width * 320 / (height * 180), 16 / 9, places=1)

    def test_clamp_keeps_the_crop_inside_the_frame(self):
        self.assertEqual(autocrop.clamp(None), autocrop.FULL)
        self.assertEqual(autocrop.clamp((float("nan"), 0, 1, 1)), autocrop.FULL)
        for got, want in zip(autocrop.clamp((-0.5, 0.9, 2.0, 0.5)), (0.0, 0.9, 1.0, 0.1)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertEqual(autocrop.clamp((0.2, 0.2, 0.3, 0.3)), (0.2, 0.2, 0.3, 0.3))


class MatchTests(unittest.TestCase):
    def test_tracks_the_window_after_it_moves(self):
        before = scene(CORNERS["top-right"])
        after = scene(CORNERS["top-right"])
        moved = np.roll(after, 6, axis=1)
        rect, quality = autocrop.match(before, moved, CORNERS["top-right"])
        self.assertIsNotNone(rect, msg=f"correlation too low: {quality}")
        self.assertGreater(quality, 0.6)
        self.assertAlmostEqual(rect[0] * 320, CORNERS["top-right"][0] * 320 + 6, delta=3)

    def test_unrelated_frames_do_not_match(self):
        rect, quality = autocrop.match(scene(CORNERS["top-right"]), scene(None, seed=5),
                                       CORNERS["top-right"])
        self.assertLess(quality, 0.9)
        if rect is not None:
            self.assertLess(quality, 0.6)


class FollowerTests(unittest.TestCase):
    def test_first_hit_is_used_at_once(self):
        follower = autocrop.Follower()
        found = follower.update(scene(CORNERS["top-right"]), 0.0)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(autocrop._iou(found, CORNERS["top-right"]), 0.85)
        self.assertEqual(follower.rect, found)

    def test_throttles_between_passes(self):
        follower = autocrop.Follower(interval=1.5)
        follower.update(scene(CORNERS["top-right"]), 10.0)
        self.assertFalse(follower.due(10.4))
        self.assertTrue(follower.due(11.5))

    def test_follows_a_shifted_window_without_re_detecting(self):
        follower = autocrop.Follower(interval=0)
        first = follower.update(scene(CORNERS["top-right"]), 0.0)
        self.assertIsNotNone(first)
        shifted = np.roll(scene(CORNERS["top-right"]), 5, axis=0)
        found = follower.update(shifted, 1.0)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found[1] * 180, first[1] * 180 + 5, delta=3)
        self.assertAlmostEqual(found[2], first[2], places=6)

    def test_seeded_crop_is_kept_until_tracking_fails(self):
        follower = autocrop.Follower(interval=0)
        follower.rect = CORNERS["top-right"]
        first = follower.update(scene(CORNERS["top-right"]), 0.0)
        self.assertIsNotNone(first)
        self.assertGreaterEqual(autocrop._iou(first, CORNERS["top-right"]), 0.8)

    def test_four_misses_release_the_lock(self):
        follower = autocrop.Follower(interval=0)
        follower.update(scene(CORNERS["top-right"]), 0.0)
        for step in range(5):
            self.assertIsNone(follower.update(scene(None, seed=step), step + 1.0))
        self.assertIsNone(follower.rect)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "plugins/match_sync/viewer.py").read_text(encoding="utf-8")

    def test_the_row_exposes_the_toggle(self):
        self.assertIn('QCheckBox("自动跟随摄像头")', self.source)
        self.assertIn("self.pip_follow.toggled.connect(self._pip_follow_toggled)", self.source)
        self.assertIn("self.pip_row.addWidget(self.pip_follow)", self.source)

    def test_the_detected_crop_wins_while_following(self):
        self.assertIn("if self.pip_follow.isChecked() and self.pip_auto_crop is not None:", self.source)
        self.assertIn("self._follow_pip_crop(picture)", self.source)

    def test_the_toggle_is_persisted(self):
        self.assertIn('"follow": self.pip_follow.isChecked(),', self.source)

    def test_detection_runs_on_a_small_grayscale_copy(self):
        self.assertIn("QImage.Format_Grayscale8", self.source)
        self.assertIn("def _pip_thumb(", self.source)

    def test_manual_crop_resets_the_detector(self):
        self.assertIn("self.pip_follower.reset()", self.source)

    def test_following_starts_from_a_manual_crop(self):
        self.assertIn("self.pip_follower.rect = crop", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
