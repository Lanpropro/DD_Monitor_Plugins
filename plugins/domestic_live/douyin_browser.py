"""GUI bridge to the official Douyin chat client; no HTTP write fallback."""
import json
import threading
import time
import uuid
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from .douyu_interaction import InteractionError

SCRIPT = (Path(__file__).parent / "douyin_send.js").read_text(encoding="utf-8")
CLIENT_SCRIPT = (Path(__file__).parent / "douyin_client.js").read_text(encoding="utf-8")


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
                      "official_security_changed": "官网安全环境模块结构已变化",
                      "official_security_timeout": "官网安全环境初始化超时，本次未提交",
                      "official_security_failed": "官网安全环境初始化失败，本次未提交",
                      "official_request_failed": "官网客户端调用失败", "cancelled": "窗口已关闭或请求已取消"}
            reason = labels.get(code, "官网客户端返回未知错误")
            if isinstance(result, dict):
                stages = {"page": "页面校验", "module": "模块初始化", "security": "安全环境准备",
                          "request": "官网请求", "response": "回执解析"}
                stage = stages.get(result.get("stage"), "未知")
                names = {"Error", "TypeError", "ReferenceError", "RangeError", "SyntaxError", "SecurityError",
                         "NotAllowedError", "AbortError", "TimeoutError", "NetworkError", "AxiosError"}
                name = result.get("exception")
                name = name if name in names else "其他异常"
                submitted = "是" if result.get("submitted") is True else "否"
                reason += f"；位置：{stage}；异常：{name}；已调用发送：{submitted}"
                causes = {"missing_call": "模块调用目标缺失", "not_callable": "模块依赖不可调用"}
                if result.get("cause") in causes:
                    reason += "；分类：" + causes[result["cause"]]
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
        dialog.read_button.setEnabled(True)
        task = dict(id=uuid.uuid4().hex, webRid=request["room"].split(":",1)[1],
                    roomId=request["params"]["room_id"], text=request["params"]["content"])
        page = dialog.browser.page()
        page.setAudioMuted(True)
        state = {"started": False, "submitted": False, "retry_safe": True, "polling": False, "epoch": 0}
        prepare = """JSON.stringify((() => {
            if(location.origin!=='https://live.douyin.com'||
               location.pathname.replace(/\/$/,'')!=='/'+%s) return {ready:false,reason:'page'};
            let require; const chunks=window.webpackChunkdouyin_live_v2;
            if(Array.isArray(chunks)) chunks.push([['ddm-prepare-'+%s],{},r=>{require=r}]);
            return (%s)(require);
        })())""" % (json.dumps(task["webRid"]), json.dumps(task["id"]), CLIENT_SCRIPT)
        submit = """if(window.__ddmChatTaskId!==%s){
            window.__ddmChatTaskId=%s;window.__ddmChatCancelled=false;
            window.__ddmChatSubmitted=false;window.__ddmChatResult=null;
            window.__ddmChatStage='page';
            Promise.resolve().then(()=> (%s)(%s, (%s))).then(data=>{window.__ddmChatResult=data})
            .catch(e=>{const allowed=['wrong_room_page','cancelled_before_send','official_client_changed',
            'official_client_not_ready','invalid_response','official_security_changed',
            'official_security_timeout','official_security_failed'];window.__ddmChatResult={error:
            allowed.includes(e?.message)?e.message:'official_request_failed',
            stage:['page','module','security','request','response'].includes(window.__ddmChatStage)?window.__ddmChatStage:'unknown',
            exception:['Error','TypeError','ReferenceError','RangeError','SyntaxError','SecurityError',
            'NotAllowedError','AbortError','TimeoutError','NetworkError','AxiosError'].includes(e?.name)?e.name:'Other',
            cause:window.__ddmChatStage==='module'&&e?.name==='TypeError'&&typeof e.message==='string'?
            (e.message.includes("reading 'call'")?'missing_call':e.message.includes('is not a function')?'not_callable':'other'):'other',
            submitted:!!window.__ddmChatSubmitted}});
        }""" % (json.dumps(task["id"]), json.dumps(task["id"]), SCRIPT, json.dumps(task), CLIENT_SCRIPT)
        read_state = "JSON.stringify({submitted:!!window.__ddmChatSubmitted,result:window.__ddmChatResult})"
        def retry():
            if state["submitted"] or not state["retry_safe"] or request["done"].is_set():
                return
            state.update(started=False, retry_safe=True, polling=False, epoch=state["epoch"] + 1)
            request["deadline"] = time.monotonic() + 55
            dialog.status.setText("正在重新加载官网环境；本次尚未提交弹幕。")
            dialog.browser.load(QUrl(self.platform.room_url(request["room"])))
        dialog.read_button.clicked.connect(retry)
        def poll():
            if request["done"].is_set() or request["cancelled"]() or self.closed:
                page.runJavaScript("window.__ddmChatCancelled=true")
                dialog.reject()
                return
            if state["polling"] or state["started"] and state["retry_safe"]:
                return
            state["polling"] = True
            epoch = state["epoch"]
            def received(raw):
                if epoch != state["epoch"]:
                    return  # A read-only probe from the previous page cannot start a write.
                state["polling"] = False
                if request["done"].is_set() or request["cancelled"]():
                    return
                try:
                    result = json.loads(raw) if isinstance(raw, str) else raw
                except (ValueError, TypeError):
                    result = None
                if not isinstance(result, dict):
                    if not state["started"]:
                        dialog.status.setText("官网页面尚未就绪，本次未提交弹幕。可重新加载环境。")
                    return
                if not state["started"]:
                    if not result.get("ready"):
                        if result.get("reason") == "dependency":
                            dialog.status.setText("官网发送模块的依赖尚未加载；本次尚未提交，可重新加载环境。")
                        else:
                            dialog.status.setText("正在等待官网页面及发送模块；本次尚未提交，可重新加载环境。")
                        return
                    state.update(started=True, retry_safe=False)
                    dialog.read_button.setEnabled(False)
                    dialog.status.setText("官网模块已就绪，正在提交弹幕并等待回执。")
                    page.runJavaScript(submit)
                    return
                state["submitted"] = state["submitted"] or bool(result.get("submitted"))
                outcome = result.get("result")
                if not isinstance(outcome, dict):
                    return
                safe_error = outcome.get("error") in ("official_client_not_ready", "official_client_changed", "wrong_room_page")
                if safe_error and not state["submitted"]:
                    state["retry_safe"] = True
                    dialog.read_button.setEnabled(True)
                    dialog.status.setText("官网发送模块未就绪，本次尚未提交。可重新加载环境再试。")
                    return
                request["result"] = outcome
                request["done"].set()
                dialog.accept()
            page.runJavaScript(read_state if state["started"] else prepare, received)
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
