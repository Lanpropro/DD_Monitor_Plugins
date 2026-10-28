"""Huya acknowledged web WUP chat; never retry a write request."""
import base64
import asyncio
import aiohttp
import json
import re
import secrets
import struct

import requests
from ddm.live_danmaku import tars_int, tars_bytes, tars_fields
from . import huya_string, huya_user
from .douyu_interaction import InteractionError, LoginRequired


def message_request(user, room, text):
    def record(tag, value):
        return bytes([tag << 4 | 10]) + value + b"\x0b"
    def integers(values):
        return b"".join(tars_int(tag, value) for tag, value in enumerate(values))
    content = integers((-1, 4, 0, -1, -1, -1))
    border = integers((0, 0, -1, 100, -1, 100)) + huya_string(6, "") + tars_int(7, -1) + tars_int(8, -1)
    bullet = integers((-1, 4, 0, 1, 0)) + record(5, border) + b"\x69\x0c" + tars_int(7, 0) + tars_int(8, -1)
    body = (record(0, user) + tars_int(1, room["channel"]) + tars_int(2, room["sid"]) +
            huya_string(3, text) + tars_int(4, 0) + record(5, content) + record(6, bullet) +
            b"\x79\x0c" + tars_int(8, room["id"]) + b"\x99\x0c" + record(10, integers((0, 0, 0))) + tars_int(11, 0))
    return record(0, body)


def parse_reply(data, request_id):
    if len(data) < 4 or len(data) > 4 * 1024 * 1024 or struct.unpack(">I", data[:4])[0] != len(data):
        raise InteractionError("虎牙发送结果未确认，请在官网核对后再试")
    envelope = tars_fields(data[4:])
    if (envelope.get(1) != 3 or envelope.get(4) != request_id or
            envelope.get(5) != "liveuif" or envelope.get(6) != "sendMessage"):
        raise InteractionError("虎牙发送响应不匹配，请在官网核对后再试")
    entries = tars_fields(envelope.get(7, b"" )).get(0, [])
    values = dict(zip(entries[::2], entries[1::2]))
    if "" in values and tars_fields(values[""]).get(0, -1) != 0:
        raise InteractionError("虎牙互动接口拒绝请求，不能据此判断账号过期或禁言；本次结果请在官网核对")
    if "tRsp" not in values:
        raise InteractionError("虎牙发送响应缺少回执，请在官网核对后再试")
    result = tars_fields(values["tRsp"]).get(0, {})
    if not isinstance(result, dict) or type(result.get(0)) is not int:
        raise InteractionError("虎牙发送回执无效，请在官网核对后再试")
    if result[0] != 0:
        raise InteractionError("虎牙拒绝弹幕（代码 %s），请到官网检查禁言、等级或频率限制" % result[0])
    return "虎牙已确认发送"


class HuyaAPI:
    def __init__(self, platform, cookies, *, session_factory=requests.Session):
        self.platform, self.cookies, self.session_factory = platform, cookies.copy(), session_factory

    def send_text(self, room, text, cancelled=lambda: False, *, progress=lambda stage: None):
        if not isinstance(text, str) or not text.strip() or len(text) > 100:
            raise InteractionError("请输入 1–100 字弹幕")
        canonical = self.platform.normalize(room)
        if not canonical.startswith("huya:") or "://" in canonical:
            raise InteractionError("虎牙房间号无效")
        def check():
            if cancelled():
                raise InteractionError("请求已取消")
        check()
        with self.session_factory() as session:
            session.cookies.update(self.cookies)
            session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": self.platform.room_url(canonical)})
            progress("account")
            try:
                account = self.platform.account_info(session, cancelled)
            except RuntimeError:
                raise LoginRequired("虎牙未登录或登录已过期，请重新登录") from None
            check()
            progress("gateway")
            with session.get(self.platform.room_url(canonical), timeout=(3, 5)) as response:
                response.raise_for_status()
                match = re.search(r"\bTT_ROOM_DATA\s*=\s*", response.text)
                if match is None:
                    raise InteractionError("虎牙未返回房间发言信息，请在官网完成验证")
                info, _ = json.JSONDecoder().raw_decode(response.text[match.end():].lstrip())
            if not isinstance(info, dict) or any(type(info.get(k)) is not int or info[k] <= 0 for k in ("id", "channel", "sid")):
                raise InteractionError("该房间未提供直播频道；未开播聊天室发送尚未适配，本次未发送")
            user, base = huya_user(session, account["uid"])
            request_id = secrets.randbelow(2**30) + 1
            values = b"\x08" + tars_int(0, 1) + huya_string(0, "tReq") + tars_bytes(1, message_request(user, info, text.strip()))
            body = (tars_int(1, 3) + tars_int(2, 0) + tars_int(3, 0) + tars_int(4, request_id) +
                huya_string(5, "liveuif") + huya_string(6, "sendMessage") + tars_bytes(7, values) +
                tars_int(8, 5000) + b"\x98\x0c\xa8\x0c")
            check()
            return asyncio.run(send_wup(body, base, user, int(account["uid"]), request_id, cancelled, progress=progress))


async def send_wup(body, base, user, uid, request_id, cancelled, *, progress=lambda stage: None,
                   client_factory=aiohttp.ClientSession):
    sent = False
    fields = tars_fields(user)
    auth = (tars_int(0, uid) + huya_string(1, fields[3]) + huya_string(2, fields[4]) +
            huya_string(3, fields[1]) + tars_int(4, 1) + huya_string(5, ""))
    def check():
        if cancelled():
            raise InteractionError("请求已取消；如已提交，请在官网核对结果")
    try:
        async with asyncio.timeout(12):
            async with client_factory(timeout=aiohttp.ClientTimeout(total=12)) as session:
                progress("connect")
                async with session.ws_connect("wss://cdnws.api.huya.com/",
                        params={"baseinfo": base64.b64encode(base).decode("ascii")},
                        origin="https://www.huya.com", max_msg_size=4*1024*1024,
                        timeout=aiohttp.ClientWSTimeout(ws_close=.25)) as ws:
                    check()
                    progress("login")
                    await ws.send_bytes(tars_int(0, 10) + tars_bytes(1, auth))
                    async for message in ws:
                        check()
                        if message.type != aiohttp.WSMsgType.BINARY:
                            raise InteractionError("虎牙互动连接已中断，请在官网核对发送结果")
                        command = tars_fields(message.data)
                        if command.get(0) == 11 and not sent:
                            if tars_fields(command.get(1, b"" )).get(0) != 0:
                                raise LoginRequired("虎牙互动凭证未通过验证，请重新登录")
                            check()
                            sent = True
                            await ws.send_bytes(tars_int(0, 3) + tars_bytes(1, struct.pack(">I", len(body)+4)+body) + tars_int(2, request_id))
                            progress("wait_ack")
                        elif command.get(0) == 4 and sent:
                            data = command.get(1, b"")
                            if len(data) < 4:
                                continue
                            envelope = tars_fields(data[4:])
                            if (envelope.get(4) != request_id or envelope.get(5) != "liveuif" or
                                    envelope.get(6) != "sendMessage"):
                                continue
                            result = parse_reply(data, request_id)
                            progress("ack")
                            return result
    except InteractionError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError):
        raise InteractionError("虎牙发送结果未确认，请在官网核对后再试" if sent else
                               "虎牙互动连接未建立，本次未发送，请稍后重试") from None
    raise InteractionError("虎牙未返回发送回执，请在官网核对后再试" if sent else "虎牙互动身份未确认，本次未发送")
