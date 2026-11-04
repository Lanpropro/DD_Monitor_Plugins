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
delivery_module = importlib.import_module("ddm_plugin_domestic_live.douyin_interaction")


class BlockNetwork(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        info.block(info.requestUrl().scheme() not in ("data", "about"))


class BrowserTests(unittest.TestCase):
    def exercise(self, behavior, expected_error=None, *, module_error=False, delayed_dependency=False,
                 security_status="success", room_id="999999", uid="42", hold_ack=False, expected_stages=(),
                 late_security=False, invalid_security=False, review_403=False, review_shutdown=False,
                 review_delivery=False, cancelled_delivery=False, observe_own=False):
        dialogs = []
        initialization = "throw new ReferenceError('PRIVATE');" if module_error else ""
        if module_error == "missing_call":
            initialization = "throw new TypeError(\"Cannot read properties of undefined (reading 'call') PRIVATE\");"
        html = """<html><head></head><body><script>
            window.calls=0; window.webpackChunkdouyin_live_v2=[];
            window.__STORE__={roomStore:{roomInfo:{roomId:%s}},userStore:{userInfo:{id_str:%s}}};
            const security={getSecuritySdkInitWebStatus:async()=>%s};
            const securityFactory=function(){return 'getSecuritySdkInitWebStatus:function securitySdkInitWeb:function';};
            const requireModule=id=>{ %s if(id==='789')return security; return {W:async params=>{
                window.calls++; window.params=params;
                %s
            }};};
            requireModule.m={123:function(){return '/webcast/room/chat/ paste_edit_method';},789:securityFactory};
            const loadedChunks=new Set(); window.loaderCaptures=0;
            window.webpackChunkdouyin_live_v2.push=entry=>{
                // The official runtime executes a callback only for a new chunk ID.
                if(entry[0].some(id=>!loadedChunks.has(id)) && entry[2]){
                    window.loaderCaptures++; entry[2](requireModule);
                }
                entry[0].forEach(id=>loadedChunks.add(id));
            };
            </script></body></html>""" % (json.dumps(room_id), json.dumps(uid), json.dumps(security_status), initialization, behavior)
        if invalid_security:
            html=html.replace("const security={getSecuritySdkInitWebStatus:async()=>", "const security={unused:async()=>")
        if late_security:
            html=html.replace("const loadedChunks=new Set();", """
                delete requireModule.m[789];
                setTimeout(()=>{requireModule.m[789]=securityFactory;},800);
                const loadedChunks=new Set();""")
        if delayed_dependency:
            html = html.replace("requireModule.m={123:function(){return '/webcast/room/chat/ paste_edit_method';},789:securityFactory};", """
                const cache={};
                const factories={123:function(module,exports,require){
                    const client=require(456); // /webcast/room/chat/ paste_edit_method
                    exports.W=client.W;
                },789:function(module){module.exports=security;
                    return 'getSecuritySdkInitWebStatus:function securitySdkInitWeb:function';}};
                const webpackRequire=id=>{
                    if(cache[id])return cache[id].exports;
                    const module=cache[id]={exports:{}};
                    factories[id].call(module.exports,module,module.exports,webpackRequire);
                    return module.exports;
                };
                webpackRequire.m=factories;
                setTimeout(()=>{factories[456]=function(module){module.exports=requireModule();};},800);
            """)
            html = html.replace("entry[2](requireModule)", "entry[2](webpackRequire)")

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
        logs = []
        tracker = delivery_module.BroadcastReceipts()
        bridge = module.BrowserChat(SimpleNamespace(room_url=lambda _: "https://live.douyin.com/123", _chat_delivery=tracker),
                                    SimpleNamespace(window=None, log=logs.append), parent)
        outcome = {}
        accepted, release = threading.Event(), threading.Event()
        cancelled = lambda: cancelled_delivery and bool(outcome)

        def request():
            try:
                outcome["result"] = bridge.request("douyin:123", {"room_id": "999999", "content": "fixture", "_sender_uid":"42"}, cancelled)
                if hold_ack:
                    accepted.set()
                    release.wait(3)
            except Exception as error:
                outcome["error"] = error
            finally:
                bridge.finish(cancelled, delivery_unconfirmed=review_delivery)

        with patch("ddm.platform_login.PlatformFollowDialog", Dialog):
            worker = threading.Thread(target=request)
            worker.start()
            deadline = time.monotonic() + 12
            while worker.is_alive() and time.monotonic() < deadline:
                QTest.qWait(20)
                if hold_ack and accepted.is_set() and not release.is_set():
                    outcome['held_open'] = bridge.active is not None and bridge.active.isVisible()
                    release.set()
            if worker.is_alive():
                bridge.shutdown()
                QTest.qWait(200)
            worker.join(1)
            self.assertFalse(worker.is_alive(), "Qt browser bridge did not finish")
            self.assertEqual(len(dialogs), 1)
            if hold_ack:
                self.assertTrue(outcome.get('held_open'), 'Accepted response must not destroy the page before delivery confirmation')
            if review_403 or review_delivery and not cancelled_delivery:
                QTest.qWait(100)
                self.assertIs(bridge.active, dialogs[0])
                self.assertTrue(dialogs[0].isVisible(), 'Keep the unconfirmed page available for a manual official-page comparison')
                self.assertFalse(dialogs[0].read_button.isEnabled())
                self.assertIn("0条", dialogs[0].status.text())
                dialogs[0].read_button.click()
                QTest.qWait(250)
                blocked=dict(room="douyin:123", params={"room_id":"999999","content":"another fixture"},
                             cancelled=lambda:False, done=threading.Event())
                bridge.open(blocked)
                self.assertTrue(blocked['done'].is_set())
                self.assertEqual(blocked['result']['error'], 'review_open')
                self.assertEqual(len(dialogs), 1)
                if observe_own:
                    tracker.observe("douyin:123", {"message_id":"7560000000000000001", "uid":"42"})
                    tracker.observe("douyin:123", {"message_id":"other", "uid":"24"})
                    tracker.observe("douyin:124", {"message_id":"other-room", "uid":"42"})
                    QTest.qWait(600)
                    self.assertIn("0条", dialogs[0].status.text())
                    tracker.observe("douyin:123", {"message_id":"new-own", "uid":"42", "text":"PRIVATE"})
                    QTest.qWait(600)
                    self.assertIn("至少1条", dialogs[0].status.text())
                    self.assertIn("原软件请求仍未确认", dialogs[0].status.text())
                    self.assertTrue(any("阶段=review_self_broadcast " in line for line in logs))
            snapshot = []
            dialogs[0].browser.page().runJavaScript(
                "JSON.stringify({calls:window.calls,params:window.params,loaderCaptures:window.loaderCaptures})", snapshot.append)
            deadline = time.monotonic() + 3
            while not snapshot and time.monotonic() < deadline:
                QTest.qWait(20)
            self.assertTrue(snapshot)
            state = json.loads(snapshot[0])
            self.assertEqual(state["loaderCaptures"], 1, "Reuse the document's captured loader, not a consumed chunk ID")
            self.assertEqual(state["calls"], 0 if module_error or invalid_security or security_status != "success" or room_id != "999999" or uid != "42" else 1)
            if state["calls"]:
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
            for stage in expected_stages:
                self.assertTrue(any(f"阶段={stage} " in line for line in logs), (stage, logs))
            self.assertNotIn("PRIVATE", "\n".join(logs))
            self.assertNotIn("7560000000000000001", "\n".join(logs))
            if cancelled_delivery:
                self.assertIsNone(bridge.active)
                self.assertFalse(dialogs[0].isVisible())
            if review_403 or review_delivery and not cancelled_delivery:
                if review_shutdown:
                    bridge.shutdown()
                else:
                    dialogs[0].reject()
                self.assertFalse(dialogs[0].isVisible())
                self.assertIsNone(bridge.active)
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

    def test_request_failure_preserves_numeric_status_and_fixed_code_only(self):
        self.exercise("throw Object.assign(new Error('PRIVATE'),{code:'ERR_NETWORK',response:{status:403,"
                      "data:{status_code:10003,content:'PRIVATE',cookie:'PRIVATE'}}});",
                      ["HTTP：403", "接口状态：10003", "请求代码：ERR_NETWORK", "已调用发送：是"])

    def test_request_failure_excludes_arbitrary_status_and_code(self):
        self.exercise("throw Object.assign(new Error('PRIVATE'),{code:'PRIVATE',response:{status:'PRIVATE',"
                      "data:{status_code:{cookie:'PRIVATE'}}}});", ["官网请求", "已调用发送：是"])

    def test_http_403_keeps_page_for_review_without_retry_until_user_closes(self):
        self.exercise("throw Object.assign(new Error('PRIVATE'),{response:{status:403}});",
                      ["HTTP：403", "已调用发送：是"], review_403=True)

    def test_shutdown_closes_retained_http_403_page(self):
        self.exercise("throw Object.assign(new Error('PRIVATE'),{response:{status:403}});",
                      ["HTTP：403"], review_403=True, review_shutdown=True)

    def test_unmatched_delivery_keeps_accepted_page_until_user_closes(self):
        self.exercise("return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',content_self_see_status:0}}};",
                      review_delivery=True, expected_stages=("ack", "delivery_unconfirmed_review"))

    def test_cancelled_unmatched_delivery_does_not_retain_page(self):
        self.exercise("return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',content_self_see_status:0}}};",
                      review_delivery=True, cancelled_delivery=True)

    def test_retained_page_observes_own_broadcast_without_accepting_original_send(self):
        self.exercise("return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',content_self_see_status:0}}};",
                      review_delivery=True, observe_own=True)

    def test_invalid_response_has_distinct_stage(self):
        self.exercise("return {data:{cookie:'PRIVATE'}};",
                      ["官网未返回有效回执", "回执解析", "已调用发送：是"])

    def test_module_initialization_failure_never_invokes_send(self):
        self.exercise("return {};", ["模块初始化", "ReferenceError", "已调用发送：否"], module_error=True)

    def test_helper_waits_for_its_late_dependency_before_initialization(self):
        self.exercise("return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',"
                      "content_self_see_status:0}}};", delayed_dependency=True)

    def test_module_typeerror_has_fixed_cause_without_raw_message(self):
        self.exercise("return {};", ["TypeError", "模块调用目标缺失", "已调用发送：否"], module_error="missing_call")

    def test_security_timeout_does_not_submit(self):
        self.exercise("return {};", ["安全环境初始化超时", "已调用发送：否"], security_status="timeout")

    def test_security_failure_does_not_submit(self):
        self.exercise("return {};", ["安全环境初始化失败", "已调用发送：否"], security_status="fail")

    def test_late_security_module_is_waited_for_before_single_submission(self):
        self.exercise("return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',"
                      "content_self_see_status:0}}};", late_security=True)

    def test_missing_security_export_is_distinct_and_never_submits(self):
        self.exercise("return {};", ["模块已找到", "初始化导出不可用", "已调用发送：否"], invalid_security=True)

    def test_wrong_live_session_never_submits(self):
        self.exercise("return {};", ["官网当前直播与目标直播不一致", "已调用发送：否"], room_id="888888")

    def test_wrong_account_never_submits(self):
        self.exercise("return {};", ["官网当前账号与所选账号不一致", "已调用发送：否"], uid="43")

    def test_accepted_request_keeps_page_alive_until_caller_finishes(self):
        self.exercise("return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',"
                      "content_self_see_status:0}}};", hold_ack=True)

    def test_browser_stages_separate_page_loading_from_pending_request(self):
        self.exercise("await new Promise(resolve=>setTimeout(resolve,400));"
                      "return {data:{status_code:0,data:{msg_id_str:'7560000000000000001',content_self_see_status:0}}};",
                      expected_stages=("page_loading", "page_loaded", "client_ready", "request", "ack", "page_closed"))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
