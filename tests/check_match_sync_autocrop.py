"""Checks the webcam auto-crop detector and the viewer wiring around it."""

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


def scaled(gray, factor):
    height, width = gray.shape[:2]
    rows = np.repeat(np.arange(height), factor)
    cols = np.repeat(np.arange(width), factor)
    return gray[np.ix_(rows, cols)]


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

    def test_the_big_frame_wins_when_both_resolutions_agree(self):
        small = scene(CORNERS["top-right"])
        big = scaled(small, 2)
        found, score = autocrop.detect_best([big, small], faces=False)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(autocrop._iou(found, CORNERS["top-right"]), 0.85)
        self.assertGreater(score, autocrop.SCORE_FLOOR)

    def test_detect_best_skips_empty_frames(self):
        self.assertEqual(autocrop.detect_best([None, np.zeros((0, 0), dtype=np.uint8)]),
                         (None, 0.0))

    def test_borders_join_the_two_pixels_of_a_border_line(self):
        frame = autocrop.analyse(scene(CORNERS["top-right"]))
        positions = [line.pos for line in frame["columns"]]
        self.assertTrue(any(abs(pos - 224) <= 2 for pos in positions), msg=positions)
        self.assertTrue(any(abs(pos - 308) <= 2 for pos in positions), msg=positions)


class SnapTests(unittest.TestCase):
    def test_a_loose_box_is_pulled_in(self):
        wanted = CORNERS["top-right"]
        gray = scene(wanted)
        loose = (wanted[0] - 0.035, wanted[1] - 0.035, wanted[2] + 0.05, wanted[3] + 0.05)
        found = autocrop.snap(gray, loose)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(autocrop._iou(found, wanted), 0.85)

    def test_a_tight_box_is_pushed_out(self):
        wanted = CORNERS["bottom-left"]
        gray = scene(wanted)
        tight = (wanted[0] + 0.03, wanted[1] + 0.03, wanted[2] - 0.06, wanted[3] - 0.06)
        found = autocrop.snap(gray, tight)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(autocrop._iou(found, wanted), 0.8)

    def test_a_flat_frame_keeps_the_box(self):
        flat = np.full((180, 320), 90, np.float32)
        self.assertEqual(autocrop.snap(flat, (0.2, 0.2, 0.3, 0.3)), (0.2, 0.2, 0.3, 0.3))

    def test_the_dialog_snaps_after_a_drag(self):
        self.source = (ROOT / "plugins/match_sync/viewer.py").read_text(encoding="utf-8")
        self.assertIn("self.snap_gray = None", self.source)
        self.assertIn("if self.selecting and self.snap_gray is not None and event.button() == Qt.LeftButton:", self.source)
        self.assertIn("def _snap_selection(self):", self.source)
        self.assertIn("canvas.snap_gray = self._pip_thumb(", self.source)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "plugins/match_sync/viewer.py").read_text(encoding="utf-8")

    def test_the_row_exposes_the_auto_crop_button(self):
        self.assertIn('QPushButton("自动框选")', self.source)
        self.assertIn("self.pip_auto_button.clicked.connect(self._auto_pip_crop_clicked)", self.source)
        self.assertIn("self.pip_row.addWidget(self.pip_auto_button)", self.source)

    def test_the_button_runs_the_detector(self):
        self.assertIn("def _auto_pip_crop_for(", self.source)
        self.assertIn("detect_best(frames, faces=True)", self.source)
        self.assertIn("snapped = snap_auto_crop(frames[0], rect)", self.source)
        self.assertIn("row.pip_crop = clamp_auto_crop(snapped if snapped is not None else rect)", self.source)

    def test_the_first_enable_detects_once(self):
        self.assertIn("QTimer.singleShot(1200, self._auto_pip_first)", self.source)
        self.assertIn("self.auto_pip_done = False", self.source)

    def test_detection_runs_on_a_small_grayscale_copy(self):
        self.assertIn("QImage.Format_Grayscale8", self.source)
        self.assertIn("def _pip_thumb(", self.source)

    def test_following_is_gone(self):
        for text in ("pip_follow", "自动跟随摄像头", "pip_follower"):
            self.assertNotIn(text, self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
