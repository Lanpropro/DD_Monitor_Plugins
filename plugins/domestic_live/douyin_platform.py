"""Douyin room lookup and playback adapter."""
import json
from pathlib import Path
import re
import time
import uuid
import zlib
from urllib.parse import parse_qsl, urlsplit

import requests
from streamlink.plugin.api import useragents
from streamlink.plugins.douyin import Douyin
from streamlink.stream.http import HTTPStream
from ddm import plugins as api
from .platform_base import NumericLivePlatform

from .douyin_errors import DouyinLookupError, DouyinVerificationRequired


from .douyin_accounts import DouyinAccounts


class DouyinPlatform(DouyinAccounts, NumericLivePlatform):
    kind = "douyin"
    interaction_user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/130.0.0.0 Safari/537.36")
    share_hosts = ("v.douyin.com", "webcast.amemv.com")
    label = "抖音"
    room_input_hint = "抖音直播间链接"
    profile_browser_init_script = (Path(__file__).parent / "douyin_profile.js").read_text(encoding="utf-8")
    room_browser_init_script = (Path(__file__).parent / "douyin_room.js").read_text(encoding="utf-8")
    hosts = ("live.douyin.com", "douyin.com", "www.douyin.com")
    image_hosts = ("douyinpic.com", "byteimg.com", "ibytedtos.com", "douyincdn.com")
    def __init__(self):
        super().__init__()
        from .douyin_interaction import BroadcastReceipts
        self._chat_delivery = BroadcastReceipts()
        self._anchor_uids = {}
        self._follow_profiles = {}
        self._follow_faces = {}
        self._profile_additions = {}
        self._context = None

    def normalize(self, room_id):
        text = str(room_id or "").strip()
        uid = self._follow_reference_uid(text)
        if uid:
            return next((rid for rid, owner in list(self._anchor_uids.items()) if owner == uid), text)
        return super().normalize(text)

    def room_url(self, room_id):
        canonical = self.normalize(room_id)
        uid = self._follow_reference_uid(canonical)
        if uid:
            sec_uid = self._follow_profiles.get(uid)
            if not sec_uid:
                raise ValueError("账号主页资料尚未读取，请重新读取关注")
            return "https://www.douyin.com/user/" + sec_uid
        return super().room_url(canonical)

    def _share_room(self, session, uid, cancelled, *, canonical="", identity_only=False):
        # 个人页下播后没有 room_data；直播资料的 web_rid 也可能为空。
        # 通过最近直播记录的内部 ID 读取官方分享页，得到长期有效的网页房间号。
        with session.get("https://live.douyin.com/webcast/room/info_by_user/",
                params={"aid": 6383, "user_id": uid},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://live.douyin.com/"},
                timeout=(4, 8)) as response:
            response.raise_for_status()
            payload = response.json()
        internal = self._share_identity(payload, uid)
        if cancelled():
            return {}
        if (canonical and type(payload.get("status_code")) is int and payload["data"] == {} and
                re.fullmatch(r"[1-9][0-9]{0,19}", uid)):
            # A successful empty record means this known anchor has no current live.
            # Keep the verified fixed room; there is no live share page to resolve.
            return {"status": 4, "owner": {"id_str": uid, "web_rid": canonical.split(":", 1)[1]}}
        if not internal:
            return {}
        room = payload["data"]
        if identity_only and type(room.get("status")) is int:
            owner = room.get("owner") or {}
            owner_uid = str(owner.get("id_str") or owner.get("id") or uid)
            rid = str(room.get("web_rid") or owner.get("web_rid") or
                      (canonical.split(":", 1)[1] if canonical else ""))
            if owner_uid != uid or (canonical and rid != canonical.split(":", 1)[1]):
                raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
            if re.fullmatch(r"[1-9][0-9]{0,19}", rid):
                # 关注只需要身份和状态；接口已确认时不再依赖直播分享页或媒体流。
                return dict(room, owner=dict(owner, id_str=uid, web_rid=rid))
            if not rid and room["status"] == 4:
                return dict(room, owner=dict(owner, id_str=uid, web_rid=""))
        if canonical and type(room.get("status")) is int and room["status"] == 4:
            # The owner mapping and API identity are verified; an ended room need not
            # expose a usable live share page (or any streams) to confirm its status.
            raw = canonical.split(":", 1)[1]
            owner = room.get("owner") or {}
            if (str(owner.get("id_str") or owner.get("id") or uid) != uid or
                    str(owner.get("web_rid") or raw) != raw):
                raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
            return dict(room, owner=dict(owner, id_str=uid, web_rid=raw))
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
        if type(payload.get("status_code")) is not int or payload["status_code"] != 0 or not isinstance(data, dict):
            raise RuntimeError("抖音关注主播信息获取失败，请稍后重试")
        if not data:
            return ""
        internal = str(data.get("id_str") or "")
        if not re.fullmatch(r"[0-9]{1,20}", internal) or str(data.get("owner_user_id")) != uid:
            raise RuntimeError("抖音关注主播信息不匹配，请稍后重试")
        return internal

    @staticmethod
    def _sdk_stream_data(page, value):
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

    @staticmethod
    def _share_info(page, internal, uid, visitor):
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
                        "live_core_sdk_data": {"pull_data": {"stream_data": DouyinPlatform._sdk_stream_data(page, pull.get("streamData", "")),
                            "options": {"qualities": [
                            dict(item, sdk_key=item.get("sdkKey")) for item in qualities]}}}}}

        raise RuntimeError("抖音直播分享页信息获取失败，请稍后重试")

    def danmaku_client(self, room_id, parent=None):
        client = super().danmaku_client(room_id, parent)
        canonical = self.normalize(room_id)
        client.message.connect(lambda event: self._chat_delivery.observe(canonical, event))
        return client

    def _playback_cookies(self):
        from .douyin_interaction import playback_cookies
        return playback_cookies(self)

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
            reference_uid = self._follow_reference_uid(room_id)
            if reference_uid and self.normalize(room_id) == room_id:
                result = self.resolve_follow_account(dict(room_id=room_id, anchor_uid=reference_uid,
                    live=False, live_known=False), lambda: False)
                if not result.get("resolved_room_id"):
                    return api.RoomInfo(room_id=room_id, platform=self.kind, uname="", title="", live=False,
                        face=result.get("face") or "",
                        extra={"live_known": result["live_known"], "anchor_uid": reference_uid,
                               "follow_account_ref": True, "playback_mode": "stream"})
            room_id, share = self._resolve_share_input(room_id)
            canonical = self.normalize(room_id)
            raw = canonical.split(":", 1)[1]
            initial = self._profile_additions.pop(canonical, None)
            if initial is not None and time.monotonic() - initial[0] < 60:
                return initial[1]
            info = {"room": share} if share is not None else self.room_data(canonical)
            room = info["room"]
            owner = room.get("owner") or info.get("anchor") or {}
            status_only = room["status"] == 4 and not room.get("id_str")
            owner_uid = str(owner.get("id_str") or owner.get("id") or "")
            face = self._image_url(owner.get("avatar_thumb"))
            if not face and room["status"] == 4:
                face = self._follow_avatar(owner_uid)
            return api.RoomInfo(room_id=canonical, uname="" if status_only else owner.get("nickname") or f"抖音 · {raw}",
                title="" if status_only else room.get("title") or "抖音直播间", live=room["status"] == 2,
                platform=self.kind, face=face,
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
        if self._follow_reference_uid(canonical):
            raise RuntimeError("关注账号当前未取得可用直播间，请等待开播后刷新状态")
        uid = self._anchor_uids.get(canonical)
        if uid:
            try:
                room = self._share_room(requests, uid, lambda: False, canonical=canonical)
                if room:
                    if room["owner"]["web_rid"] != canonical.split(":", 1)[1]:
                        raise ValueError("Mismatched Douyin room")
                    return {"room": room, "ttwid": room.pop("ttwid", "")}
            except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
                cached_error = self._lookup_error("主播接口", error)
                try:
                    return self._room_page(canonical, session)
                except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as page_error:
                    if isinstance(page_error, DouyinVerificationRequired):
                        raise
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
                        raise DouyinVerificationRequired("抖音页面包含验证相关内容，未返回直播资料。请在软件内使用主播个人主页链接添加，"
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
        if self._follow_reference_uid(canonical):
            raise RuntimeError("关注账号当前未取得可用直播间，请等待开播后刷新状态")
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
                        pull = (room.get("stream_url") or {}).get("live_core_sdk_data", {}).get("pull_data", {})
                        if "stream_data" in pull:
                            pull["stream_data"] = DouyinPlatform._sdk_stream_data(page, pull["stream_data"])
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
        # 这些档位对应网页 SDK 的 ld/sd/hd/uhd；分辨率、帧率只用本房间元数据。
        sdk_keys = {"sd1": "ld", "sd2": "sd", "hd1": "hd", "full_hd1": "uhd"}
        metadata = stream_url.get("live_core_sdk_data", {}).get("pull_data", {}).get(
            "options", {}).get("qualities", [])
        metadata = {item.get("sdk_key"): item for item in metadata}
        raw = stream_url.get("live_core_sdk_data", {}).get("pull_data", {}).get("stream_data", "")
        try:
            sdk = json.loads(raw) if isinstance(raw, str) and raw else raw
        except ValueError:
            sdk = {}
        data = sdk.get("data", {}) if isinstance(sdk, dict) else {}
        # 分享页有时不列原画元数据，但 SDK 仍给出实际 origin 地址。
        if isinstance(data, dict) and "origin" in data and "origin" not in metadata:
            metadata["origin"] = {"name": "原画"}
        sdk_names = {value: key for key, value in sdk_keys.items()}
        for sdk_key, item in metadata.items():
            if item.get("disable") or not isinstance(data, dict):
                continue
            main = (data.get(sdk_key) or {}).get("main") or {}
            if not isinstance(main, dict) or main.get("enableEncryption") in (True, 1):
                continue
            key = sdk_names.get(sdk_key, sdk_key)
            for protocol, candidates in (("flv", urls), ("hls", hls_urls)):
                url = main.get(protocol)
                if isinstance(url, str) and urlsplit(url).scheme in ("http", "https"):
                    candidates.setdefault(key, url)
        keys = sorted(urls.keys() | hls_urls.keys(),
                      key=lambda key: (key == "origin", Douyin.stream_weight(key)[0]), reverse=True)
        if not keys:
            return {}
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
