"""Douyin playback; state stays on the platform instance."""
import json
import re
import zlib
from urllib.parse import urlsplit
from streamlink.plugins.douyin import Douyin
from streamlink.stream.http import HTTPStream


class DouyinPlayback:
    def _playback_cookies(self):
        from .douyin_interaction import playback_cookies
        return playback_cookies(self)

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
