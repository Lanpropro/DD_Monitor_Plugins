"""Huya acknowledged web WUP chat; never retry a write request."""
import base64
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
        raise InteractionError("虎牙拒绝请求，请到官网检查登录状态和发言限制")
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
                raise InteractionError("虎牙房间当前不支持发言，请在官网确认开播状态")
            user, base = huya_user(session, account["uid"])
            request_id = secrets.randbelow(2**30) + 1
            values = b"\x08" + tars_int(0, 1) + huya_string(0, "tReq") + tars_bytes(1, message_request(user, info, text.strip()))
            body = (tars_int(1, 3) + tars_int(2, 0) + tars_int(3, 0) + tars_int(4, request_id) +
                huya_string(5, "liveuif") + huya_string(6, "sendMessage") + tars_bytes(7, values) +
                tars_int(8, 5000) + b"\x98\x0c\xa8\x0c")
            check()
            progress("wait_ack")
            try:
                with session.post("https://cdnws.api.huya.com/",
                        params={"baseinfo": base64.b64encode(base).decode("ascii")},
                        data=struct.pack(">I", len(body) + 4) + body, timeout=(3, 5), allow_redirects=False,
                        headers={"Content-Type": "application/octet-stream"}) as response:
                    if response.is_redirect:
                        raise InteractionError("虎牙发送结果未确认，请在官网核对后再试")
                    response.raise_for_status()
                    result = parse_reply(response.content, request_id)
            except (requests.RequestException, ValueError, KeyError, TypeError) as error:
                if isinstance(error, InteractionError):
                    raise
                raise InteractionError("虎牙发送结果未确认，请在官网核对后再试") from None
            progress("ack")
            return result
