"""Pure Douyin page decoding and room identity validation."""
import json
import re
from .douyin_errors import DouyinLookupError


def share_identity(payload, uid):
    data = payload.get("data")
    if type(payload.get("status_code")) is not int or payload["status_code"] != 0 or not isinstance(data, dict):
        raise RuntimeError("抖音关注主播信息获取失败，请稍后重试")
    if not data:
        return ""
    internal = str(data.get("id_str") or "")
    if not re.fullmatch(r"[0-9]{1,20}", internal) or str(data.get("owner_user_id")) != uid:
        raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
    return internal

def sdk_stream_data(page, value):
    if not isinstance(value, str) or not re.fullmatch(r"\$[0-9a-f]+", value):
        return value
    # React Flight 将较长的 SDK JSON 放在独立的 UTF-8 文本记录中。
    chunks = re.findall(r'self\.__(?:rsc|pace)_f\.push\(\s*\[\s*\d+\s*,\s*("(?:\\.|[^"\\])*")\s*]\s*\)', page)
    wire = "".join(json.loads(chunk) for chunk in chunks).encode("utf-8")
    record = re.search(rb"(?:^|\n)" + value[1:].encode() + rb":T([0-9a-f]+),", wire)
    if record is None:
        return ""
    try:
        return wire[record.end():record.end() + int(record[1], 16)].decode("utf-8")
    except UnicodeDecodeError:
        return ""

def share_info(page, internal, uid, visitor):
    chunks = re.findall(r'self\.__rsc_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', page)
    for chunk in reversed(chunks):
        text = re.sub(r"^\w+:", "", json.loads(chunk))
        if not text.startswith('["$",'):
            continue
        node = json.JSONDecoder().raw_decode(text)[0]
        if len(node) != 4 or not isinstance(node[3], dict):
            continue
        data = node[3].get("data")
        if not isinstance(data, dict) or not isinstance(data.get("room"), dict):
            continue
        room = data["room"]
        owner = room.get("owner") or {}
        owner_uid = str(owner.get("idStr") or "")
        if (str(room.get("idStr")) != internal or
                (uid and owner_uid != uid) or not re.fullmatch(r"[1-9][0-9]{0,19}", owner_uid)):
            continue
        rid = str(owner.get("webRid") or "")
        if not re.fullmatch(r"[0-9]{1,20}", rid) or type(room.get("status")) is not int:
            break
        stream = room.get("streamUrl") or {}
        pull = stream.get("liveCoreSdkData", {}).get("pullData", {})
        qualities = pull.get("options", {}).get("qualities", [])
        return {"id_str": internal, "status": room["status"], "title": room.get("title", ""),
                "ttwid": visitor if isinstance(visitor, str) else "",
                "owner": {"id_str": owner_uid, "web_rid": rid, "nickname": owner.get("nickname", ""),
                          "avatar_thumb": {"url_list": (owner.get("avatarThumb") or {}).get("urlList", [])}},
                "cover": {"url_list": (room.get("cover") or {}).get("urlList", [])},
                "stream_url": {"flv_pull_url": stream.get("flvPullUrl") or {},
                    "hls_pull_url_map": stream.get("hlsPullUrlMap") or {},
                    "live_core_sdk_data": {"pull_data": {"stream_data": sdk_stream_data(page, pull.get("streamData", "")),
                        "options": {"qualities": [
                        dict(item, sdk_key=item.get("sdkKey")) for item in qualities]}}}}}

    raise RuntimeError("抖音直播分享页信息获取失败，请稍后重试")

def page_info(page):
    # 与 Streamlink 8.6.1 相同的公开页面数据格式，保留网页的画质元数据。
    chunks = re.findall(r'self\.__pace_f\.push\(\s*\[\s*\d+\s*,\s*("(?:\\.|[^"\\])*")\s*]\s*\)', page)
    for chunk in reversed(chunks):
        if "roomStore" not in chunk:
            continue
        try:
            payload = json.loads(re.sub(r"^\w+:", "", json.loads(chunk)))
            if not isinstance(payload, list):
                continue
        except (ValueError, TypeError):
            continue
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("state"), dict):
                continue
            try:
                info = item["state"]["roomStore"]["roomInfo"]
                room = info["room"]
                if type(room.get("status")) is int and room.get("id_str"):
                    pull = (room.get("stream_url") or {}).get("live_core_sdk_data", {}).get("pull_data", {})
                    if "stream_data" in pull:
                        pull["stream_data"] = sdk_stream_data(page, pull["stream_data"])
                    return info
            except (KeyError, TypeError, AttributeError):
                continue
    raise DouyinLookupError("抖音直播间网页未返回可识别的房间资料，可能是验证页或页面格式变化；请稍后重试，持续失败时请反馈完整链接、插件版本与失败时间")
