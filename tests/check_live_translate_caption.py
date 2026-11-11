"""Offline caption surface checks; visual ordering still needs a real host window."""
import argparse
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QImage, QPainter
from ddm import theme
from ddm.tile_widgets import Tile
from ddm.video_caption import CaptionStyle, VideoCaption

APP = QApplication.instance() or QApplication([])


class CaptionSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tile = QWidget()
        self.tile.resize(800, 486)
        self.video = QWidget(self.tile)
        self.video.setGeometry(0, 0, 800, 450)
        self.bottom = QWidget(self.tile)
        self.bottom.setGeometry(0, 450, 800, 36)
        self.bottom.tile_y = lambda: 450
        self.tile.video = self.video
        self.tile.bottom = self.bottom
        self.tile.setProperty('fullscreen', False)
        self.tile.show()
        APP.processEvents()

    def tearDown(self):
        self.tile.close()
        self.tile.deleteLater()
        APP.processEvents()

    def test_style_is_bounded_and_text_combination_is_explicit(self):
        style = CaptionStyle.from_value(
            {'mode': 'translation', 'lines': 99, 'opacity': -1, 'width': 2,
             'height': 90, 'font_size': 999})
        self.assertEqual((style.mode, style.lines, style.opacity, style.width,
                          style.height, style.font_size),
                         ('translation', 4, 0, 20, 45, 64))

    def test_line_budget_and_independent_visibility(self):
        caption = VideoCaption(self.tile)
        caption.set_caption('原文', '译文', style={'lines': 3})
        self.assertEqual(caption._line_budgets(['original', 'translation']),
                         {'original': 2, 'translation': 1})
        caption.set_caption_visible(False)
        self.assertFalse(caption.isVisible())
        caption.set_caption_visible(True)
        self.assertTrue(caption.isVisible())

    def test_caption_shows_both_texts_and_hides_for_pip(self):
        caption = VideoCaption(self.tile)
        caption.set_caption('原文', '译文')
        APP.processEvents()
        self.assertTrue(caption.isVisible())
        self.assertEqual([key for key, _text in caption._texts()], ['original', 'translation'])
        self.assertEqual(caption._font(100, 2).family(), theme.danmaku_font_family())
        caption.set_caption('原文', '译文', style={'font_size': 36})
        self.assertEqual(caption._font(300, 1).pixelSize(), 36)
        self.tile.setProperty('pictureInPicture', True)
        caption.sync_geometry()
        self.assertFalse(caption.isVisible())

    def test_background_follows_text_width_instead_of_the_whole_box(self):
        caption = VideoCaption(self.tile)
        caption.set_caption('短', '', style={'mode': 'original', 'opacity': 100,
                                            'width': 100, 'height': 45,
                                            'font_size': 24})
        image = QImage(caption.width(), caption.height(), QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        caption._render_badge(painter)
        painter.end()
        middle = image.height() // 2
        self.assertEqual(image.pixelColor(1, middle).alpha(), 0)
        self.assertEqual(image.pixelColor(image.width() - 2, middle).alpha(), 0)
        self.assertGreater(image.pixelColor(image.width() // 2, middle).alpha(), 0)

    def test_playback_bar_position_reduces_safe_caption_geometry(self):
        caption = VideoCaption(self.tile)
        caption.set_caption('原文', '译文', style={'height': 45, 'width': 100})
        APP.processEvents()
        self.tile.setProperty('fullscreen', True)
        self.bottom.tile_y = lambda: 300
        caption.sync_geometry()
        self.assertLessEqual(caption.geometry().bottom(), 300 - VideoCaption.GAP)
        self.tile.setProperty('fullscreen', False)

    def test_clear_caption_removes_text_and_hides_surface(self):
        caption = VideoCaption(self.tile)
        caption.set_caption('原文', '译文')
        caption.clear_caption()
        self.assertEqual(caption._texts(), [])
        self.assertFalse(caption.isVisible())

    def test_real_tile_hook_clears_caption_when_room_changes(self):
        tile = Tile({'room_id': 'fixture', 'uname': 'test', 'live': True})
        try:
            tile.resize(800, 486)
            tile.show()
            APP.processEvents()
            tile.set_translation_caption('原文', '译文')
            APP.processEvents()
            self.assertTrue(tile.video_caption.isVisible())
            tile.set_room(None)
            self.assertFalse(tile.video_caption.isVisible())
        finally:
            tile.close()
            tile.deleteLater()
            APP.processEvents()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining])
