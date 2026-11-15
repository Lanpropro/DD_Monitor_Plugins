"""public live: extracted platform responsibility."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
import zlib
from urllib.parse import parse_qs, quote, unquote, urlsplit
import requests
from streamlink import Streamlink
from streamlink.stream.hls import HLSStream
from ddm import plugins as api
from ddm.auto_quality import AUTO_QUALITY
from ddm.global_danmaku import GlobalDanmakuClient, page_json, renderers
from ddm.live_danmaku import USER_AGENT


from .public_http import public_request, official_url, image_url, thumbnail  # Legacy helper exports.


class PublicLivePlatform(api.Platform):
    playback_mode = "stream"
    hosts = ()

    def __init__(self):
        self._qualities = {}

    def matches(self, room_id):
        text = str(room_id or "").strip()
        if text.startswith(self.kind + ":"):
            return True
        try:
            return urlsplit(text).hostname in self.hosts
        except ValueError:
            return False

    def room_quality_options(self, room_id):
        return [{"qn": AUTO_QUALITY, "desc": "自动（根据网络选择流畅的最高画质）", "label": "自动"}] + [
                {key: item[key] for key in ("qn", "desc", "label", "bandwidth") if key in item} for item in
                self._qualities.get(room_id, [{"qn": 10000, "desc": "最高可用", "label": "最高可用"}])]

    def rooms_status(self, room_ids):
        result = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self.room_info, rid): rid for rid in room_ids}
            for future in as_completed(futures):
                try:
                    result[futures[future]] = future.result().as_dict()
                except Exception:  # noqa: BLE001
                    continue  # 网络失败保留上次状态
        if room_ids and not result:
            raise RuntimeError(f"{self.label} 状态获取失败")
        return result

    def _quality_streams(self, streams):
        options = []
        for name, stream in streams.items():
            if name in ("best", "worst") or not isinstance(stream, HLSStream):
                continue
            master = stream.multivariant
            playlist = next((item for item in master.playlists if item.uri == stream.to_url()), None) if master else None
            info = playlist.stream_info if playlist else None
            resolution = info.resolution if info else None
            if not resolution or not any(codec.startswith(("avc1", "avc3")) for codec in info.codecs):
                continue
            width, height = resolution.width, resolution.height
            fps = info.framerate or 0
            desc = f"{name} · {width}x{height}" + (f" · {fps:g}fps" if fps else "")
            options.append({"key": name, "desc": desc, "label": name,
                            "pixels": width * height, "fps": fps, "bandwidth": info.bandwidth or 0})
        options.sort(key=lambda item: (item["pixels"], item["fps"], item["bandwidth"]), reverse=True)
        for index, item in enumerate(options):
            item["qn"] = 10000 if index == 0 else 5000000 + (zlib.crc32(item["key"].encode()) & 0xFFFFFF)
        return options

    def play_url(self, room_id, quality=10000, *, preview=False):
        canonical = self.normalize(room_id)
        session = Streamlink({"http-timeout": 8, "webbrowser": False})
        session.http.request = partial(session.http.request, retries=2)
        try:
            parser = self.parser(session, self.room_url(canonical))
            streams = parser.streams()
            options = self._quality_streams(streams)
            if not options:
                raise RuntimeError("没有可用的公开 H.264 直播流")
            self._qualities[canonical] = options
            selected = options[-1] if preview or quality == AUTO_QUALITY else next(
                (item for item in options if item["qn"] == quality), options[0])
            url = streams[selected["key"]].to_url()
            return url, selected["qn"], self.kind, {"User-Agent": USER_AGENT, "Referer": self.origin}
        except api.LoginRequiredError:
            raise
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"{self.label} 直播取流失败，请稍后重试") from error
        finally:
            session.http.close()

    def preview_url(self, room_id):
        return self.play_url(room_id, preview=True)

    def danmaku_client(self, room_id, parent=None):
        return GlobalDanmakuClient(room_id, self, parent)
