"""Single-attempt Douyin web chat with explicit server acknowledgement."""
import os
import time
import threading
from collections import deque
import re
from types import SimpleNamespace
import requests
from .douyu_interaction import ChatReceipt, InteractionError, LoginRequired


class BroadcastReceipts:
    def __init__(self):
        self.condition = threading.Condition()
        self.recent = deque(maxlen=512)
        self.own_uid = ""

    def observe(self, room, event):
        mid = event.get("message_id")
        if not mid:
            return
        with self.condition:
            self.recent.append((time.monotonic(), room, str(mid)))
            self.condition.notify_all()
        if self.own_uid and event.get("uid") == self.own_uid:
            event["self_sent"] = True

    def wait(self, room, mid, cancelled, timeout=8):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not cancelled():
                now = time.monotonic()
                if any(now - stamp < 30 and rid == room and message == mid
                       for stamp, rid, message in self.recent):
                    return True
                if now >= deadline:
                    return False
                self.condition.wait(min(.1, deadline - now))
        return False


def playback_cookies(platform):
    jar = requests.cookies.RequestsCookieJar()
    if os.environ.get("DDM_NO_SAVE") == "1":
        return jar
    from ddm.account_store import AccountStore
    from PySide6.QtCore import QByteArray
    from PySide6.QtNetwork import QNetworkCookie
    context = getattr(platform, "_context", None)
    window = context.window if context else None
    memory = getattr(window, "_platform_login_sessions", {}).get("douyin")
    saved = memory["cookies"] if memory is not None else AccountStore("douyin").load()
    for raw in saved:
        for cookie in QNetworkCookie.parseCookies(QByteArray(raw.encode("utf-8"))):
            domain = cookie.domain().lstrip(".").lower()
            if domain != "douyin.com" and not domain.endswith(".douyin.com"):
                continue
            if not cookie.isSessionCookie() and cookie.expirationDate().toSecsSinceEpoch() <= time.time():
                continue
            jar.set(bytes(cookie.name()).decode(), bytes(cookie.value()).decode(),
                    domain=cookie.domain(), path=cookie.path() or "/", secure=cookie.isSecure())
    return jar


def preparation_error(stage, error):
    if isinstance(error, ValueError):
        reason = "官网返回空内容或非 JSON 数据"
    elif isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else None
        reason = f"HTTP {status}" if isinstance(status, int) else "HTTP 请求失败"
    elif isinstance(error, requests.Timeout):
        reason = "请求超时"
    else:
        reason = "网络连接失败"
    return InteractionError(f"抖音{stage}失败（{reason}），本次未提交弹幕。请稍后重试。")


class DouyinAPI:
    def __init__(self, platform, cookies, *, session_factory=requests.Session, transport=None):
        self.platform, self.cookies, self.session_factory = platform, cookies.copy(), session_factory
        self.transport = transport

    def send_text(self, room, text, cancelled=lambda: False, *, progress=lambda stage: None):
        if not isinstance(text, str) or not text.strip() or len(text) > 50:
            raise InteractionError("请输入 1–50 字抖音弹幕")
        canonical = self.platform.normalize(room)
        if not re.fullmatch(r"douyin:[1-9][0-9]{0,19}", canonical):
            raise InteractionError("请先添加抖音直播间再发送弹幕")
        def check():
            if cancelled():
                raise InteractionError("请求已取消，本次未发送")
        check()
        with self.session_factory() as session:
            session.cookies.update(self.cookies)
            session.headers.update({"User-Agent": getattr(self.platform, "interaction_user_agent", "Mozilla/5.0"), "Referer": self.platform.room_url(canonical)})
            progress("account")
            try:
                account = self.platform.account_info(session, cancelled)
            except (requests.RequestException, ValueError) as error:
                raise preparation_error("账号确认", error) from None
            except RuntimeError:
                raise LoginRequired("抖音登录身份未确认，请在软件内重新登录") from None
            delivery = getattr(self.platform, "_chat_delivery", None)
            if delivery is not None:
                delivery.own_uid = str(account["uid"])
            check()
            progress("gateway")
            try:
                info = self.platform.room_data(canonical, SimpleNamespace(http=session))
            except (requests.RequestException, ValueError) as error:
                raise preparation_error("直播间信息读取", error) from None
            except RuntimeError:
                raise InteractionError("抖音直播间信息读取失败，本次未提交弹幕；请在官网检查是否需要安全验证") from None
            data = info.get("room", {})
            rid = str(data.get("id_str") or "")
            if not re.fullmatch(r"[1-9][0-9]{0,19}", rid) or data.get("status") != 2:
                raise InteractionError("抖音房间未开播或未返回当场直播 ID，本次未发送")
            check()
            params = {"aid": 6383, "app_name": "douyin_web", "live_id": 1,
                      "device_platform": "web", "room_id": rid, "content": text.strip(), "type": 0}
            progress("wait_ack")
            try:
                if self.transport is not None:
                    payload = self.transport(canonical, params, cancelled)
                else:
                    # This GET is a write operation: no retries, redirects or alternate writes.
                    with session.get("https://live.douyin.com/webcast/room/chat/", params=params,
                                     allow_redirects=False, timeout=(3, 8)) as response:
                        if response.is_redirect:
                            raise InteractionError("抖音发送接口发生跳转，结果未确认；请在官网核对后再试")
                        response.raise_for_status()
                        payload = response.json()
            except InteractionError:
                raise
            except (requests.RequestException, ValueError):
                raise InteractionError("抖音发送结果未确认（阶段：room/chat），请在官网核对；不要立即重复发送") from None
            if not isinstance(payload, dict) or type(payload.get("status_code")) is not int:
                raise InteractionError("抖音发送回执格式无效，结果未确认")
            code = payload["status_code"]
            if code != 0:
                raise InteractionError(f"抖音未确认发送（阶段：room/chat；状态码：{code}）。可能需要官网签名或安全验证，请在官网核对。")
            receipt = payload.get("data")
            # The web client's WebcastChatMessage converter reads msg_id_str.
            mid = str(receipt.get("msg_id_str") or receipt.get("msg_id") or receipt.get("id_str") or
                      receipt.get("id") or receipt.get("message_id") or "") if isinstance(receipt, dict) else ""
            if not re.fullmatch(r"[1-9][0-9]{0,19}", mid):
                raise InteractionError("抖音响应缺少消息回执 ID，结果未确认；请在官网核对")
            progress("ack")
            progress("wait_broadcast")
            if delivery is None or not delivery.wait(canonical, mid, cancelled):
                raise InteractionError("抖音接口已受理，但未在弹幕连接中观察到该消息。广播投递未确认，草稿已保留；不要立即重复发送，请在官网核对。")
            progress("broadcast")
            return ChatReceipt(str(account["uid"]), account.get("uname") or "我", text.strip())
