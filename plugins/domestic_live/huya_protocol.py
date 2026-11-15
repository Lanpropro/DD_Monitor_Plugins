"""Huya WUP encoding and credential-scoped request/response validation."""
import base64
import struct
import requests
from ddm.live_danmaku import tars_bytes, tars_fields, tars_int

def huya_string(tag, value):
    data = str(value).encode("utf-8")
    return (bytes([tag << 4 | 6, len(data)]) if len(data) < 256 else
            bytes([tag << 4 | 7]) + struct.pack(">I", len(data))) + data



def huya_user(session, uid):
    cookies = {c.name: c.value for c in session.cookies if
               c.domain.lstrip(".") == "huya.com" or c.domain.lstrip(".").endswith(".huya.com")}
    raw = ";".join(f"{key}={value}" for key, value in cookies.items())
    ua = "webh5&1.0.0&websocket"
    user = (tars_int(0, int(uid)) + huya_string(1, cookies.get("guid", "")) +
            huya_string(2, "") + huya_string(3, ua) + huya_string(4, raw) + tars_int(5, 0))
    base = (tars_int(0, int(uid)) + huya_string(1, cookies.get("guid", "")) +
            huya_string(2, ua) + huya_string(8, raw))
    return user, base



def huya_wup_request(session, servant, method, uid, request):
    """官网只读 WUP 请求，登录凭证只发送到虎牙官方接口。"""
    _user, base = huya_user(session, uid)
    values = b"\x08" + tars_int(0, 1) + huya_string(0, "tReq") + tars_bytes(1, request)
    body = (tars_int(1, 3) + tars_int(2, 0) + tars_int(3, 0) + tars_int(4, 1) +
            huya_string(5, servant) + huya_string(6, method) + tars_bytes(7, values) +
            tars_int(8, 8000) + b"\x98\x0c\xa8\x0c")
    try:
        with session.post("https://cdnws.api.huya.com/",
                params={"baseinfo": base64.b64encode(base).decode("ascii")},
                data=struct.pack(">I", len(body) + 4) + body,
                headers={"Content-Type": "application/octet-stream", "Referer": "https://www.huya.com/"},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            data = response.content
    except requests.RequestException:
        # requests 的异常 URL 含 baseinfo，不能将其中的账号凭证带入日志或提示。
        raise RuntimeError("虎牙官方接口请求失败，请稍后重试") from None
    if len(data) < 4 or len(data) > 4 * 1024 * 1024 or struct.unpack(">I", data[:4])[0] != len(data):
        raise RuntimeError("虎牙接口数据格式异常，请稍后重试")
    envelope = tars_fields(data[4:])
    if envelope.get(1) != 3 or envelope.get(6) != method:
        raise RuntimeError("虎牙接口响应不匹配，请稍后重试")
    entries = tars_fields(envelope.get(7, b""))[0]
    values = dict(zip(entries[::2], entries[1::2]))
    if "" in values and tars_fields(values[""]).get(0, -1) != 0:
        raise RuntimeError("虎牙接口拒绝请求，请重新登录后重试")
    if "tRsp" not in values:
        raise RuntimeError("虎牙接口响应缺少数据，请稍后重试")
    return tars_fields(values["tRsp"])[0]



def huya_follow_request(session, method, uid, target):
    user, _base = huya_user(session, uid)
    request = b"\x0a\x0a" + user + b"\x0b" + tars_int(1, int(target)) + b"\x0b"
    return huya_wup_request(session, "huyauserui", method, uid, request)
