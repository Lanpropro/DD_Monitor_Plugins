"""Offline codec negotiation using HLS metadata and FLV video tag fixtures."""
import argparse
import os
import sys
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def flv(payload):
    tag = b"\x09" + len(payload).to_bytes(3, "big") + bytes(7) + payload
    return b"FLV\x01\x01\x00\x00\x00\x09" + bytes(4) + tag + len(tag).to_bytes(4, "big")


def unavailable(api, operation):
    try:
        operation()
    except api.StreamCodecUnavailable:
        return
    raise AssertionError("Unverified codec must be unavailable")


def check_douyin_hls(platform, module, session, api):
    import av
    from av.video.stream import VideoStream

    def encoded(codec, container_format="mpegts"):
        output = BytesIO()
        options = {"movflags": "frag_keyframe+empty_moov"} if container_format == "mp4" else {}
        with av.open(output, mode="w", format=container_format, options=options) as container:
            stream = container.add_stream("libx264" if codec == "avc" else "libx265", rate=25)
            assert isinstance(stream, VideoStream)
            stream.width = stream.height = 16
            stream.pix_fmt = "yuv420p"
            if codec == "hevc":
                stream.options = {"x265-params": "log-level=error:pools=none"}
            frame = av.VideoFrame(16, 16, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            for packet in [*stream.encode(frame), *stream.encode(None)]:
                container.mux(packet)
        return output.getvalue()

    def reply(url, data):
        response = Mock(url=url)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.side_effect = lambda size: (data[index:index + size] for index in range(0, len(data), size))
        return response

    def resolve(routes, codec="avc", urls=("https://cdn.test/hls",)):
        streams = {}
        for index, url in enumerate(urls):
            stream = module.HTTPStream(session, url)
            stream.ddm_quality = 3000010
            streams[str(index)] = stream

        def get(url, **kwargs):
            assert kwargs["stream"] and all(0 < value <= 3 for value in kwargs["timeout"])
            value = routes[url]
            if isinstance(value, Exception):
                raise value
            return reply(url, value)

        with patch.object(platform, "_streams", return_value=streams), \
                patch.object(module, "Streamlink", return_value=session), \
                patch.object(session.http, "get", side_effect=get) as request:
            result = platform.play_url_for_codec("douyin:123", 3000010, codec=codec)
            assert result[1:3] == (3000010, "douyin") and result[-1] == codec
            return result, request.call_count

    masters = {codec: ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,CODECS="' + tag
        + ',mp4a.40.2"\nvideo.m3u8\n').encode()
        for codec, tag in (("avc", "avc1.640028"), ("hevc", "hvc1.1.6.L120"), ("av1", "av01.0.08M.08"))}
    result, calls = resolve({"https://cdn.test/flv": module.requests.HTTPError("FLV rejected"),
        "https://cdn.test/hls": masters["avc"]}, urls=("https://cdn.test/flv", "https://cdn.test/hls"))
    assert result[0] == "https://cdn.test/video.m3u8" and calls == 2
    for codec in ("avc", "hevc"):
        result, calls = resolve({"https://cdn.test/hls": masters[codec]}, codec)
        assert result[0].endswith("/video.m3u8") and calls == 1
        media = encoded(codec)
        assert module.StreamCodecProbe._media_codec(media) == codec
        routes = {"https://cdn.test/hls": b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\npart.ts\n",
                  "https://cdn.test/part.ts": media}
        result, calls = resolve(routes, codec)
        assert result[0].endswith("/hls") and calls == 2
        routes["https://cdn.test/master"] = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nhls\n"
        result, calls = resolve(routes, codec, urls=("https://cdn.test/master",))
        assert result[0].endswith("/hls") and calls == 3
        fragmented = encoded(codec, "mp4")
        split = fragmented.index(b"moof") - 4
        result, calls = resolve({
            "https://cdn.test/hls": b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2,\npart.m4s\n',
            "https://cdn.test/init.mp4": fragmented[:split], "https://cdn.test/part.m4s": fragmented[split:]}, codec)
        assert result[0].endswith("/hls") and calls == 3
    unavailable(api, partial(resolve, {"https://cdn.test/hls": masters["av1"]}))
    unavailable(api, partial(resolve, {"https://cdn.test/hls": masters["av1"]}, "hevc"))
    try:
        resolve({"https://cdn.test/flv": module.requests.Timeout("fixture timeout"),
                 "https://cdn.test/hls": b"unrecognized"}, urls=("https://cdn.test/flv", "https://cdn.test/hls"))
    except api.StreamCodecUnavailable:
        raise AssertionError("A failed CDN must remain a network failure if no candidate succeeds") from None
    except RuntimeError:
        pass
    else:
        raise AssertionError("Failed candidates must terminate")

    probe = module.StreamCodecProbe(session, {})
    probe.remaining = 25
    response = reply("https://cdn.test/huge", b"x" * 1000000)
    with patch.object(session.http, "get", return_value=response) as get:
        assert len(probe._read(response.url)[1]) == 25 and probe.remaining == 0
        try:
            probe._read(response.url)
        except ValueError:
            pass
        else:
            raise AssertionError("Total byte budget must stop new requests")
        assert get.call_count == 1
        response.__exit__.assert_called_once()
    probe = module.StreamCodecProbe(session, {})
    with patch.object(session.http, "get", return_value=reply("https://cdn.test/small", b"x")) as get:
        for _index in range(8):
            probe._read("https://cdn.test/small")
        try:
            probe._read("https://cdn.test/small")
        except ValueError:
            pass
        else:
            raise AssertionError("Request budget must terminate recursive manifests")
        assert get.call_count == 8
    with patch.object(module.time, "monotonic", return_value=probe.deadline + 1), \
            patch.object(session.http, "get") as get:
        try:
            probe._read("https://cdn.test/late")
        except module.requests.Timeout:
            pass
        else:
            raise AssertionError("Deadline must stop requests")
        get.assert_not_called()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    os.environ["DDM_NO_SAVE"] = "1"
    from ddm import plugins as api
    manager = api.PluginManager(plugins_dir=str(ROOT / "plugins"), enabled=["domestic_live", "global_live"])
    manager.load()
    platforms: dict[str, Any] = manager.platforms
    domestic = sys.modules[type(platforms["douyin"]).__module__]
    overseas = sys.modules[type(platforms["twitch"]).__module__]
    from streamlink import Streamlink
    from streamlink.stream.hls import HLSStream, parse_m3u8
    session = Streamlink()
    try:
        samples = {"avc": flv(b"\x17\x00\x00\x00\x00\x01"),
                   "hevc": flv(b"\x90hvc1\x01"), "av1": flv(b"\x90av01\x01")}
        for codec, value in samples.items():
            expected = codec
            assert domestic.flv_video_codec([value]) == expected
            assert domestic.flv_video_codec([value[i:i + 3] for i in range(0, len(value), 3)]) == expected
        for value in (b"#EXTM3U", b"FLV", flv(b"\x17")[:-5], bytes(300000)):
            assert domestic.flv_video_codec([value]) == ""

        for kind in ("huya", "douyu"):
            platform = platforms[kind]
            with patch.object(platform, "play_url", return_value=("https://cdn.test/source", 10000, kind, {})) as play:
                unavailable(api, partial(platform.play_url_for_codec, kind + ":123", 10000, codec="hevc"))
                unavailable(api, partial(platform.play_url_for_codec, kind + ":123", 10000, codec="av1"))
                play.assert_not_called()
                assert platform.play_url_for_codec(kind + ":123", 10000, codec="avc")[-1] == "avc"
                play.assert_called_once_with(kind + ":123", 10000)

        douyu = platforms["douyu"]
        parser = Mock(session=session, DID="fixture", _URL_PLAY="https://www.douyu.com/play/{rid}")
        parser._get_encryption.return_value = (123, {"key": "fixture", "rand_str": "fixture",
            "enc_time": 123, "is_special": False, "enc_data": "fixture"})
        reply = Mock()
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        reply.json.return_value = {"error": 0, "data": {"rtmp_url": "https://cdn.test", "rtmp_live": "live.flv"}}
        with patch.object(session.http, "post", return_value=reply) as post:
            douyu._request_source(parser, "123")
            assert post.call_args.kwargs["data"]["hevc"] == "0"
        stream = domestic.HTTPStream(session, "https://cdn.test/live.flv")
        stream.ddm_quality = 10000
        with patch.object(douyu, "_streams", return_value={"source": stream}), \
                patch.object(domestic, "Streamlink", return_value=session), \
                patch.object(session.http, "get", side_effect=AssertionError("Douyu must not probe media")):
            assert douyu.play_url_for_codec("douyu:123", codec="avc")[-1] == "avc"

        platform = platforms["douyin"]
        stream = domestic.HTTPStream(session, "https://cdn.test/live.flv")
        stream.ddm_quality = 3000010
        response = Mock(url="https://cdn.test/live.flv")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(platform, "_streams", return_value={"source": stream}), \
                patch.object(domestic, "Streamlink", return_value=session), \
                patch.object(session.http, "get", return_value=response) as get:
            response.iter_content.side_effect = lambda _size: iter([samples["hevc"]])
            result = platform.play_url_for_codec("douyin:123", 3000010, codec="hevc")
            assert result[1:3] == (3000010, "douyin") and result[-1] == "hevc"
            assert result[3]["Referer"] == "https://live.douyin.com/"
            assert get.call_count == 1
            unavailable(api, partial(platform.play_url_for_codec, "douyin:123", codec="avc"))
            response.iter_content.side_effect = lambda _size: iter([samples["av1"]])
            unavailable(api, partial(platform.play_url_for_codec, "douyin:123", codec="hevc"))
            get.side_effect = domestic.requests.Timeout("fixture timeout")
            try:
                platform.play_url_for_codec("douyin:123", codec="hevc")
            except api.StreamCodecUnavailable:
                raise AssertionError("Network failure cannot become codec unavailable") from None
            except RuntimeError:
                pass
            else:
                raise AssertionError("Network failure must terminate")

        check_douyin_hls(platform, domestic, session, api)

        def hls(name, codecs, height=1080):
            stream = HLSStream(session, "https://cdn.test/" + name + ".m3u8")
            stream.multivariant = parse_m3u8(
                '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x'
                + str(height) + ',FRAME-RATE=60,CODECS="' + ','.join(codecs) + '"\n' + stream.to_url(),
                base_uri='https://cdn.test/master.m3u8')
            return stream

        streams = {"source": hls("source", ["avc1.640028", "mp4a.40.2"]),
                   "low": hls("low", ["avc3.640028"], 360),
                   "hevc": hls("hevc", ["hvc1.1.6.L120.90", "mp4a.40.2"]),
                   "hevc_low": hls("hevc_low", ["hev1.1.6.L120.90"], 360),
                   "av1": hls("av1", ["av01.0.08M.08"]),
                   "unknown": hls("unknown", []),
                   "mixed": hls("mixed", ["hvc1.1.6.L120.90", "av01.0.08M.08"])}
        for kind, room in (("twitch", "twitch:fixture"), ("youtube", "youtube:abcdefghijk")):
            platform = platforms[kind]
            avc = platform._quality_streams(streams)
            assert [item["key"] for item in avc] == ["source", "low"]
            assert avc[0]["qn"] == 10000
            assert [item["key"] for item in platform._quality_streams(streams, "hevc")] == ["hevc", "hevc_low"]
            with patch.object(platform, "parser") as parse:
                parse.return_value.streams.return_value = streams
                result = platform.play_url_for_codec(room, 10000, codec="hevc")
                assert result[0].endswith("/hevc.m3u8") and result[-1] == "hevc"
                if kind == "twitch":
                    assert parse.call_args.kwargs["options"] == {"supported-codecs": ["h265"]}
                assert platform.play_url_for_codec(room, overseas.AUTO_QUALITY, codec="hevc")[0].endswith("/hevc_low.m3u8")
                assert platform.play_url(room)[0].endswith("/source.m3u8")
                assert len(platform.play_url(room)) == 4
                assert platform.preview_url(room)[0].endswith("/low.m3u8")
                assert platform._qualities[room][1]["qn"] == avc[1]["qn"]
                if kind == "twitch":
                    assert parse.call_args.kwargs["options"] == {"supported-codecs": ["h264"]}
                parse.return_value.streams.return_value = {"av1": streams["av1"]}
                unavailable(api, partial(platform.play_url_for_codec, room, codec="hevc"))
                unavailable(api, partial(platform.play_url_for_codec, room, codec="av1"))
                parse.return_value.streams.side_effect = overseas.requests.Timeout("fixture timeout")
                try:
                    platform.play_url_for_codec(room, codec="hevc")
                except api.StreamCodecUnavailable:
                    raise AssertionError("Parser failure cannot become codec unavailable") from None
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Parser failure must terminate")
        twitch_parser = overseas.Twitch(session, "https://www.twitch.tv/fixture", options={"supported-codecs": ["h265"]})
        assert twitch_parser.usher.supported_codecs == ["h265"]
    finally:
        session.http.close()
        manager.unload()
    print("PASS: strict AVC/HEVC, AV1 rejection, Twitch preferences, quality/preview compatibility and bounded failures")


if __name__ == "__main__":
    main()
