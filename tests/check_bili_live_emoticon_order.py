"""Durable, shared emote ordering and decoded-image reuse with synthetic storage."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['DDM_NO_SAVE'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bili_live_support import session
from PySide6.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton
from ddm_plugin_bili_live import emoticons as api
from ddm_plugin_bili_live.workers import _active_requests

APP = QApplication.instance() or QApplication([])


def settle(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise AssertionError('emoticon state did not settle')


def package(name, identities=('a', 'b', 'c'), package_id=None):
    return {'pkg_type': 2, 'pkg_name': name, 'pkg_id': package_id or name,
            'emoticons': [{'emoji': item, 'emoticon_unique': item, 'perm': 1}
                          for item in identities]}


def order_type(test):
    cls = getattr(api, 'EmoticonOrder', None)
    test.assertIsNotNone(cls, 'emoticons need an injectable durable order service')
    return cls


def labels(popup):
    return [popup.grid.itemAtPosition(i // 5, i % 5).widget().text()
            for i in range(popup.grid.count())]


class OrderStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'preferences' / 'emoticon_order.json'

    def test_restart_preserves_missing_room_packages_and_items(self):
        order = order_type(self)(path=self.path)
        a, b, c = package('A'), package('B'), package('C')
        order.save_packages([a, b, c])
        order.save_packages([c, a])
        order.save('2:A', a['emoticons'])
        order.save('2:A', [a['emoticons'][2], a['emoticons'][0]])
        restarted = order_type(self)(path=self.path)
        self.assertEqual([p['pkg_name'] for p in restarted.arrange_packages([a, b, c])], ['C', 'B', 'A'])
        self.assertEqual([e['emoji'] for e in restarted.arrange('2:A', a['emoticons'])], ['c', 'b', 'a'])
        self.assertEqual(list(self.path.parent.glob('.partial-*')), [])

    def test_stable_ids_survive_display_name_and_url_changes(self):
        order = order_type(self)(path=self.path)
        a, b = package('first', package_id=10), package('second', package_id=11)
        order.save_packages([b, a])
        order.save('2:10', list(reversed(a['emoticons'])))
        a['pkg_name'] = 'renamed'
        a['emoticons'][0].update(emoji='new display', url='https://example.invalid/new.png')
        self.assertEqual([p['pkg_id'] for p in order.arrange_packages([a, b])], [11, 10])
        self.assertEqual([e['emoticon_unique'] for e in order.arrange('2:10', a['emoticons'])], ['c', 'b', 'a'])

    def test_default_no_save_never_opens_real_storage(self):
        cls = order_type(self)
        with patch.object(Path, 'open', side_effect=AssertionError('real order storage touched')):
            order = cls()
            order.save_packages([package('ephemeral')])
            order.save('2:ephemeral', [{'emoji': 'temporary'}])
        self.assertEqual(order.orders['_tabs'], ['2:ephemeral'])

    def test_malformed_file_and_unwritable_destination_keep_sorting_usable(self):
        cls = order_type(self)
        self.path.parent.mkdir()
        self.path.write_text('{broken', encoding='utf-8')
        order = cls(path=self.path)
        self.assertEqual(order.orders, {})
        self.path.write_text(json.dumps({'version': 1, 'orders': {'_tabs': ['2:A', '2:A'], 'bad': [3]}}), encoding='utf-8')
        order = cls(path=self.path)
        self.assertEqual(order.orders, {'_tabs': ['2:A']})
        previous = self.path.read_bytes()
        with patch('os.replace', side_effect=PermissionError('locked')):
            order.save_packages([package('B'), package('A')])
        self.assertEqual(order.orders['_tabs'], ['2:B', '2:A'])
        self.assertEqual(self.path.read_bytes(), previous, 'failed atomic write must preserve existing preferences')
        self.assertEqual(list(self.path.parent.glob('.partial-*')), [])


class PopupOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.order = order_type(self)(path=Path(self.temp.name) / 'order.json')
        self.popups = []
        self.addCleanup(self.cleanup)

    def popup(self, packs):
        popup = api.EmoticonPopup(order=self.order)
        self.popups.append(popup)
        popup.set_packages(packs)
        popup.show()
        settle(lambda: popup.grid.count() == len(popup._emotes))
        return popup

    def cleanup(self):
        for popup in self.popups:
            popup.hide()
        for worker in list(_active_requests):
            worker.wait(3000)
        for popup in self.popups:
            popup.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_tab_drag_refresh_and_reopen_share_order_without_losing_selection(self):
        packs = [package('A'), package('B'), package('C')]
        first = self.popup(packs)
        second = self.popup(packs)
        first.tabs.setCurrentIndex(1)
        settle(lambda: first.grid.count() == 3)
        body = first.scroll.widget()
        self.assertTrue(first.tabs.isMovable())
        first.tabs.moveTab(2, 0)
        self.assertEqual([p['pkg_name'] for p in second._packages], ['C', 'A', 'B'])
        self.assertEqual(first.tabs.tabData(first.tabs.currentIndex()), '2:B')
        self.assertIs(first.scroll.widget(), body, 'moving tabs must retain active cells')
        first.set_packages([package('A'), package('B'), package('C')])
        self.assertEqual([p['pkg_name'] for p in first._packages], ['C', 'A', 'B'])
        self.assertIs(first.scroll.widget(), body, 'unchanged fresh catalog must retain icons')
        second.hide()
        self.order.save_packages([packs[1], packs[0], packs[2]])
        second.show()
        self.assertEqual([p['pkg_name'] for p in second._packages], ['B', 'A', 'C'])
        third = self.popup(packs)
        self.assertEqual([p['pkg_name'] for p in third._packages], ['B', 'A', 'C'])

    def test_in_package_drag_reorders_other_popup_without_emitting_selection(self):
        first = self.popup([package('A')])
        second = self.popup([package('A')])
        selected = []
        first.selected.connect(lambda *args: selected.append(args))
        original = first.grid.itemAtPosition(0, 0).widget()
        target = first.grid.itemAtPosition(0, 2).widget()
        global_target = target.mapToGlobal(target.rect().center())
        QTest.mousePress(original, Qt.LeftButton, pos=original.rect().center())
        local = original.mapFromGlobal(global_target)
        move = QMouseEvent(QEvent.MouseMove, QPointF(local), QPointF(global_target),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        APP.sendEvent(original, move)
        release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(original.rect().center()),
                              QPointF(global_target), Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        APP.sendEvent(original, release)
        self.assertEqual(labels(first), ['b', 'c', 'a'])
        self.assertEqual(labels(second), ['b', 'c', 'a'])
        self.assertIs(first.grid.itemAtPosition(0, 2).widget(), original)
        self.assertEqual(selected, [], 'drag release must never send the dragged emote')
        self.assertTrue(first.isVisible())
        QTest.mouseClick(original, Qt.LeftButton)
        self.assertEqual(len(selected), 1, 'normal clicks must still select after a completed drag')

    def test_changed_catalog_keeps_selected_package_and_known_images(self):
        from copy import deepcopy
        packs = [package('A'), package('B')]
        packs[1]['current_cover'] = 'https://example.invalid/known-cover.png'
        packs[1]['emoticons'][0]['url'] = 'https://example.invalid/known-emote.png'
        image = QImage(32, 32, QImage.Format_ARGB32)
        image.fill(QColor('green'))
        with patch.object(api, '_load_image', return_value=image):
            popup = self.popup(packs)
            popup.tabs.setCurrentIndex(1)
            settle(lambda: popup.grid.count() == 3 and not popup._buttons[0].icon().isNull())
            settle(lambda: not _active_requests)
        refreshed = deepcopy(packs)
        refreshed[1]['emoticons'][1]['perm'] = 0
        with patch.object(api, '_load_image', side_effect=AssertionError('known icons loaded again')):
            popup.set_packages(refreshed)
            settle(lambda: popup.grid.count() == 3)
            self.assertEqual(popup.tabs.tabData(popup.tabs.currentIndex()), '2:B')
            self.assertFalse(popup.tabs.tabIcon(1).isNull())
            self.assertFalse(popup._buttons[0].icon().isNull())
            self.assertFalse(popup._buttons[1].isEnabled())
            settle(lambda: not _active_requests)

    def test_shared_emote_order_retains_icons_and_scroll(self):
        pack = package('A', tuple(str(i) for i in range(40)))
        popup = self.popup([pack])
        bar = popup.scroll.verticalScrollBar()
        bar.setValue(120)
        APP.processEvents()
        before = bar.value()
        original = popup.grid.itemAtPosition(0, 0).widget()
        image = QImage(32, 32, QImage.Format_ARGB32)
        image.fill(QColor('magenta'))
        from PySide6.QtGui import QIcon, QPixmap
        original.setIcon(QIcon(QPixmap.fromImage(image)))
        self.order.save('2:A', list(reversed(pack['emoticons'])))
        self.assertEqual(labels(popup)[-1], '0')
        self.assertIs(popup.grid.itemAtPosition(7, 4).widget(), original)
        self.assertFalse(original.icon().isNull())
        self.assertEqual(bar.value(), before)


class ImageReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = patch.object(api.config, 'REPO', str(self.root))
        self.repo.start()
        self.addCleanup(self.repo.stop)
        self.image = QImage(32, 32, QImage.Format_ARGB32)
        self.image.fill(QColor('#00a1d6'))

    def test_legacy_host_image_is_reused_without_http(self):
        url = 'https://example.invalid/legacy.png'
        path = self.root / 'cache/emoticons' / (hashlib.md5(url.encode()).hexdigest() + '.png')
        path.parent.mkdir(parents=True)
        self.assertTrue(self.image.save(str(path)))
        with patch.object(api.requests, 'get', side_effect=AssertionError('cached image downloaded')):
            result = api._load_image(url, 'bili_live/emoticons')
        self.assertFalse(result.isNull())
        self.assertEqual(result.pixelColor(0, 0), QColor('#00a1d6'))

    def test_plugin_disk_image_and_decoded_memory_are_reused(self):
        url = 'https://example.invalid/plugin.png'
        path = self.root / 'cache/bili_live/emoticons' / (hashlib.sha256(url.encode()).hexdigest() + '.image')
        path.parent.mkdir(parents=True)
        self.assertTrue(self.image.save(str(path), 'PNG'))
        with patch.object(api.requests, 'get', side_effect=AssertionError('cached image downloaded')):
            first = api._load_image(url, 'bili_live/emoticons')
            path.unlink()
            first.fill(QColor('red'))
            second = api._load_image(url, 'bili_live/emoticons')
        self.assertEqual(second.pixelColor(0, 0), QColor('#00a1d6'), 'cached image must be reusable and isolated from caller mutation')

    def test_concurrent_reopens_download_and_decode_one_image(self):
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        self.assertTrue(self.image.save(buffer, 'PNG'))
        buffer.close()
        started, release = threading.Event(), threading.Event()
        calls = []
        def get(url, **kwargs):
            calls.append(url)
            started.set()
            release.wait(3)
            return SimpleNamespace(content=bytes(data), headers={}, raise_for_status=lambda: None)
        url = 'https://example.invalid/concurrent.png'
        with patch.object(api.requests, 'get', side_effect=get):
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(api._load_image, url, 'bili_live/emoticons') for _ in range(5)]
                self.assertTrue(started.wait(2))
                release.set()
                images, errors = [], []
                for future in futures:
                    try:
                        images.append(future.result(timeout=3))
                    except Exception as error:
                        errors.append(str(error))
        self.assertEqual(errors, [], 'concurrent cached-image reads must not race atomic downloads')
        self.assertEqual(calls, [url])
        self.assertTrue(all(not image.isNull() for image in images))
        cached = list((self.root / 'cache/bili_live/emoticons').glob('*.image'))
        self.assertEqual(len(cached), 1)

    def test_decoded_memory_limit_evicts_older_images(self):
        self.assertTrue(hasattr(api, '_EMOTICON_CACHE_LIMIT'), 'decoded images require a bounded memory cache')
        urls = ['https://example.invalid/one.png', 'https://example.invalid/two.png']
        paths = []
        for url in urls:
            path = self.root / 'cache/bili_live/emoticons' / (hashlib.sha256(url.encode()).hexdigest() + '.image')
            path.parent.mkdir(parents=True, exist_ok=True)
            self.image.save(str(path), 'PNG')
            paths.append(path)
        with patch.object(api, '_EMOTICON_CACHE_LIMIT', self.image.sizeInBytes()):
            api._load_image(urls[0], 'bili_live/emoticons')
            api._load_image(urls[1], 'bili_live/emoticons')
            for path in paths:
                path.unlink()
            with patch.object(api.requests, 'get', side_effect=RuntimeError('offline')):
                self.assertFalse(api._load_image(urls[1], 'bili_live/emoticons').isNull())
                with self.assertRaises(RuntimeError):
                    api._load_image(urls[0], 'bili_live/emoticons')


if __name__ == '__main__':
    unittest.main()
