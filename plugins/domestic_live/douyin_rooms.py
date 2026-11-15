"""Douyin room queries; state stays on the platform instance."""
import json
import re
import time
import uuid
from urllib.parse import parse_qsl, urlsplit
import requests
from streamlink.plugin.api import useragents
from ddm import plugins as api
from .douyin_errors import DouyinLookupError, DouyinVerificationRequired
from .douyin_room_data import share_identity, sdk_stream_data, share_info, page_info


class DouyinRooms:
    _share_identity = staticmethod(share_identity)
    _sdk_stream_data = staticmethod(sdk_stream_data)
    _share_info = staticmethod(share_info)
    _page_info = staticmethod(page_info)

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
