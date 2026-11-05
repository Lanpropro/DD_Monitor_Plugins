"""Douyu web gift API and bounded, acknowledged chat gateway requests.

Protocol references and current verification limits are recorded in README.md.
No request that could send a message or spend currency is retried.
"""
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import ssl
import time
from urllib.parse import unquote, urlsplit

import aiohttp
import requests

from ddm.live_danmaku import douyu_messages, douyu_packet

BASE = "https://www.douyu.com"
GIFTS = "https://gift.douyucdn.cn/api/gift/v5/web/list"
DONATE = BASE + "/japi/gift/donate/mainsite/v3"
CHAT_SEND_TIMEOUT = 12.0


@dataclass(frozen=True)
class ChatReceipt:
    uid: str
    uname: str
    text: str


class InteractionError(ValueError):
    pass


class LoginRequired(InteractionError):
    pass


def web_cookies(jar, room):
    """Use cookies visible on the room page, excluding passport/message subdomains."""
    path = "/" + room_number(room)
    result = {}
    for cookie in sorted(jar, key=lambda c: (len(c.path or "/"),
                         c.domain.lstrip(".").lower() == "www.douyu.com")):
        domain = cookie.domain.lstrip(".").lower()
        scope = cookie.path or "/"
        if (domain in ("douyu.com", "www.douyu.com") and not cookie.is_expired() and
                (path == scope or path.startswith(scope.rstrip("/") + "/"))):
            result[cookie.name] = unquote(cookie.value)
    return result


def room_number(room):
    value = str(room or "")
    if value.startswith("douyu:"):
        value = value[6:]
    if not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise InteractionError("斗鱼房间号无效")
    return value


def api_data(payload):
    if not isinstance(payload, dict) or str(payload.get("error")) != "0":
        code = payload.get("error") if isinstance(payload, dict) else None
        if str(code) in ("901", "-1"):
            raise LoginRequired("斗鱼未登录或登录已过期，请重新登录")
        raise InteractionError(f"斗鱼拒绝请求（代码 {code if isinstance(code, (int, str)) and str(code).lstrip('-').isdigit() else 'unknown'}），请到官网查看账号或房间限制")
    data = payload.get("data", {})
    return json.loads(data) if isinstance(data, str) else data


@dataclass(frozen=True)
class Gift:
    id: int
    name: str
    price: int
    currency: str
    skin_id: int
    maximum: int
    disabled: str = ""
    image: str = field(default="", compare=False)

    def cost(self, count=1):
        value = self.price * count
        return f"{format_yuchi(value)} 鱼翅" if self.currency == "YUCHI" else f"{value} 鱼丸"


def format_yuchi(value):
    whole, fraction = divmod(value, 100)
    return str(whole) if not fraction else f"{whole}.{fraction:02d}".rstrip("0")


@dataclass(frozen=True)
class GiftTab:
    id: int
    name: str
    items: tuple


@dataclass(frozen=True)
class Wallet:
    yuchi: int  # hundredths of a fish fin, matching priceInfo.price
    yuwan: int


@dataclass(frozen=True)
class GiftPanel:
    gifts: tuple
    tabs: tuple
    wallet: Wallet | None = None
    wallet_error: str = ""


def wallet_data(payload):
    if not isinstance(payload, dict) or str(payload.get("error")) != "0" or not isinstance(payload.get("msg"), dict):
        raise LoginRequired("请先登录斗鱼后查看余额")
    data = payload["msg"]
    gold = data.get("gold")
    if isinstance(gold, dict):
        gold = gold.get("gold")
    def amount(value):
        try:
            number = Decimal(str(value))
            if isinstance(value, bool) or not number.is_finite() or number < 0 or number != number.to_integral_value():
                raise ValueError()
            return int(number)
        except (InvalidOperation, ValueError):
            raise InteractionError("斗鱼余额数据格式已变化，请刷新重试") from None
    return Wallet(amount(gold), amount(data.get("silver")))


def gift_tabs(payload, gifts):
    data = api_data(payload)
    metadata = data.get("tabs", [])
    tabs = []
    for item in metadata if isinstance(metadata, list) else []:
        if isinstance(item, dict) and type(item.get("id")) is int and item["id"] > 0 and not any(t[0] == item["id"] for t in tabs):
            tabs.append((item["id"], str(item.get("name") or item["id"])))
    if not tabs:
        return (GiftTab(1, "礼物", tuple(gifts)),)
    memberships = {item.get("id"): item.get("tabIds", []) for item in data["giftList"] if isinstance(item, dict)}
    known = {tab[0] for tab in tabs}
    result = {key: [] for key, name in tabs}
    for gift in gifts:
        ids = memberships.get(gift.id)
        destinations = {key for key in ids if type(key) is int and key in known} if isinstance(ids, list) else set()
        for key in destinations or {tabs[0][0]}:
            result[key].append(gift)
    return tuple(GiftTab(key, name, tuple(result[key])) for key, name in tabs)


def gift_catalog(payload):
    data = api_data(payload)
    if not isinstance(data, dict) or not isinstance(data.get("giftList"), list):
        raise InteractionError("斗鱼礼物列表格式已变化")
    gifts = []
    seen = set()
    for item in data["giftList"]:
        if not isinstance(item, dict):
            continue
        basic, price = item.get("basicInfo", {}), item.get("priceInfo", {})
        if not isinstance(basic, dict) or not isinstance(price, dict):
            continue
        gid, amount = item.get("id"), price.get("price")
        if type(gid) is not int or gid <= 0 or gid in seen or type(amount) is not int or amount < 0:
            continue
        seen.add(gid)
        currency = price.get("priceType", "")
        reason = ""
        if item.get("donateStatus") != 1:
            reason = "当前不可赠送"
        elif basic.get("giftType") != 0 or item.get("isFace", 0) or basic.get("bizTag"):
            reason = "特殊礼物请在官网赠送"
        elif currency not in ("YUCHI", "YUWAN"):
            reason = "暂不支持此礼物货币"
        skin = item.get("defaultSkinId", 0)
        if type(skin) is not int or skin < 0:
            continue
        skins = data.get("skinData", {})
        skin_info = skins.get(str(skin), {}) if isinstance(skins, dict) else {}
        if not isinstance(skin_info, dict):
            continue
        if skin_info.get("isFace"):
            reason = "特殊礼物请在官网赠送"
        limit = item.get("countLimit", 0)
        maximum = min(limit, 9999) if type(limit) is int and limit > 0 else 9999
        once = data.get("onceLimit", 0)
        if currency == "YUCHI" and type(once) is int and once > 0 and amount:
            maximum = min(maximum, once // amount)
        if maximum < 1:
            reason, maximum = "超过单次赠送限额", 1
        pictures = skin_info.get("giftPic") or basic.get("giftPic") or []
        prefix = skin_info.get("picUrlPrefix") or item.get("picUrlPrefix") or ""
        picture = next((p.get("src", "") for p in pictures if isinstance(p, dict) and
                        p.get("type") == "image/png"), "")
        picture = prefix.rstrip("/") + picture if picture.startswith("/") else picture
        parts = urlsplit(picture)
        if parts.scheme != "https" or not (parts.hostname or "").endswith(".douyucdn.cn"):
            picture = ""
        gifts.append(Gift(gid, str(skin_info.get("skinName") or item.get("name") or gid),
                          amount, currency, skin, maximum, reason, picture))
    return tuple(gifts)


def _check_cancel(cancelled):
    if cancelled():
        raise InteractionError("操作已取消")


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise InteractionError("斗鱼发送准备超时，本次未发送，请稍后再试")
    return remaining


def _chat_http_timeout(deadline):
    remaining = _remaining(deadline)
    return min(2, remaining), min(3, remaining)


class DouyuAPI:
    def __init__(self, cookies, *, session_factory=requests.Session):
        self.cookies = cookies.copy()
        self.session_factory = session_factory

    def _session(self, room):
        session = self.session_factory()
        session.cookies.update(self.cookies)
        session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": BASE + "/" + room,
                                "Origin": BASE})
        return session

    @staticmethod
    def _read(session, url, *, timeout=(4, 8), **kwargs):
        with session.get(url, timeout=timeout, **kwargs) as response:
            response.raise_for_status()
            return response.json()

    def catalog(self, room, cancelled=lambda: False):
        room = room_number(room)
        _check_cancel(cancelled)
        with self._session(room) as session:
            return gift_catalog(self._read(session, GIFTS, params={"rid": room}))

    def gift_panel(self, room, cancelled=lambda: False):
        room = room_number(room)
        _check_cancel(cancelled)
        with self._session(room) as session:
            payload = self._read(session, GIFTS, params={"rid": room})
            gifts = gift_catalog(payload)
            tabs = gift_tabs(payload, gifts)
            _check_cancel(cancelled)
            wallet, error = None, ""
            try:
                wallet = wallet_data(self._read(session, BASE + "/lapi/member/api/getInfo", params={"client_type": 0}))
            except InteractionError as failure:
                error = str(failure)
            except (requests.RequestException, ValueError):
                error = "余额读取失败，请刷新重试"
            _check_cancel(cancelled)
            return GiftPanel(gifts, tabs, wallet, error)

    def wallet(self, room, cancelled=lambda: False):
        room = room_number(room)
        _check_cancel(cancelled)
        with self._session(room) as session:
            wallet = wallet_data(self._read(session, BASE + "/lapi/member/api/getInfo", params={"client_type": 0}))
        _check_cancel(cancelled)
        return wallet

    def _account(self, session, cancelled, *, timeout=(4, 8)):
        _check_cancel(cancelled)
        payload = self._read(session, BASE + "/lapi/member/api/getInfo", params={"client_type": 0}, timeout=timeout)
        if str(payload.get("error")) != "0" or not isinstance(payload.get("msg"), dict):
            raise LoginRequired("斗鱼未登录或登录已过期，请重新登录")
        uid = str(payload["msg"].get("uid") or "")
        if not uid.isascii() or not uid.isdigit() or int(uid) <= 0:
            raise LoginRequired("斗鱼账号身份无效，请重新登录")
        return uid

    def send_gift(self, room, gift, count, cancelled=lambda: False):
        room = room_number(room)
        if not isinstance(gift, Gift) or gift.disabled or type(count) is not int or not 1 <= count <= gift.maximum:
            raise InteractionError("请选择可赠送的礼物和有效数量")
        with self._session(room) as session:
            self._account(session, cancelled)
            # Revalidate price, skin and permission before spending. A changed
            # catalog requires a fresh user confirmation, never a silent update.
            current = next((g for g in gift_catalog(self._read(session, GIFTS, params={"rid": room}))
                            if g.id == gift.id), None)
            if current != gift:
                raise InteractionError("礼物价格或状态已变化，请刷新列表后重新确认")
            api_data(self._read(session, BASE + "/wgapi/livenc/liveweb/csrfApi/getCsrfCookie"))
            token = web_cookies(session.cookies, room).get("acf_ccn", "")
            if not token:
                raise InteractionError("斗鱼网页请求校验尚未取得，请到官网完成验证后重试")
            _check_cancel(cancelled)
            try:
                with session.post(DONATE, data={"giftId": gift.id, "giftCount": count,
                        "roomId": room, "skinId": gift.skin_id, "bizExt": "{}", "ctn": token},
                        timeout=(4, 8), allow_redirects=False) as response:
                    if response.is_redirect:
                        raise InteractionError("赠送结果未确认，请到斗鱼官网核对消费记录；请勿直接重试")
                    response.raise_for_status()
                    result = api_data(response.json())
            except (requests.RequestException, json.JSONDecodeError) as error:
                raise InteractionError("赠送结果未确认，请到斗鱼官网核对消费记录；请勿直接重试") from error
            return result

    def send_text(self, room, text, cancelled=lambda: False, *, progress=lambda stage: None):
        room = room_number(room)
        if not isinstance(text, str) or not text.strip() or len(text) > 100:
            raise InteractionError("请输入 1–100 字弹幕")
        deadline = time.monotonic() + CHAT_SEND_TIMEOUT
        stage = "account"
        with self._session(room) as session:
            try:
                progress(stage)
                uid = self._account(session, cancelled, timeout=_chat_http_timeout(deadline))
                _check_cancel(cancelled)
                stage = "gateway"
                progress(stage)
                with session.post(BASE + "/lapi/live/gateway/web/" + room + "?isH5=1",
                                  timeout=_chat_http_timeout(deadline), allow_redirects=False) as response:
                    response.raise_for_status()
                    gateway = api_data(response.json())
            except requests.Timeout as error:
                label = "账号确认" if stage == "account" else "网关发现"
                raise InteractionError(f"斗鱼{label}超时，本次未发送，请稍后再试") from error
            servers = gateway.get("wss", []) if isinstance(gateway, dict) else []
            server = next((s for s in servers if isinstance(s, dict) and
                           s.get("domain") == "wsproxy.douyu.com" and
                           type(s.get("port")) is int and 6671 <= s["port"] <= 6675), None)
            if server is None:
                raise InteractionError("斗鱼未返回可用的互动网关")
            cookies = web_cookies(session.cookies, room)
            asyncio.run(send_chat(server, room, uid, text.strip(), cookies, cancelled,
                                  timeout=_remaining(deadline), progress=progress))
            return ChatReceipt(uid, cookies.get("acf_nickname") or "我", text.strip())


def packet(values):
    def escape(value):
        return str(value).replace("@", "@A").replace("/", "@S")
    return douyu_packet("".join(f"{escape(k)}@={escape(v)}/" for k, v in values.items()))


def chat_ssl_context():
    # The Douyu interaction gateway negotiates TLS 1.2 RSA-GCM, which is
    # excluded from Python's default cipher list. Keep the default trust store,
    # hostname checks and protocol floor; add only the two negotiated suites.
    context = ssl.create_default_context()
    context.set_ciphers(":".join(c["name"] for c in context.get_ciphers()) +
                        ":AES128-GCM-SHA256:AES256-GCM-SHA384")
    return context


async def send_chat(server, room, uid, text, cookies, cancelled, *, client_factory=aiohttp.ClientSession,
                    timeout=CHAT_SEND_TIMEOUT, progress=lambda stage: None):
    did = cookies.get("dy_did", "")
    jwt = cookies.get("acf_dmjwt_token", "")
    legacy = cookies.get("acf_username") and cookies.get("acf_auth_wl")
    if not did or not (jwt or legacy):
        raise InteractionError("斗鱼互动网关令牌尚未取得，请到官网完成账号验证；当前请求未发送")
    rt = str(int(time.time()))
    login = {"type": "loginreq", "roomid": room, "username": cookies.get("acf_username", ""),
             "password": cookies.get("acf_auth_wl", ""), "ltkid": cookies.get("acf_ltkid", ""),
             "biz": cookies.get("acf_biz", ""), "stk": cookies.get("acf_stk", ""),
             "jwt": jwt, "devid": did, "ct": 0, "pt": 2,
             "cvr": 0, "tvr": 7, "apd": "", "rt": rt, "ver": "20220825", "aver": "218101901",
             "vk": hashlib.md5((rt + "r5*^5;}2#${XF[h+;'./.Q'1;,-]f'p[" + did).encode()).hexdigest()}
    sent = False
    buffer = bytearray()
    try:
        progress("connect")
        async with asyncio.timeout(timeout):
            async with client_factory(timeout=aiohttp.ClientTimeout(total=timeout)) as client:
                async with client.ws_connect(f"wss://{server['domain']}:{server['port']}/",
                        origin=BASE, max_msg_size=4 * 1024 * 1024, ssl=chat_ssl_context(),
                        timeout=aiohttp.ClientWSTimeout(ws_close=0.25)) as ws:
                    _check_cancel(cancelled)
                    progress("login")
                    await ws.send_bytes(packet(login))
                    async for message in ws:
                        _check_cancel(cancelled)
                        if message.type != aiohttp.WSMsgType.BINARY:
                            raise InteractionError("斗鱼互动连接已中断")
                        buffer.extend(message.data)
                        sent_before_packet = sent
                        for event in douyu_messages(buffer):
                            kind = event.get("type")
                            if kind in ("loginres", "chatres", "error", "livres", "lsigres", "h5ckres"):
                                code = str(event.get("res", event.get("code", "")))
                                progress("reply_" + kind + ("_" + code if code.lstrip("-").isdigit() else ""))
                            if kind == "loginres":
                                if event.get("userid") != uid:
                                    raise LoginRequired("斗鱼互动登录未确认当前账号，请重新登录")
                                if not sent:
                                    _check_cancel(cancelled)
                                    sent = True
                                    await ws.send_bytes(packet({"type": "chatmessage", "content": text.replace("\\", "\\\\"),
                                        "sender": uid, "dy": did, "col": 0, "pe": 0, "ifs": 0,
                                        "nc": 0, "dat": 0, "rev": 0}))
                                    progress("wait_ack")
                            elif kind == "chatres" and sent:
                                # This frame was received before writing the
                                # message; it cannot acknowledge that write.
                                if not sent_before_packet:
                                    continue
                                if event.get("rid", room) != room:
                                    continue
                                if event.get("res") != "0":
                                    code = str(event.get("res", ""))
                                    safe_code = code if code.lstrip("-").isdigit() else "unknown"
                                    raise InteractionError(f"斗鱼拒绝弹幕（代码 {safe_code}），请到官网查看禁言、频率或账号限制")
                                progress("ack")
                                return "斗鱼已返回发送回执"
                            elif kind in ("error", "livres", "lsigres"):
                                raise InteractionError("弹幕发送结果未确认，请在直播间核对后再试" if sent else
                                    "斗鱼互动网关需要新版校验，当前直连接口尚未适配，请在官网发送")
    except InteractionError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        raise InteractionError("弹幕发送结果未确认，请在直播间核对后再试" if sent else
                               "斗鱼互动网关连接失败，请稍后重试或在官网发送") from error
    raise InteractionError("弹幕发送结果未确认，请在直播间核对后再试" if sent else "斗鱼互动登录未完成")
