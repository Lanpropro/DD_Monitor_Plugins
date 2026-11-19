"""Shared room input, quality metadata and numeric-platform playback contracts."""
import re
from urllib.parse import urljoin, urlsplit

import requests
from streamlink import Streamlink
from streamlink.exceptions import PluginError
from ddm import plugins as api

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
