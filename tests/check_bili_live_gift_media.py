"""Raw GIF animation, bounded unauthenticated media and atomic cache publication."""
from dataclasses import replace
import base64
import math
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
from bili_live_support import bili,HOST
from bili_live_fixtures import room_payload
from ddm_plugin_bili_live.gift_api import normalize_catalog,GiftTab
from ddm_plugin_bili_live.gift_controller import GiftPanelState
from ddm_plugin_bili_live.gift_popup import GiftPopup,GiftButton
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent
from PySide6.QtGui import QImage,QMovie
from ddm_plugin_bili_live.gift_media import GiftImageLabel,GiftMediaLoader
from ddm_plugin_bili_live.workers import _active_requests

APP=QApplication.instance() or QApplication([])
# Two valid 1x1 GIF frames, each with its own graphic-control extension.
GIF=(b'GIF89a'+bytes.fromhex('01000100800000000000ffffff')+
 bytes.fromhex('21f904000a0000002c0000000001000100000202440100')*2+b';')
def settle(predicate):
    end=time.monotonic()+4
    while time.monotonic()<end:
        APP.processEvents()
        if predicate():return
        time.sleep(.005)
    raise AssertionError('media not finished')

def sized_gif(width,height):
    """Two solid frames; frequent LZW clear codes keep the fixture dependency-free."""
    def frame(color):
        codes=[code for _ in range(width*height) for code in (4,color)]+[5]
        packed=bytearray();bits=value=0
        for code in codes:
            value|=code<<bits;bits+=3
            while bits>=8:
                packed.append(value&255);value>>=8;bits-=8
        if bits:packed.append(value)
        blocks=b''.join(bytes([len(packed[i:i+255])])+packed[i:i+255] for i in range(0,len(packed),255))
        return (bytes.fromhex('21f904000a0000002c00000000')+
            width.to_bytes(2,'little')+height.to_bytes(2,'little')+b'\x00\x02'+blocks+b'\x00')
    return (b'GIF89a'+width.to_bytes(2,'little')+height.to_bytes(2,'little')+
        bytes.fromhex('800000000000ffffff')+frame(0)+frame(1)+b';')

class MediaTests(unittest.TestCase):
    def run_at_fractional_dpi(self):
        if os.environ.get('BILI_GIFT_DPI_CHILD'):return True
        # QLabel's native QIcon storage uses the QApplication screen DPR; just
        # mocking devicePixelRatioF() cannot verify the actual retained pixels.
        for dpr in ('1.5','2.25'):
            with self.subTest(dpr=dpr):
                env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_SCALE_FACTOR':dpr,'BILI_GIFT_DPI_CHILD':'1'}
                result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--host',str(HOST),
                    f'MediaTests.{self._testMethodName}'],env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return False

    def test_static_backing_pixels_match_fractional_device_pixel_ratio(self):
        if not self.run_at_fractional_dpi():return
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'image.png'
            original=QImage(140,140,QImage.Format_ARGB32);original.fill(0xffff6600)
            self.assertTrue(original.save(str(path)))
            image=GiftImageLabel();image.show();APP.processEvents()
            try:
                dpr=image.devicePixelRatioF();self.assertIn(dpr,(1.5,2.25))
                image.set_file(str(path));APP.processEvents()
                pixmap=image.pixmap()
                self.assertEqual(pixmap.width(),math.ceil(40*dpr))
                self.assertEqual(pixmap.devicePixelRatio(),dpr)
                self.assertEqual(pixmap.deviceIndependentSize().width(),40)
            finally:
                image.close();image.set_file('');APP.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_movie_frames_keep_dpi_backing_pixels_and_logical_size(self):
        if not self.run_at_fractional_dpi():return
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'image.gif';path.write_bytes(sized_gif(140,140))
            image=GiftImageLabel();image.show();APP.processEvents()
            try:
                dpr=image.devicePixelRatioF();self.assertIn(dpr,(1.5,2.25))
                image.set_file(str(path));APP.processEvents()
                self.assertIsNotNone(image.movie)
                self.assertEqual(image.movie.scaledSize().width(),math.ceil(40*dpr))
                pixmap=image.pixmap()
                self.assertFalse(pixmap.isNull())
                self.assertEqual(pixmap.width(),math.ceil(40*dpr))
                self.assertEqual(pixmap.devicePixelRatio(),dpr)
                self.assertEqual(pixmap.deviceIndependentSize().width(),40)
                image.movie.jumpToFrame(1);APP.processEvents()
                self.assertEqual(image.pixmap().devicePixelRatio(),dpr)
            finally:
                image.close();image.set_file('');APP.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_higher_resolution_static_is_retained_over_small_gif_in_either_load_order(self):
        if not self.run_at_fractional_dpi():return
        with tempfile.TemporaryDirectory() as root:
            static=Path(root)/'image.png';gif=Path(root)/'image.gif'
            original=QImage(140,140,QImage.Format_ARGB32);original.fill(0xffff6600)
            self.assertTrue(original.save(str(static)));gif.write_bytes(sized_gif(40,40))
            button=GiftButton();button.move(300,400);button.show()
            popup=GiftPopup(cache_dir=root)
            item=normalize_catalog(room_payload(),'90001').tabs[0].items[1]
            try:
                with patch.object(popup.media,'request'):
                    popup.set_state(GiftPanelState(tabs=(GiftTab('main','礼物',(item,)),),wallet=99999))
                    popup.open_at(button);APP.processEvents()
                    image=popup._cards[0].image;dpr=image.devicePixelRatioF()
                    for order in ((item.image_url,item.gif_url),(item.gif_url,item.image_url)):
                        with self.subTest(order=order):
                            popup._loaded.clear();image.set_file('');popup._visible_media()
                            for url in order:
                                popup._media_loaded(popup._generation,url,str(static if url==item.image_url else gif))
                            self.assertIsNone(image.movie,'a 40px GIF must not replace the 140px original at fractional DPR')
                            self.assertEqual(image.pixmap().width(),math.ceil(40*dpr))
                            self.assertEqual(image.pixmap().deviceIndependentSize().width(),40)
            finally:
                popup.shutdown();button.close();popup.deleteLater()
                APP.sendPostedEvents(None,QEvent.DeferredDelete);APP.processEvents()

    def test_two_frame_movie_pauses_and_resumes(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'image.gif';p.write_bytes(GIF)
            image=GiftImageLabel();image.set_file(str(p));image.show();APP.processEvents()
            self.assertIsNotNone(image.movie);self.assertEqual(image.movie.frameCount(),2)
            image.set_playing(True);self.assertEqual(image.movie.state(),QMovie.Running)
            original=image.movie;image.set_file(str(p))
            self.assertIs(image.movie,original,'scroll/refresh must not restart an already loaded animation')
            image.hide();APP.processEvents();self.assertEqual(image.movie.state(),QMovie.Paused)
            image.show();image.set_playing(True);self.assertEqual(image.movie.state(),QMovie.Running)
            image.set_file('missing');self.assertIsNone(image.movie)
            image.close()

    def test_downloads_are_bounded_cache_reused_and_stale_ignored(self):
        entered,release=threading.Event(),threading.Event();lock=threading.Lock()
        concurrent=[0,0];calls=[]
        def get(url,**kw):
            self.assertNotIn('cookies',kw);self.assertEqual(kw['timeout'],10)
            with lock:
                concurrent[0]+=1;concurrent[1]=max(concurrent);calls.append(url)
                if concurrent[0]==2:entered.set()
            release.wait(2)
            with lock:concurrent[0]-=1
            return Mock(content=GIF,headers={},status_code=200)
        with tempfile.TemporaryDirectory() as root,patch('ddm_plugin_bili_live.gift_media.requests.get',side_effect=get):
            loader=GiftMediaLoader(root);loaded=[];loader.loaded.connect(lambda *x:loaded.append(x))
            loader.request(tuple(f'https://example.invalid/{n}.gif' for n in range(6)),1)
            settle(entered.is_set);loader.cancel();release.set();settle(lambda:not _active_requests)
            self.assertLessEqual(concurrent[1],4);self.assertEqual(loaded,[])
            loader.request(('https://example.invalid/0.gif',),2);settle(lambda:bool(loaded))
            before=len(calls);loader.request(('https://example.invalid/0.gif',),3)
            settle(lambda:any(x[0]==3 for x in loaded));self.assertEqual(len(calls),before)
            self.assertEqual(Path(loaded[-1][2]).read_bytes(),GIF)
            loader.shutdown();settle(lambda:not _active_requests)

    def test_failed_gif_fallback_preserves_other_visible_images(self):
        other_entered,release,fallback_requested=threading.Event(),threading.Event(),threading.Event()
        def get(url,**kw):
            if url.endswith('/0.gif'):
                other_entered.wait(2)
                raise ValueError('broken first GIF')
            if url.endswith('/1.gif'):
                other_entered.set();release.wait(3)
            if url.endswith('/0.png'):fallback_requested.set()
            return Mock(content=GIF,headers={},status_code=200)
        item=normalize_catalog(room_payload(),'90001').tabs[0].items[1]
        items=tuple(replace(item,key=f'gift:{n}',gif_url=f'https://example.invalid/{n}.gif',
            image_url=f'https://example.invalid/{n}.png') for n in range(5))
        with tempfile.TemporaryDirectory() as root,patch('ddm_plugin_bili_live.gift_media.requests.get',side_effect=get):
            button=GiftButton();button.move(300,400);button.show()
            popup=GiftPopup(cache_dir=root)
            try:
                popup.set_state(GiftPanelState(tabs=(GiftTab('main','礼物',items),),wallet=99999))
                popup.open_at(button)
                settle(fallback_requested.is_set)
                release.set();settle(lambda:not _active_requests)
                images=popup.findChildren(GiftImageLabel)
                self.assertEqual(len(images),5)
                self.assertTrue(all(image.movie is not None or not image.pixmap().isNull() for image in images))
            finally:
                release.set();popup.shutdown();settle(lambda:not _active_requests)
                popup.deleteLater();button.close();APP.processEvents()

    def test_invalid_or_incomplete_download_is_not_published(self):
        with tempfile.TemporaryDirectory() as root,patch('ddm_plugin_bili_live.gift_media.requests.get',return_value=Mock(content=b'partial',headers={'Content-Length':'900'},status_code=200)):
            loader=GiftMediaLoader(root);loaded=[];failed=[]
            loader.loaded.connect(lambda *x:loaded.append(x));loader.failed.connect(lambda *x:failed.append(x))
            loader.request(('file:///local','https://example.invalid/bad.gif'),1)
            settle(lambda:len(failed)==2);self.assertEqual(loaded,[])
            self.assertEqual(list(Path(root).iterdir()),[]);loader.shutdown()

if __name__=='__main__':unittest.main()
