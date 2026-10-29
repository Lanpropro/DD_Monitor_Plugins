"""GUI bridge to the official Douyin chat client; no HTTP write fallback."""
import json
import threading
import time
import uuid
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from .douyu_interaction import InteractionError

SCRIPT = (Path(__file__).parent / "douyin_send.js").read_text(encoding="utf-8")


class BrowserChat(QObject):
    requested = Signal(object)

    def __init__(self, platform, context, parent):
        super().__init__(parent)
        self.platform, self.context = platform, context
        self.active = None
        self.closed = False
        self.requested.connect(self.open)

    def request(self, room, params, cancelled):
        request = dict(room=room, params=dict(params), cancelled=cancelled,
                       done=threading.Event(), result=None, deadline=time.monotonic() + 55)
        self.requested.emit(request)
        while not request["done"].wait(.1):
            if cancelled() or time.monotonic() > request["deadline"]:
                request["done"].set()
                raise InteractionError("抖音官网发送已取消或超时，结果未确认；请在官网核对，不要立即重复发送")
        result = request["result"]
        if not isinstance(result, dict) or result.get("error"):
            code = result.get("error", "cancelled") if isinstance(result, dict) else "cancelled"
            # Only fixed error categories leave the browser; never arbitrary exception messages.
            labels = {"wrong_room_page": "官网页面与目标直播间不匹配",
                      "cancelled_before_send": "提交前已取消", "official_client_changed": "官网发送模块结构已变化",
                      "official_client_not_ready": "官网发送模块未就绪", "invalid_response": "官网未返回有效回执",
                      "official_request_failed": "官网客户端调用失败", "cancelled": "窗口已关闭或请求已取消"}
            reason = labels.get(code, "官网客户端返回未知错误")
            raise InteractionError(f"抖音官网发送未确认（阶段：官方客户端；原因：{reason}），请先核对直播间内是否出现该条消息。")
        return result

    def open(self, request):
        if self.closed or request["done"].is_set() or request["cancelled"]():
            request["done"].set()
            return
        from ddm.platform_login import PlatformFollowDialog
        dialog = PlatformFollowDialog(self.platform, self.parent(), login_only=True,
                                      defer_login=True, owner=self.context.window)
        self.active = dialog
        dialog.setWindowTitle("抖音 · 官网弹幕发送")
        dialog.status.setText("正在加载官网互动环境；如有登录或安全验证，请在下方完成。请勿重复点击网页发送。")
        dialog.remember.hide()
        dialog.forget_button.hide()
        dialog.read_button.setText("重新加载环境")
        dialog.read_button.clicked.disconnect()
        dialog.read_button.setEnabled(False)
        task = dict(id=uuid.uuid4().hex, webRid=request["room"].split(":",1)[1],
                    roomId=request["params"]["room_id"], text=request["params"]["content"])
        page = dialog.browser.page()
        page.setAudioMuted(True)
        state = {"started": False, "submitted": False, "retry_safe": False, "polling": False}
        bootstrap = """(() => {
            if(location.origin!=='https://live.douyin.com'||
               location.pathname.replace(/\/$/,'')!=='/'+%s) return null;
            if(window.__ddmChatTaskId!==%s){
                window.__ddmChatTaskId=%s;window.__ddmChatCancelled=false;
                window.__ddmChatSubmitted=false;window.__ddmChatResult=null;
                Promise.resolve().then(()=> (%s)(%s)).then(data=>{window.__ddmChatResult=data})
                .catch(e=>{const allowed=['wrong_room_page','cancelled_before_send','official_client_changed',
                'official_client_not_ready','invalid_response'];window.__ddmChatResult={error:
                allowed.includes(e.message)?e.message:'official_request_failed'}});
            }
            return {started:true,submitted:!!window.__ddmChatSubmitted,result:window.__ddmChatResult};
        })()""" % (json.dumps(task["webRid"]), json.dumps(task["id"]), json.dumps(task["id"]), SCRIPT, json.dumps(task))
        read_state = "({started:!!window.__ddmChatTaskId,submitted:!!window.__ddmChatSubmitted,result:window.__ddmChatResult})"
        def retry():
            if state["submitted"] or not state["retry_safe"] or request["done"].is_set():
                return
            state.update(started=False, retry_safe=False)
            request["deadline"] = time.monotonic() + 55
            dialog.read_button.setEnabled(False)
            dialog.status.setText("正在重新加载官网环境；本次尚未提交弹幕。")
            # Reload creates a fresh JS context. Submitted or uncertain writes cannot use this action.
            dialog.browser.reload()
        dialog.read_button.clicked.connect(retry)
        def poll():
            if request["done"].is_set() or request["cancelled"]() or self.closed:
                page.runJavaScript("window.__ddmChatCancelled=true")
                dialog.reject()
                return
            if state["polling"] or state["retry_safe"]:
                return
            state["polling"] = True
            def received(result):
                state["polling"] = False
                if request["done"].is_set() or not isinstance(result, dict):
                    return
                if result.get("started"):
                    state["started"] = True
                    state["submitted"] = state["submitted"] or bool(result.get("submitted"))
                    dialog.status.setText("官网正在提交弹幕，等待回执，请勿重复发送。" if state["submitted"] else
                                          "正在准备官网发送模块；如页面要求验证，请在下方完成。")
                outcome = result.get("result")
                if not isinstance(outcome, dict):
                    return
                safe_error = outcome.get("error") in ("official_client_not_ready", "official_client_changed", "wrong_room_page")
                if safe_error and not state["submitted"]:
                    state["retry_safe"] = True
                    dialog.read_button.setEnabled(True)
                    dialog.status.setText("官网发送模块未就绪，本次尚未提交。可点击「重新加载环境」再试。")
                    return
                request["result"] = outcome
                request["done"].set()
                dialog.accept()
            # A page may render correctly even when loadFinished reports a failed subresource.
            # Start by checking the actual document, not by waiting for all media requests.
            page.runJavaScript(read_state if state["started"] else bootstrap, received)
        timer = QTimer(dialog)
        timer.setInterval(150)
        timer.timeout.connect(poll)
        def finished(_result):
            timer.stop()
            request["done"].set()
            if self.active is dialog:
                self.active = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.browser.load(QUrl(self.platform.room_url(request["room"])))
        timer.start()
        dialog.show()

    def shutdown(self):
        self.closed = True
        if self.active is not None:
            self.active.reject()
