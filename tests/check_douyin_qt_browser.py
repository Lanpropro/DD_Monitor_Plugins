"""Real Qt native-input regression on isolated pages; no accounts or real messages."""
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
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QWidget
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from ddm.plugins import PluginManager
APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
MANAGER = PluginManager(plugins_dir=str(args.plugins_dir or Path(__file__).resolve().parents[1] / "plugins"), enabled=["domestic_live"])
MANAGER.load()
module = importlib.import_module("ddm_plugin_domestic_live.douyin_browser")
delivery_module = importlib.import_module("ddm_plugin_domestic_live.douyin_interaction")


class BlockNetwork(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        info.block(info.requestUrl().scheme() not in ("data", "about"))


class BrowserTests(unittest.TestCase):
    def exercise(self, *, text="中文🙂 hello", uid="42", room="999999", duplicate=False,
                 existing="", mismatch=False, receive="matched", cancel_before=False,
                 cancel_input=False, close_before=False, reload_before=False, late_context=False,
                 textarea=False, wrong_origin=False, official_scaffold=False, scaffold_text="\u200b"):
        dialogs, logs, snapshots = [], [], []
        tracker = delivery_module.BroadcastReceipts()
        cancellation = threading.Event()
        if cancel_before:
            cancellation.set()
        editor = '<textarea></textarea>' if textarea else '<div contenteditable="true"></div>'
        html = """<html><body><script>
        window.calls=0;window.events=[];
        window.__STORE__={roomStore:{roomInfo:{roomId:%s}},userStore:{userInfo:{id_str:%s}}};
        </script><div id="chatInput">%s<button class="webcast-chatroom___send-btn" disabled>Send</button></div>
        <script>
        const box=document.querySelector('#chatInput [contenteditable],#chatInput textarea');
        const button=document.querySelector('button');
        if(box.tagName==='TEXTAREA')box.value=%s;else box.textContent=%s;
        %s
        box.addEventListener('input',e=>{window.events.push({type:'input',trusted:e.isTrusted});button.disabled=false;%s});
        box.addEventListener('keydown',e=>{window.events.push({type:e.key,trusted:e.isTrusted});
            if(e.key==='Enter'){e.preventDefault();window.calls++;window.sent=box.value??box.textContent;%s}});
        %s
        %s
        %s
        </script></body></html>""" % (json.dumps(room),json.dumps("0" if late_context else uid),editor,json.dumps(existing),json.dumps(existing),
            "box.insertAdjacentHTML('afterbegin',"+json.dumps('<span data-zero-space="true" contenteditable="false">'+scaffold_text+'</span>')+");" if official_scaffold else "",
            "if(box.tagName==='TEXTAREA')box.value='changed';else box.textContent='changed';" if mismatch else "",
            "window.sent=window.sent.replace(/\\u200b/g,'');" if official_scaffold else "",
            "document.querySelector('#chatInput').appendChild(box.cloneNode(true));" if duplicate else "",
            "setTimeout(()=>{window.__STORE__.userStore.userInfo.id_str='42';},300);" if late_context else "",
            "box.style.display='none';setTimeout(()=>box.style.display='block',450);" if reload_before else "")
        class Dialog(QDialog):
            def __init__(self,*a,**kw):
                super().__init__()
                self.setGeometry(-8000,-8000,1100,720)
                self.status=QLabel(self)
                self.remember=QPushButton(self)
                self.forget_button=QPushButton(self)
                self.read_button=QPushButton(self)
                self.read_button.clicked.connect(lambda:None)
                self.browser=QWebEngineView(self)
                self.browser.setGeometry(0,80,1050,580)
                self.profile=QWebEngineProfile(self)
                self.block=BlockNetwork(self.profile)
                self.profile.setUrlRequestInterceptor(self.block)
                self.browser.setPage(QWebEnginePage(self.profile,self.browser))
                follow=QWebEngineScript();follow.setName('ddm-follow-list')
                follow.setInjectionPoint(QWebEngineScript.DocumentCreation)
                follow.setWorldId(QWebEngineScript.MainWorld)
                follow.setSourceCode('window.forbiddenHook=true;')
                self.browser.page().scripts().insert(follow)
                self.loads=0
                def load(url):
                    self.loads+=1
                    self.browser.setHtml(html,QUrl('https://invalid.example/123' if wrong_origin else 'https://live.douyin.com/123'))
                self.browser.load=load
                dialogs.append(self)
                if close_before:QTimer.singleShot(250,self.reject)
                if reload_before:QTimer.singleShot(200,self.read_button.click)
            def deleteLater(self):
                pass
        parent=QWidget()
        bridge=module.BrowserChat(SimpleNamespace(room_url=lambda _: 'https://live.douyin.com/123',_chat_delivery=tracker),SimpleNamespace(window=None,log=logs.append),parent)
        outcome={}; observed=False; cancelled=cancellation.is_set
        def worker_body():
            try:
                outcome['result']=bridge.request('douyin:123',{'room_id':'999999','content':text,'_sender_uid':'42'},cancelled)
            except Exception as error:outcome['error']=error
            finally:bridge.finish(cancelled,delivery_unconfirmed=isinstance(outcome.get('error'),delivery_module.DeliveryUnconfirmed))
        def snapshot(data):
            nonlocal observed
            if not isinstance(data,str):return
            try:state=json.loads(data)
            except ValueError:return
            snapshots.append(state)
            if cancel_input and state.get('events'):cancellation.set()
            if state.get('calls') and not observed:
                observed=True
                event={'message_id':'7560000000000000001','uid':'42','text':text}
                if receive=='wrong_uid':event['uid']='24'
                if receive=='wrong_text':event['text']='another message'
                if receive!='none':tracker.observe('douyin:124' if receive=='wrong_room' else 'douyin:123',event)
        driver=QTimer();driver.setInterval(20)
        def inspect():
            if dialogs:dialogs[0].browser.page().runJavaScript('JSON.stringify({calls:window.calls,events:window.events,sent:window.sent,hook:!!window.forbiddenHook})',snapshot)
        driver.timeout.connect(inspect)
        try:
            with patch('ddm.platform_login.PlatformFollowDialog',Dialog), patch.object(module,'DELIVERY_TIMEOUT',.35,create=True):
                worker=threading.Thread(target=worker_body);worker.start();driver.start()
                end=time.monotonic()+7
                while worker.is_alive() and time.monotonic()<end:QTest.qWait(20)
                if worker.is_alive():cancellation.set();bridge.shutdown();QTest.qWait(250)
                worker.join(1);self.assertFalse(worker.is_alive())
                QTest.qWait(100);inspect();QTest.qWait(50)
                if cancel_before:
                    self.assertFalse(dialogs);self.assertIn('error',outcome);return
                self.assertEqual(len(dialogs),1)
                self.assertTrue(snapshots)
                state=snapshots[-1]
                expected_send=not (uid!='42' or room!='999999' or duplicate or existing or mismatch or cancel_input or close_before or wrong_origin or (official_scaffold and scaffold_text!="\u200b"))
                self.assertEqual(state.get('calls',0),1 if expected_send else 0,(state,outcome,logs))
                self.assertFalse(state['hook'],'The follow-list script must not run in the sender page')
                self.assertTrue(all(e['trusted'] for e in state['events']))
                if expected_send:self.assertEqual(state['sent'],text)
                if expected_send and receive=='matched':
                    self.assertEqual(outcome.get('result'),{'broadcast_id':'7560000000000000001'})
                    self.assertIsNone(bridge.active)
                elif expected_send:
                    self.assertIsInstance(outcome.get('error'),delivery_module.DeliveryUnconfirmed)
                    self.assertTrue(dialogs[0].isVisible())
                    self.assertFalse(dialogs[0].read_button.isEnabled())
                    blocked=dict(room='douyin:123',params={},done=threading.Event(),cancelled=lambda:False)
                    bridge.open(blocked);self.assertEqual(blocked['result']['error'],'review_open')
                else:
                    self.assertIn('error',outcome)
                    if '已有草稿' in str(outcome['error']):
                        self.assertTrue(dialogs[0].isVisible(),'Preflight rejection must retain the original editor')
                        self.assertFalse(dialogs[0].read_button.isEnabled())
                        self.assertIs(bridge.active,dialogs[0])
                        summary=next(line for line in logs if 'official_draft_exists' in line)
                        self.assertIn('raw_length=',summary)
                        self.assertIn('read_length=',summary)
                        if existing=='\n':self.assertIn('lf=1',summary)
                        if existing.strip():self.assertNotIn(existing,summary)
                        dialogs[0].reject();self.assertIsNone(bridge.active)

                if reload_before:self.assertEqual(dialogs[0].loads,2)
                self.assertNotIn(text,'\n'.join(logs))
                self.assertNotIn('7560000000000000001','\n'.join(logs))
        finally:
            driver.stop();bridge.shutdown()
            for dialog in dialogs:
                dialog.browser.setPage(QWebEnginePage(dialog.browser));QDialog.deleteLater(dialog)
            parent.deleteLater();QTest.qWait(50)

    def test_native_unicode_input_and_single_enter(self):self.exercise()
    def test_official_empty_zero_space_node_is_not_a_draft(self):self.exercise(official_scaffold=True)
    def test_official_zero_space_node_does_not_hide_a_real_draft(self):self.exercise(official_scaffold=True,existing='existing draft')
    def test_unmarked_zero_width_draft_is_not_overwritten(self):self.exercise(existing='\u200b')
    def test_marked_node_containing_real_text_is_not_overwritten(self):self.exercise(official_scaffold=True,scaffold_text='\u200bdraft')
    def test_native_textarea_input(self):self.exercise(textarea=True)
    def test_late_login_context_waits_without_an_extra_click(self):self.exercise(late_context=True)
    def test_wrong_account_never_enters_text(self):self.exercise(uid='43')
    def test_wrong_room_never_enters_text(self):self.exercise(room='888888')
    def test_wrong_origin_never_enters_text(self):self.exercise(wrong_origin=True)
    def test_multiple_visible_editors_are_rejected(self):self.exercise(duplicate=True)
    def test_existing_official_draft_is_not_overwritten(self):self.exercise(existing='existing draft')
    def test_newline_content_is_diagnosed_without_sending(self):self.exercise(existing='\n')

    def test_draft_shape_summary_only_logs_bounded_integer_counts(self):
        self.assertEqual(module.draft_shape_summary({'raw_length':5,'read_length':True,'lf':'secret','other':-1,'br_nodes':1000001,'text':'private','uid':'42'}),'raw_length=5')
        self.assertEqual(module.draft_shape_summary('private'),'shape_unavailable')
        self.assertEqual(module.draft_shape_summary({}),'shape_unavailable')
    def test_changed_editor_text_is_not_submitted(self):self.exercise(mismatch=True)
    def test_cancel_before_open_never_loads_page(self):self.exercise(cancel_before=True)
    def test_cancel_after_typing_never_presses_enter(self):self.exercise(cancel_input=True)
    def test_user_close_before_ready_never_submits(self):self.exercise(close_before=True,reload_before=True)
    def test_reload_before_typing_still_submits_once(self):self.exercise(reload_before=True)
    def test_no_broadcast_retains_page_and_does_not_retry(self):self.exercise(receive='none')
    def test_other_user_does_not_confirm_submission(self):self.exercise(receive='wrong_uid')
    def test_other_room_does_not_confirm_submission(self):self.exercise(receive='wrong_room')
    def test_other_text_does_not_confirm_submission(self):self.exercise(receive='wrong_text')

if __name__=='__main__':unittest.main(argv=[sys.argv[0]],verbosity=2)
