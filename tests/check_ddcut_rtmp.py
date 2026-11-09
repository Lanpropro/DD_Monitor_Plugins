"""Local RTMP protocol/push, archive lifecycle and embedded Qt review regression."""
from contextlib import contextmanager
import asyncio
import importlib
import json
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import check_ddcut_projects as fixtures
from check_ddcut_projects import app, args, remainder, ffmpeg, media, ui, until
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtWidgets import QLineEdit, QMainWindow

capture = importlib.import_module('ddcut_ranges_test.rtmp_capture')
inputs = importlib.import_module('ddcut_ranges_test.rtmp_inputs')
ArchiveServer = importlib.import_module('ddcut_ranges_test.preview_server').ArchiveServer


class RtmpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ddcut-rtmp-')
        self.root = Path(self.temporary.name)
        self.options = dict(ffmpeg=ffmpeg,directory=str(self.root/'recordings'))
        self.hub = media.TaskHub(self.root/'state')
        original = SimpleNamespace(options=lambda:dict(self.options),sources=lambda:[],close=lambda:None)
        self.inputs = inputs.RtmpInputs(original,self.hub)
        self.service = inputs.InputRecordingService(original,self.inputs)

    def tearDown(self):
        self.inputs.close()
        self.hub.close()
        self.temporary.cleanup()

    @contextmanager
    def sender(self, definition, seconds, *, key=None, application='live'):
        log = tempfile.TemporaryFile(dir=self.root)
        command = [ffmpeg,'-v','error','-re','-f','lavfi','-i','testsrc2=size=320x180:rate=20',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t',str(seconds),
            '-c:v','libx264','-threads','1','-g','40','-c:a','aac','-f','flv',
            f"rtmp://127.0.0.1:{definition['port']}/{application}/{key or definition['key']}"]
        process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.stdin.write(b'q\n')
                process.stdin.flush()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdin.close()
            log.close()

    def tick(self):
        self.inputs.poll()

    def test_acknowledgement_units_ping_and_active_shutdown(self):
        sent = []
        class Session:
            total_read_bytes = 5000001*8
            state = {}
            def write_chunk_to_stream(self,message):
                sent.append(message)
            async def drain(self):
                pass
        session = Session()
        controller = capture.PushController(None)
        asyncio.run(controller.acknowledge(session))
        self.assertEqual(sent[0].seq_number,5000001)
        asyncio.run(controller.acknowledge(session))
        self.assertEqual(len(sent),1)
        asyncio.run(controller.on_unknown_message(session,SimpleNamespace(msg_type_id=4,payload=b'\x00\x06\x00\x00\x00\x2a')))
        self.assertEqual(sent[-1].timestamp,42)
        definition = self.inputs.add('退出收尾')
        receiver = self.inputs.receivers[definition['id']]
        with self.sender(definition,20) as sender:
            until(lambda:receiver.snapshot()['info'] is not None,timeout=12)
            info = receiver.snapshot()['info']
            self.inputs.close()
            self.assertFalse(receiver.thread.is_alive())
            metadata = json.loads(Path(info['path']).with_name('recording.json').read_text(encoding='utf-8'))
            self.assertTrue(metadata['complete'])
            self.assertGreater(metadata['duration'],0)

    def test_real_push_growing_archive_disconnect_reconnect_and_preserved_files(self):
        definition = self.inputs.add('录制回归')
        receiver = self.inputs.receivers[definition['id']]
        paths = []
        for index in range(2):
            with self.sender(definition,6) as sender:
                until(lambda:receiver.snapshot()['info'] is not None,timeout=12)
                info = receiver.snapshot()['info']
                self.assertIsNone(sender.poll())
                self.assertGreaterEqual(info['duration'],2)
                paths.append(info['path'])
                self.tick()
                target = self.root/f'live-cut-{index}.mp4'
                media.export_clip(media.Job('RTMP live cut','export'),ffmpeg,info,0,.5,'precise',target,self.root/'work')
                self.assertAlmostEqual(media.probe(media.Job('probe','test'),ffmpeg,target,self.root/'work')['duration'],.5,delta=.1)
                until(lambda:sender.poll() is not None,timeout=10)
                self.assertEqual(sender.returncode,0)
            until(lambda:receiver.snapshot()['state']=='waiting')
            self.tick()
            metadata = json.loads(Path(info['path']).with_name('recording.json').read_text(encoding='utf-8'))
            self.assertTrue(metadata['complete'])
            self.assertIn('#EXT-X-PLAYLIST-TYPE:VOD',Path(info['path']).read_text(encoding='utf-8'))
            self.assertAlmostEqual(metadata['duration'],6,delta=.15)
        self.assertNotEqual(*paths)
        port,key = definition['port'],definition['key']
        self.inputs.close()
        restored = inputs.RtmpInputs(self.inputs.service,self.hub)
        self.inputs = restored
        self.assertEqual((restored.definitions[0]['port'],restored.definitions[0]['key']),(port,key))
        restored.remove(definition['id'])
        self.assertTrue(all(Path(path).is_file() for path in paths))
        self.assertFalse(restored.receivers)
        self.assertFalse(receiver.thread.is_alive())

    def test_auth_disabled_duplicate_publish_port_collision_and_release(self):
        definition = self.inputs.add('密钥测试')
        receiver = self.inputs.receivers[definition['id']]
        for kwargs in (dict(key='invalid-key'),dict(application='other')):
            with self.sender(definition,.5,**kwargs) as sender:
                until(lambda:sender.poll() is not None)
                self.assertNotEqual(sender.returncode,0)
            self.assertFalse(list(self.root.glob('recordings/DDCut_Replay/*')))
            self.assertTrue(receiver.snapshot()['error'])
        with self.sender(definition,8) as first:
            until(lambda:receiver.snapshot()['state']=='recording')
            with self.sender(definition,.5) as duplicate:
                until(lambda:duplicate.poll() is not None)
                self.assertNotEqual(duplicate.returncode,0)
            self.assertIsNone(first.poll())
            self.inputs.set_enabled(definition['id'],False)
            until(lambda:receiver.snapshot()['state']=='disabled')
        with self.sender(definition,.5) as disabled:
            until(lambda:disabled.poll() is not None)
            self.assertNotEqual(disabled.returncode,0)
        with self.assertRaises(OSError):
            capture.RtmpReceiver({**definition,'id':'collision'},self.options,queue.Queue())
        port = definition['port']
        self.inputs.remove(definition['id'])
        with socket.socket() as check:
            self.assertNotEqual(check.connect_ex(('127.0.0.1',port)),0)

    def test_native_embedded_controls_cover_live_review_original_mode_and_remove(self):
        host = QMainWindow()
        ui.theme.apply('dark')
        host.setStyleSheet(ui.theme.qss())
        host.setAttribute(Qt.WA_ShowWithoutActivating if args.native else Qt.WA_DontShowOnScreen)
        if args.native:
            host.move(-30000,-30000)
        server = ArchiveServer()
        owner = SimpleNamespace(context=SimpleNamespace(window=host),hub=self.hub,service=self.service,inputs=self.inputs,
                                capture_mode='original',preview_server=server)
        view = ui.Workspace(owner)
        host.setCentralWidget(view)
        host.resize(1440,900)
        host.show()
        timer = QTimer(host)
        timer.timeout.connect(lambda:(self.inputs.poll(),view.refresh_sources(),view.refresh_live(),view.rtmp_page.refresh()))
        timer.start(200)
        try:
            view.show_inputs()
            page = view.rtmp_page
            page.name.setText('OBS回归')
            page.add()
            definition = self.inputs.definitions[0]
            self.assertIn(str(definition['port']),page.address.text())
            self.assertEqual(page.key.text(),definition['key'])
            self.assertEqual(page.key.echoMode(),QLineEdit.EchoMode.Password)
            with self.sender(definition,9) as sender:
                until(lambda:bool(self.inputs.sources()),timeout=12)
                self.assertIs(view.library_review.currentWidget(),page)
                page.review()
                self.assertIs(view.library_review.currentWidget(),view.review_panel)
                self.assertEqual(view.current['room']['platform'],'rtmp')
                until(lambda:view.preview.core and view.preview.core.time_pos is not None)
                first = view.timeline.duration
                until(lambda:view.timeline.duration>first,timeout=10)
                cover = Path(view.current['path']).with_name('cover.png')
                until(cover.is_file)
                view.refresh_sources()
                self.assertTrue(self.inputs.live_recording('rtmp:'+definition['id'],view.current['session_id']).get('cover_path'))
                self.assertIsNone(sender.poll())
                host.resize(920,600)
                view.show_inputs()
                app.processEvents()
                self.assertLessEqual(host.width(),920)
                self.assertTrue(page.address.isVisible())
                if args.screenshots:
                    args.screenshots.mkdir(parents=True,exist_ok=True)
                    page.grab().save(str(args.screenshots/'rtmp-inputs.png'))
            until(lambda:self.inputs.receivers[definition['id']].snapshot()['state']=='waiting')
            self.tick()
            before = list(self.root.glob('recordings/DDCut_Replay/*/*.m3u8'))
            with patch.object(ui.QMessageBox,'question',return_value=ui.QMessageBox.Yes):
                page.remove()
            self.assertFalse(self.inputs.definitions)
            self.assertTrue(all(path.is_file() for path in before))
        finally:
            timer.stop()
            view.close()
            host.close()
            server.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
            app.processEvents()


if __name__=='__main__':
    results=[]
    def checks():
        result=unittest.main(argv=[sys.argv[0],*remainder],exit=False)
        results.append(result.result.wasSuccessful())
        app.quit()
    app.setQuitOnLastWindowClosed(False)
    QTimer.singleShot(0,checks)
    app.exec()
    app.shutdown()
    raise SystemExit(0 if results and results[0] else 1)
