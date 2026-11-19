"""Huya account, follow, room and playback adapter; state belongs to each platform."""
import base64
import hashlib
from html import unescape
import json
import os
import re
import struct
import threading
import time
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

import requests
from streamlink import Streamlink
from streamlink.plugins.huya import Huya
from streamlink.stream.http import HTTPStream
from ddm import plugins as api
from ddm.live_danmaku import tars_int
from .huya_protocol import huya_string, huya_user, huya_wup_request, huya_follow_request
from .platform_base import LiveQualityPlatform
from .diagnostic_errors import diagnostic_reason

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
        if reason not in ("HTTP_401", "HTTP_403", "HTTP_404", "HTTP_410", "HTTP_502", "HTTP_503", "HTTP_504", "STALL", "LIVE_EOF",
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
            # A fresh playlist probe can pass again after playback failed with 403.
            # Do not rotate through HLS forever while a healthy signed FLV is available.
            prefer_flv = (any(urlsplit(stream.to_url()).hostname in failures for stream in hls) and
                          any(urlsplit(stream.to_url()).hostname not in failures for stream in flv))
            candidates = (flv[:2] + hls[:2] if prefer_flv else hls[:2] + flv[:2]) if flv else hls[:4]
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
                               f"CDN={urlsplit(playable).hostname} 画质={qn} "
                               f"传输={getattr(stream, 'ddm_transport', 'hls')} 避让线路数={len(failures)}")
                    return playable, qn if isinstance(qn, int) else 10000, "huya", headers
                except requests.RequestException as error:
                    logger = getattr(self, "_diagnostic_log", None)
                    if logger is not None:
                        logger(f"[平台诊断] 阶段=source_probe 房间={self.normalize(room_id)} "
                               f"CDN={urlsplit(url).hostname} 原因={diagnostic_reason(error)}")
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
