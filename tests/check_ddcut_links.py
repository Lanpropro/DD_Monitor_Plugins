"""Local URI relay, isolated Windows registration and native add-dialog flow."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
import uuid

from check_ddcut_projects import app, args, remainder, media, ui, until
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtWidgets import QMainWindow
from ddm import add_room_dialog

server_api = importlib.import_module('ddcut_ranges_test.link_server')
links_api = importlib.import_module('ddcut_ranges_test.deep_links')
windows = importlib.import_module('ddcut_ranges_test.deep_link_windows')


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ddcut-links-')
        self.root = Path(self.temporary.name)
        self.hub = media.TaskHub(self.root/'中文 空格')
        self.registry = 'Software\\DDCutReplayTests\\'+uuid.uuid4().hex

    def tearDown(self):
        windows.unregister(self.hub.root/'link-endpoint.json',registry_path=self.registry)
        self.hub.close()
        self.temporary.cleanup()

    def post(self,server,uri,**headers):
        request = Request(f'http://127.0.0.1:{server.http.server_port}/link',data=json.dumps({'uri':uri}).encode(),
            headers={'Content-Type':'application/json','X-DDCut-Key':server.token,**headers})
        with build_opener(ProxyHandler({})).open(request,timeout=3) as response:
            return response.status

    def test_url_validation_and_token_queue_lifecycle(self):
        self.assertEqual(server_api.room_link('ddcut://www.douyu.com/6657'),'https://www.douyu.com/6657')
        self.assertEqual(server_api.room_link('ddcut://www.youtube.com/watch?v=abc#fragment'),'https://www.youtube.com/watch?v=abc')
        for uri in ('ddcut://evil.example/1','ddcut://live.bilibili.com.evil.example/1',
                    'ddcut://user@live.bilibili.com/1','ddcut://live.bilibili.com:9999/1',
                    'ddcut://live.bilibili.com/1\n','file:///C:/temp','https://live.bilibili.com/1'):
            with self.assertRaises(ValueError,msg=uri):
                server_api.room_link(uri)
        path = self.hub.root/'link-endpoint.json'
        server = server_api.LinkServer(path)
        try:
            for headers in ({'X-DDCut-Key':'wrong'},{'Origin':'https://example.com'}):
                with self.assertRaises(HTTPError) as error:
                    self.post(server,'ddcut://live.bilibili.com/123',**headers)
                self.assertEqual(error.exception.code,403)
            self.assertEqual(self.post(server,'ddcut://live.bilibili.com/123'),202)
            self.assertEqual(server.requests.get_nowait(),'https://live.bilibili.com/123')
            with self.assertRaises(HTTPError) as error:
                self.post(server,'ddcut://evil.example/123')
            self.assertEqual(error.exception.code,400)
            for _ in range(16):
                self.post(server,'ddcut://live.bilibili.com/123')
            with self.assertRaises(HTTPError) as error:
                self.post(server,'ddcut://live.bilibili.com/123')
            self.assertEqual(error.exception.code,429)
        finally:
            server.close()
        self.assertFalse(path.exists())
        self.assertFalse(server.thread.is_alive())

    def test_isolated_registry_command_conflict_and_removal(self):
        import winreg
        endpoint = self.hub.root/'link-endpoint.json'
        launch = self.root/'中文 启动.cmd'
        windows.register(endpoint,launch,registry_path=self.registry)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.registry+'\\shell\\open\\command') as key:
            value = winreg.QueryValueEx(key,'')[0]
        self.assertIn('deep_link_relay.ps1',value)
        self.assertTrue(value.endswith('-Uri "%1"'))
        self.assertIn(str(launch),value)
        with self.assertRaises(RuntimeError):
            windows.register(self.root/'other.json',launch,registry_path=self.registry)
        with self.assertRaises(RuntimeError):
            windows.unregister(self.root/'other.json',registry_path=self.registry)
        windows.unregister(endpoint,registry_path=self.registry)
        with self.assertRaises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.registry)

    @contextmanager
    def window(self):
        host = QMainWindow()
        host.setAttribute(Qt.WA_DontShowOnScreen)
        host._normalize_room_input = lambda value:'123' if value=='https://live.bilibili.com/123' else ''
        added = []
        host._add_room_id = lambda identity,**kwargs:added.append((identity,kwargs))
        host.sidebar = SimpleNamespace(folder_state=lambda:[],create_folder=lambda name:None)
        owner = SimpleNamespace(context=SimpleNamespace(window=host),hub=self.hub,
            inputs=SimpleNamespace(rows=lambda:[],definitions=[]))
        links = links_api.DeepLinks(owner,registry_path=self.registry)
        owner.links = links
        view = SimpleNamespace(owner=owner,show_library=lambda:None,guard=lambda callback:callback())
        from ddcut_ranges_test.rtmp_page import RtmpPage
        page = RtmpPage(view)
        host.setCentralWidget(page)
        host.resize(920,600)
        ui.theme.apply('dark')
        host.setStyleSheet(ui.theme.qss())
        host.show()
        original = add_room_dialog.AddRoomDialog
        class HiddenDialog(original):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs)
                self.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            with patch.object(add_room_dialog,'AddRoomDialog',HiddenDialog):
                yield links,host,page,added
        finally:
            links.set_enabled(False)
            host.close()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
            app.processEvents()

    def test_qt_opt_in_prefill_confirmation_cancel_and_resume(self):
        with self.window() as (links,host,page,added):
            self.assertIsNone(links.server)
            page.link_enabled.setChecked(True)
            self.assertTrue(self.hub.preferences['room_links'])
            self.post(links.server,'ddcut://live.bilibili.com/123')
            links.poll()
            self.assertEqual(links.dialog.edit.text(),'https://live.bilibili.com/123')
            self.assertFalse(added)
            links.dialog.reject()
            self.assertIsNone(links.dialog)
            links.request('ddcut://live.bilibili.com/123')
            links.dialog.accept()
            self.assertEqual(added,[('123',{'folder_id':''})])
            links.close()
            self.assertTrue(self.hub.preferences['room_links'])
            restored = links_api.DeepLinks(links.owner,registry_path=self.registry)
            self.assertIsNotNone(restored.server)
            restored.close()
            page.link_enabled.setChecked(False)
            self.assertFalse(self.hub.preferences['room_links'])
            self.assertIsNone(links.server)
            self.assertLessEqual(host.width(),920)

    def relay(self,endpoint,launch):
        script = Path(windows.__file__).with_name('deep_link_relay.ps1')
        return subprocess.run(['powershell.exe','-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass',
            '-File',str(script),'-Endpoint',str(endpoint),'-Launch',str(launch),'-Uri','ddcut://live.bilibili.com/123'],
            capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=50)

    def test_actual_powershell_warm_and_cold_relay_with_unicode_paths(self):
        endpoint = self.hub.root/'link-endpoint.json'
        server = server_api.LinkServer(endpoint)
        try:
            result = self.relay(endpoint,self.root/'must-not-launch.cmd')
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            self.assertEqual(server.requests.get(timeout=3),'https://live.bilibili.com/123')
        finally:
            server.close()
        result_path = self.root/'cold-result.json'
        process_path = self.root/'cold-process.json'
        source = self.root/'cold-server.py'
        module = Path(server_api.__file__).resolve()
        source.write_text('import importlib.util,json,os\n'
            f'with open({str(process_path)!r},"w") as out:json.dump(os.getppid(),out)\n'
            f"spec=importlib.util.spec_from_file_location('coldserver',{str(module)!r})\n"
            'm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
            f's=m.LinkServer({str(endpoint)!r})\n'
            'try:\n value=s.requests.get(timeout=40)\n'
            f' with open({str(result_path)!r},"w",encoding="utf-8") as out:json.dump(value,out)\n'
            'finally:s.close()\n',encoding='utf-8')
        launch = self.hub.root/'冷启动.cmd'
        launch.write_text('@echo off\r\nchcp 65001 >nul\r\n'+subprocess.list2cmdline([sys.executable,str(source)])+'\r\n',encoding='utf-8')
        result = self.relay(endpoint,launch)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        until(result_path.exists)
        self.assertEqual(json.loads(result_path.read_text(encoding='utf-8')),'https://live.bilibili.com/123')
        until(lambda:not endpoint.exists())
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        process = kernel.OpenProcess(0x00100000,False,json.loads(process_path.read_text()))
        if process:
            try:
                self.assertEqual(kernel.WaitForSingleObject(process,5000),0)
            finally:
                kernel.CloseHandle(process)

    def test_removed_plugin_clears_its_protocol_and_endpoint(self):
        with self.window() as (links,host,page,added):
            links.set_enabled(True)
            owner = links.owner
            owner.context.name = 'ddcut_replay'
            owner.context._manager = SimpleNamespace(_removed={'ddcut_replay'})
            plugin_type = importlib.import_module('ddcut_ranges_test').DDCutReplay
            plugin = plugin_type()
            plugin.context,plugin.links,plugin.hub = owner.context,links,self.hub
            plugin.on_unload()
            self.assertIsNone(links.server)
            self.assertFalse(links.endpoint.exists())
            self.assertFalse(self.hub.preferences['room_links'])


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
