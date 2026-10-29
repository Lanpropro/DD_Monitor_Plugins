"""Run the real browser bridge in off-the-record Qt pages with synthetic modules only."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument("--host", type=Path, required=True)
parser.add_argument("--plugins-dir", type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ["DDM_NO_SAVE"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QWidget
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from ddm.plugins import PluginManager

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
MANAGER = PluginManager(plugins_dir=str(args.plugins_dir or Path(__file__).resolve().parents[1] / "plugins"),
                        enabled=["domestic_live"])
MANAGER.load()
module = importlib.import_module("ddm_plugin_domestic_live.douyin_browser")


class BlockNetwork(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        info.block(info.requestUrl().scheme() not in ("data", "about"))


class BrowserTests(unittest.TestCase):
    def exercise(self, behavior, expected_error=None, *, module_error=False):
        dialogs = []
        html = """<html><head></head><body><script>
            window.calls=0; window.webpackChunkdouyin_live_v2=[];
            const requireModule=()=>{ %s return {W:async params=>{
                window.calls++; window.params=params;
                %s
            }};};
            requireModule.m={123:function(){return '/webcast/room/chat/ paste_edit_method';}};
            window.webpackChunkdouyin_live_v2.push=entry=>entry[2](requireModule);
            </script></body></html>""" % ("throw new ReferenceError('PRIVATE');" if module_error else "", behavior)

        class Dialog(QDialog):
            def __init__(self, *a, **kw):
                super().__init__()
                self.status = QLabel(self)
                self.remember = QPushButton(self)
                self.forget_button = QPushButton(self)
                self.read_button = QPushButton(self)
                self.read_button.clicked.connect(lambda: None)
                self.browser = QWebEngineView(self)
                self.profile = QWebEngineProfile(self)
                self.interceptor = BlockNetwork(self.profile)
                self.profile.setUrlRequestInterceptor(self.interceptor)
                self.browser.setPage(QWebEnginePage(self.profile, self.browser))
                self.browser.load = lambda url: self.browser.setHtml(html, QUrl("https://live.douyin.com/123"))
                dialogs.append(self)

            def deleteLater(self):
                pass  # Keep the completed page alive until its invocation count has been checked.

        parent = QWidget()
        bridge = module.BrowserChat(SimpleNamespace(room_url=lambda _: "https://live.douyin.com/123"),
                                    SimpleNamespace(window=None), parent)
        outcome = {}

        def request():
            try:
                outcome["result"] = bridge.request("douyin:123", {"room_id": "999999", "content": "fixture"}, lambda: False)
            except Exception as error:
                outcome["error"] = error

        with patch("ddm.platform_login.PlatformFollowDialog", Dialog):
            worker = threading.Thread(target=request)
            worker.start()
            deadline = time.monotonic() + 12
            while worker.is_alive() and time.monotonic() < deadline:
                QTest.qWait(20)
            if worker.is_alive():
                bridge.shutdown()
                QTest.qWait(200)
            worker.join(1)
            self.assertFalse(worker.is_alive(), "Qt browser bridge did not finish")
            self.assertEqual(len(dialogs), 1)
            snapshot = []
            dialogs[0].browser.page().runJavaScript(
                "JSON.stringify({calls:window.calls,params:window.params})", snapshot.append)
            deadline = time.monotonic() + 3
            while not snapshot and time.monotonic() < deadline:
                QTest.qWait(20)
            self.assertTrue(snapshot)
            state = json.loads(snapshot[0])
            self.assertEqual(state["calls"], 0 if module_error else 1)
            if not module_error:
                self.assertEqual(state["params"]["room_id_str"], "999999")
            if expected_error:
                self.assertIsInstance(outcome.get("error"), module.InteractionError)
                text = str(outcome["error"])
                for part in expected_error:
                    self.assertIn(part, text)
                self.assertNotIn("PRIVATE", text)
                self.assertNotIn("msToken", text)
            else:
                self.assertNotIn("error", outcome)
                self.assertEqual(outcome["result"], {"status_code": 0, "data": {
                    "msg_id_str": "7560000000000000001", "content_self_see_status": 0}})
        bridge.shutdown()
        for dialog in dialogs:
            dialog.browser.setPage(QWebEnginePage(dialog.browser))
            QDialog.deleteLater(dialog)
        parent.deleteLater()
        QTest.qWait(50)

    def test_prepare_submit_and_sanitized_callback(self):
        self.exercise("return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',"
                      "content_self_see_status:0,content:'PRIVATE',cookie:'PRIVATE'}}};")

    def test_request_typeerror_is_classified_without_message_or_retry(self):
        self.exercise("throw new TypeError('PRIVATE https://invalid/?msToken=PRIVATE');",
                      ["官网客户端调用失败", "官网请求", "TypeError", "已调用发送：是"])

    def test_arbitrary_exception_name_is_not_exported(self):
        self.exercise("throw {name:'PRIVATE',message:'PRIVATE',stack:'PRIVATE'};",
                      ["官网客户端调用失败", "其他异常", "已调用发送：是"])

    def test_axios_failure_keeps_safe_type(self):
        self.exercise("throw Object.assign(new Error('PRIVATE'),{name:'AxiosError'});",
                      ["官网请求", "AxiosError", "已调用发送：是"])

    def test_invalid_response_has_distinct_stage(self):
        self.exercise("return {data:{cookie:'PRIVATE'}};",
                      ["官网未返回有效回执", "回执解析", "已调用发送：是"])

    def test_module_initialization_failure_never_invokes_send(self):
        self.exercise("return {};", ["模块初始化", "ReferenceError", "已调用发送：否"], module_error=True)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
