"""Reference-style cards, icon-only completion and bounded avatar delivery."""
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from check_bili_watchlater import APP, models
from check_bili_watchlater_interactions import pages
from ddm_plugin_bili_watchlater import widgets
from ddm_plugin_bili_watchlater.card_presentation import (avatar_thumbnail, cover_description,
    cover_footer_layout, cover_statistics, finished_icon_rect, publication_text)
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QPixmap


class CardStyleTests(unittest.TestCase):
    def setUp(self):
        self.item = models.WatchLaterItem(aid=1, bvid='fixture', title='标题与 emoji 😀 两行信息', owner_name='UP主',
            owner_face='https://i0.hdslb.com/bfs/face/fixture.jpg', duration=600, progress=500,
            view_count=12345, danmaku_count=432, videos=3, pubdate=int(datetime(2026,10,24,12).timestamp()))
        self.page = pages.WatchLaterPage(SimpleNamespace(credentials=lambda _: None))
        self.page._geometry_settings = Mock()

    def tearDown(self):
        self.page.shutdown()
        self.page.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def card(self):
        self.page._items, self.page._loaded_once = [self.item], True
        self.page._rebuild_cards([self.item])
        return self.page._cards[0]

    @contextmanager
    def shown(self):
        with patch.object(self.page, '_load'), patch.object(self.page, '_cache_fresh', return_value=True), \
                patch.object(self.page, '_apply_saved_geometry', return_value=True), patch.object(self.page, '_start_entrance'), \
                patch.object(self.page.covers, '_pump'), patch.object(self.page.renderer, 'danmaku_provider', return_value=None):
            self.page.resize(980, 700)
            self.page.show()
            APP.processEvents()
            yield

    def test_finished_is_only_an_icon_on_both_card_covers_with_accessible_text(self):
        for flexible in (True, False):
            cover = widgets.CoverWidget(self.page, flexible=flexible)
            cover.set_item(self.item, None)
            self.assertNotIn('已看完', [text for text, _ in widgets.cover_badges(self.item)])
            self.assertIn('已看完', cover.accessibleDescription())
            self.assertEqual(cover.toolTip(), '')
            self.assertTrue(cover._composited_cover())
            cover.deleteLater()

    def test_footer_icons_and_duration_never_overlap_at_narrow_widths(self):
        self.item.view_count, self.item.danmaku_count, self.item.duration = 10**9, 10**8, 99999
        for width in (80, 96, 140, 224, 360, 600):
            rect = QRectF(0, 0, width, width * 9 / 16)
            stats, duration, time_rect = cover_footer_layout(rect, self.item, APP.font())
            self.assertTrue(duration)
            self.assertGreaterEqual(time_rect.left(), rect.left())
            self.assertFalse(finished_icon_rect(rect).intersects(time_rect))
            for _kind, _text, icon, label in stats:
                self.assertTrue(rect.contains(icon))
                self.assertTrue(rect.contains(label))
                self.assertLess(label.right(), time_rect.left())

    def test_unknown_statistics_stay_absent_and_known_zero_stays_zero(self):
        self.item.view_count = self.item.danmaku_count = None
        self.assertEqual(cover_statistics(self.item), [])
        self.item.view_count, self.item.danmaku_count = 0, 0
        self.assertEqual(cover_statistics(self.item), [('play','0'),('danmaku','0')])

    def test_async_stat_updates_invalidate_render_cache_and_accessibility(self):
        card = self.card()
        before = card.cover._composited_cover().cacheKey()
        card.set_view_count(76543)
        card.set_danmaku_count(789)
        self.assertNotEqual(card.cover._composited_cover().cacheKey(), before)
        self.assertIn('789', card.cover.accessibleDescription())
        self.assertIn('789', cover_description(self.item))

    def test_uploader_and_all_three_actions_share_one_row_at_all_column_sizes(self):
        card = self.card()
        with self.shown():
            self.page.grid_layout.removeWidget(card)
            for width in (90, 120, 180, 240, 310, 420):
                card.resize(width, 400)
                card.fit_cell(width, 400)
                card.layout().activate()
                card.uploader_row.layout().activate()
                for button in (card.play_button, card.open_button, card.remove_button):
                    self.assertIs(button.parentWidget(), card.uploader_row)
                    self.assertTrue(card.uploader_row.rect().contains(button.geometry()), (width, button.geometry()))
                    position = button.mapTo(card, QPoint())
                    self.assertTrue(card.rect().contains(QRect(position, button.size())))
                self.assertAlmostEqual(card.cover.width()/card.cover.height(), 16/9, delta=.04,
                    msg=(width, card.size(), card.cover.size(), card.uploader_row.minimumSizeHint()))
                self.assertEqual(card.avatar.isVisible(), width >= 210)
                self.assertTrue(card.title_label.alignment() & Qt.AlignTop)

    def test_date_uses_publication_then_added_date_and_never_fabricates_one(self):
        self.assertEqual(publication_text(self.item), ('10-24','发布于 2026-10-24'))
        self.item.add_at, self.item.pubdate = self.item.pubdate, 0
        self.assertEqual(publication_text(self.item), ('10-24','加入稍后再看于 2026-10-24'))
        self.item.add_at = 0
        self.assertEqual(publication_text(self.item), ('',''))

    def test_avatar_requests_are_small_and_keep_existing_processing_and_query(self):
        self.assertEqual(avatar_thumbnail('//i0.hdslb.com/face.jpg?key=x'), 'https://i0.hdslb.com/face.jpg@64w_64h_1c.webp?key=x')
        self.assertEqual(avatar_thumbnail('https://i0.hdslb.com/face.jpg@32w.webp'), 'https://i0.hdslb.com/face.jpg@32w.webp')
        self.assertEqual(avatar_thumbnail('https://example.com/face.jpg'), 'https://example.com/face.jpg')
        self.assertEqual(avatar_thumbnail(''), '')

    def test_reusing_card_updates_owner_date_and_discards_an_old_avatar(self):
        card = self.card()
        pixmap = QPixmap(64,64)
        pixmap.fill(Qt.red)
        card.avatar.set_pixmap(pixmap)
        replacement = models.WatchLaterItem(aid=2, bvid='other', title='新标题', owner_name='<UP 😀>', pubdate=0)
        card.update_item(replacement)
        self.assertIsNone(card.avatar._pixmap)
        self.assertEqual(card.owner_label.full_text(), '<UP 😀>')
        self.assertEqual(card.date_label.full_text(), '')
        self.assertEqual(card.title_label.full_text(), '新标题')

    def test_shared_loader_prioritizes_covers_before_visible_avatars_and_releases_them(self):
        self.item.cover_url = 'https://i0.hdslb.com/bfs/archive/fixture.jpg'
        self.page._items, self.page._loaded_once = [self.item], True
        with self.shown(), patch.object(self.page.covers, 'prioritize') as priority:
            self.page._refetch_visible_covers()
            card = self.page._cards[0]
            self.assertEqual(priority.call_args.args[0], [self.item.cover_url, card.avatar_url])
            pixmap = QPixmap(64,64)
            pixmap.fill(Qt.blue)
            with patch.object(self.page.covers, 'cached', return_value=pixmap):
                self.page._cover_ready(card.avatar_url)
                self.assertIs(card.avatar._pixmap, pixmap)
            self.page._release_covers()
            self.assertIsNone(card.avatar._pixmap)
            self.assertEqual(self.page._avatars_by_url, {})

    def test_late_avatar_delivery_does_not_touch_a_deleted_card(self):
        self.page._items, self.page._loaded_once = [self.item], True
        with self.shown():
            self.page._refetch_visible_covers()
            card = self.page._cards[0]
            url = card.avatar_url
            self.page._drop_view(card, self.page.grid_layout)
            self.page._cards.clear()
            self.page._cards_by_key.clear()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            with patch.object(self.page.covers, 'cached') as cached:
                self.page._cover_ready(url)
                cached.assert_not_called()


if __name__ == '__main__':
    unittest.main()
