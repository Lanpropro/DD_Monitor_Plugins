"""真实 Qt 控件回归：逐房间草稿、表情分流、异步失败和过期请求隔离。"""
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
os.environ['DDM_NO_SAVE'] = '1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bili_live_support import session
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent, QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QToolButton
from ddm import theme
from ddm_plugin_bili_live import emoticons as image_api
from bili_live_support import bili, session
APP = QApplication.instance() or QApplication([])

def settle(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise AssertionError('后台请求未在限时内完成')

class ComposerTests(unittest.TestCase):

    def test_pending_refresh_keeps_same_room_emoticons_usable_but_context_change_clears_them(self):
        packs=[{'pkg_type':3,'pkg_name':'保留','emoticons':[{'emoji':'[保留]','perm':1}]}]
        with patch.object(bili,'get_emoticons',return_value=packs):
            self.composer.open_emoticons()
            settle(lambda:len(self.composer.popup.scroll.findChildren(QToolButton))==1)
        popup=self.composer.popup;body=popup.scroll.widget()
        entered,release=threading.Event(),threading.Event()
        def load(*args,**kwargs):
            entered.set();release.wait(3)
            raise ValueError('refresh failed')
        try:
            with patch.object(bili,'get_emoticons',side_effect=load):
                popup.retry_button.click();settle(entered.is_set)
                self.assertIs(popup.scroll.widget(),body)
                self.assertEqual(self.composer._catalog_key,self.composer._context())
                self.assertTrue(popup.refresh_spinner.isVisible())
                popup.scroll.findChildren(QToolButton)[0].click()
                self.assertEqual(self.composer.edit.text(),'[保留]')
                self.composer.open_emoticons()
                self.assertTrue(popup.refresh_spinner.isVisible())
                release.set();settle(lambda:self.composer._catalog_pending is None)
                self.assertIs(popup.scroll.widget(),body)
                self.assertIn('refresh failed',popup.status.text())
                self.assertFalse(popup.refresh_spinner.isVisible())
                self.composer.set_room('202')
                self.assertEqual(popup.scroll.findChildren(QToolButton),[])
                self.assertEqual(popup.tabs.count(),0)
        finally:release.set()


    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ddm_plugin_bili_live.composer'), '缺少独立的弹幕输入控件')
        from ddm_plugin_bili_live.composer import DanmakuComposer
        self.saved = (session.SESSION_DATA, session.BILI_JCT, session.BUVID3)
        session.set_login_cookies('test-session', 'test-csrf', 'device-test')
        self.composer = DanmakuComposer(session)
        self.composer.set_room('101')
        self.composer.resize(360, self.composer.HEIGHT)
        self.composer.show()
        APP.processEvents()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        from ddm_plugin_bili_live.composer import _active_requests
        for worker in list(_active_requests):
            worker.wait(3000)
        APP.processEvents()
        self.composer.close()
        session.set_login_cookies(*self.saved)
        APP.processEvents()

    def test_success_clears_only_the_sending_rooms_draft(self):
        from ddm_plugin_bili_live.composer import DanmakuComposer
        other = DanmakuComposer(session)
        other.set_room('202')
        other.edit.setText('房间二的草稿')
        self.composer.edit.setText('房间一')
        with patch.object(bili, 'send_danmaku', return_value={'code': 0}) as send:
            QTest.keyClick(self.composer.edit, Qt.Key_Return)
            settle(lambda: not self.composer.busy)
        self.assertEqual(self.composer.edit.text(), '')
        self.assertEqual(other.edit.text(), '房间二的草稿')
        self.assertEqual(send.call_args.args, ('101', '房间一'))
        other.close()

    def test_failure_retains_draft_and_shows_reason(self):
        self.composer.edit.setText('保留我')
        with patch.object(bili, 'send_danmaku', side_effect=ValueError('发送频率过快')):
            self.composer.send_button.click()
            settle(lambda: not self.composer.busy)
        self.assertEqual(self.composer.edit.text(), '保留我')
        self.assertIn('发送频率过快', self.composer.feedback.text())
        self.assertTrue(self.composer.send_button.isEnabled())

    def test_small_emoticon_inserts_emoji_at_cursor(self):
        self.composer.edit.setText('前后')
        self.composer.edit.setCursorPosition(1)
        with patch.object(bili, 'send_danmaku') as send:
            self.composer.choose_emoticon(3, {'emoji': '[doge]', 'emoticon_unique': 'emoji_9', 'perm': 1})
        self.assertEqual(self.composer.edit.text(), '前[doge]后')
        send.assert_not_called()

    def test_picker_buttons_render_smaller_circular_hover_targets_in_both_themes(self):
        # A square hover background or an oversized entry button breaks the
        # compact round controls used alongside the host's 26px settings button.
        try:
            self.composer.edit.setFocus()
            for mode in ('dark', 'light'):
                theme.apply(mode)
                APP.setStyleSheet(theme.qss())
                for button in (self.composer.emote_button, self.composer.gift_button):
                    with self.subTest(mode=mode, button=button.accessibleName()):
                        self.assertEqual(button.width(), button.height())
                        self.assertLess(button.height(), 26)
                        button.clearFocus()
                        APP.sendEvent(button, QEvent(QEvent.Leave))
                        images = []
                        for hovered in (False, True):
                            button.setProperty('hovered', hovered)
                            button.style().unpolish(button)
                            button.style().polish(button)
                            APP.processEvents()
                            # Include the parent behind the transparent round corners.
                            images.append(self.composer.grab().toImage())
                        def pixel(image, x, y):
                            scale = image.devicePixelRatioF()
                            return image.pixelColor(round((button.x() + x) * scale),
                                                    round((button.y() + y) * scale))
                        normal, hover = images
                        mid = button.width() // 2
                        for x, y in ((1, 1), (button.width()-2, 1),
                                     (1, button.height()-2), (button.width()-2, button.height()-2)):
                            self.assertEqual(pixel(normal, x, y), pixel(hover, x, y),
                                             '圆形悬停背景不能填满方形角落')
                        for x, y in ((mid, 1), (1, mid)):
                            self.assertNotEqual(pixel(normal, x, y), pixel(hover, x, y),
                                                '圆形边缘应显示主程序的悬停背景')
                        button.setProperty('hovered', False)
        finally:
            for button in (self.composer.emote_button, self.composer.gift_button):
                button.setProperty('hovered', False)
            theme.apply('dark')

    def test_emoticon_icon_is_legible_without_a_font_glyph_in_both_themes(self):
        button = self.composer.emote_button
        original_font = button.font()
        try:
            tiny_font = button.font()
            tiny_font.setPixelSize(1)
            button.setFont(tiny_font)
            for mode in ('dark', 'light'):
                theme.apply(mode)
                button.clearFocus()
                APP.processEvents()
                image = button.grab().toImage()
                scale = image.devicePixelRatioF()
                ink = []
                foreground = theme.qcolor(theme.TEXT2)
                background = image.pixelColor(round(2 * scale), round(button.height() / 2 * scale))
                alpha = foreground.alphaF()
                expected = QColor(*(round(foreground.getRgb()[i] * alpha + background.getRgb()[i] * (1 - alpha)) for i in range(3)))
                for y in range(image.height()):
                    for x in range(image.width()):
                        pixel = image.pixelColor(x, y)
                        if max(abs(pixel.red() - expected.red()), abs(pixel.green() - expected.green()), abs(pixel.blue() - expected.blue())) < 12:
                            ink.append((x / scale, y / scale))
                self.assertTrue(ink)
                left, right = (min((x for x, y in ink)), max((x for x, y in ink)))
                top, bottom = (min((y for x, y in ink)), max((y for x, y in ink)))
                self.assertGreaterEqual(right - left, 13, '表情图标不能退化成很小的字体字符')
                self.assertGreaterEqual(bottom - top, 13)
                self.assertGreater(left, 2)
                self.assertLess(right, button.width() - 2)
                self.assertEqual(button.accessibleName(), '选择直播间表情')
        finally:
            button.setFont(original_font)
            theme.apply('dark')

    def test_large_emoticons_send_full_unique_without_touching_draft(self):
        for pkg_type in (1, 2, 5):
            with self.subTest(pkg_type=pkg_type):
                self.composer.edit.setText('文字草稿')
                with patch.object(bili, 'send_danmaku', return_value={'code': 0}) as send:
                    self.composer.choose_emoticon(pkg_type, {'emoji': '好', 'emoticon_unique': 'room_101_123456', 'perm': 1})
                    settle(lambda: not self.composer.busy)
                self.assertEqual(send.call_args.args, ('101', 'room_101_123456'))
                self.assertEqual(send.call_args.kwargs['dm_type'], 1)
                self.assertEqual(self.composer.edit.text(), '文字草稿')

    def test_locked_unknown_or_incomplete_emoticons_never_send(self):
        with patch.object(bili, 'send_danmaku') as send:
            for pkg, emote in ((2, {'emoji': '锁定', 'perm': 0, 'emoticon_unique': 'room_101_3'}), (8, {'emoji': '未知', 'perm': 1, 'emoticon_unique': 'a'}), (1, {'emoji': '缺标识', 'perm': 1})):
                self.composer.choose_emoticon(pkg, emote)
        send.assert_not_called()

    def test_send_disables_text_input_until_success_failure_or_timeout(self):
        for error in (None, ValueError('发送失败'), bili.requests.Timeout()):
            with self.subTest(error=type(error).__name__):
                entered, release = threading.Event(), threading.Event()
                def send(*args, **kwargs):
                    entered.set()
                    release.wait(3)
                    if error is not None:
                        raise error
                    return {'code': 0}
                self.composer.edit.setText('等待发送的草稿')
                with patch.object(bili, 'send_danmaku', side_effect=send) as sending:
                    try:
                        self.composer.send_text()
                        self.assertFalse(self.composer.edit.isEnabled())
                        settle(entered.is_set)
                        QTest.keyClicks(self.composer.edit, 'extra input')
                        self.assertEqual(self.composer.edit.text(), '等待发送的草稿')
                    finally:
                        release.set()
                        settle(lambda: not self.composer.busy)
                self.assertEqual(sending.call_count, 1)
                self.assertTrue(self.composer.edit.isEnabled())
                self.assertEqual(self.composer.edit.text(), '' if error is None else '等待发送的草稿')

    def test_pending_send_blocks_duplicates_and_keeps_ui_responsive(self):
        entered, release = (threading.Event(), threading.Event())

        def send(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {'code': 0}
        self.composer.edit.setText('发送中')
        with patch.object(bili, 'send_danmaku', side_effect=send) as sending:
            self.composer.send_button.click()
            settle(entered.is_set)
            self.assertTrue(self.composer.busy)
            self.assertFalse(self.composer.send_button.isEnabled())
            self.assertFalse(self.composer.edit.isEnabled())
            self.composer.send_text()
            self.composer.choose_emoticon(1, {'perm': 1, 'emoticon_unique': 'official_1'})
            self.composer.edit.setText('下一条草稿')
            release.set()
            settle(lambda: not self.composer.busy)
        self.assertEqual(sending.call_count, 1)
        self.assertEqual(self.composer.edit.text(), '下一条草稿')
        self.assertTrue(self.composer.edit.isEnabled())

    def test_room_or_account_change_ignores_old_send_completion(self):
        for changed in ('room', 'account'):
            with self.subTest(changed=changed):
                self.composer.set_room('101')
                session.set_login_cookies('test-session', 'test-csrf', 'device-test')
                release = threading.Event()
                self.composer.edit.setText('旧消息')
                with patch.object(bili, 'send_danmaku', side_effect=lambda *a, **kw: release.wait(2)):
                    self.composer.send_text()
                    self.assertFalse(self.composer.edit.isEnabled())
                    if changed == 'room':
                        self.composer.set_room('202')
                    else:
                        session.set_sessdata('other-session')
                        # The cookie-only test session must mirror BiliSession's notification.
                        session.changed.emit(session.snapshot())
                    self.assertTrue(self.composer.edit.isEnabled())
                    self.composer.edit.setText('新的草稿')
                    release.set()
                    settle(lambda: not self.composer.busy)
                self.assertEqual(self.composer.edit.text(), '新的草稿')

    def test_ime_and_text_shortcuts_do_not_trigger_player_actions(self):
        edit = self.composer.edit
        event = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_F, Qt.NoModifier, 'f')
        event.ignore()
        APP.sendEvent(edit, event)
        self.assertTrue(event.isAccepted())
        with patch.object(bili, 'send_danmaku') as send:
            edit.setText('已有草稿')
            APP.sendEvent(edit, QInputMethodEvent('拼音', []))
            QTest.keyClick(edit, Qt.Key_Return)
            APP.processEvents()
        send.assert_not_called()

    def test_popup_renders_packages_permissions_empty_error_and_real_selection(self):
        packs = [{'pkg_type': 3, 'pkg_name': '小表情', 'emoticons': [{'emoji': '[doge]', 'perm': 1, 'url': 'https://example.com/emote.png'}]}, {'pkg_type': 2, 'pkg_name': '主播表情', 'emoticons': [{'emoji': '锁定', 'perm': 0, 'emoticon_unique': 'room_101_4', 'unlock_show_text': '粉丝团'}]}]
        pixmap = QImage(32, 32, QImage.Format_ARGB32)
        pixmap.fill(QColor('#00a1d6'))
        with patch.object(bili, 'get_emoticons', return_value=packs), patch.object(image_api, '_load_image', return_value=pixmap):
            self.composer.emote_button.click()
            popup = self.composer.popup
            settle(lambda: any((b.text() == '[doge]' for b in popup.findChildren(QToolButton))))
            self.assertEqual([popup.tabs.tabText(i) for i in range(popup.tabs.count())], ['小表情', '主播表情'])
            popup.tabs.setCurrentIndex(1)
            settle(lambda: any((b.text() == '锁定' for b in popup.findChildren(QToolButton))))
            buttons = popup.findChildren(QToolButton)
            locked = next((b for b in buttons if b.text() == '锁定'))
            self.assertFalse(locked.isEnabled())
            self.assertIn('粉丝团', locked.toolTip())
            self.assertTrue(self.composer.emote_button.screen().availableGeometry().contains(popup.geometry()))
            popup.tabs.setCurrentIndex(0)
            settle(lambda: any((b.text() == '[doge]' for b in popup.findChildren(QToolButton))))
            next((b for b in popup.findChildren(QToolButton) if b.text() == '[doge]')).click()
            self.assertEqual(self.composer.edit.text(), '[doge]')
        popup.close()
        popup.set_packages([])
        self.assertIn('暂无', popup.status.text())
        popup.set_error('网络不可用')
        self.assertIn('网络不可用', popup.status.text())
        self.assertFalse(popup.retry_button.isHidden())

    def test_catalog_timeout_shows_retry_without_claiming_a_send(self):
        import requests
        with patch.object(bili, 'get_emoticons', side_effect=requests.Timeout()):
            self.composer.open_emoticons()
            settle(lambda: '超时' in self.composer.popup.status.text())
        self.assertNotIn('发送', self.composer.popup.status.text())
        self.assertFalse(self.composer.popup.retry_button.isHidden())

    def test_first_catalog_survives_lazy_device_cookie_initialization(self):
        session.set_login_cookies('test-session', 'test-csrf', '')
        packs = [{'pkg_type': 3, 'pkg_name': '小表情', 'emoticons': [{'emoji': '[好]', 'perm': 1}]}]

        def load(*args, **kwargs):
            session.set_login_cookies('test-session', 'test-csrf', 'new-device')
            return packs
        with patch.object(bili, 'get_emoticons', side_effect=load):
            self.composer.open_emoticons()
            settle(lambda: any((b.text() == '[好]' for b in self.composer.popup.findChildren(QToolButton))))
        self.assertTrue(self.composer.popup.retry_button.isEnabled())

    def test_account_change_discards_permissions_and_releases_pending_catalog(self):
        entered, release = (threading.Event(), threading.Event())

        def load(*args, **kwargs):
            entered.set()
            release.wait(2)
            return [{'pkg_type': 2, 'emoticons': [{'emoji': '旧账号表情', 'perm': 1, 'emoticon_unique': 'room_101_99'}]}]
        with patch.object(bili, 'get_emoticons', side_effect=load):
            self.composer.open_emoticons()
            settle(entered.is_set)
            session.set_login_cookies('other-session', 'other-csrf')
            self.composer.sync_credentials()
            self.assertFalse(self.composer.popup.isVisible())
            release.set()
            from ddm_plugin_bili_live.composer import _active_requests
            settle(lambda: not _active_requests)
        with patch.object(bili, 'get_emoticons', return_value=[]):
            self.composer.open_emoticons()
            settle(lambda: '暂无' in self.composer.popup.status.text())
        self.assertFalse(any((b.text() == '旧账号表情' for b in self.composer.popup.findChildren(QToolButton))))

    def test_manual_refresh_replaces_cached_catalog_after_error_and_empty_result(self):
        with patch.object(bili, 'get_emoticons', side_effect=[ValueError('临时失败'), [], [{'pkg_type': 3, 'emoticons': [{'emoji': '[刷新]', 'perm': 1}]}]]) as get:
            self.composer.open_emoticons()
            settle(lambda: '临时失败' in self.composer.popup.status.text())
            self.composer.popup.retry_button.click()
            settle(lambda: '暂无' in self.composer.popup.status.text())
            self.composer.popup.retry_button.click()
            settle(lambda: any((b.text() == '[刷新]' for b in self.composer.popup.findChildren(QToolButton))))
        self.assertEqual(get.call_count, 3)

    def test_timeout_retains_draft_and_reports_uncertain_result_without_retry(self):
        import requests
        self.composer.edit.setText('不要重复')
        with patch.object(bili, 'send_danmaku', side_effect=requests.Timeout()) as send:
            self.composer.send_text()
            settle(lambda: not self.composer.busy)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.composer.edit.text(), '不要重复')
        self.assertIn('未确认', self.composer.feedback.text())

    def test_closing_tile_during_request_does_not_destroy_running_thread(self):
        from ddm_plugin_bili_live.composer import DanmakuComposer, _active_requests
        entered, release = (threading.Event(), threading.Event())
        composer = DanmakuComposer(session)
        composer.set_room('101')
        composer.edit.setText('关闭前发送')

        def send(*a, **kw):
            entered.set()
            release.wait(2)
            return {'code': 0}
        with patch.object(bili, 'send_danmaku', side_effect=send):
            composer.send_text()
            settle(entered.is_set)
            composer.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertTrue(_active_requests)
            release.set()
            settle(lambda: not _active_requests)
if __name__ == '__main__':
    unittest.main()
