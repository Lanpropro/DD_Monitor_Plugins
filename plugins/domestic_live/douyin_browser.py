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
                       done=threading.Event(), result=None)
        self.requested.emit(request)
        deadline = time.monotonic() + 55
        while not request["done"].wait(.1):
            if cancelled() or time.monotonic() > deadline:
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
            raise InteractionError(f"抖音官网发送未确认（阶段：官方客户端；原因：{reason}），请在页面完成登录或安全验证后核对结果")
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
        dialog.read_button.hide()
        task = dict(id=uuid.uuid4().hex, webRid=request["room"].split(":",1)[1],
                    roomId=request["params"]["room_id"], text=request["params"]["content"])
        started = False
        page = dialog.browser.page()
        page.setAudioMuted(True)
        def loaded(ok):
            nonlocal started
            if started or not ok or request["done"].is_set() or request["cancelled"]():
                return
            url = dialog.browser.url()
            if url.scheme() != "https" or url.host() != "live.douyin.com" or url.path().rstrip("/") != "/" + task["webRid"]:
                return
            started = True
            page.runJavaScript("""window.__ddmChatCancelled=false;window.__ddmChatResult=null;
                Promise.resolve().then(()=> (%s)(%s)).then(data=>{window.__ddmChatResult=data})
                .catch(e=>{const allowed=['wrong_room_page','cancelled_before_send','official_client_changed',
                'official_client_not_ready','invalid_response'];window.__ddmChatResult={error:
                allowed.includes(e.message)?e.message:'official_request_failed'}});""" % (SCRIPT,json.dumps(task)))
        def poll():
            if request["done"].is_set() or request["cancelled"]() or self.closed:
                page.runJavaScript("window.__ddmChatCancelled=true")
                dialog.reject()
                return
            if not started:
                return
            def received(result):
                if isinstance(result, dict) and not request["done"].is_set():
                    request["result"] = result
                    request["done"].set()
                    dialog.accept()
            page.runJavaScript("window.__ddmChatResult",received)
        timer = QTimer(dialog)
        timer.setInterval(150)
        timer.timeout.connect(poll)
        dialog.browser.loadFinished.connect(loaded)
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
