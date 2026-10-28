"""国内公开直播流：虎牙、斗鱼、抖音关注卡片与 VLC 格子内播放。"""
import base64
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import re
import struct
import threading
import time
import uuid
import zlib
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

import requests
from streamlink import Streamlink
from streamlink.plugin.api import useragents
from streamlink.exceptions import PluginError
from streamlink.plugins.huya import Huya
from streamlink.plugins.douyu import Douyu
from streamlink.plugins.douyin import Douyin
from streamlink.stream.http import HTTPStream

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel

from ddm.dialogs import SettingsDialog
from ddm import plugins as api
from ddm.live_danmaku import tars_bytes, tars_fields, tars_int

__path__ = [os.path.dirname(__file__)]
__package__ = __name__
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__


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


class LiveQualityPlatform(api.Platform):
    share_hosts = ()

    def __init__(self):
        self._qualities = {}                  # 只保存档位元数据，不保存带签名的地址

    def _share_url(self, text):
        parts = urlsplit(str(text or "").strip())
        if parts.hostname not in self.share_hosts:
            return ""
        if (parts.scheme not in ("http", "https") or parts.username or parts.password or
                parts.port not in (None, 80, 443) or not parts.path.strip("/")):
            raise ValueError(f"请使用{self.label}官方直播分享链接")
        return parts._replace(fragment="").geturl()

    def _resolve_share_input(self, text):
        """后台返回规范输入及分享页房间数据；界面的 normalize 不联网。"""
        current = self._share_url(text)
        if not current:
            return text, None
        seen = set()
        for _ in range(4):
            if not self._share_url(current):
                return self.normalize(current), None
            if current in seen:
                raise ValueError("分享链接循环跳转，请重新复制直播间链接")
            seen.add(current)
            with requests.get(current, headers={"User-Agent": "Mozilla/5.0"},
                    timeout=(3, 5), allow_redirects=False, stream=True) as response:
                response.raise_for_status()
                target = response.headers.get("Location", "")
                if response.status_code in (301, 302, 303, 307, 308) and target:
                    current = urljoin(current, target)
                    # 在请求下一跳之前检查平台、协议、凭证和端口。
                    parts = urlsplit(current)
                    if (parts.scheme not in ("http", "https") or
                            parts.hostname not in (*self.hosts, *self.share_hosts)):
                        raise ValueError("分享链接没有指向对应平台的直播间")
                    self.normalize(current)
                else:
                    return self._share_page_room(current, response)
        if not self._share_url(current):
            return self.normalize(current), None
        raise ValueError("分享链接跳转过多，请使用完整直播间链接")

    def _share_page_room(self, url, response):
        raise ValueError("分享链接未指向可识别的直播间，请重新复制直播间链接")

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
    share_hosts = ("hy.fan",)
    hosts = ("www.huya.com", "huya.com", "m.huya.com")
    label = "虎牙"
    room_input_hint = "虎牙直播间链接"
    playback_mode = "stream"
    account_login_url = "https://www.huya.com/?evt_fe=login"
    account_cookie_domain = "huya.com"
    follow_login_url = "https://www.huya.com/?evt_fe=login"
    follow_cookie_domain = "huya.com"

    def __init__(self):
        super().__init__()
        self._failed_cdns = {}
        self._cdn_lock = threading.Lock()

    def report_playback_failure(self, room_id, url, reason):
        if reason not in ("HTTP_401", "HTTP_403", "HTTP_404", "HTTP_410", "HTTP_502", "HTTP_503", "HTTP_504", "STALL",
                          "Timeout", "ReadTimeout", "ConnectionError", "SSLError"):
            return
        canonical = self.normalize(room_id)
        host = urlsplit(url).hostname or ""
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
            return
        with self._cdn_lock:
            self._failed_cdns.setdefault(canonical, {})[host] = time.monotonic()
        logger = getattr(self, "_diagnostic_log", None)
        if logger is not None:
            logger(f"[平台诊断] 阶段=source_rejected 房间={canonical} CDN={host} 原因={reason} 避让秒数=60")

    def _playback_cookies(self):
        jar = requests.cookies.RequestsCookieJar()
        if os.environ.get("DDM_NO_SAVE") == "1":
            return jar
        try:
            from ddm.account_store import AccountStore
        except ImportError:
            return jar
        from PySide6.QtCore import QByteArray
        from PySide6.QtNetwork import QNetworkCookie
        context = getattr(self, "_context", None)
        window = context.window if context else None
        session = getattr(window, "_platform_login_sessions", {}).get("huya")
        cookies = session["cookies"] if session is not None else AccountStore("huya").load()
        for raw in cookies:
            for cookie in QNetworkCookie.parseCookies(QByteArray(raw.encode("utf-8"))):
                domain = cookie.domain().lstrip(".").lower()
                if domain != "huya.com" and not domain.endswith(".huya.com"):
                    continue
                expiry = None if cookie.isSessionCookie() else cookie.expirationDate().toSecsSinceEpoch()
                if expiry is not None and expiry <= time.time():
                    continue
                jar.set(bytes(cookie.name()).decode("utf-8"), bytes(cookie.value()).decode("utf-8"),
                        domain=cookie.domain(), path=cookie.path() or "/", expires=expiry,
                        secure=cookie.isSecure())
        return jar

    def _authorize_4k(self, session, data, rate):
        try:
            uid = self.account_info(session, lambda: False)["uid"]
        except RuntimeError as error:
            raise getattr(api, "LoginRequiredError", RuntimeError)(
                "虎牙 4K 需要有效登录，请重新登录具备对应 VIP 权益的账号") from error
        pid = int(data["data"][0]["gameLiveInfo"]["uid"])
        user, _base = huya_user(session, uid)
        request = b"\x0a\x0a" + user + b"\x0b" + tars_int(1, pid) + b"\x0b"
        vip = huya_wup_request(session, "wupui", "getPlatformVIPUserInfo", uid, request)
        # 与官网 isPlatformVip 一致：账号有效会员，并且该房间开放会员权益。
        if vip.get(0) != 1 or vip.get(3) != 1:
            raise RuntimeError("虎牙账号没有该直播间的 4K VIP 权益，请选择 2K 或更低画质")
        tiers = huya_wup_request(session, "huyaliveui", "getLivingMultiStreamInfo", uid, request)
        tier = next((item for item in tiers.get(1, []) if item.get(1) == rate), None)
        if tier is None or not (tier.get(5) == 1 or tier.get(6) == 5):
            raise RuntimeError("虎牙官方未授权所选 4K 档位，请选择其他画质")
        return uid

    @staticmethod
    def _account_stream_params(session, uid, info, bitrate, parser):
        user, _base = huya_user(session, uid)
        request = (b"\x0a" + huya_string(0, info["sFlvUrl"]) + huya_string(1, info["sStreamName"]) +
                   tars_int(2, 0) + b"\x3a" + user + b"\x0b" + tars_int(4, 66) + b"\x0b")
        token = huya_wup_request(session, "liveui", "getCdnTokenInfoEx", uid, request)
        params = dict(parse_qsl(unescape(token.get(0, ""))))
        # 官网 rotUid 保留高 32 位，只对低 32 位左旋 8 位；不能沿用匿名随机 UID。
        uid = int(uid)
        low = uid & 0xFFFFFFFF
        converted = (uid & ~0xFFFFFFFF) | ((low << 8 | low >> 24) & 0xFFFFFFFF)
        timestamp = int(time.time() * 1000)
        seqid = uid + timestamp
        template = base64.b64decode(unquote(params.pop("fm")), validate=True).decode("utf-8")
        if not all(value in template for value in ("$0", "$1", "$2", "$3")):
            raise RuntimeError("虎牙授权签名格式异常，请稍后重试")
        digest = hashlib.md5(f"{seqid}|{params['ctype']}|100".encode()).hexdigest()
        signed = template.replace("$0", str(converted)).replace("$1", info["sStreamName"])
        signed = signed.replace("$2", digest).replace("$3", params["wsTime"])
        params.update(parser._CONSTANTS)
        params.update(wsSecret=hashlib.md5(signed.encode()).hexdigest(), seqid=seqid, u=converted,
                      sdk_sid=timestamp, ratio=bitrate)
        return params

    def _select_quality(self, room_id, quality, preview):
        options = self._qualities[room_id]
        if preview:
            return min(options, key=lambda item: (item["rate"] == 0, item["rate"]))
        if quality in (250, 10000):
            # Auto starts with a public tier; gated HDR tiers must be selected explicitly.
            public = [item for item in options if 0 < item["rate"] <= 4000 and not item.get("restricted")]
            return max(public, key=lambda item: item["rate"]) if public else options[0]
        return super()._select_quality(room_id, quality, preview)

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
            return parts.scheme in ("http", "https") and parts.hostname in (*self.hosts, *self.share_hosts)
        except ValueError:
            return False

    def normalize(self, room_id: str) -> str:
        text = str(room_id or "").strip()
        if share := self._share_url(text):
            return share
        raw = text[5:] if text.startswith("huya:") else text
        if "://" in raw:
            parts = urlsplit(raw)
            if (parts.scheme not in ("http", "https") or
                    parts.hostname not in self.hosts or
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
            session.http.cookies.update(self._playback_cookies())
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
            options = [{"qn": 1000000 + item["iBitRate"],
                        "desc": item.get("sDisplayName") or (
                            "原画" if item["iBitRate"] == 0 else f"{item['iBitRate']} kbps"),
                        "rate": item["iBitRate"]}
                       for item in data.get("vMultiStreamInfo", [])
                       if isinstance(item.get("iBitRate"), int) and item["iBitRate"] >= 0]
            if not options:
                options.append({"qn": 1000000, "desc": "原画", "rate": 0})
            for option in options:
                option["label"] = option["desc"]
                option["restricted"] = bool(re.search(r"(?<!\d)4\s*K", option["desc"], re.I))
                if option["restricted"]:
                    option["desc"] += "（需对应 VIP 权益，授权播放待实测）"
            self._qualities[room_id] = sorted(options, key=lambda item: (
                bool(re.search(r"(?<!\d)2\s*K", item["label"], re.I)), not item["restricted"],
                item["rate"] == 0, item["rate"]), reverse=True)
            selected = self._select_quality(room_id, quality, preview)
            bitrate = selected["rate"]
            uid = self._authorize_4k(session.http, data, bitrate) if selected["restricted"] else None
            streams = {}
            for line_index, info in enumerate(data.get("data", [{}])[0].get("gameStreamInfoList", [])):
                # 复用官网 HLS 线路；会员 4K 用账号令牌，其余档位沿用公开取流。
                base = info.get("sHlsUrl")
                if not base:
                    continue
                name = info["sStreamName"]
                if uid is not None:
                    params = self._account_stream_params(session.http, uid, info, bitrate, parser)
                else:
                    qs = dict(parse_qsl(unescape(info["sHlsAntiCode"])))
                    params = parser._get_stream_params(qs.get("fm", ""), qs.get("fs", ""),
                        qs.get("ctype", "huya_live"), qs.get("wsTime", ""), name, bitrate)
                url = f"{base}/{name}.{info['sHlsUrlSuffix']}"
                if url.startswith("//"):
                    url = "https:" + url
                stream = HTTPStream(session, url, params=params)
                stream.ddm_quality = selected["qn"]
                key = f"{info['sCdnType'].lower()}_source"
                streams[key if key not in streams else f"{key}_{line_index}"] = stream
                # Some match rooms reject HLS while their official FLV works.
                # Do not substitute anonymous parameters for member-only tiers.
                if uid is None and info.get("sFlvUrl") and info.get("sFlvAntiCode"):
                    flv_qs = dict(parse_qsl(unescape(info["sFlvAntiCode"])))
                    # Page-issued URLs can end after a short preview. Sign the FLV
                    # anti-code itself (not the HLS code) for sustained playback.
                    if not flv_qs.get("fm") or not flv_qs.get("wsTime"):
                        continue
                    flv_params = parser._get_stream_params(
                        flv_qs["fm"], flv_qs.get("fs", ""), flv_qs.get("ctype", "huya_live"),
                        flv_qs["wsTime"], name, bitrate)
                    flv_base = info["sFlvUrl"]
                    if flv_base.startswith("//"):
                        flv_base = "https:" + flv_base
                    flv = HTTPStream(session, f"{flv_base}/{name}.{info.get('sFlvUrlSuffix', 'flv')}", params=flv_params)
                    flv.ddm_quality = selected["qn"]
                    flv.ddm_transport = "flv"
                    key = f"{info['sCdnType'].lower()}_flv_source"
                    streams[key if key not in streams else f"{key}_{line_index}"] = flv
        except RuntimeError:
            session.http.close()
            raise
        except Exception as error:  # noqa: BLE001
            session.http.close()
            raise RuntimeError("虎牙房间获取失败，请稍后重试") from error
        return session, parser, streams

    def room_info(self, room_id: str) -> api.RoomInfo:
        room_id, _share = self._resolve_share_input(room_id)
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

    @staticmethod
    def _verify_4k(url, headers):
        import av

        options = {"headers": "".join(f"{key}: {value}\r\n" for key, value in headers.items())}
        try:
            with av.open(url, options=options, timeout=(4, 6)) as container:
                # 不只检查 HLS 返回 200，避免把低清回退误报为 4K。
                for index, packet in enumerate(container.demux(video=0)):
                    for frame in packet.decode():
                        return frame.width >= 3840 and frame.height >= 2160
                    if index >= 90:
                        break
        except (av.FFmpegError, ValueError):
            pass
        return False

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
            now = time.monotonic()
            with self._cdn_lock:
                failures = {host: stamp for host, stamp in self._failed_cdns.get(self.normalize(room_id), {}).items()
                            if now - stamp < 60}
                self._failed_cdns[self.normalize(room_id)] = failures
            # 已在播放中失败的线路排在其他线路后；全部失败时仍允许新签名的有界尝试。
            candidates.sort(key=lambda stream: (
                getattr(stream, "ddm_transport", "hls") == "flv",
                failures.get(urlsplit(stream.to_url()).hostname, float("-inf"))))
            # Keep two FLV CDNs: the first may reject a match room while the second works.
            hls = [stream for stream in candidates if getattr(stream, "ddm_transport", "hls") != "flv"]
            flv = [stream for stream in candidates if getattr(stream, "ddm_transport", "hls") == "flv"]
            candidates = hls[:2] + flv[:2] if flv else hls[:4]
            seen = set()
            attempts = 0
            for stream in candidates:
                url = stream.to_url()
                if url in seen:
                    continue
                seen.add(url)
                if attempts >= 4:
                    break
                attempts += 1
                try:
                    if getattr(stream, "ddm_transport", "hls") == "flv":
                        target = self._probe_flv(url, headers, handoff=True)
                        if isinstance(target, tuple):
                            playable, token = target
                            from ddm.hls_proxy import FLV_HANDOFF_HEADER
                            headers[FLV_HANDOFF_HEADER] = token
                        else:
                            playable = target if isinstance(target, str) else url if target else ""
                    else:
                        playable = self._probe_hls(url, headers)
                    if not playable:
                        continue
                    qn = getattr(stream, "ddm_quality", 10000)
                    restricted = any(item["qn"] == qn and item.get("restricted") for item in
                                     self._qualities.get(room_id, []))
                    if restricted and not self._verify_4k(playable, headers):
                        continue
                    logger = getattr(self, "_diagnostic_log", None)
                    if logger is not None:
                        logger(f"[平台诊断] 阶段=source_selected 房间={self.normalize(room_id)} "
                               f"CDN={urlsplit(playable).hostname} 画质={qn} 避让线路数={len(failures)}")
                    return playable, qn if isinstance(qn, int) else 10000, "huya", headers
                except requests.RequestException as error:
                    logger = getattr(self, "_diagnostic_log", None)
                    if logger is not None:
                        logger(f"[平台诊断] 阶段=source_probe 房间={self.normalize(room_id)} "
                               f"CDN={urlsplit(url).hostname} 原因={LivePlatformsPlugin._diagnostic_reason(error)}")
                    continue
            # 10000 means auto/highest available, not an explicit HDR tier.
            # Try the site's source tier once and return its actual quality ID.
            source = next((item for item in self._qualities.get(room_id, []) if item["rate"] == 0), None)
            if quality in (250, 10000) and not preview and source is not None and source["qn"] != 10000:
                first = self._select_quality(room_id, quality, preview)
                if first["qn"] != source["qn"]:
                    session.http.close()
                    return self.play_url(room_id, source["qn"], preview=False)
            raise RuntimeError("虎牙所选画质未能获取可播放视频，请重试或手动选择其他画质")
        finally:
            session.http.close()

    @staticmethod
    def _probe_flv(url, headers, *, handoff=False):
        response = requests.get(url, headers=headers, stream=True, timeout=(4, 6))
        retained = False
        try:
            response.raise_for_status()
            prefix = bytearray()
            chunks = response.iter_content(65536)
            for chunk in chunks:
                prefix.extend(chunk)
                if len(prefix) >= 9:
                    break
            valid = (len(prefix) >= 9 and prefix[:4] == b"FLV\x01" and
                     bool(prefix[4] & 5) and struct.unpack(">I", prefix[5:9])[0] >= 9)
            if not valid:
                return ""
            if handoff:
                from ddm.hls_proxy import retain_flv_response
                token = retain_flv_response(response, bytes(prefix), chunks)
                retained = True
                return response.url, token
            return response.url
        finally:
            if not retained:
                response.close()

    @staticmethod
    def _probe_hls(url, headers):
        """播放列表可读不代表分片可读；只探测一个分片，不下载或保存整段直播。"""
        current, original = url, ""
        for _ in range(2):
            with requests.get(current, headers=headers, stream=True, timeout=(4, 6)) as response:
                response.raise_for_status()
                chunks, length = [], 0
                for chunk in response.iter_content(4096):
                    chunks.append(chunk)
                    length += len(chunk)
                    if length > 128 * 1024:
                        return ""
                text = b"".join(chunks).decode("utf-8-sig", errors="replace")
                if not text.startswith("#EXTM3U"):
                    return ""
                current = response.url
                original = original or current
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            media = next((urljoin(current, line) for line in lines if not line.startswith("#")), "")
            parts = urlsplit(media)
            if parts.scheme not in ("http", "https") or parts.username or parts.password:
                return ""
            if any(line.startswith("#EXT-X-STREAM-INF:") for line in lines):
                current = media
                continue
            if not any(line.startswith("#EXTINF:") for line in lines):
                return ""
            with requests.get(media, headers=headers, stream=True, timeout=(4, 6)) as response:
                response.raise_for_status()
                prefix = next(response.iter_content(512), b"").lstrip().lower()
                if prefix and not prefix.startswith((b"<html", b"<!doctype", b"<?xml")):
                    return original
            return ""
        return ""

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
            return parts.scheme in ("http", "https") and parts.hostname in (*self.hosts, *self.share_hosts)
        except ValueError:
            return False

    def normalize(self, room_id: str) -> str:
        text = str(room_id or "").strip()
        if share := self._share_url(text):
            return share
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
    share_hosts = ("s.douyu.com",)
    label = "斗鱼"
    room_input_hint = "斗鱼直播间链接"
    hosts = ("www.douyu.com", "douyu.com", "m.douyu.com")
    image_hosts = ("douyucdn.cn", "douyu.com")
    follow_login_url = "https://www.douyu.com/directory/myFollow?from=normalFollow"
    follow_cookie_domain = "douyu.com"
    account_login_url = "https://passport.douyu.com/index/login?client_id=1"

    def danmaku_client(self, room_id: str, parent=None):
        from .douyu_danmaku import DouyuDanmakuClient
        return DouyuDanmakuClient(room_id, self, parent)

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
        room_id, _share = self._resolve_share_input(room_id)
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
                    ids = {match[1]} if match else set(re.findall(
                        r'''(?:\broomI[Dd]|["']roomI[Dd]["'])\s*:\s*([1-9][0-9]{0,19})\b''', response.text))
                if len(ids) != 1 or raw in ids:
                    raise ValueError("Missing actual room ID")
                room = self._room_data(ids.pop())
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

    def _playback_cookies(self):
        jar = requests.cookies.RequestsCookieJar()
        if os.environ.get("DDM_NO_SAVE") == "1":
            return jar
        try:
            from ddm.account_store import AccountStore
        except ImportError:
            return jar  # Older hosts still support public playback without saved accounts.
        from PySide6.QtCore import QByteArray
        from PySide6.QtNetwork import QNetworkCookie
        context = getattr(self, "_context", None)
        window = context.window if context else None
        session = getattr(window, "_platform_login_sessions", {}).get("douyu")
        cookies = session["cookies"] if session is not None else AccountStore("douyu").load()
        for raw in cookies:
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
        actual_rate = data.get("rate", rate)
        if actual_rate != rate:
            try:
                self.account_info(session.http, lambda: False)
            except RuntimeError as error:
                raise getattr(api, "LoginRequiredError", RuntimeError)(
                    "斗鱼登录已失效或未登录，请重新登录后获取所选画质") from error
            raise RuntimeError("斗鱼未返回所选画质，请重试或选择其他线路/画质")
        stream = HTTPStream(session, f"{data['rtmp_url']}/{data['rtmp_live']}")
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


class DouyinLookupError(RuntimeError):
    """可直接向用户展示的诊断，不包含响应正文、凭证或签名 URL。"""

    @property
    def user_message(self):
        return str(self)


class DouyinPlatform(NumericLivePlatform):
    kind = "douyin"
    share_hosts = ("v.douyin.com", "webcast.amemv.com")
    label = "抖音"
    room_input_hint = "抖音直播间链接"
    profile_browser_init_script = (Path(__file__).parent / "douyin_profile.js").read_text(encoding="utf-8")
    hosts = ("live.douyin.com", "douyin.com", "www.douyin.com")
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

    def __init__(self):
        super().__init__()
        self._anchor_uids = {}
        self._profile_additions = {}
        self._context = None

    def restore_follow_rooms(self, rooms):
        for room in rooms:
            rid, uid = str(room.get("room_id") or ""), str(room.get("anchor_uid") or "")
            if re.fullmatch(r"douyin:[0-9]{1,20}", rid) and re.fullmatch(r"[0-9]{1,20}", uid):
                self._anchor_uids[rid] = uid
        if self._context is not None:
            self._context.set_setting("douyin_room_owners", dict(self._anchor_uids))

    def follow_accounts(self, session, cancelled):
        """关注身份独立于房间；读取阶段不逐个等待直播资料。"""
        return self.follow_rooms(session, cancelled, accounts_only=True)

    def resolve_follow_account(self, account, cancelled):
        uid = str(account.get("anchor_uid") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,19}", uid) or cancelled():
            return dict(account)
        try:
            room = self._follow_room(requests, uid, cancelled)
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
            if cancelled():
                return dict(account)
            if not account.get("room_id"):
                raise self._lookup_error("关注主播直播资料", error) from error
            # 已有固定房间号时，不让补充接口失败阻止刷新；仍核对主播身份。
            try:
                info = self._room_page(self.normalize(account["room_id"]), expected_uid=uid)
            except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as page_error:
                raise DouyinLookupError(f"{self._lookup_error('关注主播直播资料', error)}；"
                    f"直播间网页回退也失败：{self._lookup_error('直播间资料', page_error)}") from page_error
            data = info["room"]
            owner = data.get("owner") or info.get("anchor") or {}
            room = {"web_rid": account["room_id"].split(":", 1)[1],
                    "status": data["status"], "title": data.get("title", ""),
                    "face": owner.get("avatar_thumb"), "cover": data.get("cover")}
        if not room or cancelled():
            return dict(account)
        canonical = self.normalize("douyin:" + str(room["web_rid"]))
        if account.get("room_id") and account["room_id"] != canonical:
            raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
        self._anchor_uids[canonical] = uid
        result = dict(account)
        result.update(room_id=canonical, live=room.get("status") == 2,
                      live_known=type(room.get("status")) is int,
                      title=room.get("title") or "",
                      cover_url=self._image_url(room.get("cover")))
        if not result.get("face"):
            result["face"] = self._image_url(room.get("face"))
        return result

    def profile_sec_uid(self, text):
        parts = urlsplit(str(text or "").strip())
        if parts.hostname not in ("douyin.com", "www.douyin.com") or not parts.path.startswith("/user/"):
            return ""
        if (parts.scheme not in ("http", "https") or parts.username or parts.password
                or parts.port not in (None, 80, 443)):
            raise ValueError("请使用抖音官方主播主页链接")
        sec_uid = parts.path.removeprefix("/user/").rstrip("/")
        if not re.fullmatch(r"MS4wLjAB[A-Za-z0-9_-]{10,180}", sec_uid):
            raise ValueError("请粘贴主播的完整个人主页链接，不支持「我的主页」链接")
        return sec_uid

    def profile_room(self, uid, cancelled, *, web_rid=""):
        if not re.fullmatch(r"[0-9]{1,20}", str(uid)) or int(uid) <= 0:
            raise ValueError("抖音主页未返回有效主播身份")
        if cancelled():
            return ""
        if web_rid:
            canonical = self.normalize("douyin:" + str(web_rid))
            self._anchor_uids[canonical] = str(uid)
            return canonical
        room = self._follow_room(requests, str(uid), cancelled)
        if cancelled():
            return ""
        if not room:
            raise RuntimeError("官网暂未返回可用的直播记录，请在主播开播时重试，或使用直播间完整链接")
        canonical = self.normalize("douyin:" + room["web_rid"])
        self._anchor_uids[canonical] = str(uid)
        return canonical

    def profile_room_from_page(self, user, cancelled):
        canonical = self.profile_room(user["uid"], cancelled, web_rid=user.get("web_rid", ""))
        if not canonical or cancelled():
            return ""
        if user.get("web_rid"):
            # 官网已确认主播和固定房间号；添加阶段不再依赖另一条匿名 HTTP 请求。
            status = user.get("status")
            info = api.RoomInfo(room_id=canonical, platform=self.kind,
                uname=user.get("nickname") or f"抖音 · {user['web_rid']}",
                title=user.get("title") or "抖音直播间", live=status == 2,
                face=self._image_url(user.get("avatar")), cover_url=self._image_url(user.get("cover")),
                extra={"playback_mode": "stream", "live_known": type(status) is int,
                       "anchor_uid": str(user["uid"])})
            self._profile_additions[canonical] = (time.monotonic(), info)
        return canonical

    def follow_rooms(self, session, cancelled, *, accounts_only=False) -> list:
        if cancelled():
            return []
        account = self.account_info(session, cancelled)
        params = {"aid": 6383, "device_platform": "webapp", "user_id": account.get("uid"),
                  "count": 20, "source_type": 2, "is_top": 1, "offset": 0,
                  "min_time": 0, "max_time": 0, "gps_access": 0, "address_book_access": 0}
        if account.get("sec_uid"):
            params["sec_user_id"] = account["sec_uid"]
        headers = {"User-Agent": "Mozilla/5.0", "Referer": self.follow_login_url}
        known = {uid: rid for rid, uid in self._anchor_uids.items()}
        rooms, cursors = {}, {(0, 0, 0)}
        while not cancelled():
            try:
                with session.get("https://www.douyin.com/aweme/v1/web/user/following/list/",
                        params=params, headers=headers, timeout=(4, 8)) as response:
                    response.raise_for_status()
                    if not response.content:
                        raise DouyinLookupError("抖音关注接口返回空内容，请在官方页面完成登录或验证后重试")
                    payload = response.json()
            except (requests.RequestException, ValueError) as error:
                raise self._lookup_error("关注列表", error) from error
            if not isinstance(payload, dict):
                raise DouyinLookupError("抖音关注响应格式异常，请稍后重试；持续失败时请反馈插件版本与失败时间")
            if payload.get("status_code") != 0:
                code = payload.get("status_code")
                detail = f"（状态 {code}）" if isinstance(code, int) else "（响应格式异常）"
                raise RuntimeError(f"抖音关注获取失败{detail}，请在官方页面完成登录或验证后重试")
            if not isinstance(payload.get("followings"), list):
                raise RuntimeError("抖音关注响应缺少列表，请稍后重试")
            for user in payload["followings"]:
                if cancelled():
                    return []
                if not isinstance(user, dict):
                    raise DouyinLookupError("抖音关注响应包含无效账号资料，请重新读取")
                room = user.get("room_data") or {}
                if isinstance(room, str):
                    try:
                        room = json.loads(room)
                    except ValueError:
                        room = {}
                if not isinstance(room, dict):
                    room = {}
                uid = str(user.get("uid") or "")
                owner = room.get("owner") or {}
                if not isinstance(owner, dict):
                    owner = {}
                owner_uid = str(owner.get("id_str") or owner.get("id") or "")
                if owner_uid and owner_uid != uid:
                    raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
                rid = user.get("web_rid") or room.get("web_rid") or owner.get("web_rid")
                if rid and not re.fullmatch(r"[0-9]{1,20}", str(rid)):
                    rid = ""
                if rid and self.normalize("douyin:" + str(rid)) in rooms:
                    continue
                # 官网列表已提供固定房间和状态时直接采用，避免逐个查询拖慢读取。
                # 补充接口返回空记录或超时时，不能丢掉官网已经确认的房间号。
                if not accounts_only and uid and (not rid or type(room.get("status")) is not int):
                    try:
                        resolved = self._follow_room(session, uid, cancelled)
                    except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
                        if not rid:
                            raise self._lookup_error("关注主播直播资料", error) from error
                        resolved = {}
                    if cancelled():
                        return []
                    if resolved:
                        if rid and str(resolved["web_rid"]) != str(rid):
                            raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
                        room, rid = resolved, resolved["web_rid"]
                if accounts_only and not rid:
                    rid = known.get(uid, "").removeprefix("douyin:")
                if not rid and not accounts_only:
                    continue
                if accounts_only and not re.fullmatch(r"[1-9][0-9]{0,19}", uid):
                    raise RuntimeError("抖音关注响应缺少有效账号身份，请稍后重试")
                canonical = self.normalize("douyin:" + str(rid)) if rid else ""
                key = uid if accounts_only else canonical
                if key in rooms:
                    continue
                if canonical and re.fullmatch(r"[1-9][0-9]{0,19}", uid):
                    self._anchor_uids[canonical] = uid
                face = next((url for item in [user.get("avatar_medium") or {}, user.get("avatar_thumb") or {}]
                             for raw in item.get("url_list", []) if (url := self._image_url(raw))), "")
                rooms[key] = api.RoomInfo(room_id=canonical, platform=self.kind,
                    uname=user.get("remark_name") or user.get("nickname") or str(rid),
                    title=room.get("title", ""), live=room.get("status") == 2,
                    face=face or self._image_url(room.get("face")), cover_url=self._image_url(room.get("cover")),
                    extra={"playback_mode": "stream", "live_known": type(room.get("status")) is int,
                           "anchor_uid": uid, "sec_uid": str(user.get("sec_uid") or "")}).as_dict()
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
        room = self._share_room(session, uid, cancelled)
        if not room:
            return {}
        owner = room["owner"]
        rid = owner["web_rid"]
        return {"web_rid": rid, "status": room["status"], "title": room.get("title", ""),
                "face": owner.get("avatar_thumb"), "cover": room.get("cover")}

    def _share_room(self, session, uid, cancelled):
        # 个人页下播后没有 room_data；直播资料的 web_rid 也可能为空。
        # 通过最近直播记录的内部 ID 读取官方分享页，得到长期有效的网页房间号。
        with session.get("https://live.douyin.com/webcast/room/info_by_user/",
                params={"aid": 6383, "user_id": uid},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://live.douyin.com/"},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            payload = response.json()
        internal = self._share_identity(payload, uid)
        if not internal or cancelled():
            return {}
        with requests.get("https://webcast.amemv.com/webcast/reflow/" + internal,
                headers={"User-Agent": "Mozilla/5.0"}, timeout=(4, 8)) as response:
            response.raise_for_status()
            response.encoding = "utf-8"
            page = response.text
            visitor = response.cookies.get("ttwid")
        if cancelled():
            return {}
        return self._share_info(page, internal, uid, visitor)

    @staticmethod
    def _share_identity(payload, uid):
        data = payload.get("data")
        if payload.get("status_code") != 0 or not isinstance(data, dict):
            raise RuntimeError("抖音关注主播信息获取失败，请稍后重试")
        if not data:
            return ""
        internal = str(data.get("id_str") or "")
        if not re.fullmatch(r"[0-9]{1,20}", internal) or str(data.get("owner_user_id")) != uid:
            raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
        return internal

    @staticmethod
    def _share_info(page, internal, uid, visitor):
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
            owner_uid = str(owner.get("idStr") or "")
            if (str(room.get("idStr")) != internal or
                    (uid and owner_uid != uid) or not re.fullmatch(r"[1-9][0-9]{0,19}", owner_uid)):
                continue
            rid = str(owner.get("webRid") or "")
            if not re.fullmatch(r"[0-9]{1,20}", rid) or type(room.get("status")) is not int:
                break
            stream = room.get("streamUrl") or {}
            qualities = stream.get("liveCoreSdkData", {}).get("pullData", {}).get("options", {}).get("qualities", [])
            return {"id_str": internal, "status": room["status"], "title": room.get("title", ""),
                    "ttwid": visitor if isinstance(visitor, str) else "",
                    "owner": {"id_str": owner_uid, "web_rid": rid, "nickname": owner.get("nickname", ""),
                              "avatar_thumb": {"url_list": (owner.get("avatarThumb") or {}).get("urlList", [])}},
                    "cover": {"url_list": (room.get("cover") or {}).get("urlList", [])},
                    "stream_url": {"flv_pull_url": stream.get("flvPullUrl") or {},
                        "hls_pull_url_map": stream.get("hlsPullUrlMap") or {},
                        "live_core_sdk_data": {"pull_data": {"options": {"qualities": [
                            dict(item, sdk_key=item.get("sdkKey")) for item in qualities]}}}}}

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

    def _share_page_room(self, url, response):
        parts = urlsplit(url)
        match = re.fullmatch(r"/(?:douyin/)?webcast/reflow/([1-9][0-9]{0,19})/?", parts.path)
        if parts.hostname != "webcast.amemv.com" or match is None:
            return super()._share_page_room(url, response)
        # 复用官网分享页解析，并核对当场直播 ID；返回的是主播固定 webRid。
        response.encoding = "utf-8"
        room = self._share_info(response.text, match[1], "", response.cookies.get("ttwid"))
        canonical = self.normalize("douyin:" + room["owner"]["web_rid"])
        self._anchor_uids[canonical] = room["owner"]["id_str"]
        return canonical, room

    def room_info(self, room_id: str) -> api.RoomInfo:
        try:
            room_id, share = self._resolve_share_input(room_id)
            canonical = self.normalize(room_id)
            raw = canonical.split(":", 1)[1]
            initial = self._profile_additions.pop(canonical, None)
            if initial is not None and time.monotonic() - initial[0] < 60:
                return initial[1]
            info = {"room": share} if share is not None else self.room_data(canonical)
            room = info["room"]
            owner = room.get("owner") or info.get("anchor") or {}
            return api.RoomInfo(room_id=canonical, uname=owner.get("nickname") or f"抖音 · {raw}",
                title=room.get("title") or "抖音直播间", live=room["status"] == 2,
                platform=self.kind, face=self._image_url(owner.get("avatar_thumb")),
                cover_url=self._image_url(room.get("cover")),
                extra={"playback_mode": "stream", "live_known": True,
                       "anchor_uid": str(owner.get("id_str") or owner.get("id") or "")})
        except (requests.RequestException, ValueError, KeyError, TypeError, StopIteration) as error:
            raise self._lookup_error("直播间资料", error) from error

    @staticmethod
    def _lookup_error(subject, error):
        if isinstance(error, DouyinLookupError):
            return error
        if isinstance(error, requests.Timeout):
            detail = "请求超时，请稍后重试"
        elif isinstance(error, requests.HTTPError):
            status = error.response.status_code if error.response is not None else None
            detail = (f"请求被官网拒绝（HTTP {status}），请稍后重试" if status in (403, 429)
                      else f"请求失败（HTTP {status}），请稍后重试")
        elif isinstance(error, requests.exceptions.SSLError):
            detail = "安全连接失败，请检查系统时间及网络代理设置"
        elif isinstance(error, json.JSONDecodeError):
            detail = "返回内容不是有效 JSON 资料，请稍后重试；持续失败时请反馈插件版本与失败时间"
        elif isinstance(error, requests.RequestException):
            detail = "连接失败，请检查网络或代理设置后重试"
        else:
            detail = "返回内容未包含可识别的资料，请稍后重试；持续失败时请反馈完整链接、插件版本与失败时间"
        return DouyinLookupError(f"抖音{subject}{detail}")

    def room_data(self, room_id, session=None):
        canonical = self.normalize(room_id)
        uid = self._anchor_uids.get(canonical)
        if uid:
            try:
                room = self._share_room(requests, uid, lambda: False)
                if room:
                    if room["owner"]["web_rid"] != canonical.split(":", 1)[1]:
                        raise ValueError("Mismatched Douyin room")
                    return {"room": room, "ttwid": room.pop("ttwid", "")}
            except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
                cached_error = self._lookup_error("主播接口", error)
                try:
                    return self._room_page(canonical, session)
                except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as page_error:
                    raise DouyinLookupError(f"{cached_error}；直播间网页回退也失败：{self._lookup_error('直播间资料', page_error)}") from page_error
        return self._room_page(canonical, session)

    def _room_page(self, canonical, session=None, *, expected_uid=""):
        getter = session.http.get if session is not None else requests.get
        response = getter(self.room_url(canonical), headers={
            "User-Agent": useragents.DEFAULT, "Referer": "https://live.douyin.com/"},
            cookies={"__ac_nonce": uuid.uuid4().hex[:21]}, timeout=(4, 8))
        try:
            response.raise_for_status()
            response.encoding = "utf-8"
            info = self._page_info(response.text)
            visitor = response.cookies.get("ttwid")
            info["ttwid"] = visitor if isinstance(visitor, str) else ""
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as page_error:
            logger = getattr(self, "_diagnostic_log", None)
            if logger is not None:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                content_type = content_type if content_type in ("text/html", "application/json", "text/plain") else "other"
                encoding = response.headers.get("Content-Encoding", "").lower()
                encoding = encoding if encoding in ("", "gzip", "deflate", "br", "zstd") else "other"
                host = urlsplit(response.url or "").hostname
                host = host if host in self.hosts else "other"
                page = response.text
                markers = {name: name in page for name in
                           ("__pace_f", "roomStore", "streamStore", "__rsc_f", "RENDER_DATA")}
                markers["verification_text"] = any(word in page.lower() for word in
                    ("验证码", "verifycenter", "captcha"))
                logger(f"[平台诊断] 阶段=page_response 房间={canonical} HTTP={response.status_code} "
                       f"类型={content_type} 编码={encoding or 'none'} 字节数={len(response.content)} "
                       f"最终主机={host} 跳转次数={len(response.history)} 标记={json.dumps(markers)}")
            # 正文格式不兼容时使用独立的公开数据入口，不重复请求同一张网页。
            if response.status_code == 200:
                try:
                    visitor = response.cookies.get("ttwid")
                    info = self._room_enter(canonical, visitor if isinstance(visitor, str) else "")
                except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
                    page = response.text
                    if (any(word in page.lower() for word in ("验证码", "verifycenter", "captcha")) and
                            not any(marker in page for marker in ("roomStore", "__rsc_f", "RENDER_DATA"))):
                        raise DouyinLookupError("抖音页面包含验证相关内容，未返回直播资料。请在软件内使用主播个人主页链接添加，"
                            "或重新读取抖音关注，并在官网页面完成验证后重试。") from error
                    raise DouyinLookupError(str(self._lookup_error("直播间网页", page_error))
                        + "；公开接口回退也失败：" + str(self._lookup_error("房间接口", error))) from error
            else:
                raise
        finally:
            response.close()
        owner = info["room"].get("owner") or info.get("anchor") or {}
        owner_uid = str(owner.get("id_str") or owner.get("id") or "")
        if expected_uid and owner_uid != expected_uid:
            raise DouyinLookupError("抖音关注主播信息不匹配，请稍后重试")
        web_rid = str(owner.get("web_rid") or "")
        if web_rid and web_rid != canonical.split(":", 1)[1]:
            raise ValueError("Mismatched Douyin room")
        cached_uid = self._anchor_uids.get(canonical)
        if cached_uid and owner_uid != cached_uid and not web_rid:
            raise ValueError("Mismatched Douyin anchor")
        if re.fullmatch(r"[0-9]{1,20}", owner_uid):
            self._anchor_uids[canonical] = owner_uid
        return info

    def _room_enter(self, canonical, visitor=""):
        raw = self.normalize(canonical).split(":", 1)[1]
        with requests.get("https://live.douyin.com/webcast/room/web/enter/",
                params={"aid": 6383, "app_name": "douyin_web", "live_id": 1,
                        "device_platform": "web", "web_rid": raw},
                headers={"User-Agent": useragents.DEFAULT, "Referer": "https://live.douyin.com/"},
                cookies={"ttwid": visitor} if visitor else {}, timeout=(4, 8)) as response:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("status_code") != 0:
                raise DouyinLookupError("抖音公开房间接口未返回有效资料，请稍后重试")
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                raise DouyinLookupError("抖音公开房间接口资料格式异常")
            rooms = data.get("data") or []
            if not isinstance(rooms, list):
                raise DouyinLookupError("抖音公开房间接口资料格式异常")
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                owner = room.get("owner") or data.get("user") or {}
                if not isinstance(owner, dict):
                    continue
                uid = str(owner.get("id_str") or owner.get("id") or "")
                if (str(owner.get("web_rid") or "") != raw or
                        not re.fullmatch(r"[1-9][0-9]{0,19}", uid) or
                        type(room.get("status")) is not int or not room.get("id_str")):
                    continue
                return {"room": dict(room, owner=owner),
                        "ttwid": response.cookies.get("ttwid") or visitor}
        raise DouyinLookupError("抖音公开房间接口没有返回身份匹配的房间，未导入其他主播")

    async def room_data_async(self, session, room_id):
        # 弹幕使用可取消的异步请求，共用关注/视频的身份校验与分享页解析。
        canonical = self.normalize(room_id)
        uid = self._anchor_uids.get(canonical)
        if uid:
            async with session.get("https://live.douyin.com/webcast/room/info_by_user/",
                    params={"aid": 6383, "user_id": uid}, headers={
                        "User-Agent": "Mozilla/5.0", "Referer": "https://live.douyin.com/"}) as response:
                response.raise_for_status()
                internal = self._share_identity(await response.json(), uid)
            if internal:
                async with session.get("https://webcast.amemv.com/webcast/reflow/" + internal,
                        headers={"User-Agent": "Mozilla/5.0"}) as response:
                    response.raise_for_status()
                    visitor = response.cookies.get("ttwid")
                    room = self._share_info(await response.text(encoding="utf-8"), internal, uid,
                                            visitor.value if visitor else "")
                if room["owner"]["web_rid"] != canonical.split(":", 1)[1]:
                    raise ValueError("Mismatched Douyin room")
                return {"room": room, "ttwid": room.pop("ttwid", "")}
        async with session.get(self.room_url(canonical),
                cookies={"__ac_nonce": uuid.uuid4().hex[:21]}) as response:
            response.raise_for_status()
            info = self._page_info(await response.text())
            visitor = response.cookies.get("ttwid")
            info["ttwid"] = visitor.value if visitor else ""
        owner = info["room"].get("owner") or info.get("anchor") or {}
        owner_uid = str(owner.get("id_str") or owner.get("id") or "")
        if uid and owner_uid != uid:
            raise ValueError("Mismatched Douyin anchor")
        if re.fullmatch(r"[0-9]{1,20}", owner_uid):
            self._anchor_uids[canonical] = owner_uid
        return info

    @staticmethod
    def _page_info(page):
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
                        return info
                except (KeyError, TypeError, AttributeError):
                    continue
        raise DouyinLookupError("抖音直播间网页未返回可识别的房间资料，可能是验证页或页面格式变化；请稍后重试，持续失败时请反馈完整链接、插件版本与失败时间")

    def _streams(self, session, room_id, quality=250, *, preview=False):
        room = self.room_data(room_id, session)["room"]
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


class PlatformLogoControl(QObject):
    """插件自己的设置入口与关注卡片标识；不修改本体类。"""
    def __init__(self, context, platforms, label):
        super().__init__(context.window)
        self.context = context
        self.platforms = platforms
        self.label = label
        self.name = context.name + "_show_platform_logo"
        self.enabled = bool(context.setting("show_platform_logo", True))
        QApplication.instance().installEventFilter(self)
        self.refresh()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Show and isinstance(watched, SettingsDialog):
            manager = (watched._plugin_manager if hasattr(watched, "_plugin_manager") else
                       watched.plugin_page.manager)
            if manager is self.context.manager:
                page = watched.general_page
                if page.findChild(QCheckBox, self.name) is None:
                    anchor = next((page._checks[key] for key in ("sidebar_platform_logos", "live_alert")
                                   if key in page._checks), None)
                    if anchor is None:
                        return False
                    grid = page.layout().itemAt(1).layout()
                    row = grid.getItemPosition(grid.indexOf(anchor))[0] + 1
                    check = QCheckBox(self.label, page)
                    check.setObjectName(self.name)
                    check.setChecked(self.enabled)
                    for index in range(grid.count() - 1, -1, -1):
                        old_row, column, rows, columns = grid.getItemPosition(index)
                        if old_row >= row:
                            item = grid.takeAt(index)
                            grid.addItem(item, old_row + 1, column, rows, columns)
                    grid.addWidget(check, row, 0, 1, 2)
                    watched.accepted.connect(lambda: self.save(check.isChecked()))
        elif (event.type() in (QEvent.Show, QEvent.ShowToParent, QEvent.DynamicPropertyChange)
                and isinstance(watched, QLabel) and watched.objectName() == "NavPlatformBadge"
                and not self.enabled):
            parent = watched.parentWidget()
            while parent is not None and parent.objectName() != "NavItem":
                parent = parent.parentWidget()
            if parent is not None and parent.window() is self.context.window:
                room = parent.room
                platform = ("bilibili" if str(room.get("room_id", "")).isdigit() else
                            room.get("platform") or str(room.get("room_id", "")).partition(":")[0])
                if platform in self.platforms:
                    if watched.property("showPlatform") is not False:
                        watched.setProperty("showPlatform", False)
                    watched.hide()
        return False

    def save(self, enabled):
        self.enabled = bool(enabled)
        self.context.set_setting("show_platform_logo", self.enabled)
        self.refresh()

    def refresh(self):
        sidebar = self.context.window.sidebar
        # 沿用宿主的单平台、悬停预览和紧凑布局规则。
        sidebar._sync_count()
        if not self.enabled:
            for item in sidebar._items:
                room = item.room
                platform = ("bilibili" if str(room.get("room_id", "")).isdigit() else
                            room.get("platform") or str(room.get("room_id", "")).partition(":")[0])
                if platform in self.platforms:
                    item.platform_badge.setProperty("showPlatform", False)
                    item.platform_badge.hide()
                    item.thumb._layout_overlay()

    def detach(self):
        QApplication.instance().removeEventFilter(self)
        for check in self.context.window.findChildren(QCheckBox, self.name):
            check.hide()
            check.deleteLater()
        self.enabled = True
        self.refresh()
        self.deleteLater()


class LivePlatformsPlugin(api.Plugin):
    @staticmethod
    def _diagnostic_reason(error):
        """仅记录错误类别；原始异常可能带签名 URL、代理密码和 Cookie。"""
        reasons, seen = [], set()
        while error is not None and id(error) not in seen and len(reasons) < 5:
            seen.add(id(error))
            detail = type(error).__name__
            if isinstance(error, requests.HTTPError) and error.response is not None:
                detail += f"(HTTP {error.response.status_code})"
            elif isinstance(error, requests.exceptions.SSLError):
                detail += "(证书校验失败)" if "CERTIFICATE_VERIFY_FAILED" in str(error) else "(TLS连接失败)"
            elif isinstance(error, requests.exceptions.ProxyError):
                detail += "(代理连接失败)"
            elif isinstance(error, requests.ConnectionError):
                if any(token in str(error) for token in ("getaddrinfo failed", "NameResolutionError")):
                    detail += "(域名解析失败)"
            elif isinstance(error, DouyinLookupError):
                detail += f"({error.user_message})"
            reasons.append(detail)
            error = error.__cause__ or error.__context__
        return " <- ".join(reasons)

    def _enable_diagnostics(self, context, platform):
        platform._diagnostic_log = context.log
        for operation in ("room_info", "play_url"):
            original = getattr(platform, operation)

            def diagnosed(*args, _original=original, _operation=operation, **kwargs):
                started = time.monotonic()
                try:
                    return _original(*args, **kwargs)
                except Exception as error:  # noqa: BLE001
                    try:
                        canonical = platform.normalize(args[0] if args else kwargs.get("room_id", ""))
                    except (ValueError, TypeError):
                        canonical = ""
                    room = canonical if re.fullmatch(r"[a-z]+:[A-Za-z0-9_-]{1,80}", canonical) else "分享链接或无效输入"
                    mapped = (f" UID映射={'有' if platform._anchor_uids.get(canonical) else '无'}"
                              if platform.kind == "douyin" else "")
                    context.log(f"[平台诊断] 平台={platform.kind} 阶段={_operation} 房间={room}"
                                f"{mapped} 耗时={time.monotonic() - started:.1f}s "
                                f"原因={self._diagnostic_reason(error)}")
                    raise

            setattr(platform, operation, diagnosed)

    @staticmethod
    def _player_error_category(prefix, text):
        lower = text.lower()
        status = re.search(r"HTTP(?:/\d(?:\.\d)?| error)?\s+([45]\d\d)", text, re.IGNORECASE)
        if status:
            return "HTTP_" + status[1]
        if any(word in lower for word in ("certificate", "tls", "ssl")):
            return "TLS_OR_CERTIFICATE"
        if any(word in lower for word in ("getaddrinfo", "resolve", "name or service not known")):
            return "DNS"
        if "timed out" in lower or "timeout" in lower:
            return "TIMEOUT"
        if prefix.startswith(("vd", "ffmpeg/video")) or "decoder" in lower or "hwdec" in lower:
            return "VIDEO_DECODE"
        if prefix.startswith("vo") or any(word in lower for word in ("direct3d", "d3d11", "swapchain")):
            return "VIDEO_OUTPUT"
        if prefix.startswith("ao") or "wasapi" in lower:
            return "AUDIO_OUTPUT"
        if "invalid data" in lower or prefix.startswith("demux"):
            return "DEMUX_OR_FORMAT"
        if any(word in lower for word in ("connection", "network", "failed to open", "protocol")):
            return "NETWORK_OR_OPEN"
        return "OTHER_MPV_ERROR"

    def _detach_player_diagnostic(self, tile):
        record = self._diagnostic_players.pop(tile, None)
        if record is None:
            return
        try:
            record["core"].unregister_event_callback(record["callback"])
        except (ValueError, RuntimeError):
            pass
        try:
            record["player"].stateChanged.disconnect(record["state_callback"])
        except (RuntimeError, TypeError):
            pass

    def on_event(self, event, payload):
        if event == api.EVENT_CLOSING and getattr(self, "_huya_interaction", None) is not None:
            self._huya_interaction.shutdown()
        if event == api.EVENT_CLOSING and getattr(self, "_douyu_interaction", None) is not None:
            self._douyu_interaction.shutdown()
        if (event == api.EVENT_STREAM_FAILED and self.context is not None and payload.get("source_url")
                and getattr(self, "_diagnostic_enabled", False)):
            room = str((payload.get("room") or {}).get("room_id") or "")
            if room.startswith("huya:"):
                self.context.platform("huya").report_playback_failure(
                    room, payload["source_url"], payload.get("reason"))
        if event == api.EVENT_CLOSING and self.context is not None:
            platform = self.context.platform("douyin")
            if platform is not None:
                # 关闭事件位于主线程；持久化已确认的映射，不在网络线程读取界面或写配置。
                platform.restore_follow_rooms([])
        if not getattr(self, "_diagnostic_enabled", False):
            return
        if event == api.EVENT_CLOSING:
            for tile in list(self._diagnostic_players):
                self._detach_player_diagnostic(tile)
            return
        tile = payload.get("tile")
        if event == api.EVENT_TILE_STOPPED:
            self._detach_player_diagnostic(tile)
            return
        if event != api.EVENT_STREAM_RESOLVED:
            return
        source = payload.get("source")
        room = str(getattr(source, "room_id", ""))
        if not re.fullmatch(r"(?:huya|douyu|douyin):[A-Za-z0-9_-]{1,80}", room):
            self._detach_player_diagnostic(tile)
            return
        context = self.context
        player = getattr(context.window, "players", {}).get(tile)
        core = getattr(player, "player", None)
        if core is None or not callable(getattr(core, "register_event_callback", None)):
            context.log(f"[平台诊断] 阶段=player_attach 房间={room} 结果=播放器不支持事件观察")
            return
        previous = self._diagnostic_players.get(tile)
        if previous and previous["core"] is core:
            previous["room"] = room
            previous["seen"].clear()
            return
        self._detach_player_diagnostic(tile)
        record = {"core": core, "player": player, "room": room, "seen": set()}

        def callback(event):
            if event.event_id.value != 2:  # libmpv LOG_MESSAGE；只观察现有 error 日志，不改变日志级别。
                return
            data = event.data
            if data.level not in ("error", "fatal"):
                return
            category = self._player_error_category(data.prefix, data.text)
            if category not in record["seen"]:
                record["seen"].add(category)
                context.log(f"[平台诊断] 阶段=player_error 房间={record['room']} 类别={category}")

        def state_callback(state):
            if state in ("playing", "buffering", "error"):
                mode = player.decode_mode if player.decode_mode in ("auto", "none", "d3d11va", "nvdec", "dxva2") else "other"
                context.log(f"[平台诊断] 阶段=player_state 房间={record['room']} 状态={state} 解码方式={mode}")

        record.update(callback=callback, state_callback=state_callback)
        core.register_event_callback(callback)
        player.stateChanged.connect(state_callback)
        self._diagnostic_players[tile] = record
        context.log(f"[平台诊断] 阶段=player_attach 房间={room} 结果=已开始观察播放器")

    def on_load(self, context: api.PluginContext) -> None:
        huya = HuyaPlatform()
        huya._context = context
        context.register_platform(huya)
        douyu = DouyuPlatform()
        douyu._context = context
        context.register_platform(douyu)
        if callable(getattr(context, "register_tile_control", None)) and hasattr(api, "TileControl"):
            from .douyu_composer import DouyuInteraction, HuyaComposer
            self._douyu_interaction = DouyuInteraction(context, douyu)
            self._huya_interaction = DouyuInteraction(context, huya, composer_class=HuyaComposer, control_id="huya_interaction")
        douyin = DouyinPlatform()
        saved = context.setting("douyin_room_owners", {})
        if isinstance(saved, dict):
            douyin.restore_follow_rooms([{"room_id": rid, "anchor_uid": uid} for rid, uid in saved.items()])
        douyin._context = context
        context.register_platform(douyin)
        self._diagnostic_enabled = True
        self._diagnostic_players = {}
        for platform in (huya, douyu, douyin):
            self._enable_diagnostics(context, platform)
        context.log("[平台诊断] 已启用；记录失败响应特征与播放器错误类别，不记录正文、凭证或请求地址")
        if QApplication.instance() is not None and context.window is not None:
            self._logo_control = PlatformLogoControl(context, ("bilibili", "huya", "douyu", "douyin"), "显示国内平台 Logo")

    def on_unload(self):
        if getattr(self, "_huya_interaction", None) is not None:
            self._huya_interaction.shutdown()
            self._huya_interaction = None
        if getattr(self, "_douyu_interaction", None) is not None:
            self._douyu_interaction.shutdown()
            self._douyu_interaction = None
        for tile in list(getattr(self, "_diagnostic_players", {})):
            self._detach_player_diagnostic(tile)
        self._diagnostic_enabled = False
        if getattr(self, "_logo_control", None) is not None:
            self._logo_control.detach()
            self._logo_control = None


plugin = LivePlatformsPlugin()
