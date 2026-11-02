"""Checks for the talker camera detector behind the "自动框选" button."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "match_sync_autocrop", ROOT / "plugins" / "match_sync" / "autocrop.py")
AUTOCROP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUTOCROP
SPEC.loader.exec_module(AUTOCROP)


def picture(width=320, height=180, window=(60, 34, 200, 112), level=190):
    """A flat dark frame with one bright rectangle standing in for the window."""
    frame = np.full((height, width), 28, dtype=np.uint8)
    x, y, w, h = window
    frame[y:y + h, x:x + w] = level
    return frame


def overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0.0
    common = (right - left) * (bottom - top)
    return common / float(aw * ah + bw * bh - common)


class DetectorTests(unittest.TestCase):
    def test_no_frames_is_no_answer(self):
        self.assertEqual(AUTOCROP.detect([], faces=True), (None, 0.0))
        self.assertEqual(AUTOCROP.detect([None, None], faces=True), (None, 0.0))

    def test_a_flat_frame_has_no_camera_inside(self):
        flat = np.full((180, 320), 24, dtype=np.uint8)
        rect, confidence = AUTOCROP.detect([flat, flat.copy(), flat.copy()], faces=True)
        self.assertIsNone(rect)
        self.assertEqual(confidence, 0.0)

    def test_faces_can_be_switched_off(self):
        frame = picture()
        self.assertEqual(AUTOCROP.detect([frame], faces=False), (None, 0.0))

    def test_the_window_around_a_face_becomes_the_crop(self):
        frame = picture()
        face = (150.0, 74.0, 32.0, 32.0)
        with mock.patch.object(AUTOCROP, "_face_clusters",
                               lambda frames: [{"box": face, "frames": len(frames)}]):
            rect, confidence = AUTOCROP.detect([frame, frame.copy()], faces=True)
        self.assertIsNotNone(rect)
        found = (rect[0] * 320, rect[1] * 180, rect[2] * 320, rect[3] * 180)
        self.assertGreaterEqual(overlap(found, (60, 34, 200, 112)), 0.55)
        self.assertGreater(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_the_confirmed_frames_raise_the_confidence(self):
        frame = picture()
        face = (150.0, 74.0, 32.0, 32.0)
        with mock.patch.object(AUTOCROP, "_face_clusters",
                               lambda frames: [{"box": face, "frames": len(frames)}]):
            _rect, twice = AUTOCROP.detect([frame, frame.copy()], faces=True)
            with mock.patch.object(AUTOCROP, "_face_clusters",
                                   lambda frames: [{"box": face, "frames": 1}]):
                _rect, once = AUTOCROP.detect([frame, frame.copy()], faces=True)
        self.assertGreater(twice, once)

    def test_a_half_outside_face_gives_no_box(self):
        frame = picture(width=64, height=64, window=(0, 0, 64, 64))
        face = (4.0, 4.0, 40.0, 40.0)
        with mock.patch.object(AUTOCROP, "_face_clusters",
                               lambda frames: [{"box": face, "frames": len(frames)}]):
            rect, _confidence = AUTOCROP.detect([frame], faces=True)
        self.assertIsNone(rect)


class GeometryTests(unittest.TestCase):
    def test_a_crop_stays_inside_the_picture(self):
        rect = AUTOCROP.clamp((-0.4, -0.2, 1.6, 1.4))
        self.assertGreaterEqual(rect[0], 0.0)
        self.assertGreaterEqual(rect[1], 0.0)
        self.assertLessEqual(rect[0] + rect[2], 1.0001)
        self.assertLessEqual(rect[1] + rect[3], 1.0001)

    def test_a_tiny_crop_is_widened_to_the_minimum(self):
        x, y, w, h = AUTOCROP.clamp((0.4, 0.4, 0.001, 0.001))
        self.assertGreaterEqual(w, AUTOCROP.MIN_WIDTH - 1e-6)
        self.assertGreaterEqual(h, AUTOCROP.MIN_HEIGHT - 1e-6)

    def test_snap_pulls_an_edge_onto_the_border(self):
        frame = picture()
        # A box eight pixels too narrow around the bright rectangle.
        loose = (68.0 / 320, 42.0 / 180, 178.0 / 320, 92.0 / 180)
        snapped = AUTOCROP.snap(frame, loose, tolerance=0.05)
        self.assertIsNotNone(snapped)
        found = (snapped[0] * 320, snapped[1] * 180, snapped[2] * 320, snapped[3] * 180)
        self.assertGreaterEqual(overlap(found, (60, 34, 200, 112)), 0.70)

    def test_snap_only_moves_onto_a_strong_edge(self):
        flat = np.full((180, 320), 40, dtype=np.uint8)
        snapped = AUTOCROP.snap(flat, (0.2, 0.2, 0.4, 0.4), tolerance=0.03)
        self.assertEqual(tuple(round(value, 6) for value in snapped), (0.2, 0.2, 0.4, 0.4))

    def test_the_border_score_needs_three_sides(self):
        frame = picture()
        inside = (60.0 / 320, 40.0 / 180, 190.0 / 320, 100.0 / 180)
        hugging = (0.0, 40.0 / 180, 190.0 / 320, 100.0 / 180)
        self.assertGreater(AUTOCROP._border_score(frame, inside), 0.0)
        self.assertGreaterEqual(AUTOCROP._border_score(frame, hugging), 0.0)

    def test_the_seed_follows_the_face_and_stays_inside(self):
        rect = AUTOCROP._seed((300.0, 10.0, 30.0, 30.0), 3.6, 320.0, 180.0)
        self.assertIsNotNone(rect)
        self.assertGreaterEqual(rect[0], 0.0)
        self.assertGreaterEqual(rect[1], 0.0)
        self.assertLessEqual(rect[0] + rect[2], 1.0001)
        self.assertLessEqual(rect[1] + rect[3], 1.0001)
        self.assertGreaterEqual(rect[2], AUTOCROP.MIN_WIDTH)

    def test_a_wider_face_means_a_larger_window(self):
        small = AUTOCROP._seed((40.0, 40.0, 20.0, 20.0), 3.6, 320.0, 180.0)
        large = AUTOCROP._seed((40.0, 40.0, 40.0, 40.0), 3.6, 320.0, 180.0)
        self.assertGreater(large[2] * large[3], small[2] * small[3])

    def test_the_cascades_are_optional(self):
        self.assertIsInstance(AUTOCROP.available(), bool)


if __name__ == "__main__":
    unittest.main()
