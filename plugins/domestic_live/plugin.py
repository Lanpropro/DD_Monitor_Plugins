"""国内公开直播流：虎牙、斗鱼、抖音关注卡片与 VLC 格子内播放。"""
import base64
from html import unescape
import json
import os
from pathlib import Path
import re
import struct
import time
import uuid
import zlib
from urllib.parse import parse_qsl, unquote, urlsplit

import requests
from streamlink import Streamlink
from streamlink.exceptions import PluginError
from streamlink.plugins.huya import Huya
from streamlink.plugins.douyu import Douyu
from streamlink.plugins.douyin import Douyin
from streamlink.stream.http import HTTPStream

from ddm import plugins as api
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


def huya_follow_request(session, method, uid, target):
    """官网 huyauserui 的只读 WUP 请求，复用本体已验证的 TARS 编解码。"""
    user, base = huya_user(session, uid)
    request = b"\x0a" + b"\x0a" + user + b"\x0b" + tars_int(1, int(target)) + b"\x0b"
    values = b"\x08" + tars_int(0, 1) + huya_string(0, "tReq") + tars_bytes(1, request)
    body = (tars_int(1, 3) + tars_int(2, 0) + tars_int(3, 0) + tars_int(4, 1) +
            huya_string(5, "huyauserui") + huya_string(6, method) + tars_bytes(7, values) +
            tars_int(8, 8000) + b"\x98\x0c\xa8\x0c")
    with session.post("https://cdnws.api.huya.com/",
            params={"baseinfo": base64.b64encode(base).decode("ascii")},
            data=struct.pack(">I", len(body) + 4) + body,
            headers={"Content-Type": "application/octet-stream", "Referer": "https://www.huya.com/"},
            timeout=(4, 8)) as response:
        response.raise_for_status()
        data = response.content
    if len(data) < 4 or len(data) > 4 * 1024 * 1024 or struct.unpack(">I", data[:4])[0] != len(data):
        raise RuntimeError("虎牙关注数据格式异常，请稍后重试")
    envelope = tars_fields(data[4:])
    if envelope.get(1) != 3 or envelope.get(6) != method:
        raise RuntimeError("虎牙关注响应不匹配，请稍后重试")
    entries = tars_fields(envelope.get(7, b""))[0]
    values = dict(zip(entries[::2], entries[1::2]))
    if "" in values and tars_fields(values[""]).get(0, -1) != 0:
        raise RuntimeError("虎牙关注读取失败，请重新登录后重试")
    if "tRsp" not in values:
        raise RuntimeError("虎牙关注响应缺少列表数据，请稍后重试")
    return tars_fields(values["tRsp"])[0]


class LiveQualityPlatform(api.Platform):
    def __init__(self):
        self._qualities = {}                  # 只保存档位元数据，不保存带签名的地址

    def room_quality_options(self, room_id: str) -> list[dict]:
        return [{key: value for key, value in item.items() if key in ("qn", "desc", "label")} for item in
                self._qualities.get(room_id, [{"qn": 10000, "desc": "最高可用"}])]

    def _select_quality(self, room_id, quality, preview):
        options = self._qualities[room_id]
        if preview:
            return options[-1]
        return next((item for item in options if item["qn"] == quality), options[0])

    def preview_url(self, room_id: str) -> tuple:
        return self.play_url(room_id, 10000, preview=True)

    def danmaku_client(self, room_id: str, parent=None):
        from ddm.live_danmaku import LiveDanmakuClient
        return LiveDanmakuClient(room_id, self, parent)


class HuyaPlatform(LiveQualityPlatform):
    kind = "huya"
    label = "虎牙"
    playback_mode = "stream"
    account_login_url = "https://www.huya.com/?evt_fe=login"
    account_cookie_domain = "huya.com"
    follow_login_url = "https://www.huya.com/?evt_fe=login"
    follow_cookie_domain = "huya.com"

    def follow_rooms(self, session, cancelled) -> list:
        if cancelled():
            return []
        account = self.account_info(session, cancelled)
        if cancelled():
            return []
        uid = account["uid"]
        data = huya_follow_request(session, "getAllSubscribeToUidList", uid, uid)
        targets = data.get(1)
        if not isinstance(targets, list) or any(not isinstance(value, int) or value <= 0 for value in targets):
            raise RuntimeError("虎牙关注列表格式异常，请稍后重试")
        rooms = {}
        for target in dict.fromkeys(targets):
            if cancelled():
                return []
            profile = huya_follow_request(session, "getUserProfile", uid, target).get(0, {})
            user, presenter = profile.get(0, {}), profile.get(1, {})
            rid = presenter.get(10) or presenter.get(3)
            if not rid:
                continue  # 普通用户和注销账号没有可导入的直播间。
            if user.get(0) != target:
                raise RuntimeError("虎牙关注主播信息不匹配，请稍后重试")
            canonical = self.normalize("huya:" + str(rid))
            if canonical not in rooms:
                # 账号资料中的最近直播不能代表当前开播状态，复用房间状态查询。
                rooms[canonical] = self.room_info(canonical).as_dict()
                if cancelled():
                    return []
        return list(rooms.values())

    def account_info(self, session, cancelled) -> dict:
        if cancelled():
            return {}
        with session.get("https://l.huya.com/udb_web/udbport2.php",
                params={"m": "HuyaLogin", "do": "checkLogin", "callback": "ddmAccount"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": self.account_login_url},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            match = re.fullmatch(r"\s*ddmAccount\((.*)\)\s*;?\s*", response.text, re.S)
            if match is None:
                raise RuntimeError("虎牙账号确认失败，请在官方页面重新登录后重试")
            data = json.loads(match[1])
        uid = str(data.get("uid") or "")
        if data.get("isLogined") is not True or not uid.isdigit() or int(uid) <= 0:
            raise RuntimeError("虎牙未登录或登录已过期，请在官方页面完成登录")
        face = str(data.get("userLogo") or "")
        if face.startswith("//"):
            face = "https:" + face
        parts = urlsplit(face)
        if (parts.scheme != "https" or parts.username or parts.password or
                not (parts.hostname or "").endswith(".msstatic.com")):
            face = ""
        return {"uid": uid, "uname": data.get("userNick") or data.get("userName") or uid,
                "face": face}

    def matches(self, room_id: str) -> bool:
        text = str(room_id or "").strip()
        if text.startswith("huya:"):
            return True
        try:
            parts = urlsplit(text)
            return parts.scheme in ("http", "https") and parts.hostname in (
                "www.huya.com", "huya.com", "m.huya.com")
        except ValueError:
            return False

    def normalize(self, room_id: str) -> str:
        text = str(room_id or "").strip()
        raw = text[5:] if text.startswith("huya:") else text
        if "://" in raw:
            parts = urlsplit(raw)
            if (parts.scheme not in ("http", "https") or
                    parts.hostname not in ("www.huya.com", "huya.com", "m.huya.com") or
                    parts.username or parts.password or parts.port not in (None, 80, 443)):
                raise ValueError("请使用虎牙官方直播间链接")
            raw = parts.path.strip("/")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", raw):
            raise ValueError("请填写 huya:房间号，或虎牙官方直播间链接")
        return f"huya:{raw}"

    def _streams(self, room_id: str, quality: int = 250, *, preview=False):
        session = Streamlink({"http-timeout": 8})
        parser = Huya(session, self.room_url(room_id))
        try:
            page = session.http.get(parser.url).text
            config = re.search(r"\bvar\s+hyPlayerConfig\s*=\s*\{", page)
            if config is None:
                return session, parser, {}
            script = page[config.end():].split("</script>", 1)[0]
            match = re.search(r'"?stream"?\s*:\s*', script)
            if match is None:
                return session, parser, {}
            data, _end = json.JSONDecoder().raw_decode(script[match.end():].lstrip())
            if isinstance(data, str):
                data = json.loads(base64.b64decode(data, validate=True))
            options = [{"qn": 10000 if item["iBitRate"] == 0 else 1000000 + item["iBitRate"],
                        "desc": item.get("sDisplayName") or (
                            "原画" if item["iBitRate"] == 0 else f"{item['iBitRate']} kbps"),
                        "rate": item["iBitRate"]}
                       for item in data.get("vMultiStreamInfo", [])
                       if isinstance(item.get("iBitRate"), int) and item["iBitRate"] >= 0]
            if not any(item["rate"] == 0 for item in options):
                options.append({"qn": 10000, "desc": "原画", "rate": 0})
            self._qualities[room_id] = sorted(options, key=lambda item: (
                item["rate"] == 0, item["rate"]), reverse=True)
            selected = self._select_quality(room_id, quality, preview)
            bitrate = selected["rate"]
            streams = {}
            for info in data.get("data", [{}])[0].get("gameStreamInfoList", []):
                # 使用网页明确提供的 HLS 线路；签名参数仍交给固定版本的 Streamlink。
                base = info.get("sHlsUrl")
                if not base:
                    continue
                name = info["sStreamName"]
                qs = dict(parse_qsl(unescape(info["sHlsAntiCode"])))
                params = parser._get_stream_params(qs.get("fm", ""), qs.get("fs", ""),
                    qs.get("ctype", "huya_live"), qs.get("wsTime", ""), name, bitrate)
                url = f"{base}/{name}.{info['sHlsUrlSuffix']}"
                if url.startswith("//"):
                    url = "https:" + url
                stream = HTTPStream(session, url, params=params)
                stream.ddm_quality = selected["qn"]
                streams[f"{info['sCdnType'].lower()}_source"] = stream
        except Exception as error:  # noqa: BLE001
            session.http.close()
            raise RuntimeError("虎牙房间获取失败，请稍后重试") from error
        return session, parser, streams

    def room_info(self, room_id: str) -> api.RoomInfo:
        canonical = self.normalize(room_id)
        raw = canonical.split(":", 1)[1]
        try:
            with requests.get(self.room_url(canonical), headers={
                    "User-Agent": "Mozilla/5.0", "Referer": "https://www.huya.com/"},
                    timeout=(4, 8)) as response:
                response.raise_for_status()
                page = response.text
            def page_data(name):
                match = re.search(r"\b" + name + r"\s*=\s*", page)
                if match is None:
                    raise ValueError("Missing room data")
                data, _end = json.JSONDecoder().raw_decode(page[match.end():].lstrip())
                if not isinstance(data, dict):
                    raise ValueError("Invalid room data")
                return data

            room = page_data("TT_ROOM_DATA")
            profile = page_data("TT_PROFILE_INFO")
            if not isinstance(room.get("isOn"), bool):
                raise ValueError("Missing live status")

            def image_url(value):
                url = str(value or "")
                if url.startswith("//"):
                    url = "https:" + url
                parts = urlsplit(url)
                host = parts.hostname or ""
                if (parts.scheme in ("http", "https") and not parts.username and
                        not parts.password and (host == "msstatic.com" or host.endswith(".msstatic.com"))):
                    return url
                return ""

            return api.RoomInfo(
                room_id=canonical, uname=profile.get("nick") or f"虎牙 · {raw}",
                title=room.get("introduction") or "虎牙直播间", live=room["isOn"], platform=self.kind,
                face=image_url(profile.get("avatar")), cover_url=image_url(room.get("screenshot")),
                extra={"playback_mode": self.playback_mode, "live_known": True},
            )
        except (requests.RequestException, ValueError) as error:
            raise RuntimeError("虎牙房间信息获取失败，请稍后重试") from error

    def rooms_status(self, room_ids: list) -> dict:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        result = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self.room_info, rid): rid for rid in room_ids}
            for future in as_completed(futures):
                try:
                    result[futures[future]] = future.result().as_dict()
                except Exception:  # noqa: BLE001
                    continue                    # 请求失败保留旧状态，不误报下播
        if room_ids and not result:
            raise RuntimeError("虎牙状态获取失败")
        return result

    def play_url(self, room_id: str, quality: int = 250, *, preview=False) -> tuple:
        session, _parser, streams = self._streams(room_id, quality, preview=preview)
        try:
            if not streams:
                raise RuntimeError("虎牙房间未开播，或没有可用的公开直播流")
            headers = {"User-Agent": session.http.headers["User-Agent"],
                       "Referer": "https://www.huya.com/"}
            # HLS 分片交给 FFmpeg 转封装，避免旧 VLC 处理虎牙 FLV 时间戳时停帧。
            # 不写 Cookie、不保存带签名的 URL。
            candidates = list(streams.values())
            seen = set()
            for stream in candidates:
                url = stream.to_url()
                if url in seen:
                    continue
                seen.add(url)
                if len(seen) > 3:
                    break
                try:
                    with requests.get(url, headers=headers, stream=True, timeout=(4, 6)) as response:
                        response.raise_for_status()
                        if next(response.iter_content(7), b"") != b"#EXTM3U":
                            continue
                        qn = getattr(stream, "ddm_quality", 10000)
                        return response.url, qn if isinstance(qn, int) else 10000, "huya", headers
                except requests.RequestException:
                    continue
            raise RuntimeError("虎牙直播线路暂不可用，请重试")
        finally:
            session.http.close()

    def room_url(self, room_id: str) -> str:
        canonical = self.normalize(room_id)
        return f"https://www.huya.com/{canonical.split(':', 1)[1]}"


class NumericLivePlatform(LiveQualityPlatform):
    """斗鱼、抖音共用的数字房间链接、原画和状态查询。"""
    hosts = ()
    image_hosts = ()

    def matches(self, room_id: str) -> bool:
        text = str(room_id or "").strip()
        if text.startswith(self.kind + ":"):
            return True
        try:
            parts = urlsplit(text)
            return parts.scheme in ("http", "https") and parts.hostname in self.hosts
        except ValueError:
            return False

    def normalize(self, room_id: str) -> str:
        text = str(room_id or "").strip()
        raw = text[len(self.kind) + 1:] if text.startswith(self.kind + ":") else text
        if "://" in raw:
            parts = urlsplit(raw)
            if (parts.scheme not in ("http", "https") or parts.hostname not in self.hosts or
                    parts.username or parts.password or parts.port not in (None, 80, 443)):
                raise ValueError(f"请使用{self.label}官方直播间链接")
            raw = self._room_id_from_url(parts)
        if not re.fullmatch(r"[0-9]{1,20}", raw):
            raise ValueError(f"请填写 {self.kind}:房间号，或{self.label}官方直播间链接")
        return f"{self.kind}:{raw}"

    def _room_id_from_url(self, parts) -> str:
        return parts.path.strip("/")

    def room_url(self, room_id: str) -> str:
        return f"https://{self.hosts[0]}/{self.normalize(room_id).split(':', 1)[1]}"

    def _image_url(self, value) -> str:
        if isinstance(value, dict):
            value = next(iter(value.get("url_list", [])), "")
        url = str(value or "")
        if url.startswith("//"):
            url = "https:" + url
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (parts.scheme in ("http", "https") and not parts.username and not parts.password and
                any(host == domain or host.endswith("." + domain) for domain in self.image_hosts)):
            return url
        return ""

    def rooms_status(self, room_ids: list) -> dict:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        result = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self.room_info, rid): rid for rid in room_ids}
            for future in as_completed(futures):
                try:
                    result[futures[future]] = future.result().as_dict()
                except Exception:  # noqa: BLE001
                    continue                    # 请求失败保留旧状态
        if room_ids and not result:
            raise RuntimeError(f"{self.label}状态获取失败")
        return result

    def play_url(self, room_id: str, quality: int = 250, *, preview=False) -> tuple:
        session = Streamlink({"http-timeout": 8})
        try:
            streams = self._streams(session, room_id, quality, preview=preview)
            if not streams:
                raise RuntimeError(f"{self.label}房间未开播，或没有可用的公开直播流")
            stream = streams.get("worst" if preview else "best") or next(iter(streams.values()))
            qn = getattr(stream, "ddm_quality", 10000)
            qn = qn if isinstance(qn, int) else 10000
            url = stream.to_url()
            headers = {"User-Agent": session.http.headers["User-Agent"],
                       "Referer": f"https://{self.hosts[0]}/"}
            if self.kind == "douyu":
                # 斗鱼部分地址只允许一个消费者，提前探流会使随后播放器连接短时间断开。
                return url, qn, self.kind, headers
            candidates = [stream] + [item for item in streams.values() if item is not stream
                                     and getattr(item, "ddm_quality", 10000) == qn]
            for candidate in candidates:
                try:
                    with session.http.get(candidate.to_url(), headers=headers, stream=True, timeout=(4, 6)) as response:
                        response.raise_for_status()
                        prefix = next(response.iter_content(7), b"")
                        if prefix[:3] == b"FLV" or prefix == b"#EXTM3U":
                            return response.url, qn, self.kind, headers
                except (requests.RequestException, PluginError):
                    continue  # 抖音部分房间拒绝 FLV，同画质 HLS 仍可用。
            raise RuntimeError(f"{self.label}直播线路暂不可用，请重试")
        except RuntimeError:
            raise
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"{self.label}取流失败，请检查房间链接或稍后重试") from error
        finally:
            session.http.close()


class DouyuPlatform(NumericLivePlatform):
    kind = "douyu"
    label = "斗鱼"
    hosts = ("www.douyu.com", "douyu.com", "m.douyu.com")
    image_hosts = ("douyucdn.cn", "douyu.com")
    follow_login_url = "https://www.douyu.com/directory/myFollow?from=normalFollow"
    follow_cookie_domain = "douyu.com"
    account_login_url = "https://passport.douyu.com/index/login?client_id=1"

    def account_info(self, session, cancelled) -> dict:
        if cancelled():
            return {}
        with session.get("https://www.douyu.com/lapi/member/api/getInfo",
                params={"client_type": 0}, headers={"User-Agent": "Mozilla/5.0",
                "Referer": self.follow_login_url}, timeout=(4, 8)) as response:
            response.raise_for_status()
            payload = response.json()
        data = payload.get("msg")
        uid = str(data.get("uid") or "") if isinstance(data, dict) else ""
        if str(payload.get("error")) != "0" or not uid.isdigit() or int(uid) <= 0:
            raise RuntimeError("斗鱼未登录或登录已过期，请在官方页面完成登录")
        # 官方网页 USER_INFO.nickname 与头像缓存同样来自 acf_ Cookie。
        cookies = {cookie.name: unquote(cookie.value) for cookie in session.cookies
                   if cookie.domain.lstrip(".") == "douyu.com"}
        uname = cookies.get("acf_nickname") or uid
        face = cookies.get("acf_avatar", "")
        if face.endswith("_"):
            face += "middle.jpg"
        elif face.endswith("="):
            face += "middle"
        face = self._image_url(face)
        if not face and not cancelled():
            try:
                with session.get(f"https://www.douyu.com/lapi/member/userInfo/getInfo/{uid}",
                        params={"size": "middle", "icon": 1},
                        headers={"User-Agent": "Mozilla/5.0", "Referer": self.follow_login_url},
                        timeout=(4, 8)) as response:
                    response.raise_for_status()
                    avatar = response.json().get("data")
                if isinstance(avatar, str):
                    avatar = json.loads(avatar)
                icon = avatar.get("icon") if isinstance(avatar, dict) else None
                if isinstance(icon, dict) and str(icon.get("code")) == "0":
                    image = icon.get("msg")
                    image = image.get("middle") if isinstance(image, dict) else image
                    if isinstance(image, str):
                        face = self._image_url("https://apic.douyucdn.cn/upload/" + image)
            except (requests.RequestException, ValueError):
                pass  # 头像暂不可用仍保留已确认的账号 ID。
        return {"uid": uid, "uname": uname, "face": face}

    def follow_rooms(self, session, cancelled) -> list:
        result = {}
        for page in range(1, 101):
            if cancelled():
                return []
            with session.get("https://www.douyu.com/wgapi/livenc/liveweb/follow/list",
                    params={"page": page, "sort": 0, "cid1": 0},
                    headers={"User-Agent": "Mozilla/5.0", "Referer": self.follow_login_url},
                    timeout=(4, 8)) as response:
                response.raise_for_status()
                payload = response.json()
            if str(payload.get("error")) != "0":
                raise RuntimeError("斗鱼关注获取失败，请先在页面登录；登录已过期时请重新登录")
            # 官方网页 GSON.parse 就是 JSON.parse，接口 data 为 JSON 字符串。
            data = payload.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise RuntimeError("斗鱼关注数据格式已变化，请稍后重试")
            for room in data["list"]:
                if not isinstance(room, dict) or not room.get("room_id"):
                    continue  # 关注页中的视频/频道推荐不是直播间
                canonical = self.normalize(f"douyu:{room['room_id']}")
                live = str(room.get("show_status")) == "1" and not room.get("videoLoop")
                result[canonical] = api.RoomInfo(room_id=canonical,
                    uname=room.get("nickname") or canonical, title=room.get("room_name") or "",
                    platform=self.kind, live=live, face=self._image_url(room.get("avatar_small")),
                    cover_url=self._image_url(room.get("room_src")),
                    extra={"playback_mode": "stream", "live_known": True}).as_dict()
            try:
                page_count = int(data["pageCount"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("斗鱼关注分页信息缺失，请稍后重试") from error
            if page_count < 0 or page_count > 100:
                raise RuntimeError("斗鱼关注列表页数异常，请稍后重试")
            if page >= page_count:
                return list(result.values())
        raise RuntimeError("斗鱼关注列表未完整读取，请稍后重试")

    def _room_data(self, raw):
        with requests.get(f"https://www.douyu.com/betard/{raw}", headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://www.douyu.com/"},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            return response.json()["room"]

    def room_info(self, room_id: str) -> api.RoomInfo:
        canonical = self.normalize(room_id)
        raw = canonical.split(":", 1)[1]
        try:
            try:
                room = self._room_data(raw)
            except ValueError:
                # 靓号的资料接口可能返回提示页，从公开直播页取得实际房间号。
                with requests.get(self.room_url(canonical), headers={
                        "User-Agent": "Mozilla/5.0", "Referer": "https://www.douyu.com/"},
                        timeout=(4, 8)) as response:
                    response.raise_for_status()
                    match = re.search(r"\bwindow\.room_id\s*=\s*([1-9][0-9]{0,19})\s*;", response.text)
                if match is None or match[1] == raw:
                    raise ValueError("Missing actual room ID")
                room = self._room_data(match[1])
            if type(room.get("show_status")) is not int or not room.get("room_id"):
                raise ValueError("Missing room status")
            canonical = self.normalize(f"douyu:{room['room_id']}")
            return api.RoomInfo(room_id=canonical, uname=room.get("nickname") or f"斗鱼 · {raw}",
                title=room.get("room_name") or "斗鱼直播间", platform=self.kind,
                live=room["show_status"] == 1 and not room.get("videoLoop"),
                face=self._image_url(room.get("owner_avatar") or room.get("avatar_small")),
                cover_url=self._image_url(room.get("room_pic") or room.get("coverSrc")),
                extra={"playback_mode": "stream", "live_known": True})
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            raise RuntimeError("斗鱼房间信息获取失败，请稍后重试") from error

    @staticmethod
    def _playback_cookies():
        jar = requests.cookies.RequestsCookieJar()
        if os.environ.get("DDM_NO_SAVE") == "1":
            return jar
        try:
            from ddm.account_store import AccountStore
        except ImportError:
            return jar  # Older hosts still support public playback without saved accounts.
        from PySide6.QtCore import QByteArray
        from PySide6.QtNetwork import QNetworkCookie
        for raw in AccountStore("douyu").load():
            for cookie in QNetworkCookie.parseCookies(QByteArray(raw.encode("utf-8"))):
                domain = cookie.domain().lstrip(".").lower()
                if domain != "douyu.com" and not domain.endswith(".douyu.com"):
                    continue
                expiry = None if cookie.isSessionCookie() else cookie.expirationDate().toSecsSinceEpoch()
                if expiry is not None and expiry <= time.time():
                    continue
                jar.set(bytes(cookie.name()).decode("utf-8"), bytes(cookie.value()).decode("utf-8"),
                        domain=cookie.domain(), path=cookie.path() or "/", expires=expiry,
                        secure=cookie.isSecure())
        return jar

    def _streams(self, session, room_id, quality=250, *, preview=False):
        session.http.cookies.update(self._playback_cookies())
        info = self.room_info(room_id)
        if not info.live:
            return {}
        parser = Douyu(session, self.room_url(info.room_id))
        raw = self.normalize(info.room_id).split(":", 1)[1]
        data = self._request_source(parser, raw)
        # 网页默认的 P2P 边缘线路可能很快断流，优先使用接口明确给出的普通 CDN。
        cdns = {item.get("cdn") for item in data.get("cdnsWithName", [])}
        cdn = ""
        if (urlsplit(data.get("rtmp_url", "")).hostname or "").endswith(".edgesrv.com"):
            cdn = next((name for name in ("hw-h5", "tct-h5", "ali-h5") if name in cdns), "")
        options = [{"qn": 10000 if item["rate"] == 0 else 2000000 + item["rate"],
                    "desc": item.get("name") or (
                        "原画" if item["rate"] == 0 else f"{item.get('bit', 0)} kbps"),
                    "rate": item["rate"], "bit": item.get("bit", 0)}
                   for item in data.get("multirates", [])
                   if isinstance(item.get("rate"), int) and item["rate"] >= 0]
        if not any(item["rate"] == 0 for item in options):
            options.append({"qn": 10000, "desc": "原画", "rate": 0, "bit": 0})
        self._qualities[room_id] = sorted(options, key=lambda item: (
            item["rate"] == 0, item["bit"]), reverse=True)
        selected = self._select_quality(room_id, quality, preview)
        rate = selected["rate"]
        if cdn or data.get("rate") != rate:
            data = self._request_source(parser, raw, cdn, rate=rate)
            if not data:
                raise RuntimeError("斗鱼所选画质取流失败，请重试或选择其他画质")
        if not data:
            return {}
        stream = HTTPStream(session, f"{data['rtmp_url']}/{data['rtmp_live']}")
        actual_rate = data.get("rate", rate)
        stream.ddm_quality = 10000 if actual_rate == 0 else 2000000 + actual_rate
        return {"source": stream}

    def _request_source(self, parser, raw, cdn="", *, rate=0):
        # 与固定版本 Streamlink 的公开网页请求一致，请求 AVC 及网页提供的画质/CDN。
        encryption = parser._get_encryption(parser.DID)
        if not encryption:
            return {}
        timestamp, values = encryption
        auth = parser._compute_auth(raw, timestamp, values["key"], values["rand_str"],
                                   values["enc_time"], values["is_special"])
        with parser.session.http.post(parser._URL_PLAY.format(rid=raw), data={
                "enc_data": values["enc_data"], "tt": str(timestamp), "did": parser.DID,
                "auth": auth, "cdn": cdn, "rate": str(rate), "hevc": "0", "fa": "0", "ive": "0"},
                headers={"Content-Type": "application/x-www-form-urlencoded"}) as response:
            response.raise_for_status()
            result = response.json()
        data = result.get("data")
        if result.get("error") != 0 or not isinstance(data, dict):
            return {}
        if urlsplit(data.get("rtmp_url", "")).scheme != "https" or not data.get("rtmp_live"):
            raise ValueError("Invalid public stream")
        return data


class DouyinPlatform(NumericLivePlatform):
    kind = "douyin"
    label = "抖音"
    hosts = ("live.douyin.com", "douyin.com")
    image_hosts = ("douyinpic.com", "byteimg.com", "ibytedtos.com", "douyincdn.com")
    account_login_url = "https://www.douyin.com/user/self"
    account_cookie_domain = "douyin.com"
    follow_login_url = "https://www.douyin.com/user/self"
    follow_cookie_domain = "douyin.com"
    follow_browser_url = ("https://www.douyin.com/aweme/v1/web/user/following/list/",
                          "https://www.douyin.com/aweme/v1/web/user/profile/self/")
    follow_browser_init_script = (Path(__file__).parent / "douyin_follows.js").read_text(encoding="utf-8")
    follow_browser_script = """async (url) => {
        const endpoint = new URL(url);
        if (endpoint.pathname === '/aweme/v1/web/user/following/list/') {
            return await window.__ddmDouyinFollows.read(endpoint.searchParams);
        }
        const deadline = Date.now() + 15000;
        while (Date.now() < deadline) {
            const chunks = window.webpackChunkdouyin_web;
            let require;
            if (chunks) chunks.push([['ddm-follow-' + Date.now()], {}, r => { require = r; }]);
            if (require && require.m) {
                const modules = Object.entries(require.m);
                const common = modules.find(([, fn]) => fn.toString().includes('CHANNEL_PC_WEB:function') &&
                    fn.toString().includes('COMMON_SEARCH_PARAMS:function'));
                const client = modules.find(([, fn]) => fn.toString().includes('skipCheckCode') &&
                    fn.toString().includes('securitySdkInitWeb') && fn.toString().includes('withCredentials'));
                if (common && client) {
                    if (endpoint.origin !== location.origin ||
                        endpoint.pathname !== '/aweme/v1/web/user/profile/self/') throw new Error('Invalid endpoint');
                    const params = {...require(common[0]).COMMON_SEARCH_PARAMS,
                        ...Object.fromEntries(endpoint.searchParams)};
                    // 官网客户端负责设备参数、签名初始化和验证弹窗。
                    return await require(client[0]).U2(endpoint.pathname, params, {timeout: 12000});
                }
            }
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        throw new Error('Official request client not ready');
    }"""

    def follow_rooms(self, session, cancelled) -> list:
        if cancelled():
            return []
        account = self.account_info(session, cancelled)
        params = {"aid": 6383, "device_platform": "webapp", "user_id": account.get("uid"),
                  "count": 20, "source_type": 2, "is_top": 1, "offset": 0,
                  "min_time": 0, "max_time": 0, "gps_access": 0, "address_book_access": 0}
        if account.get("sec_uid"):
            params["sec_user_id"] = account["sec_uid"]
        headers = {"User-Agent": "Mozilla/5.0", "Referer": self.follow_login_url}
        rooms, cursors = {}, {(0, 0, 0)}
        while not cancelled():
            with session.get("https://www.douyin.com/aweme/v1/web/user/following/list/",
                    params=params, headers=headers, timeout=(4, 8)) as response:
                response.raise_for_status()
                if not response.content:
                    raise RuntimeError("抖音关注读取被官网拒绝，请在官方页面重新登录后重试")
                payload = response.json()
            if payload.get("status_code") != 0:
                code = payload.get("status_code")
                detail = f"（状态 {code}）" if isinstance(code, int) else "（响应格式异常）"
                raise RuntimeError(f"抖音关注获取失败{detail}，请在官方页面完成登录或验证后重试")
            if not isinstance(payload.get("followings"), list):
                raise RuntimeError("抖音关注响应缺少列表，请稍后重试")
            for user in payload["followings"]:
                if cancelled():
                    return []
                room = user.get("room_data") or {}
                if isinstance(room, str):
                    room = json.loads(room)
                rid = user.get("web_rid") or room.get("web_rid") or (room.get("owner") or {}).get("web_rid")
                from_list = bool(rid)
                if not rid and user.get("uid"):
                    room = self._follow_room(session, str(user["uid"]), cancelled)
                    if cancelled():
                        return []
                    rid = room.get("web_rid")
                if not rid:
                    continue  # 普通用户和注销账号没有可导入的直播间。
                canonical = self.normalize("douyin:" + str(rid))
                if canonical in rooms:
                    continue
                if from_list:
                    info = self.room_info(canonical)
                    if cancelled():
                        return []
                    room = {"status": 2 if info.live else 4, "title": info.title,
                            "face": info.face, "cover": info.cover_url}
                face = next((url for item in [user.get("avatar_medium") or {}, user.get("avatar_thumb") or {}]
                             for raw in item.get("url_list", []) if (url := self._image_url(raw))), "")
                rooms[canonical] = api.RoomInfo(room_id=canonical, platform=self.kind,
                    uname=user.get("remark_name") or user.get("nickname") or str(rid),
                    title=room.get("title", ""), live=room.get("status") == 2,
                    face=face or self._image_url(room.get("face")), cover_url=self._image_url(room.get("cover")),
                    extra={"playback_mode": "stream", "live_known": "status" in room}).as_dict()
            if payload.get("has_more") in (False, 0):
                return list(rooms.values())
            if payload.get("has_more") not in (True, 1):
                raise RuntimeError("抖音关注分页信息缺失，请稍后重试")
            cursor = tuple(payload.get(key) for key in ("offset", "min_time", "max_time"))
            if any(value is None for value in cursor) or cursor in cursors:
                raise RuntimeError("抖音关注分页没有前进，请稍后重试")
            cursors.add(cursor)
            params.update(zip(("offset", "min_time", "max_time"), cursor))
        return []

    def _follow_room(self, session, uid, cancelled):
        # 个人页下播后没有 room_data；直播资料的 web_rid 也可能为空。
        # 通过最近直播记录的内部 ID 读取官方分享页，得到长期有效的网页房间号。
        with session.get("https://live.douyin.com/webcast/room/info_by_user/",
                params={"aid": 6383, "user_id": uid},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://live.douyin.com/"},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data")
        if payload.get("status_code") != 0 or not isinstance(data, dict):
            raise RuntimeError("抖音关注主播信息获取失败，请稍后重试")
        if not data or cancelled():
            return {}
        internal = str(data.get("id_str") or "")
        if not re.fullmatch(r"[0-9]{1,20}", internal) or str(data.get("owner_user_id")) != uid:
            raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
        with requests.get("https://webcast.amemv.com/webcast/reflow/" + internal,
                headers={"User-Agent": "Mozilla/5.0"}, timeout=(4, 8)) as response:
            response.raise_for_status()
            response.encoding = "utf-8"
            page = response.text
        if cancelled():
            return {}
        chunks = re.findall(r'self\.__rsc_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', page)
        for chunk in reversed(chunks):
            text = re.sub(r"^\w+:", "", json.loads(chunk))
            if not text.startswith('["$",'):
                continue
            node = json.loads(text)
            if len(node) != 4 or not isinstance(node[3], dict):
                continue
            data = node[3].get("data")
            if not isinstance(data, dict) or not isinstance(data.get("room"), dict):
                continue
            room = data["room"]
            owner = room.get("owner") or {}
            if str(room.get("idStr")) != internal or str(owner.get("idStr")) != uid:
                continue
            rid = str(owner.get("webRid") or "")
            if not re.fullmatch(r"[0-9]{1,20}", rid) or type(room.get("status")) is not int:
                break
            return {"web_rid": rid, "status": room["status"], "title": room.get("title", ""),
                    "face": {"url_list": (owner.get("avatarThumb") or {}).get("urlList", [])},
                    "cover": {"url_list": (room.get("cover") or {}).get("urlList", [])}}
        raise RuntimeError("抖音直播分享页信息获取失败，请稍后重试")

    def account_info(self, session, cancelled) -> dict:
        if cancelled():
            return {}
        with session.get("https://www.douyin.com/aweme/v1/web/user/profile/self/",
                params={"aid": 6383, "device_platform": "webapp", "source": "channel_pc_web",
                        "personal_center_strategy": 1},
                headers={"User-Agent": "Mozilla/5.0", "Referer": self.account_login_url},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            payload = response.json()
        data = payload.get("user")
        uid = str(data.get("uid") or "") if isinstance(data, dict) else ""
        if str(payload.get("status_code")) != "0" or not uid.isdigit() or int(uid) <= 0:
            raise RuntimeError("抖音未登录或登录已过期，请在官方页面完成登录")
        avatar = data.get("avatar_thumb") or {}
        face = next((url for value in avatar.get("url_list", []) if (url := self._image_url(value))), "")
        return {"uid": uid, "uname": data.get("nickname") or uid, "face": face,
                "sec_uid": data.get("sec_uid") or ""}

    def _room_id_from_url(self, parts) -> str:
        path = super()._room_id_from_url(parts)
        if re.fullmatch(r"categorynew/[0-9]+_[0-9]+", path):
            room_ids = [value for key, value in parse_qsl(parts.query, keep_blank_values=True)
                        if key == "live_web_rid"]
            if len(room_ids) != 1:
                raise ValueError("抖音分类页链接需包含唯一的 live_web_rid 直播间号")
            return room_ids[0]
        return path

    def room_info(self, room_id: str) -> api.RoomInfo:
        canonical = self.normalize(room_id)
        raw = canonical.split(":", 1)[1]
        try:
            with requests.get(self.room_url(canonical), headers={
                    "User-Agent": "Mozilla/5.0", "Referer": "https://live.douyin.com/"},
                    cookies={"__ac_nonce": uuid.uuid4().hex[:21]}, timeout=(4, 8)) as response:
                response.raise_for_status()
                page = response.text
            info = self._page_info(page)
            room = info["room"]
            owner = room.get("owner") or info.get("anchor") or {}
            return api.RoomInfo(room_id=canonical, uname=owner.get("nickname") or f"抖音 · {raw}",
                title=room.get("title") or "抖音直播间", live=room["status"] == 2,
                platform=self.kind, face=self._image_url(owner.get("avatar_thumb")),
                cover_url=self._image_url(room.get("cover")),
                extra={"playback_mode": "stream", "live_known": True})
        except (requests.RequestException, ValueError, KeyError, TypeError, StopIteration) as error:
            raise RuntimeError("抖音房间信息获取失败，请使用直播间完整链接或稍后重试") from error

    @staticmethod
    def _page_info(page):
        # 与 Streamlink 8.6.1 相同的公开页面数据格式，保留网页的画质元数据。
        chunks = re.findall(r'self\.__pace_f\.push\(\[\d+,("\w+:.+?")]\)</script>', page)
        for chunk in reversed(chunks):
            if "state" not in chunk or "streamStore" not in chunk:
                continue
            payload = json.loads(re.sub(r"^\w+:", "", json.loads(chunk)))
            state = next(item["state"] for item in payload
                         if isinstance(item, dict) and "state" in item)
            info = state["roomStore"]["roomInfo"]
            room = info["room"]
            if type(room.get("status")) is not int or not room.get("id_str"):
                raise ValueError("Missing room status")
            return info
        raise ValueError("Missing public room data")

    def _streams(self, session, room_id, quality=250, *, preview=False):
        page = session.http.get(self.room_url(room_id),
                                cookies={"__ac_nonce": uuid.uuid4().hex[:21]}).text
        room = self._page_info(page)["room"]
        if room["status"] != 2:
            return {}
        stream_url = room.get("stream_url") or {}
        urls = {key.lower(): value for key, value in (stream_url.get("flv_pull_url") or {}).items()
                if isinstance(value, str) and urlsplit(value).scheme in ("http", "https")}
        hls_urls = {key.lower(): value for key, value in (stream_url.get("hls_pull_url_map") or {}).items()
                    if isinstance(value, str) and urlsplit(value).scheme in ("http", "https")}
        if not urls and not hls_urls:
            return {}
        # 这些档位对应网页 SDK 的 ld/sd/hd/uhd；分辨率、帧率只用本房间元数据。
        sdk_keys = {"sd1": "ld", "sd2": "sd", "hd1": "hd", "full_hd1": "uhd"}
        metadata = stream_url.get("live_core_sdk_data", {}).get("pull_data", {}).get(
            "options", {}).get("qualities", [])
        metadata = {item.get("sdk_key"): item for item in metadata}
        keys = sorted(urls.keys() | hls_urls.keys(), key=lambda key: Douyin.stream_weight(key)[0], reverse=True)
        options = []
        for index, key in enumerate(keys):
            item = metadata.get(sdk_keys.get(key, key), {})
            label = item.get("name") or key.upper()
            desc = label
            resolution = str(item.get("resolution") or "")
            if re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", resolution):
                desc += f" · {resolution}"
            if isinstance(item.get("fps"), int) and item["fps"] > 0:
                desc += f" · {item['fps']}fps"
            # 稳定的整数仅用于软件保存/信号；不能当作像素高度或平台 rate。
            qn = 10000 if index == 0 else 3000000 + (zlib.crc32(key.encode()) & 0xFFFFFF)
            options.append({"qn": qn, "desc": desc, "label": label, "key": key})
        self._qualities[room_id] = options
        selected = self._select_quality(room_id, quality, preview)
        streams = {}
        for candidates in (urls, hls_urls):
            url = candidates.get(selected["key"])
            if not url:
                continue
            if url.startswith("http://"):
                url = "https://" + url[7:]
            stream = HTTPStream(session, url)
            stream.ddm_quality = selected["qn"]
            streams["hls" if streams else "source"] = stream
        return streams


class LivePlatformsPlugin(api.Plugin):
    def on_load(self, context: api.PluginContext) -> None:
        context.register_platform(HuyaPlatform())
        context.register_platform(DouyuPlatform())
        context.register_platform(DouyinPlatform())


plugin = LivePlatformsPlugin()
